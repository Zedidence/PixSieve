"""
Tests for ScanOrchestrator - specifically the discover+analyze merge that
lets directory-walking and image analysis overlap, plus the worker-scaling,
resolve_symlinks, and calculate_phash wiring introduced alongside it.
"""

import os

import pytest

from pixsieve.state import ScanState
from pixsieve.api import orchestrator as orch_mod
from pixsieve import config
from pixsieve.api.orchestrator import ScanOrchestrator
from pixsieve.config import LARGE_LIBRARY_WORKERS
from pixsieve.utils import disk_type
from pixsieve.utils.disk_type import Bus, DriveProfile, Media


@pytest.fixture(autouse=True)
def _no_history_file(monkeypatch):
    """Never touch the real ~/.duplicate_finder_history.json during tests."""
    monkeypatch.setattr(orch_mod.HistoryManager, 'save_directory', staticmethod(lambda directory: None))


def _make_orchestrator(temp_dir, **overrides):
    scan_state = ScanState()
    kwargs = dict(
        scan_state=scan_state,
        directories=[{'path': str(temp_dir), 'is_reference': False}],
        threshold=10,
        exact_only=False,
        perceptual_only=False,
        use_cache=False,
    )
    kwargs.update(overrides)
    return ScanOrchestrator(**kwargs), scan_state


class TestDiscoverAndAnalyzeIntegration:
    """Real (non-mocked) runs over a small temp directory of sample images."""

    def test_finds_exact_duplicates_end_to_end(self, temp_dir, sample_images):
        orchestrator, scan_state = _make_orchestrator(temp_dir)
        orchestrator.run()

        assert scan_state.status == 'complete'
        assert scan_state.total_files >= 5
        exact_groups = [g for g in scan_state.groups if g.match_type == 'exact']
        # Discovery resolves symlinks by default, so compare canonical paths -
        # the raw temp path differs on macOS (/var -> /private/var) and on
        # Windows runners (8.3 short names like RUNNER~1).
        identical_paths = {
            os.path.realpath(sample_images['identical1']),
            os.path.realpath(sample_images['identical2']),
        }
        assert any(
            identical_paths <= {os.path.realpath(img.path) for img in g.images}
            for g in exact_groups
        )

    def test_exact_only_skips_perceptual_hash_computation(self, temp_dir, sample_images):
        orchestrator, scan_state = _make_orchestrator(temp_dir, exact_only=True)
        orchestrator.run()

        assert scan_state.status == 'complete'
        assert all(g.match_type == 'exact' for g in scan_state.groups)

    def test_resolve_symlinks_false_still_discovers_plain_files(self, temp_dir, sample_images):
        orchestrator, scan_state = _make_orchestrator(temp_dir, resolve_symlinks=False)
        orchestrator.run()

        assert scan_state.status == 'complete'
        assert scan_state.total_files >= 5

    def test_empty_directory_completes_with_no_images_message(self, temp_dir):
        empty = temp_dir / 'empty'
        empty.mkdir()
        orchestrator, scan_state = _make_orchestrator(empty)
        orchestrator.run()

        assert scan_state.status == 'complete'
        assert 'No images found' in scan_state.message


class TestOrchestratorWiring:
    """Mock out analyze_images_streaming to isolate the orchestrator's own
    decisions (worker scaling, calculate_phash, resolve_symlinks passthrough)
    from the scanner internals, which are covered separately in
    test_scanner.py."""

    def _stub_streaming(self, monkeypatch, captured):
        def _fake_streaming(chunk_generator, **kwargs):
            # Drain the generator so discovered_callback/side effects run,
            # mirroring what the real function would do.
            for chunk in chunk_generator:
                pass
            captured.update(kwargs)
            from pixsieve.database import CacheStats
            return [], CacheStats(total_files=0)

        monkeypatch.setattr(orch_mod, 'analyze_images_streaming', _fake_streaming)

    def test_auto_workers_use_large_library_default_when_tuning_off(self, temp_dir, monkeypatch):
        captured = {}
        self._stub_streaming(monkeypatch, captured)

        orchestrator, scan_state = _make_orchestrator(temp_dir)
        orchestrator.run()

        assert captured['max_workers'] == LARGE_LIBRARY_WORKERS

    def test_auto_workers_follow_drive_type(self, temp_dir, monkeypatch):
        captured = {}
        self._stub_streaming(monkeypatch, captured)
        monkeypatch.setattr(config, 'AUTO_WORKERS', True)
        monkeypatch.setattr(orch_mod, 'warm_up', lambda paths: None)
        monkeypatch.setattr(disk_type, 'detect_drive',
                            lambda path: DriveProfile('usb', Media.HDD, Bus.USB))

        orchestrator, scan_state = _make_orchestrator(temp_dir)
        orchestrator.run()

        assert captured['max_workers'] == 2
        assert captured['stat_workers'] == 2
        storage = scan_state.settings['storage']
        assert storage['label'] == 'external HDD'
        assert storage['workers'] == 2
        assert scan_state.settings['detected_media_type'] == 'hdd'

    def test_explicit_workers_ignore_drive_type(self, temp_dir, monkeypatch):
        captured = {}
        self._stub_streaming(monkeypatch, captured)
        monkeypatch.setattr(config, 'AUTO_WORKERS', True)
        monkeypatch.setattr(orch_mod, 'warm_up', lambda paths: None)
        monkeypatch.setattr(disk_type, 'detect_drive',
                            lambda path: DriveProfile('usb', Media.HDD, Bus.USB))

        orchestrator, scan_state = _make_orchestrator(temp_dir, workers=4)
        orchestrator.run()

        assert captured['max_workers'] == 4
        assert captured['tuner'] is None

    def test_explicit_worker_choice_is_respected(self, temp_dir, monkeypatch):
        captured = {}
        self._stub_streaming(monkeypatch, captured)

        orchestrator, scan_state = _make_orchestrator(temp_dir, workers=2)
        orchestrator.run()

        assert captured['max_workers'] == 2

    def test_resolve_symlinks_flag_is_passed_through(self, temp_dir, monkeypatch):
        captured = {}
        self._stub_streaming(monkeypatch, captured)

        orchestrator, scan_state = _make_orchestrator(temp_dir, resolve_symlinks=False)
        assert orchestrator.resolve_symlinks is False
        # (iter_image_chunks_multi's resolve_symlinks kwarg is exercised by
        # the real generator in TestDiscoverAndAnalyzeIntegration above; here
        # we just confirm the orchestrator stores/threads the flag it was given.)

    def test_calculate_phash_callable_reflects_exact_only(self, temp_dir, monkeypatch):
        captured = {}
        self._stub_streaming(monkeypatch, captured)

        orchestrator, scan_state = _make_orchestrator(temp_dir, exact_only=True)
        orchestrator.run()

        phash_arg = captured['calculate_phash']
        assert callable(phash_arg)
        assert phash_arg() is False  # exact_only=True -> never compute pHash

    def test_calculate_phash_callable_true_when_not_exact_only(self, temp_dir, monkeypatch):
        captured = {}
        self._stub_streaming(monkeypatch, captured)

        orchestrator, scan_state = _make_orchestrator(temp_dir, exact_only=False)
        orchestrator.run()

        phash_arg = captured['calculate_phash']
        assert phash_arg() is True
