"""
Unit tests for pixsieve/utils/adaptive.py: the adjustable gate, the
throughput controller (driven by a fake clock), and how the tuner plugs
into the scanner's and file operations' thread pools.
"""

import threading
import time
from contextlib import ExitStack

import pytest

from pixsieve import config
from pixsieve.utils import adaptive
from pixsieve.utils.adaptive import AdaptiveTuner, ConcurrencyGate, ThroughputController
from pixsieve.utils.worker_policy import OpKind, WorkerDecision


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestConcurrencyGate:
    def test_limit_is_clamped(self):
        gate = ConcurrencyGate(10, floor=2, ceiling=6)
        assert gate.limit == 6
        assert gate.set_limit(0) == 2
        assert gate.set_limit(4) == 4

    def test_never_more_active_than_the_limit(self):
        gate = ConcurrencyGate(2, floor=1, ceiling=8)
        peak = [0]
        lock = threading.Lock()

        def work():
            with gate.slot():
                with lock:
                    peak[0] = max(peak[0], gate.active)
                time.sleep(0.01)

        threads = [threading.Thread(target=work) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert peak[0] == 2

    def test_raising_the_limit_releases_waiters(self):
        gate = ConcurrencyGate(1, floor=1, ceiling=4)
        entered = threading.Event()
        with gate.slot():
            t = threading.Thread(target=lambda: gate.slot().__enter__() or entered.set())
            t.start()
            assert not entered.wait(0.1)
            gate.set_limit(2)
            assert entered.wait(2)
        t.join(2)

    def test_utilization(self):
        clock = FakeClock()
        gate = ConcurrencyGate(2, floor=1, ceiling=4, clock=clock)
        with gate.slot():
            clock.advance(1)
        clock.advance(1)
        # 1 of 2 slots busy for 1s, then 0 of 2 for 1s -> 25%
        assert gate.take_utilization() == pytest.approx(0.25)
        assert gate.take_utilization() == 0.0


def _window(ctrl, clock, mb_per_s, *, busy=True, samples=100):
    """Simulate one controller window at the given throughput."""
    gate = ctrl.gate
    with ExitStack() as stack:
        if busy:
            for _ in range(gate.limit):
                stack.enter_context(gate.slot())
        for _ in range(samples):
            ctrl.record(int(mb_per_s * 1e6 * ctrl.window_s / samples))
        clock.advance(ctrl.window_s)
        return ctrl.maybe_adjust()


def _controller(initial=4, floor=2, ceiling=16):
    clock = FakeClock()
    gate = ConcurrencyGate(initial, floor, ceiling, clock=clock)
    ctrl = ThroughputController(gate, window_s=2.0, min_samples=64, clock=clock)
    return ctrl, clock


class TestThroughputController:
    def test_climbs_while_throughput_improves(self):
        ctrl, clock = _controller()
        assert _window(ctrl, clock, 100) == 5      # first real window probes upward
        assert _window(ctrl, clock, 120) == 6
        assert _window(ctrl, clock, 140) == 7

    def test_backs_off_on_a_drop_and_holds(self):
        ctrl, clock = _controller(initial=8)
        _window(ctrl, clock, 200)                   # -> 9
        assert _window(ctrl, clock, 100) == 6      # 9 * 0.75
        for _ in range(3):
            assert _window(ctrl, clock, 100) is None   # hold
        assert ctrl.gate.limit == 6

    def test_settles_when_flat_then_reprobes(self):
        ctrl, clock = _controller()
        _window(ctrl, clock, 100)                   # -> 5
        for _ in range(3):
            assert _window(ctrl, clock, 100) is None
        assert _window(ctrl, clock, 100) is None   # settled
        # Keeps running flat; after 30s it probes upward once more
        changes = [_window(ctrl, clock, 100) for _ in range(16)]
        assert [c for c in changes if c is not None] == [6]

    def test_ignores_windows_with_too_few_samples(self):
        ctrl, clock = _controller()
        assert _window(ctrl, clock, 100, samples=10) is None
        assert ctrl.gate.limit == 4

    def test_ignores_windows_where_workers_were_idle(self):
        # The producer (directory walk) was the bottleneck, not the drive
        ctrl, clock = _controller()
        assert _window(ctrl, clock, 100, busy=False) is None
        assert ctrl.gate.limit == 4

    def test_no_decision_before_window_elapses(self):
        ctrl, clock = _controller()
        ctrl.record(1000)
        clock.advance(0.5)
        assert ctrl.maybe_adjust() is None

    @pytest.mark.parametrize('rates', [
        [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400],
        [1000, 900, 800, 700, 600, 500, 400, 300, 200, 100, 50, 25],
        [100, 50, 200, 25, 400, 10, 800, 5, 1600, 1, 3200],
    ])
    def test_stays_within_floor_and_ceiling(self, rates):
        ctrl, clock = _controller(initial=4, floor=2, ceiling=8)
        for rate in rates:
            _window(ctrl, clock, rate)
            assert 2 <= ctrl.gate.limit <= 8

    def test_average_limit(self):
        ctrl, clock = _controller()
        assert ctrl.average_limit == 4
        _window(ctrl, clock, 100)
        _window(ctrl, clock, 120)
        assert 4 <= ctrl.average_limit <= 5


def _decision(source='auto', op=OpKind.SCAN, workers=4, floor=2, ceiling=6):
    return WorkerDecision(workers, source, '', op, floor=floor, ceiling=ceiling)


class TestMakeTuner:
    def test_long_auto_scan_gets_a_tuner(self):
        tuner = adaptive.make_tuner(_decision(), config.ADAPTIVE_MIN_FILES_SCAN)
        assert tuner is not None
        assert tuner.ceiling == 6
        assert tuner.gate.limit == 4

    def test_unknown_size_gets_a_tuner(self):
        assert adaptive.make_tuner(_decision(op=OpKind.COPY), None) is not None

    @pytest.mark.parametrize('decision,count', [
        (_decision(source='user'), 10**6),
        (_decision(source='env'), 10**6),
        (_decision(source='fallback'), 10**6),
        (_decision(op=OpKind.METADATA), 10**6),
        (_decision(op=OpKind.REPAIR), 10**6),
        (_decision(floor=4, ceiling=4), 10**6),
        (_decision(), config.ADAPTIVE_MIN_FILES_SCAN - 1),
        (_decision(op=OpKind.COPY), config.ADAPTIVE_MIN_FILES_OPS - 1),
    ])
    def test_no_tuner(self, decision, count):
        assert adaptive.make_tuner(decision, count) is None

    def test_summary(self):
        s = AdaptiveTuner(3, 2, 6).summary()
        assert s == {'initial': 3, 'final': 3, 'average': 3.0, 'floor': 2, 'ceiling': 6,
                     'adjustments': 0}


class TestPoolHelpers:
    def test_pool_plan_small_job_drops_tuner(self):
        tuner = AdaptiveTuner(2, 1, 4)
        assert adaptive.pool_plan(2, tuner, 10) == (2, None)
        assert adaptive.pool_plan(2, None, 10**6) == (2, None)
        assert adaptive.pool_plan(2, tuner, config.ADAPTIVE_MIN_FILES_OPS) == (4, tuner)

    def test_call_in_slot_records_size(self, tmp_path):
        f = tmp_path / 'a.bin'
        f.write_bytes(b'x' * 1234)
        tuner = AdaptiveTuner(2, 1, 4)
        assert adaptive.call_in_slot(tuner, f, lambda a, b: a + b, 1, 2) == 3
        assert tuner.controller._bytes == 1234
        assert adaptive.call_in_slot(None, f, lambda: 'plain') == 'plain'

    def test_call_in_slot_missing_file(self, tmp_path):
        tuner = AdaptiveTuner(2, 1, 4)
        adaptive.call_in_slot(tuner, tmp_path / 'gone', lambda: None)
        assert tuner.controller._count == 1


class TestScannerIntegration:
    def test_tuner_limits_concurrent_analysis(self, monkeypatch):
        from pixsieve.models import ImageInfo
        from pixsieve.scanner import parallel

        active = [0]
        peak = [0]
        lock = threading.Lock()

        def fake_analyze(path, calculate_phash, calculate_hash):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.005)
            with lock:
                active[0] -= 1
            return ImageInfo(path=path, file_size=100)

        tuner = AdaptiveTuner(2, 1, 8)
        results, _ = parallel.analyze_images_parallel(
            [f'/fake/{i}.jpg' for i in range(40)], max_workers=8, use_cache=False,
            show_progress=False, analyze_fn=fake_analyze, tuner=tuner,
        )
        assert len(results) == 40
        assert peak[0] <= 2
        assert tuner.controller._count == 40


class TestOperationsIntegration:
    def test_move_with_structure_uses_tuner_for_large_jobs(self, tmp_path, monkeypatch):
        from pixsieve.operations import move

        monkeypatch.setattr(config, 'ADAPTIVE_MIN_FILES_OPS', 5)
        src, dst = tmp_path / 'src', tmp_path / 'dst'
        src.mkdir()
        for i in range(8):
            (src / f'{i}.jpg').write_bytes(b'x' * 10)

        tuner = AdaptiveTuner(2, 1, 4)
        stats = move.move_with_structure(src, dst, max_workers=2, tuner=tuner)
        assert stats['moved'] == 8
        assert tuner.controller._count == 8
