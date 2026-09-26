"""
Unit tests for pixsieve/utils/disk_type.py.

Every test here runs on every CI platform: platform queries are replaced
with fakes (a mocked subprocess.run for PowerShell/diskutil, a fake
sysfs/procfs/udev tree for Linux), so no real drive is ever inspected.
"""

import os
import plistlib
import subprocess
import threading

import pytest

from pixsieve.utils import disk_type
from pixsieve.utils.disk_type import Bus, DriveProfile, Media, Tier


@pytest.fixture(autouse=True)
def _clear_cache():
    """Detection results are memoized per-drive; keep tests isolated."""
    disk_type.clear_cache()
    yield
    disk_type.clear_cache()


class TestTailorWorkers:
    def test_caps_down_on_confirmed_hdd(self, monkeypatch):
        monkeypatch.setattr(disk_type, 'is_rotational', lambda path: True)
        assert disk_type.tailor_workers('/photos', default_workers=8, hdd_cap=2) == 2

    def test_leaves_default_alone_on_ssd(self, monkeypatch):
        monkeypatch.setattr(disk_type, 'is_rotational', lambda path: False)
        assert disk_type.tailor_workers('/photos', default_workers=8, hdd_cap=2) == 8

    def test_never_raises_the_cap_above_the_default(self, monkeypatch):
        # A cap that isn't actually lower than the default must never be applied,
        # even on a confirmed HDD - this only ever pulls worker counts down.
        monkeypatch.setattr(disk_type, 'is_rotational', lambda path: True)
        assert disk_type.tailor_workers('/photos', default_workers=2, hdd_cap=8) == 2


class TestTier:
    @pytest.mark.parametrize('media,bus,remote,expected', [
        (Media.SSD, Bus.NVME, False, Tier.NVME),
        (Media.UNKNOWN, Bus.NVME, False, Tier.NVME),
        (Media.SSD, Bus.THUNDERBOLT, False, Tier.NVME),
        (Media.SSD, Bus.SATA, False, Tier.SSD_INTERNAL),
        (Media.SSD, Bus.UNKNOWN, False, Tier.SSD_INTERNAL),
        (Media.SSD, Bus.USB, False, Tier.SSD_EXTERNAL),
        (Media.HDD, Bus.SATA, False, Tier.HDD_INTERNAL),
        (Media.HDD, Bus.SAS, False, Tier.HDD_INTERNAL),
        (Media.HDD, Bus.UNKNOWN, False, Tier.HDD_INTERNAL),
        (Media.HDD, Bus.USB, False, Tier.HDD_EXTERNAL),
        (Media.HDD, Bus.THUNDERBOLT, False, Tier.HDD_EXTERNAL),
        (Media.UNKNOWN, Bus.USB, False, Tier.EXTERNAL_AMBIGUOUS),
        (Media.SSD, Bus.SD, False, Tier.SD_CARD),
        (Media.UNKNOWN, Bus.NETWORK, True, Tier.NETWORK),
        (Media.SSD, Bus.SATA, True, Tier.NETWORK),
        (Media.SSD, Bus.RAM, False, Tier.RAM),
        (Media.SSD, Bus.VIRTUAL, False, Tier.UNKNOWN),
        (Media.UNKNOWN, Bus.RAID, False, Tier.UNKNOWN),
        (Media.HDD, Bus.RAID, False, Tier.HDD_INTERNAL),
        (Media.UNKNOWN, Bus.SATA, False, Tier.UNKNOWN),
        (Media.UNKNOWN, Bus.UNKNOWN, False, Tier.UNKNOWN),
    ])
    def test_tier_mapping(self, media, bus, remote, expected):
        assert DriveProfile('k', media, bus, is_remote=remote).tier is expected

    def test_every_tier_has_a_label(self):
        for tier in Tier:
            assert tier.label

    def test_as_dict(self):
        d = DriveProfile('C:', Media.SSD, Bus.USB, detail='x').as_dict()
        assert d['tier'] == 'ssd_external'
        assert d['label'] == 'USB SSD'
        assert d['media'] == 'ssd' and d['bus'] == 'usb'


class TestProfileFromSpec:
    def test_known_specs(self):
        assert disk_type.profile_from_spec('usb-hdd').tier is Tier.HDD_EXTERNAL
        assert disk_type.profile_from_spec('NVMe').tier is Tier.NVME
        assert disk_type.profile_from_spec('network').tier is Tier.NETWORK
        assert disk_type.profile_from_spec(' usb ').tier is Tier.EXTERNAL_AMBIGUOUS
        assert disk_type.profile_from_spec('ssd', 'E:').source == 'override'

    def test_unknown_spec_raises(self):
        with pytest.raises(ValueError, match='usb-hdd'):
            disk_type.profile_from_spec('floppy')


class TestDetectDriveCaching:
    def test_caches_result_per_drive(self, monkeypatch, tmp_path):
        calls = []

        def fake_system():
            calls.append(1)
            return 'Linux'

        monkeypatch.setattr(disk_type.platform_module, 'system', fake_system)
        monkeypatch.setattr(disk_type, '_detect_linux',
                            lambda path: DriveProfile('', Media.SSD, Bus.SATA))

        first = disk_type.detect_media_type(str(tmp_path))
        second = disk_type.detect_media_type(str(tmp_path))

        assert first == second == 'ssd'
        assert len(calls) == 1  # second call served from cache, no re-detection

    def test_paths_on_same_filesystem_share_one_detection(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(disk_type.platform_module, 'system', lambda: 'Linux')

        def fake_detect(path):
            calls.append(path)
            return DriveProfile('', Media.HDD, Bus.SATA)

        monkeypatch.setattr(disk_type, '_detect_linux', fake_detect)
        (tmp_path / 'a').mkdir()
        (tmp_path / 'b').mkdir()

        disk_type.detect_drive(str(tmp_path / 'a'))
        disk_type.detect_drive(str(tmp_path / 'b'))
        assert len(calls) == 1

    def test_device_key_is_filled_in(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type.platform_module, 'system', lambda: 'Linux')
        monkeypatch.setattr(disk_type, '_detect_linux',
                            lambda path: DriveProfile('', Media.SSD, Bus.NVME))
        assert disk_type.detect_drive(str(tmp_path)).device_key

    def test_clear_cache_forces_redetection(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(disk_type.platform_module, 'system', lambda: 'Linux')

        def fake_detect(path):
            calls.append(path)
            return DriveProfile('', Media.SSD, Bus.NVME)

        monkeypatch.setattr(disk_type, '_detect_linux', fake_detect)
        disk_type.detect_drive(str(tmp_path))
        disk_type.clear_cache()
        disk_type.detect_drive(str(tmp_path))
        assert len(calls) == 2

    def test_falls_back_to_unknown_on_exception(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type.platform_module, 'system', lambda: 'Linux')

        def boom(path):
            raise OSError("no such tool")

        monkeypatch.setattr(disk_type, '_detect_linux', boom)

        profile = disk_type.detect_drive(str(tmp_path))
        assert profile.tier is Tier.UNKNOWN
        assert profile.source == 'unknown'
        assert disk_type.detect_media_type(str(tmp_path)) == 'unknown'

    def test_unsupported_platform_is_unknown(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type.platform_module, 'system', lambda: 'SunOS')
        assert disk_type.detect_drive(str(tmp_path)).tier is Tier.UNKNOWN

    def test_concurrent_callers_share_one_detection(self, monkeypatch, tmp_path):
        calls = []
        release = threading.Event()
        monkeypatch.setattr(disk_type.platform_module, 'system', lambda: 'Linux')

        def slow_detect(path):
            calls.append(path)
            release.wait(5)
            return DriveProfile('', Media.SSD, Bus.SATA)

        monkeypatch.setattr(disk_type, '_detect_linux', slow_detect)

        results = []
        threads = [
            threading.Thread(target=lambda: results.append(disk_type.detect_drive(str(tmp_path))))
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        release.set()
        for t in threads:
            t.join(10)

        assert len(calls) == 1
        assert len(results) == 10
        assert all(r.media is Media.SSD for r in results)

    def test_async_warmup_populates_cache(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(disk_type.platform_module, 'system', lambda: 'Linux')

        def fake_detect(path):
            calls.append(path)
            return DriveProfile('', Media.HDD, Bus.USB)

        monkeypatch.setattr(disk_type, '_detect_linux', fake_detect)
        disk_type.detect_drive_async([str(tmp_path)]).join(5)
        assert disk_type.detect_drive(str(tmp_path)).bus is Bus.USB
        assert len(calls) == 1

    def test_is_rotational_true_only_for_hdd(self, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_type, 'detect_media_type', lambda path: 'hdd')
        assert disk_type.is_rotational(str(tmp_path)) is True

        monkeypatch.setattr(disk_type, 'detect_media_type', lambda path: 'ssd')
        assert disk_type.is_rotational(str(tmp_path)) is False

        monkeypatch.setattr(disk_type, 'detect_media_type', lambda path: 'unknown')
        assert disk_type.is_rotational(str(tmp_path)) is False


class TestSameDevice:
    def test_same_directory_tree(self, tmp_path):
        (tmp_path / 'a').mkdir()
        assert disk_type.same_device(str(tmp_path / 'a'), str(tmp_path / 'not' / 'yet')) is True

    def test_stat_failure_is_none(self, monkeypatch, tmp_path):
        def fail_stat(path):
            raise OSError('gone')
        monkeypatch.setattr(disk_type.os, 'stat', fail_stat)
        assert disk_type.same_device(str(tmp_path), str(tmp_path)) is None


class TestWindowsDetection:
    def _mock_run(self, monkeypatch, stdout='', returncode=0, drive_type=0):
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr='')

        monkeypatch.setattr(disk_type.subprocess, 'run', fake_run)
        monkeypatch.setattr(disk_type, '_win_drive_type', lambda root: drive_type)
        return calls

    @pytest.mark.parametrize('stdout,media,bus,tier', [
        ('SSD|NVMe|0\r\n', Media.SSD, Bus.NVME, Tier.NVME),
        ('SSD|SATA|0\r\n', Media.SSD, Bus.SATA, Tier.SSD_INTERNAL),
        ('HDD|SATA|Unknown\r\n', Media.HDD, Bus.SATA, Tier.HDD_INTERNAL),
        ('HDD|SATA|7200\r\n', Media.HDD, Bus.SATA, Tier.HDD_INTERNAL),
        ('Unspecified|USB|0\r\n', Media.SSD, Bus.USB, Tier.SSD_EXTERNAL),
        ('Unspecified|USB|5400\r\n', Media.HDD, Bus.USB, Tier.HDD_EXTERNAL),
        ('Unspecified|USB|4294967295\r\n', Media.UNKNOWN, Bus.USB, Tier.EXTERNAL_AMBIGUOUS),
        ('4|17|0\r\n', Media.SSD, Bus.NVME, Tier.NVME),
        ('3|7|\r\n', Media.HDD, Bus.USB, Tier.HDD_EXTERNAL),
        ('Unspecified|NVMe|\r\n', Media.SSD, Bus.NVME, Tier.NVME),
        ('|Storage Spaces|\r\n', Media.UNKNOWN, Bus.UNKNOWN, Tier.UNKNOWN),
        ('|File Backed Virtual|\r\n', Media.UNKNOWN, Bus.VIRTUAL, Tier.UNKNOWN),
        ('Unspecified|SD|0\r\n', Media.SSD, Bus.SD, Tier.SD_CARD),
        ('SSD\r\n', Media.SSD, Bus.UNKNOWN, Tier.SSD_INTERNAL),
        ('HDD\r\n', Media.HDD, Bus.UNKNOWN, Tier.HDD_INTERNAL),
    ])
    def test_parses_powershell_output(self, monkeypatch, stdout, media, bus, tier):
        self._mock_run(monkeypatch, stdout=stdout)
        profile = disk_type._detect_windows('D:\\photos')
        assert (profile.media, profile.bus, profile.tier) == (media, bus, tier)

    def test_nonzero_exit_is_unknown(self, monkeypatch):
        self._mock_run(monkeypatch, stdout='', returncode=1)
        assert disk_type._detect_windows('Z:\\').tier is Tier.UNKNOWN

    def test_mapped_network_drive_skips_powershell(self, monkeypatch):
        calls = self._mock_run(monkeypatch, stdout='SSD|NVMe|0', drive_type=4)
        profile = disk_type._detect_windows('Z:\\photos')
        assert profile.tier is Tier.NETWORK
        assert profile.is_remote
        assert calls == []

    def test_ram_disk_skips_powershell(self, monkeypatch):
        calls = self._mock_run(monkeypatch, stdout='SSD|NVMe|0', drive_type=6)
        assert disk_type._detect_windows('R:\\').tier is Tier.RAM
        assert calls == []

    def test_unc_path_is_network_without_powershell(self, monkeypatch):
        # A UNC path (\\server\share\...) has no drive letter to resolve, and
        # must not reach PowerShell at all (its "drive" is interpolated into
        # the script).
        def fail_run(*args, **kwargs):
            raise AssertionError("PowerShell should not be invoked for a UNC path")
        monkeypatch.setattr(disk_type.subprocess, 'run', fail_run)

        for path in ('\\\\server\\share\\photos', "\\\\server\\x'; evil; '\\photos"):
            profile = disk_type._detect_windows(path)
            assert profile.tier is Tier.NETWORK
            assert profile.media is Media.UNKNOWN

    def test_no_drive_letter_is_unknown(self, monkeypatch):
        def fail_run(*args, **kwargs):
            raise AssertionError("PowerShell should not be invoked without a drive letter")
        monkeypatch.setattr(disk_type.subprocess, 'run', fail_run)
        assert disk_type._detect_windows('relative\\path').tier is Tier.UNKNOWN

    def test_drive_letter_is_the_only_interpolated_value(self, monkeypatch):
        calls = self._mock_run(monkeypatch, stdout='SSD|NVMe|0')
        disk_type._detect_windows('e:\\photos')
        script = calls[0][-1]
        assert "-DriveLetter 'E'" in script


class TestMacOSDetection:
    def _mock(self, monkeypatch, plists, fstype='apfs'):
        """`plists` maps the diskutil target to its info dict (None = failure)."""
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            info = plists.get(cmd[-1])
            if info is None:
                return subprocess.CompletedProcess(cmd, 1, stdout='', stderr='')
            return subprocess.CompletedProcess(
                cmd, 0, stdout=plistlib.dumps(info).decode('utf-8'), stderr='')

        monkeypatch.setattr(disk_type.subprocess, 'run', fake_run)
        monkeypatch.setattr(disk_type, '_macos_mount_fstype', lambda path: fstype)
        return calls

    @pytest.mark.parametrize('info,tier', [
        ({'SolidState': True, 'BusProtocol': 'Apple Fabric'}, Tier.NVME),
        ({'SolidState': True, 'BusProtocol': 'PCI-Express'}, Tier.NVME),
        ({'SolidState': True, 'BusProtocol': 'USB'}, Tier.SSD_EXTERNAL),
        ({'SolidState': False, 'BusProtocol': 'USB'}, Tier.HDD_EXTERNAL),
        ({'SolidState': True, 'BusProtocol': 'Thunderbolt'}, Tier.NVME),
        ({'SolidState': False, 'BusProtocol': 'SATA'}, Tier.HDD_INTERNAL),
        ({'BusProtocol': 'Secure Digital'}, Tier.SD_CARD),
        ({'SolidState': True, 'BusProtocol': 'Disk Image'}, Tier.UNKNOWN),
    ])
    def test_parses_diskutil_plist(self, monkeypatch, info, tier):
        self._mock(monkeypatch, {'/Volumes/Photos': info})
        assert disk_type._detect_macos('/Volumes/Photos').tier is tier

    def test_apfs_volume_consults_physical_store(self, monkeypatch):
        calls = self._mock(monkeypatch, {
            '/Volumes/Photos': {
                'DeviceIdentifier': 'disk4s1',
                'APFSPhysicalStores': [{'APFSPhysicalStore': 'disk3s2'}],
            },
            'disk3s2': {'SolidState': True, 'BusProtocol': 'USB'},
        })
        profile = disk_type._detect_macos('/Volumes/Photos')
        assert profile.tier is Tier.SSD_EXTERNAL
        assert len(calls) == 2

    def test_falls_back_to_parent_whole_disk(self, monkeypatch):
        self._mock(monkeypatch, {
            '/Volumes/Photos': {'DeviceIdentifier': 'disk2s1', 'ParentWholeDisk': 'disk2',
                                'SolidState': False},
            'disk2': {'BusProtocol': 'USB'},
        })
        assert disk_type._detect_macos('/Volumes/Photos').tier is Tier.HDD_EXTERNAL

    def test_network_mount_skips_diskutil(self, monkeypatch):
        calls = self._mock(monkeypatch, {}, fstype='smbfs')
        profile = disk_type._detect_macos('/Volumes/share')
        assert profile.tier is Tier.NETWORK
        assert calls == []

    def test_diskutil_failure_is_unknown(self, monkeypatch):
        self._mock(monkeypatch, {})
        assert disk_type._detect_macos('/Volumes/x').tier is Tier.UNKNOWN

    def test_legacy_text_output(self, monkeypatch):
        def fake_run(cmd, capture_output, text, timeout):
            return subprocess.CompletedProcess(cmd, 0, stdout='   Solid State:   Yes\n', stderr='')
        monkeypatch.setattr(disk_type.subprocess, 'run', fake_run)
        monkeypatch.setattr(disk_type, '_macos_mount_fstype', lambda path: None)
        assert disk_type._detect_macos('/').media is Media.SSD


class FakeLinuxTree:
    """
    A fake /sys, /proc/mounts and udev database under tmp_path. Symlinks are
    emulated through a realpath mapping so this also runs on Windows CI.
    """

    def __init__(self, root, monkeypatch):
        self.sys = str(root / 'sys')
        self.udev = str(root / 'udev')
        self.mounts = str(root / 'mounts')
        self.links = {}
        os.makedirs(self.udev)
        monkeypatch.setattr(disk_type, '_SYSFS', self.sys)
        monkeypatch.setattr(disk_type, '_UDEV_DB', self.udev)
        monkeypatch.setattr(disk_type, '_PROC_MOUNTS', self.mounts)
        monkeypatch.setattr(disk_type, '_realpath', lambda p: self.links.get(p, p))

    def _write(self, path, content=''):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)

    def disk(self, name, rotational=None, device_path=None, dev_numbers=None, partitions=()):
        block = os.path.join(self.sys, 'block', name)
        os.makedirs(os.path.join(self.sys, 'class', 'block', name), exist_ok=True)
        os.makedirs(block, exist_ok=True)
        if rotational is not None:
            self._write(os.path.join(block, 'queue', 'rotational'), f'{rotational}\n')
        if dev_numbers:
            self._write(os.path.join(block, 'dev'), dev_numbers)
        real_block = f'/sys/devices/{device_path or "platform"}/block/{name}'
        if device_path:
            self.links[block] = real_block
        for part in partitions:
            class_dir = os.path.join(self.sys, 'class', 'block', part)
            self._write(os.path.join(class_dir, 'partition'), '1')
            self.links[class_dir] = f'{real_block}/{part}'

    def slaves(self, name, members):
        for member in members:
            os.makedirs(os.path.join(self.sys, 'block', name, 'slaves', member), exist_ok=True)
        os.makedirs(os.path.join(self.sys, 'class', 'block', name), exist_ok=True)

    def udev_bus(self, dev_numbers, bus):
        self._write(os.path.join(self.udev, f'b{dev_numbers}'), f'S:disk/by-id/x\nE:ID_BUS={bus}\n')


class TestLinuxDetection:
    @pytest.fixture
    def tree(self, tmp_path, monkeypatch):
        return FakeLinuxTree(tmp_path, monkeypatch)

    def _mount(self, monkeypatch, source, fstype='ext4'):
        monkeypatch.setattr(disk_type, '_linux_mount_entry', lambda path: (source, '/data', fstype))

    def test_sata_hdd_partition(self, tree, monkeypatch):
        tree.disk('sda', rotational=1, device_path='pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0',
                  partitions=['sda1'])
        self._mount(monkeypatch, '/dev/sda1')
        profile = disk_type._detect_linux('/data')
        assert (profile.media, profile.bus, profile.tier) == (Media.HDD, Bus.SATA, Tier.HDD_INTERNAL)

    def test_sata_ssd(self, tree, monkeypatch):
        tree.disk('sdb', rotational=0, device_path='pci0000:00/ata2/host1', partitions=['sdb1'])
        self._mount(monkeypatch, '/dev/sdb1')
        assert disk_type._detect_linux('/data').tier is Tier.SSD_INTERNAL

    def test_nvme_partition(self, tree, monkeypatch):
        tree.disk('nvme0n1', rotational=0, device_path='pci0000:00/0000:00:1d.0/nvme/nvme0',
                  partitions=['nvme0n1p2'])
        self._mount(monkeypatch, '/dev/nvme0n1p2')
        assert disk_type._detect_linux('/data').tier is Tier.NVME

    def test_usb_ssd(self, tree, monkeypatch):
        tree.disk('sdc', rotational=0, device_path='pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host3',
                  partitions=['sdc1'])
        self._mount(monkeypatch, '/dev/sdc1')
        assert disk_type._detect_linux('/data').tier is Tier.SSD_EXTERNAL

    def test_usb_rotational_flag_is_ambiguous(self, tree, monkeypatch):
        # USB-SATA bridges commonly report rotational=1 even for SSDs
        tree.disk('sdd', rotational=1, device_path='pci0000:00/usb3/3-2/host4', partitions=['sdd1'])
        self._mount(monkeypatch, '/dev/sdd1')
        profile = disk_type._detect_linux('/data')
        assert profile.bus is Bus.USB
        assert profile.tier is Tier.EXTERNAL_AMBIGUOUS

    def test_sd_card(self, tree, monkeypatch):
        tree.disk('mmcblk0', rotational=0, partitions=['mmcblk0p1'])
        self._mount(monkeypatch, '/dev/mmcblk0p1')
        assert disk_type._detect_linux('/data').tier is Tier.SD_CARD

    def test_device_mapper_resolves_through_slaves(self, tree, monkeypatch):
        tree.disk('sda', rotational=1, device_path='pci0000:00/ata1/host0', partitions=['sda2'])
        tree.slaves('dm-0', ['sda2'])
        tree.links['/dev/mapper/vg-root'] = '/dev/dm-0'
        self._mount(monkeypatch, '/dev/mapper/vg-root')
        assert disk_type._detect_linux('/data').tier is Tier.HDD_INTERNAL

    def test_md_array_takes_worst_member(self, tree, monkeypatch):
        tree.disk('sda', rotational=0, device_path='pci0000:00/ata1/host0', partitions=['sda1'])
        tree.disk('sdb', rotational=1, device_path='pci0000:00/ata2/host1', partitions=['sdb1'])
        tree.slaves('md0', ['sda1', 'sdb1'])
        self._mount(monkeypatch, '/dev/md0')
        profile = disk_type._detect_linux('/data')
        assert profile.media is Media.HDD
        assert profile.bus is Bus.SATA

    def test_loop_device_is_unknown_tier(self, tree, monkeypatch):
        tree.disk('loop0', rotational=0)
        self._mount(monkeypatch, '/dev/loop0', fstype='squashfs')
        profile = disk_type._detect_linux('/data')
        assert profile.bus is Bus.VIRTUAL
        assert profile.tier is Tier.UNKNOWN

    def test_udev_fallback_for_bus(self, tree, monkeypatch):
        tree.disk('sde', rotational=0, dev_numbers='8:64', partitions=['sde1'])
        tree.udev_bus('8:64', 'usb')
        self._mount(monkeypatch, '/dev/sde1')
        assert disk_type._detect_linux('/data').tier is Tier.SSD_EXTERNAL

    def test_missing_class_entry_falls_back_to_name_stripping(self, tree, monkeypatch):
        os.makedirs(os.path.join(tree.sys, 'block', 'sdf', 'queue'))
        with open(os.path.join(tree.sys, 'block', 'sdf', 'queue', 'rotational'), 'w') as f:
            f.write('1')
        self._mount(monkeypatch, '/dev/sdf3')
        assert disk_type._detect_linux('/data').media is Media.HDD

    @pytest.mark.parametrize('fstype', ['nfs4', 'cifs', 'fuse.sshfs', 'drvfs', '9p'])
    def test_network_filesystems(self, tree, monkeypatch, fstype):
        self._mount(monkeypatch, 'server:/export', fstype=fstype)
        profile = disk_type._detect_linux('/data')
        assert profile.tier is Tier.NETWORK
        assert profile.is_remote

    def test_tmpfs_is_ram(self, tree, monkeypatch):
        self._mount(monkeypatch, 'tmpfs', fstype='tmpfs')
        assert disk_type._detect_linux('/data').tier is Tier.RAM

    def test_overlay_is_unknown(self, tree, monkeypatch):
        self._mount(monkeypatch, 'overlay', fstype='overlay')
        assert disk_type._detect_linux('/data').tier is Tier.UNKNOWN

    def test_no_mount_found_is_unknown(self, tree, monkeypatch):
        monkeypatch.setattr(disk_type, '_linux_mount_entry', lambda path: None)
        assert disk_type._detect_linux('/data').tier is Tier.UNKNOWN

    def test_missing_rotational_flag_is_unknown_media(self, tree, monkeypatch):
        tree.disk('sdg', device_path='pci0000:00/ata3/host2', partitions=['sdg1'])
        self._mount(monkeypatch, '/dev/sdg1')
        assert disk_type._detect_linux('/data').media is Media.UNKNOWN


class TestLinuxMountEntry:
    def test_longest_matching_mount_wins_and_unescapes(self, tmp_path, monkeypatch):
        target = tmp_path / 'my photos'
        target.mkdir()
        mounts = tmp_path / 'mounts'
        escaped = str(target).replace(' ', '\\040')
        mounts.write_text(
            f"/dev/sda1 {tmp_path.anchor or '/'} ext4 rw 0 0\n"
            f"/dev/sdb1 {escaped} ext4 rw 0 0\n"
            "garbage\n"
        )
        monkeypatch.setattr(disk_type, '_PROC_MOUNTS', str(mounts))
        source, mount_point, fstype = disk_type._linux_mount_entry(str(target))
        assert source == '/dev/sdb1'
        assert mount_point == str(target)
        assert disk_type._linux_block_device(str(target)) == '/dev/sdb1'

    def test_non_device_source_has_no_block_device(self, tmp_path, monkeypatch):
        mounts = tmp_path / 'mounts'
        mounts.write_text(f"server:/export {tmp_path} nfs4 rw 0 0\n")
        monkeypatch.setattr(disk_type, '_PROC_MOUNTS', str(mounts))
        assert disk_type._linux_mount_entry(str(tmp_path))[2] == 'nfs4'
        assert disk_type._linux_block_device(str(tmp_path)) is None


def test_live_detection_returns_a_profile(tmp_path):
    """Smoke test against the real machine; CI runners are VMs, so any tier is fine."""
    profile = disk_type.detect_drive(str(tmp_path))
    assert isinstance(profile.tier, Tier)
    assert profile.device_key
