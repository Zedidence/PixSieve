"""
Unit tests for pixsieve/utils/worker_policy.py: the worker table, the
resolver's precedence rules, cross-drive handling, and storage overrides.

Drive detection is always faked (disk_type.detect_drive / same_device are
patched), so nothing here depends on the machine running the tests.
"""

import logging

import pytest

from pixsieve import config
from pixsieve.utils import disk_type, io_probe, worker_policy
from pixsieve.utils.disk_type import Bus, DriveProfile, Media, Tier
from pixsieve.utils.worker_policy import (
    LADDER, OpKind, WORKER_TABLE, ladder_step, parse_storage_overrides, recommend,
    resolve_workers,
)

PROFILES = {
    Tier.NVME: DriveProfile('nvme', Media.SSD, Bus.NVME),
    Tier.SSD_INTERNAL: DriveProfile('ssd', Media.SSD, Bus.SATA),
    Tier.SSD_EXTERNAL: DriveProfile('usb-ssd', Media.SSD, Bus.USB),
    Tier.HDD_INTERNAL: DriveProfile('hdd', Media.HDD, Bus.SATA),
    Tier.HDD_EXTERNAL: DriveProfile('usb-hdd', Media.HDD, Bus.USB),
    Tier.EXTERNAL_AMBIGUOUS: DriveProfile('usb', Media.UNKNOWN, Bus.USB),
    Tier.SD_CARD: DriveProfile('sd', Media.SSD, Bus.SD),
    Tier.NETWORK: DriveProfile('nas', Media.UNKNOWN, Bus.NETWORK, is_remote=True),
    Tier.RAM: DriveProfile('ram', Media.SSD, Bus.RAM),
    Tier.UNKNOWN: DriveProfile('vm', Media.UNKNOWN, Bus.VIRTUAL),
}


class TestRecommend:
    def test_profiles_cover_every_tier(self):
        assert {p.tier for p in PROFILES.values()} == set(Tier)

    def test_table_covers_every_known_tier_and_op(self):
        for tier in Tier:
            if tier is Tier.UNKNOWN:
                assert tier not in WORKER_TABLE
            else:
                assert set(WORKER_TABLE[tier]) == set(OpKind)

    @pytest.mark.parametrize('tier', list(Tier))
    @pytest.mark.parametrize('op', list(OpKind))
    @pytest.mark.parametrize('cpu', [1, 4, 8, 64])
    def test_always_within_bounds(self, tier, op, cpu):
        n = recommend(op, PROFILES[tier], legacy=16, cpu=cpu)
        assert 1 <= n <= worker_policy.MAX_WORKERS

    @pytest.mark.parametrize('op', list(OpKind))
    @pytest.mark.parametrize('cpu', [1, 2, 4, 8, 16])
    def test_faster_drives_never_get_fewer_workers(self, op, cpu):
        order = [Tier.HDD_EXTERNAL, Tier.HDD_INTERNAL, Tier.SSD_EXTERNAL,
                 Tier.SSD_INTERNAL, Tier.NVME]
        counts = [recommend(op, PROFILES[t], legacy=32, cpu=cpu) for t in order]
        assert counts == sorted(counts), dict(zip(order, counts))

    @pytest.mark.parametrize('op', list(OpKind))
    def test_unknown_drive_keeps_legacy_default(self, op):
        assert recommend(op, PROFILES[Tier.UNKNOWN], legacy=7, cpu=8) == 7

    @pytest.mark.parametrize('tier', list(Tier))
    def test_scan_never_exceeds_legacy(self, tier):
        assert recommend(OpKind.SCAN, PROFILES[tier], legacy=6, cpu=64) <= 6

    def test_sata_hdd_vs_usb_ssd(self):
        # The motivating example: each gets its own, different count
        hdd, usb_ssd = PROFILES[Tier.HDD_INTERNAL], PROFILES[Tier.SSD_EXTERNAL]
        assert recommend(OpKind.SCAN, hdd, legacy=16, cpu=8) == 4
        assert recommend(OpKind.SCAN, usb_ssd, legacy=16, cpu=8) == 6
        assert recommend(OpKind.COPY, hdd, legacy=4, cpu=8) == 1
        assert recommend(OpKind.COPY, usb_ssd, legacy=4, cpu=8) == 2

    def test_metadata_ops_can_exceed_the_old_fixed_default(self):
        assert recommend(OpKind.METADATA, PROFILES[Tier.NVME], legacy=4, cpu=8) == 16

    def test_large_library_bump_only_on_fast_tiers(self):
        big = config.LARGE_LIBRARY_THRESHOLD
        nvme = recommend(OpKind.SCAN, PROFILES[Tier.NVME], legacy=32, cpu=4, file_count=big)
        hdd = recommend(OpKind.SCAN, PROFILES[Tier.HDD_INTERNAL], legacy=32, cpu=4, file_count=big)
        assert nvme == min(config.LARGE_LIBRARY_WORKERS, 32)
        assert hdd == 4

    def test_default_cpu_count_is_used(self, monkeypatch):
        monkeypatch.setattr(worker_policy.os, 'cpu_count', lambda: None)
        assert recommend(OpKind.REPAIR, PROFILES[Tier.NVME], legacy=4) == 1


class TestLadder:
    def test_steps(self):
        assert ladder_step(4, +1) == 6
        assert ladder_step(4, -1) == 2
        assert ladder_step(5, -1) == 4
        assert ladder_step(1, -1) == 1
        assert ladder_step(32, +1) == 32
        assert LADDER[0] == 1 and LADDER[-1] == 32


@pytest.fixture
def auto(monkeypatch):
    """Turn tuning on and fake drive detection: `drives` maps a path prefix to a profile."""
    monkeypatch.setattr(config, 'AUTO_WORKERS', True)
    monkeypatch.setattr(config, 'IO_PROBE_ENABLED', False)
    drives = {}

    def fake_detect(path):
        for prefix, profile in drives.items():
            if str(path).startswith(prefix):
                return profile
        return DriveProfile(str(path), source='unknown')

    def fake_same_device(a, b):
        da, db = fake_detect(a), fake_detect(b)
        if da.source == 'unknown' or db.source == 'unknown':
            return None
        return da.device_key == db.device_key

    monkeypatch.setattr(disk_type, 'detect_drive', fake_detect)
    monkeypatch.setattr(disk_type, 'same_device', fake_same_device)
    return drives


class TestResolvePrecedence:
    def test_explicit_count_is_used_exactly(self, auto):
        auto['/usb'] = PROFILES[Tier.HDD_EXTERNAL]
        d = resolve_workers(OpKind.SCAN, ['/usb/photos'], legacy_default=16, requested=12)
        assert d.workers == 12
        assert d.source == 'user'
        assert d.floor == 0 and d.ceiling == 0

    def test_explicit_count_on_slow_drive_warns(self, auto, caplog):
        auto['/usb'] = PROFILES[Tier.HDD_EXTERNAL]
        with caplog.at_level(logging.WARNING, logger='pixsieve.utils.worker_policy'):
            d = resolve_workers(OpKind.SCAN, ['/usb/photos'], legacy_default=16, requested=16)
        assert d.workers == 16
        assert 'external HDD' in caplog.text

    def test_explicit_count_clamped_to_upper(self, auto):
        d = resolve_workers(OpKind.METADATA, ['/x'], legacy_default=4, requested=30, upper=16)
        assert d.workers == 16

    def test_env_workers(self, auto, monkeypatch):
        auto['/usb'] = PROFILES[Tier.HDD_EXTERNAL]
        monkeypatch.setattr(config, 'ENV_WORKERS', 7)
        d = resolve_workers(OpKind.SCAN, ['/usb'], legacy_default=16)
        assert (d.workers, d.source) == (7, 'env')

    def test_explicit_beats_env(self, auto, monkeypatch):
        monkeypatch.setattr(config, 'ENV_WORKERS', 7)
        assert resolve_workers(OpKind.SCAN, ['/x'], legacy_default=16, requested=3).workers == 3

    def test_auto_off_keeps_legacy(self, monkeypatch):
        monkeypatch.setattr(config, 'AUTO_WORKERS', False)
        monkeypatch.setattr(disk_type, 'detect_drive', lambda p: pytest.fail('must not detect'))
        d = resolve_workers(OpKind.SCAN, ['/x'], legacy_default=16)
        assert (d.workers, d.source) == (16, 'fallback')

    def test_unknown_drive_keeps_legacy(self, auto):
        d = resolve_workers(OpKind.METADATA, ['/somewhere'], legacy_default=4)
        assert (d.workers, d.source) == (4, 'fallback')

    def test_auto_uses_table(self, auto):
        auto['/nvme'] = PROFILES[Tier.NVME]
        d = resolve_workers(OpKind.METADATA, ['/nvme/photos'], legacy_default=4)
        assert (d.workers, d.source) == (16, 'auto')
        assert d.label == 'NVMe SSD'
        assert 'NVMe SSD -> 16 workers (metadata)' == d.reason

    def test_decision_as_dict(self, auto):
        auto['/usb'] = PROFILES[Tier.SSD_EXTERNAL]
        d = resolve_workers(OpKind.COPY, ['/usb'], legacy_default=4).as_dict()
        assert d['workers'] == 2
        assert d['op'] == 'copy'
        assert d['drives'][0]['label'] == 'USB SSD'

    def test_singular_reason(self, auto):
        auto['/usb'] = PROFILES[Tier.HDD_EXTERNAL]
        assert resolve_workers(OpKind.COPY, ['/usb'], legacy_default=4).reason.endswith('1 worker (copy)')


class TestCrossDrive:
    def test_same_volume_move_is_a_rename(self, auto):
        auto['/nvme'] = PROFILES[Tier.NVME]
        d = resolve_workers(OpKind.COPY, ['/nvme/a'], destination='/nvme/b', legacy_default=4)
        assert d.op is OpKind.METADATA
        assert d.workers == 16

    def test_cross_volume_move_bounded_by_slower_side(self, auto):
        auto['/nvme'] = PROFILES[Tier.NVME]
        auto['/usb'] = PROFILES[Tier.SSD_EXTERNAL]
        d = resolve_workers(OpKind.COPY, ['/nvme/a'], destination='/usb/b', legacy_default=4)
        assert d.op is OpKind.COPY
        assert d.workers == 2
        assert d.label == 'NVMe SSD -> USB SSD'

    def test_undeterminable_volume_is_treated_as_cross_volume(self, auto):
        auto['/nvme'] = PROFILES[Tier.NVME]
        d = resolve_workers(OpKind.COPY, ['/nvme/a'], destination='/elsewhere', legacy_default=4)
        # destination unknown -> contributes the legacy default
        assert d.workers == 4

    def test_repair_quarantine_on_another_drive(self, auto):
        auto['/nvme'] = PROFILES[Tier.NVME]
        auto['/usb'] = PROFILES[Tier.HDD_EXTERNAL]
        d = resolve_workers(OpKind.REPAIR, ['/nvme/a'], destination='/usb/trash', legacy_default=4)
        assert d.workers == 1

    def test_repair_quarantine_on_same_drive(self, auto):
        auto['/nvme'] = PROFILES[Tier.NVME]
        d = resolve_workers(OpKind.REPAIR, ['/nvme/a'], destination='/nvme/trash',
                            legacy_default=4, upper=16)
        assert d.op is OpKind.REPAIR
        assert d.workers == min(worker_policy.os.cpu_count() or 1, 16)

    def test_multiple_sources_take_the_minimum(self, auto):
        auto['/nvme'] = PROFILES[Tier.NVME]
        auto['/hdd'] = PROFILES[Tier.HDD_INTERNAL]
        d = resolve_workers(OpKind.METADATA, ['/nvme/a', '/hdd/b'], legacy_default=4)
        assert d.workers == 2

    def test_unknown_source_contributes_legacy(self, auto):
        auto['/nvme'] = PROFILES[Tier.NVME]
        d = resolve_workers(OpKind.METADATA, ['/nvme/a', '/mystery'], legacy_default=4)
        assert d.workers == 4

    def test_same_drive_listed_twice_counts_once(self, auto):
        auto['/nvme'] = PROFILES[Tier.NVME]
        d = resolve_workers(OpKind.METADATA, ['/nvme/a', '/nvme/b'], legacy_default=4)
        assert len(d.profiles) == 1


class TestFloorCeiling:
    def test_hdd_ceiling_capped(self, auto):
        auto['/hdd'] = PROFILES[Tier.HDD_INTERNAL]
        d = resolve_workers(OpKind.REWRITE, ['/hdd'], legacy_default=4)
        assert d.workers == 2
        assert d.floor == 1
        assert d.ceiling <= config.HDD_ANALYSIS_WORKERS

    def test_scan_ceiling_never_above_legacy(self, auto, monkeypatch):
        monkeypatch.setattr(worker_policy.os, 'cpu_count', lambda: 8)
        auto['/nvme'] = PROFILES[Tier.NVME]
        d = resolve_workers(OpKind.SCAN, ['/nvme'], legacy_default=8)
        assert d.workers == 8
        assert d.ceiling == 8

    def test_ops_get_room_to_grow(self, auto):
        auto['/ssd'] = PROFILES[Tier.SSD_INTERNAL]
        d = resolve_workers(OpKind.COPY, ['/ssd'], legacy_default=4)
        assert (d.floor, d.workers, d.ceiling) == (2, 4, 6)


class TestProbeIntegration:
    def test_probe_step_down(self, auto, monkeypatch):
        monkeypatch.setattr(config, 'IO_PROBE_ENABLED', True)
        auto['/nas'] = PROFILES[Tier.NETWORK]
        monkeypatch.setattr(io_probe, 'refine_profile',
                            lambda p, files: (p, {'applied': True, 'step': -1}))
        d = resolve_workers(OpKind.METADATA, ['/nas'], legacy_default=4, sample_files=['f'] * 300)
        assert d.workers == 6   # 8 -> one ladder step down
        assert d.source == 'probe'

    def test_probe_reclassification(self, auto, monkeypatch):
        monkeypatch.setattr(config, 'IO_PROBE_ENABLED', True)
        auto['/usb'] = PROFILES[Tier.EXTERNAL_AMBIGUOUS]
        monkeypatch.setattr(
            io_probe, 'refine_profile',
            lambda p, files: (DriveProfile(p.device_key, Media.SSD, Bus.USB), {'applied': True, 'step': 0}),
        )
        d = resolve_workers(OpKind.COPY, ['/usb'], legacy_default=4, sample_files=['f'] * 300)
        assert d.workers == 2
        assert d.label == 'USB SSD'

    def test_no_probe_across_multiple_drives(self, auto, monkeypatch):
        monkeypatch.setattr(config, 'IO_PROBE_ENABLED', True)
        auto['/usb'] = PROFILES[Tier.EXTERNAL_AMBIGUOUS]
        auto['/hdd'] = PROFILES[Tier.HDD_INTERNAL]
        monkeypatch.setattr(io_probe, 'refine_profile', lambda p, f: pytest.fail('must not probe'))
        resolve_workers(OpKind.SCAN, ['/usb', '/hdd'], legacy_default=16, sample_files=['f'] * 300)

    def test_probe_disabled(self, auto, monkeypatch):
        auto['/usb'] = PROFILES[Tier.EXTERNAL_AMBIGUOUS]
        monkeypatch.setattr(io_probe, 'refine_profile', lambda p, f: pytest.fail('must not probe'))
        resolve_workers(OpKind.SCAN, ['/usb'], legacy_default=16, sample_files=['f'] * 300)
        monkeypatch.setattr(config, 'IO_PROBE_ENABLED', True)
        resolve_workers(OpKind.SCAN, ['/usb'], legacy_default=16, sample_files=['f'] * 300,
                        allow_probe=False)


class TestStorageOverrides:
    def test_parse(self):
        assert parse_storage_overrides(['usb-hdd']) == {'*': 'usb-hdd'}
        assert parse_storage_overrides(['E:=usb-ssd;/mnt/nas=network']) == {
            'E:': 'usb-ssd', '/mnt/nas': 'network',
        }
        assert parse_storage_overrides(['a=HDD', 'b=nvme']) == {'a': 'hdd', 'b': 'nvme'}
        assert parse_storage_overrides([]) == {}

    def test_parse_rejects_unknown_type(self):
        with pytest.raises(ValueError):
            parse_storage_overrides(['E:=floppy'])

    def test_override_replaces_detection(self, auto, tmp_path):
        auto[str(tmp_path)] = PROFILES[Tier.NVME]
        d = resolve_workers(OpKind.COPY, [str(tmp_path / 'photos')], legacy_default=4,
                            overrides={str(tmp_path): 'usb-hdd'})
        assert d.workers == 1
        assert d.profiles[0].source == 'override'

    def test_longest_prefix_wins(self, auto, tmp_path):
        overrides = {str(tmp_path): 'hdd', str(tmp_path / 'fast'): 'nvme'}
        d = resolve_workers(OpKind.METADATA, [str(tmp_path / 'fast' / 'x')], legacy_default=4,
                            overrides=overrides)
        assert d.workers == 16

    def test_prefix_must_match_whole_path_component(self, auto, tmp_path):
        d = resolve_workers(OpKind.METADATA, [str(tmp_path) + 'suffix'], legacy_default=4,
                            overrides={str(tmp_path): 'nvme'})
        assert d.source == 'fallback'

    def test_wildcard_override(self, auto):
        d = resolve_workers(OpKind.METADATA, ['/anything'], legacy_default=4, overrides={'*': 'sd'})
        assert d.workers == 1

    def test_env_overrides_merge_under_explicit_ones(self, auto, monkeypatch, tmp_path):
        monkeypatch.setattr(config, 'STORAGE_OVERRIDES', {str(tmp_path): 'hdd'})
        env_only = resolve_workers(OpKind.METADATA, [str(tmp_path)], legacy_default=4)
        assert env_only.workers == 2
        both = resolve_workers(OpKind.METADATA, [str(tmp_path)], legacy_default=4,
                               overrides={str(tmp_path): 'nvme'})
        assert both.workers == 16

    def test_bad_env_override_falls_back_to_detection(self, auto, monkeypatch, caplog):
        monkeypatch.setattr(config, 'STORAGE_OVERRIDES', {'*': 'floppy'})
        auto['/nvme'] = PROFILES[Tier.NVME]
        with caplog.at_level(logging.WARNING, logger='pixsieve.utils.worker_policy'):
            d = resolve_workers(OpKind.METADATA, ['/nvme'], legacy_default=4)
        assert d.workers == 16
        assert 'floppy' in caplog.text


class TestWarmUp:
    def test_noop_when_auto_off(self, monkeypatch):
        monkeypatch.setattr(disk_type, 'detect_drive_async', lambda p: pytest.fail('must not detect'))
        worker_policy.warm_up(['/x'])

    def test_detects_non_overridden_paths(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, 'AUTO_WORKERS', True)
        monkeypatch.setattr(config, 'STORAGE_OVERRIDES', {str(tmp_path / 'a'): 'hdd'})
        seen = []
        monkeypatch.setattr(disk_type, 'detect_drive_async', lambda paths: seen.extend(paths))
        worker_policy.warm_up([str(tmp_path / 'a'), str(tmp_path / 'b')])
        assert seen == [str(tmp_path / 'b')]

    def test_noop_with_env_workers(self, monkeypatch):
        monkeypatch.setattr(config, 'AUTO_WORKERS', True)
        monkeypatch.setattr(config, 'ENV_WORKERS', 3)
        monkeypatch.setattr(disk_type, 'detect_drive_async', lambda p: pytest.fail('must not detect'))
        worker_policy.warm_up(['/x'])


class TestConfigParsing:
    def test_env_storage_overrides(self, monkeypatch):
        monkeypatch.setenv('X_OVR', 'E:=usb-hdd; /mnt/nas=network ;ssd')
        assert config._env_storage_overrides('X_OVR') == {
            'E:': 'usb-hdd', '/mnt/nas': 'network', '*': 'ssd',
        }

    def test_env_workers(self, monkeypatch):
        monkeypatch.setenv('X_W', '8')
        assert config._env_workers('X_W') == 8
        monkeypatch.setenv('X_W', '99')
        assert config._env_workers('X_W') is None
        monkeypatch.setenv('X_W', 'lots')
        assert config._env_workers('X_W') is None

    def test_env_flag(self, monkeypatch):
        monkeypatch.delenv('X_F', raising=False)
        assert config._env_flag('X_F') is True
        for off in ('0', 'false', 'OFF', 'no'):
            monkeypatch.setenv('X_F', off)
            assert config._env_flag('X_F') is False
        monkeypatch.setenv('X_F', '1')
        assert config._env_flag('X_F') is True
