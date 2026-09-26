"""
Adjust an operation's concurrency while it runs, based on measured
throughput.

The worker table (utils/worker_policy.py) picks a good starting point per
drive type, but real devices vary - a "USB SSD" may sit behind a slow
bridge, a NAS may be idle or saturated by someone else. For long jobs,
ConcurrencyGate lets a thread pool sized to a ceiling run with a smaller,
adjustable number of active workers, and ThroughputController nudges that
number toward whatever maximizes bytes/second:

- climb one worker per window while throughput keeps improving (>= 5%)
- on a clear drop (>= 10% below the best recent window), back off
  multiplicatively (x0.75) and hold for a few windows
- once flat for a few windows, stop climbing; try again every 30s

Windows where workers sat idle (the producer - usually a directory walk -
was the bottleneck, not the drive) or with too few completions to be
meaningful are ignored, so they can't trigger a false back-off.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from .. import config

_logger = logging.getLogger(__name__)

_IMPROVE = 1.05      # a window this much better than the last counts as improvement
_DROP = 0.90         # ...this much worse than the best recent one counts as a drop
_BACKOFF = 0.75
_HOLD_WINDOWS = 3
_FLAT_WINDOWS = 3
_REPROBE_S = 30.0
_MIN_UTILIZATION = 0.8


class ConcurrencyGate:
    """A semaphore whose limit can change while threads hold slots."""

    def __init__(self, initial: int, floor: int, ceiling: int,
                 clock: Callable[[], float] = time.monotonic):
        self.floor = max(1, floor)
        self.ceiling = max(self.floor, ceiling)
        self._limit = min(max(initial, self.floor), self.ceiling)
        self._active = 0
        self._cond = threading.Condition()
        self._clock = clock
        # Time-integral of active slots, to measure utilization per window
        self._busy_integral = 0.0
        self._limit_integral = 0.0
        self._last_mark = clock()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    def _mark(self) -> None:
        # Caller holds self._cond
        now = self._clock()
        dt = now - self._last_mark
        if dt > 0:
            self._busy_integral += self._active * dt
            self._limit_integral += self._limit * dt
        self._last_mark = now

    @contextmanager
    def slot(self) -> Iterator[None]:
        with self._cond:
            while self._active >= self._limit:
                self._cond.wait()
            self._mark()
            self._active += 1
        try:
            yield
        finally:
            with self._cond:
                self._mark()
                self._active -= 1
                self._cond.notify()

    def set_limit(self, n: int) -> int:
        with self._cond:
            self._mark()
            self._limit = min(max(int(n), self.floor), self.ceiling)
            self._cond.notify_all()
            return self._limit

    def take_utilization(self) -> float:
        """Fraction of allowed slots that were busy since the last call (1.0 = saturated)."""
        with self._cond:
            self._mark()
            busy, allowed = self._busy_integral, self._limit_integral
            self._busy_integral = self._limit_integral = 0.0
        return busy / allowed if allowed > 0 else 0.0


class ThroughputController:
    """Hill-climb / AIMD controller over a ConcurrencyGate, driven by bytes processed."""

    def __init__(self, gate: ConcurrencyGate, *, window_s: float = 2.0, min_samples: int = 64,
                 clock: Callable[[], float] = time.monotonic):
        self.gate = gate
        self.window_s = window_s
        self.min_samples = min_samples
        self._clock = clock
        self._lock = threading.Lock()
        self._window_start = clock()
        self._bytes = 0
        self._count = 0
        self._prev_rate: Optional[float] = None
        self._best_rate: Optional[float] = None
        self._hold = 0
        self._flat = 0
        self._settled_at: Optional[float] = None
        # For reporting: time-weighted average limit
        self._limit_time = 0.0
        self._total_time = 0.0
        self.adjustments = 0

    def record(self, nbytes: int) -> None:
        with self._lock:
            self._bytes += max(0, int(nbytes))
            self._count += 1

    def maybe_adjust(self) -> Optional[int]:
        """Call often (e.g. per completed file); returns the new limit when it changed."""
        now = self._clock()
        with self._lock:
            elapsed = now - self._window_start
            if elapsed < self.window_s:
                return None
            nbytes, count = self._bytes, self._count
            self._bytes = self._count = 0
            self._window_start = now
        utilization = self.gate.take_utilization()
        limit = self.gate.limit
        self._limit_time += limit * elapsed
        self._total_time += elapsed

        if count < self.min_samples or utilization < _MIN_UTILIZATION:
            return None   # too noisy, or the drive wasn't the bottleneck
        rate = nbytes / elapsed
        new_limit = self._decide(rate, limit, now)
        self._prev_rate = rate
        if new_limit is not None and new_limit != limit:
            applied = self.gate.set_limit(new_limit)
            if applied != limit:
                self.adjustments += 1
                _logger.debug(f"Adaptive workers: {limit} -> {applied} ({rate / 1e6:.1f} MB/s)")
                return applied
        return None

    def _decide(self, rate: float, limit: int, now: float) -> Optional[int]:
        if self._best_rate is None:
            self._best_rate = rate
            return limit + 1   # first real window: start probing upward

        if self._hold > 0:
            self._hold -= 1
            self._best_rate = max(self._best_rate, rate) if self._hold else rate
            return None

        if rate < self._best_rate * _DROP:
            self._hold = _HOLD_WINDOWS
            self._flat = 0
            self._settled_at = None
            self._best_rate = rate
            return max(self.gate.floor, int(limit * _BACKOFF))

        self._best_rate = max(self._best_rate, rate)

        if self._settled_at is not None:
            if now - self._settled_at < _REPROBE_S:
                return None
            self._settled_at = None
            self._flat = 0
            return limit + 1   # periodic re-probe

        if self._prev_rate is not None and rate >= self._prev_rate * _IMPROVE:
            self._flat = 0
            return limit + 1

        self._flat += 1
        if self._flat >= _FLAT_WINDOWS:
            self._settled_at = now
        return None

    @property
    def average_limit(self) -> float:
        return self._limit_time / self._total_time if self._total_time else float(self.gate.limit)


class AdaptiveTuner:
    """A gate plus its controller - what the thread pools in scanner/parallel.py consume."""

    def __init__(self, initial: int, floor: int, ceiling: int, *, window_s: float = 2.0,
                 min_samples: int = 64, clock: Callable[[], float] = time.monotonic):
        self.gate = ConcurrencyGate(initial, floor, ceiling, clock=clock)
        self.controller = ThroughputController(self.gate, window_s=window_s,
                                               min_samples=min_samples, clock=clock)
        self.initial = self.gate.limit

    @property
    def ceiling(self) -> int:
        return self.gate.ceiling

    def slot(self):
        return self.gate.slot()

    def record(self, nbytes: int) -> None:
        self.controller.record(nbytes)

    def maybe_adjust(self) -> Optional[int]:
        return self.controller.maybe_adjust()

    def summary(self) -> dict:
        return {
            'initial': self.initial,
            'final': self.gate.limit,
            'average': round(self.controller.average_limit, 1),
            'floor': self.gate.floor,
            'ceiling': self.gate.ceiling,
            'adjustments': self.controller.adjustments,
        }


def make_tuner(decision, file_count: Optional[int]) -> Optional[AdaptiveTuner]:
    """
    An AdaptiveTuner for `decision` (a worker_policy.WorkerDecision) when the
    job is long enough to benefit, else None. Explicit, environment and
    fallback counts are never adjusted.

    Pass file_count=None when the size isn't known up front (streaming scans
    that discover and analyze concurrently): the tuner is created anyway,
    and a short job simply finishes before any window has enough samples
    to act on.
    """
    from .worker_policy import OpKind

    if decision.source not in ('auto', 'probe') or decision.ceiling <= decision.floor:
        return None
    if decision.op not in (OpKind.SCAN, OpKind.COPY, OpKind.REWRITE):
        return None   # metadata updates are quick; repair is CPU-bound
    threshold = (config.ADAPTIVE_MIN_FILES_SCAN if decision.op is OpKind.SCAN
                 else config.ADAPTIVE_MIN_FILES_OPS)
    if file_count is not None and file_count < threshold:
        return None
    return AdaptiveTuner(decision.workers, decision.floor, decision.ceiling)


def pool_plan(max_workers: int, tuner: Optional[AdaptiveTuner], task_count: int):
    """
    (pool_size, tuner) for a file operation's thread pool: the tuner (and a
    pool sized to its ceiling) only for jobs big enough to tune.
    """
    if tuner is None or task_count < config.ADAPTIVE_MIN_FILES_OPS:
        return max_workers, None
    return tuner.ceiling, tuner


def call_in_slot(tuner: Optional[AdaptiveTuner], path, fn, *args):
    """Run fn(*args), inside one of `tuner`'s slots when given, recording `path`'s size."""
    if tuner is None:
        return fn(*args)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    with tuner.slot():
        result = fn(*args)
    tuner.record(size)
    return result


__all__ = [
    'ConcurrencyGate', 'ThroughputController', 'AdaptiveTuner', 'make_tuner',
    'pool_plan', 'call_in_slot',
]
