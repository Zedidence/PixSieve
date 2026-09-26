"""
A short, read-only I/O speed test that settles what drive detection can't:
whether an external (USB) drive of unknown media behaves like an SSD or a
spinning disk, and whether a network share or RAID volume gains anything
from concurrent access.

The probe times a few dozen stat() calls and 4 KiB reads at random offsets
of files the operation is about to process anyway - first one at a time,
then from 8 threads. A drive whose throughput barely improves with
concurrency (a spinning disk, or a bridge with a queue depth of one) gets
fewer workers; one with sub-millisecond random reads is an SSD.

Safety: files are only ever opened read-only, nothing is written, and the
whole probe runs on a daemon thread with a hard deadline - a sleeping disk
or a hung share costs at most ~600ms, after which the detected profile is
used as-is.
"""

from __future__ import annotations

import logging
import os
import random
import statistics
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from .disk_type import Bus, DriveProfile, Media, Tier

_logger = logging.getLogger(__name__)

# Only probe when there's enough work for the probe's cost to matter
MIN_FILES = 200

BUDGET_MS = 400
HARD_TIMEOUT_MS = 600
_STAT_SAMPLES = 32
_READ_FILES = 16
_READ_SIZE = 4096
# Every read lands in its own zone of a file, so neither a repeated block nor
# the OS's read-ahead around an earlier read can be served from the page
# cache (Windows offers no cheap way to bypass the cache from Python).
_ZONE = 256 * 1024
_MIN_READ_FILE = 2 * _ZONE
_MIN_ZONES = 24
_PHASE_MS = 80
_CONCURRENCY = 8
_MIN_PHASE_READS = 8   # fewer reads than this in a phase can't be trusted

# Classification thresholds
_LOW_GAIN = 2.0          # 8 threads barely beat 1: seek-bound or queue-depth-1 (SSDs show ~6x)
_HDD_LATENCY_MS = 4.0    # random 4K reads this slow are a spinning disk
_SSD_LATENCY_MS = 1.0    # ...and this fast are flash
_CACHED_LATENCY_MS = 0.02  # faster than any device: served from the page cache

Reader = Callable[[str, int, int], int]


@dataclass(frozen=True)
class ProbeResult:
    meta_latency_ms: float     # median stat() latency
    read_latency_ms: float     # median 4 KiB random read latency, one at a time
    mean_read_ms: float        # mean of the same (drive caches skew the median low)
    rand_iops_qd1: float
    rand_iops_qd8: float
    concurrency_gain: float    # qd8 / qd1
    cached_suspect: bool       # results look like RAM, not the device
    elapsed_ms: float

    def as_dict(self) -> dict:
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in self.__dict__.items()}


def should_probe(profile: DriveProfile, file_count: int) -> bool:
    """True when the detected profile leaves the right worker count in doubt."""
    if profile.source == 'override' or file_count < MIN_FILES:
        return False
    if profile.tier in (Tier.EXTERNAL_AMBIGUOUS, Tier.NETWORK):
        return True
    return profile.media is Media.UNKNOWN and profile.bus in (Bus.USB, Bus.RAID)


class _FileReader:
    """
    Reads through one read-only fd per (thread, file). prepare() opens a
    thread's fds up front, so opening (slow on Windows, where antivirus
    scans on open) is never timed as part of a read.
    """

    def __init__(self):
        self._local = threading.local()
        self._all_fds: list[int] = []
        self._lock = threading.Lock()
        self._flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0)

    def _fds(self) -> dict[str, int]:
        fds = getattr(self._local, 'fds', None)
        if fds is None:
            fds = self._local.fds = {}
        return fds

    def _open(self, path: str) -> int:
        fd = os.open(path, self._flags)
        try:
            # Keep the page cache out of the measurement where the OS allows
            if hasattr(os, 'posix_fadvise'):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            elif hasattr(os, 'pread'):
                import fcntl  # macOS: F_NOCACHE
                fcntl.fcntl(fd, 48, 1)
        except Exception:
            pass
        with self._lock:
            self._all_fds.append(fd)
        return fd

    def prepare(self, paths: Sequence[str]) -> None:
        fds = self._fds()
        for path in paths:
            if path not in fds:
                fds[path] = self._open(path)

    def __call__(self, path: str, offset: int, size: int) -> int:
        fds = self._fds()
        fd = fds.get(path)
        if fd is None:
            fd = fds[path] = self._open(path)
        if hasattr(os, 'pread'):
            return len(os.pread(fd, size, offset))
        os.lseek(fd, offset, os.SEEK_SET)   # fds are per-thread, so this is safe
        return len(os.read(fd, size))

    def close_all(self) -> None:
        with self._lock:
            for fd in self._all_fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._all_fds.clear()


def _plan_blocks(readable: list[str], sizes: dict[str, int], rng: random.Random) -> list[tuple[str, int]]:
    """One random 4 KiB block per zone of each readable file, shuffled."""
    blocks = []
    for path in readable:
        for zone in range(sizes[path] // _ZONE):
            offset = zone * _ZONE + rng.randrange(_ZONE // _READ_SIZE) * _READ_SIZE
            blocks.append((path, offset))
    rng.shuffle(blocks)
    return blocks


def _run_probe(
    sample_files: Sequence[str],
    reader: Reader,
    stat: Callable[[str], os.stat_result],
    clock: Callable[[], float],
    budget_ms: int,
) -> ProbeResult | None:
    start = clock()
    deadline = start + budget_ms / 1000
    # Fresh choices each run: blocks read by an earlier probe may still be cached
    rng = random.Random()
    picks = rng.sample(list(sample_files), min(_STAT_SAMPLES, len(sample_files)))

    stat_latencies: list[float] = []
    sizes: dict[str, int] = {}
    for path in picks:
        t0 = clock()
        try:
            sizes[path] = stat(path).st_size
        except OSError:
            continue
        stat_latencies.append((clock() - t0) * 1000)
        if clock() > deadline:
            break
    if not stat_latencies:
        return None

    readable = sorted((p for p, s in sizes.items() if s >= _MIN_READ_FILE),
                      key=lambda p: sizes[p], reverse=True)[:_READ_FILES]
    blocks = _plan_blocks(readable, sizes, rng)
    if len(blocks) < _MIN_ZONES:
        return None   # too little data to measure without re-reading cached blocks

    prepare = getattr(reader, 'prepare', None)
    next_block = iter(blocks)
    block_lock = threading.Lock()

    def _take() -> tuple[str, int] | None:
        with block_lock:
            return next(next_block, None)

    def _timed_read(block: tuple[str, int]) -> float:
        t0 = clock()
        reader(block[0], block[1], _READ_SIZE)
        return (clock() - t0) * 1000

    phase = _PHASE_MS / 1000

    # One read at a time; at most a third of the blocks, the rest are for phase 2
    if prepare:
        prepare(readable)
    qd1_latencies: list[float] = []
    qd1_budget = len(blocks) // 3
    t_start = clock()
    # Leave room for the concurrent phase within the overall budget
    phase_end = min(t_start + phase, deadline - phase)
    while clock() < phase_end and len(qd1_latencies) < qd1_budget:
        block = _take()
        if block is None:
            break
        try:
            qd1_latencies.append(_timed_read(block))
        except OSError:
            break
    qd1_elapsed = clock() - t_start
    if len(qd1_latencies) < _MIN_PHASE_READS or qd1_elapsed <= 0:
        return None
    iops_qd1 = len(qd1_latencies) / qd1_elapsed

    # Fresh blocks from several threads at once, all starting together
    counts = [0] * _CONCURRENCY
    finished = [0.0] * _CONCURRENCY
    barrier = threading.Barrier(_CONCURRENCY + 1)
    shared = {'end': 0.0}

    def _worker(i: int) -> None:
        try:
            if prepare:
                prepare(readable)
            barrier.wait(max(0.01, deadline - clock()))
        except (threading.BrokenBarrierError, OSError):
            return
        while clock() < shared['end']:
            block = _take()
            if block is None:
                break
            try:
                _timed_read(block)
            except OSError:
                break
            counts[i] += 1
        finished[i] = clock()

    threads = [threading.Thread(target=_worker, args=(i,), daemon=True) for i in range(_CONCURRENCY)]
    for t in threads:
        t.start()
    try:
        barrier.wait(max(0.01, deadline - clock()))
    except threading.BrokenBarrierError:
        return None
    t_start = clock()
    shared['end'] = min(t_start + phase, deadline)
    for t in threads:
        t.join(max(0.0, shared['end'] - clock()) + 0.05)
    if sum(counts) < _MIN_PHASE_READS:
        return None   # ran out of budget or blocks - no trustworthy comparison
    qd8_elapsed = max(finished) - t_start if any(finished) else clock() - t_start
    iops_qd8 = sum(counts) / qd8_elapsed if qd8_elapsed > 0 else 0.0

    read_latency = statistics.median(qd1_latencies)
    return ProbeResult(
        meta_latency_ms=statistics.median(stat_latencies),
        read_latency_ms=read_latency,
        mean_read_ms=statistics.fmean(qd1_latencies),
        rand_iops_qd1=iops_qd1,
        rand_iops_qd8=iops_qd8,
        concurrency_gain=iops_qd8 / iops_qd1 if iops_qd1 else 0.0,
        cached_suspect=read_latency < _CACHED_LATENCY_MS,
        elapsed_ms=(clock() - start) * 1000,
    )


def probe_device(
    sample_files: Sequence[str],
    *,
    budget_ms: int = BUDGET_MS,
    hard_timeout_ms: int = HARD_TIMEOUT_MS,
    reader: Reader | None = None,
    stat: Callable[[str], os.stat_result] = os.stat,
    clock: Callable[[], float] = time.perf_counter,
) -> ProbeResult | None:
    """
    Measure the drive behind `sample_files`. Returns None when there's
    nothing usable to read or the probe overran its hard deadline.
    """
    if not sample_files:
        return None
    close_all: Callable[[], None] = lambda: None
    if reader is None:
        file_reader = _FileReader()
        reader, close_all = file_reader, file_reader.close_all

    outcome: list[ProbeResult | None] = []

    def _target() -> None:
        try:
            outcome.append(_run_probe(sample_files, reader, stat, clock, budget_ms))
        except Exception as e:
            _logger.debug(f"I/O probe failed: {e}")
            outcome.append(None)
        finally:
            close_all()

    thread = threading.Thread(target=_target, name='pixsieve-io-probe', daemon=True)
    thread.start()
    thread.join(hard_timeout_ms / 1000)
    if thread.is_alive() or not outcome:
        _logger.debug("I/O probe overran its deadline - using the detected drive profile")
        return None
    return outcome[0]


def sample_directory(root: str, limit: int = 400) -> list[str]:
    """Up to `limit` file paths under `root` (breadth-first), for probing a path directly."""
    found: list[str] = []
    pending = [root]
    while pending and len(found) < limit:
        current = pending.pop(0)
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            found.append(entry.path)
                            if len(found) >= limit:
                                break
                        elif entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.'):
                            pending.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return found


_cache: dict[str, ProbeResult | None] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def refine_profile(
    profile: DriveProfile,
    sample_files: Sequence[str],
    *,
    probe: Callable[[Sequence[str]], ProbeResult | None] | None = None,
) -> tuple[DriveProfile, dict | None]:
    """
    Probe the drive when its profile is ambiguous, and return the (possibly
    reclassified) profile plus a summary dict for logging/UI - or
    (profile, None) when no probe was warranted.

    The summary's 'step' (-1 or 0) tells the caller to move its worker
    count one LADDER step down for drives whose tier can't change (network,
    RAID) but that showed no benefit from concurrency.
    """
    if not should_probe(profile, len(sample_files)):
        return profile, None

    with _cache_lock:
        have = profile.device_key in _cache
        result = _cache.get(profile.device_key)
    if not have:
        result = (probe or probe_device)(sample_files)
        with _cache_lock:
            _cache[profile.device_key] = result

    info: dict = {'applied': False, 'step': 0}
    if result is None:
        info['reason'] = 'probe unavailable or timed out'
        return profile, info
    info['result'] = result.as_dict()
    if result.cached_suspect:
        info['reason'] = 'reads served from cache - ignored'
        return profile, info

    slow_concurrency = result.concurrency_gain < _LOW_GAIN
    if profile.media is Media.UNKNOWN and profile.tier is not Tier.NETWORK:
        if slow_concurrency or result.mean_read_ms > _HDD_LATENCY_MS:
            info.update(applied=True, reason='behaves like a spinning disk')
            return replace(profile, media=Media.HDD, detail=f'{profile.detail} [probe: hdd]'), info
        if result.mean_read_ms < _SSD_LATENCY_MS:
            info.update(applied=True, reason='behaves like an SSD')
            return replace(profile, media=Media.SSD, detail=f'{profile.detail} [probe: ssd]'), info
        info['reason'] = 'inconclusive'
        return profile, info

    if slow_concurrency:
        info.update(applied=True, step=-1, reason='no gain from concurrency')
    else:
        info['reason'] = 'concurrency helps - keeping the table value'
    return profile, info


__all__ = [
    'ProbeResult', 'probe_device', 'refine_profile', 'should_probe', 'sample_directory',
    'clear_cache', 'MIN_FILES',
]
