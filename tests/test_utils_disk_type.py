"""
Unit tests for pixsieve/utils/disk_type.py.
"""

import subprocess

import pytest

from pixsieve.utils import disk_type


@pytest.fixture(autouse=True)
def _clear_cache():
    """Detection results are memoized per-drive; keep tests isolated."""
    disk_type._cache.clear()
    yield
    disk_type._cache.clear()


class TestTailorWorkers:
    def test_caps_down_on_confirmed_hdd(self, monkeypatch):
        monkeypatch.setattr(disk_type, 'is_rotational', lambda path: True)
        assert disk_type.tailor_workers('/photos', default_workers=8, hdd_cap=2) == 2

    def test_leaves_default_alone_on_ssd(self, monkeypatch):
        monkeypatch.setattr(disk_type, 'is_rotational', lambda path: False)
        assert disk_type.tailor_workers('/photos', default_workers=8, hdd_cap=2) == 8

    def test_leaves_default_alone_when_unknown(self, monkeypatch):
        # detect_media_type() returning 'unknown' means is_rotational() is False
        monkeypatch.setattr(disk_type, 'is_rotational', lambda path: False)
        assert disk_type.tailor_workers('/photos', default_workers=4, hdd_cap=2) == 4

    def test_never_raises_the_cap_above_the_default(self, monkeypatch):
        # A cap that isn't actually lower than the default must never be applied,
        # even on a confirmed HDD - this only ever pulls worker counts down.
        monkeypatch.setattr(disk_type, 'is_rotational', lambda path: True)
        assert disk_type.tailor_workers('/photos', default_workers=2, hdd_cap=8) == 2


class TestDetectMediaTypeCaching:
    def test_caches_result_per_drive(self, monkeypatch, tmp_path):
        calls = []

        def fake_system():
            calls.append(1)
            return 'Linux'

        monkeypatch.setattr(disk_type.platform_module, 'system', fake_system)
        monkeypatch.setattr(disk_type, '_detect_linux', lambda path: 'ssd')

        first = disk_type.detect_media_type(str(tmp_path))
        second = disk_type.detect_media_type(str(tmp_path))

        assert first == second == 'ssd'
        assert len(calls) == 1  # second call served from cache, no re-detection

    def test_falls_back_to_unknown_on_exception(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type.platform_module, 'system', lambda: 'Linux')

        def boom(path):
            raise OSError("no such tool")

        monkeypatch.setattr(disk_type, '_detect_linux', boom)

        assert disk_type.detect_media_type(str(tmp_path)) == 'unknown'

    def test_is_rotational_true_only_for_hdd(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type, 'detect_media_type', lambda path: 'hdd')
        assert disk_type.is_rotational(str(tmp_path)) is True

        monkeypatch.setattr(disk_type, 'detect_media_type', lambda path: 'ssd')
        assert disk_type.is_rotational(str(tmp_path)) is False

        monkeypatch.setattr(disk_type, 'detect_media_type', lambda path: 'unknown')
        assert disk_type.is_rotational(str(tmp_path)) is False


class TestWindowsDetection:
    def _mock_run(self, monkeypatch, stdout='', returncode=0):
        def fake_run(cmd, capture_output, text, timeout):
            return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr='')
        monkeypatch.setattr(disk_type.subprocess, 'run', fake_run)

    def test_parses_ssd(self, monkeypatch):
        self._mock_run(monkeypatch, stdout='SSD\r\n')
        assert disk_type._detect_windows('C:\\Users\\me') == 'ssd'

    def test_parses_hdd(self, monkeypatch):
        self._mock_run(monkeypatch, stdout='HDD\r\n')
        assert disk_type._detect_windows('D:\\photos') == 'hdd'

    def test_unrecognized_output_is_unknown(self, monkeypatch):
        self._mock_run(monkeypatch, stdout='Unspecified\r\n')
        assert disk_type._detect_windows('E:\\') == 'unknown'

    def test_nonzero_exit_is_unknown(self, monkeypatch):
        self._mock_run(monkeypatch, stdout='', returncode=1)
        assert disk_type._detect_windows('Z:\\') == 'unknown'

    def test_no_drive_letter_is_unknown(self):
        # A UNC path (\\server\share\...) has no drive letter to resolve.
        assert disk_type._detect_windows('\\\\server\\share\\photos') == 'unknown'


class TestLinuxDetection:
    def test_maps_rotational_flag_to_hdd(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type, '_linux_block_device', lambda path: '/dev/sda1')
        monkeypatch.setattr(disk_type.os.path, 'exists', lambda p: True)
        monkeypatch.setattr('builtins.open', lambda p: _FakeFile('1'))
        assert disk_type._detect_linux(str(tmp_path)) == 'hdd'

    def test_maps_non_rotational_flag_to_ssd(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type, '_linux_block_device', lambda path: '/dev/nvme0n1p1')
        monkeypatch.setattr(disk_type.os.path, 'exists', lambda p: True)
        monkeypatch.setattr('builtins.open', lambda p: _FakeFile('0'))
        assert disk_type._detect_linux(str(tmp_path)) == 'ssd'

    def test_no_device_found_is_unknown(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type, '_linux_block_device', lambda path: None)
        assert disk_type._detect_linux(str(tmp_path)) == 'unknown'

    def test_missing_sysfs_entry_is_unknown(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type, '_linux_block_device', lambda path: '/dev/sda1')
        monkeypatch.setattr(disk_type.os.path, 'exists', lambda p: False)
        assert disk_type._detect_linux(str(tmp_path)) == 'unknown'


class _FakeFile:
    def __init__(self, content):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._content
