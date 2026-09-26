"""
Unit tests for pixsieve/utils/io_probe.py.

Classification logic is tested with crafted ProbeResults; the measurement
itself is exercised with injected readers that emulate a seek-bound drive
(one read at a time, behind a lock) and a parallel one.
"""

import os
import threading
import time

import pytest

from pixsieve.utils import io_probe
from pixsieve.utils.disk_type import Bus, DriveProfile, Media, Tier
from pixsieve.utils.io_probe import ProbeResult


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    io_probe.clear_cache()
    yield
    io_probe.clear_cache()


AMBIGUOUS = DriveProfile('usb', Media.UNKNOWN, Bus.USB)
NETWORK = DriveProfile('nas', Media.UNKNOWN, Bus.NETWORK, is_remote=True)


def _result(gain=6.0, mean_ms=0.2, cached=False):
    return ProbeResult(
        meta_latency_ms=0.1, read_latency_ms=mean_ms, mean_read_ms=mean_ms,
        rand_iops_qd1=1000 / mean_ms, rand_iops_qd8=1000 / mean_ms * gain,
        concurrency_gain=gain, cached_suspect=cached, elapsed_ms=150,
    )


FILES = [f'f{i}' for i in range(300)]


class TestShouldProbe:
    @pytest.mark.parametrize('profile,expected', [
        (AMBIGUOUS, True),
        (NETWORK, True),
        (DriveProfile('r', Media.UNKNOWN, Bus.RAID), True),
        (DriveProfile('h', Media.HDD, Bus.SATA), False),
        (DriveProfile('n', Media.SSD, Bus.NVME), False),
        (DriveProfile('u', Media.SSD, Bus.USB), False),
        (DriveProfile('v', Media.UNKNOWN, Bus.VIRTUAL), False),
        (DriveProfile('o', Media.UNKNOWN, Bus.USB, source='override'), False),
    ])
    def test_cases(self, profile, expected):
        assert io_probe.should_probe(profile, 500) is expected

    def test_small_jobs_are_not_probed(self):
        assert io_probe.should_probe(AMBIGUOUS, io_probe.MIN_FILES - 1) is False


class TestRefineProfile:
    def test_not_warranted(self):
        profile = DriveProfile('h', Media.HDD, Bus.SATA)
        assert io_probe.refine_profile(profile, FILES, probe=lambda f: pytest.fail()) == (profile, None)

    def test_ambiguous_without_concurrency_gain_is_hdd(self):
        refined, info = io_probe.refine_profile(AMBIGUOUS, FILES, probe=lambda f: _result(gain=1.1, mean_ms=0.5))
        assert refined.tier is Tier.HDD_EXTERNAL
        assert info['applied'] is True

    def test_ambiguous_with_slow_reads_is_hdd(self):
        refined, _ = io_probe.refine_profile(AMBIGUOUS, FILES, probe=lambda f: _result(gain=3, mean_ms=9))
        assert refined.media is Media.HDD

    def test_ambiguous_fast_and_parallel_is_ssd(self):
        refined, info = io_probe.refine_profile(AMBIGUOUS, FILES, probe=lambda f: _result(gain=5, mean_ms=0.3))
        assert refined.tier is Tier.SSD_EXTERNAL
        assert 'probe: ssd' in refined.detail

    def test_ambiguous_inconclusive(self):
        refined, info = io_probe.refine_profile(AMBIGUOUS, FILES, probe=lambda f: _result(gain=3, mean_ms=2))
        assert refined == AMBIGUOUS
        assert info['applied'] is False

    def test_network_without_gain_steps_down(self):
        refined, info = io_probe.refine_profile(NETWORK, FILES, probe=lambda f: _result(gain=1.0, mean_ms=3))
        assert refined == NETWORK
        assert info['step'] == -1

    def test_network_with_gain_is_unchanged(self):
        _, info = io_probe.refine_profile(NETWORK, FILES, probe=lambda f: _result(gain=4, mean_ms=3))
        assert info['step'] == 0 and info['applied'] is False

    def test_cached_results_are_ignored(self):
        refined, info = io_probe.refine_profile(AMBIGUOUS, FILES,
                                                probe=lambda f: _result(gain=1, mean_ms=0.01, cached=True))
        assert refined == AMBIGUOUS
        assert info['applied'] is False

    def test_timeout_is_ignored(self):
        refined, info = io_probe.refine_profile(AMBIGUOUS, FILES, probe=lambda f: None)
        assert refined == AMBIGUOUS
        assert info['applied'] is False

    def test_result_cached_per_drive(self):
        calls = []

        def probe(files):
            calls.append(1)
            return _result(gain=1.0)

        io_probe.refine_profile(AMBIGUOUS, FILES, probe=probe)
        io_probe.refine_profile(AMBIGUOUS, FILES, probe=probe)
        assert len(calls) == 1
        io_probe.clear_cache()
        io_probe.refine_profile(AMBIGUOUS, FILES, probe=probe)
        assert len(calls) == 2


@pytest.fixture
def big_files(tmp_path):
    """Four 2 MiB files: 32 distinct 256 KiB zones to read from."""
    paths = []
    for i in range(4):
        p = tmp_path / f'big{i}.bin'
        p.write_bytes(os.urandom(2 * 1024 * 1024))
        paths.append(str(p))
    return paths


class _Reader:
    def __init__(self, delay, serial):
        self.delay = delay
        self.lock = threading.Lock() if serial else None
        self.prepared = 0

    def prepare(self, paths):
        self.prepared += 1

    def __call__(self, path, offset, size):
        if self.lock:
            with self.lock:
                time.sleep(self.delay)
        else:
            time.sleep(self.delay)
        return size


class TestProbeDevice:
    @pytest.fixture(autouse=True)
    def _roomy_timing(self, monkeypatch):
        """
        The fake readers emulate latency with time.sleep(), which is coarse on
        some platforms (~15.6ms ticks on Windows before Python 3.11, overshoot
        on macOS CI runners). A longer phase and smaller zones keep enough
        samples per phase for the probe to report a result.
        """
        monkeypatch.setattr(io_probe, '_PHASE_MS', 400)
        monkeypatch.setattr(io_probe, '_ZONE', 16 * 1024)
        monkeypatch.setattr(io_probe, '_MIN_READ_FILE', 32 * 1024)

    def test_serial_device_shows_no_concurrency_gain(self, big_files):
        result = io_probe.probe_device(big_files, reader=_Reader(0.003, serial=True),
                                       budget_ms=3000, hard_timeout_ms=8000)
        assert result is not None
        assert result.concurrency_gain < 2.0
        assert result.mean_read_ms >= 2.5
        assert not result.cached_suspect

    def test_parallel_device_shows_concurrency_gain(self, big_files):
        result = io_probe.probe_device(big_files, reader=_Reader(0.003, serial=False),
                                       budget_ms=3000, hard_timeout_ms=8000)
        assert result is not None
        assert result.concurrency_gain > 2.0

    def test_prepare_called_outside_timing(self, big_files):
        reader = _Reader(0.001, serial=False)
        io_probe.probe_device(big_files, reader=reader, budget_ms=3000, hard_timeout_ms=8000)
        assert reader.prepared == 1 + io_probe._CONCURRENCY

    def test_instant_reads_are_flagged_as_cached(self, big_files):
        result = io_probe.probe_device(big_files, reader=lambda p, o, s: s,
                                       budget_ms=3000, hard_timeout_ms=8000)
        assert result is not None
        assert result.cached_suspect

    def test_small_files_cannot_be_measured(self, tmp_path):
        files = []
        for i in range(50):
            p = tmp_path / f's{i}.jpg'
            p.write_bytes(b'x' * 10_000)
            files.append(str(p))
        assert io_probe.probe_device(files) is None

    def test_empty_input(self):
        assert io_probe.probe_device([]) is None

    def test_hung_reader_hits_hard_deadline(self, big_files):
        start = time.monotonic()
        result = io_probe.probe_device(big_files, reader=lambda p, o, s: time.sleep(2),
                                       budget_ms=50, hard_timeout_ms=150)
        assert result is None
        assert time.monotonic() - start < 1.5

    def test_reader_errors_yield_none(self, big_files):
        def broken(path, offset, size):
            raise OSError('gone')
        assert io_probe.probe_device(big_files, reader=broken, budget_ms=500) is None

    def test_default_reader_is_read_only(self, big_files, monkeypatch):
        flags_seen = []
        real_open = os.open

        def spy_open(path, flags, *args, **kwargs):
            flags_seen.append(flags)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(io_probe.os, 'open', spy_open)
        before = {p: os.path.getmtime(p) for p in big_files}
        io_probe.probe_device(big_files, budget_ms=1000, hard_timeout_ms=3000)
        assert flags_seen
        write_bits = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
        assert all(f & write_bits == 0 for f in flags_seen)
        assert before == {p: os.path.getmtime(p) for p in big_files}


class TestSampleDirectory:
    def test_limit_and_hidden_dirs(self, tmp_path):
        (tmp_path / 'a').mkdir()
        (tmp_path / '.hidden').mkdir()
        for i in range(5):
            (tmp_path / f'top{i}.jpg').write_bytes(b'x')
            (tmp_path / 'a' / f'nested{i}.jpg').write_bytes(b'x')
            (tmp_path / '.hidden' / f'h{i}.jpg').write_bytes(b'x')

        found = io_probe.sample_directory(str(tmp_path))
        assert len(found) == 10
        assert not any('.hidden' in f for f in found)
        assert len(io_probe.sample_directory(str(tmp_path), limit=3)) == 3

    def test_missing_directory(self, tmp_path):
        assert io_probe.sample_directory(str(tmp_path / 'nope')) == []
