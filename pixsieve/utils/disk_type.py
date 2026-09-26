"""
Detect what kind of storage backs a filesystem path - its media (HDD/SSD),
the bus it is attached through (SATA, NVMe, USB, SD, network, ...) - so
read/write concurrency can be tailored to the device's actual I/O
characteristics instead of just CPU count.

Concurrent random-access I/O against a spinning disk causes seek thrashing
that *reduces* throughput past a small number of simultaneous operations,
and USB bridges / SD readers have shallow command queues - unlike internal
SSDs and NVMe drives, where more workers keep helping up to the device's
queue depth. utils/worker_policy.py turns a DriveProfile into worker counts.

Detection is best-effort: any failure (unknown filesystem, virtualized
drive, missing platform tool) yields an UNKNOWN profile, which the worker
policy treats as "keep the caller's existing default".

Results are cached per drive - the platform calls involved (PowerShell on
Windows, diskutil on macOS) cost tens to hundreds of milliseconds, and a
drive's characteristics never change during a run. clear_cache() drops the
cache so a long-running server picks up a different drive mounted at the
same letter/mount point.
"""

from __future__ import annotations

import ctypes
import logging
import ntpath
import os
import platform as platform_module
import plistlib
import re
import subprocess
import threading
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

_logger = logging.getLogger(__name__)


class Media(str, Enum):
    HDD = 'hdd'
    SSD = 'ssd'
    UNKNOWN = 'unknown'


class Bus(str, Enum):
    NVME = 'nvme'
    SATA = 'sata'
    SAS = 'sas'
    USB = 'usb'
    THUNDERBOLT = 'thunderbolt'
    SD = 'sd'
    RAID = 'raid'
    VIRTUAL = 'virtual'
    NETWORK = 'network'
    RAM = 'ram'
    UNKNOWN = 'unknown'


class Tier(str, Enum):
    """Performance class a DriveProfile falls into (see worker_policy.WORKER_TABLE)."""
    NVME = 'nvme'
    SSD_INTERNAL = 'ssd_internal'
    SSD_EXTERNAL = 'ssd_external'
    HDD_INTERNAL = 'hdd_internal'
    HDD_EXTERNAL = 'hdd_external'
    EXTERNAL_AMBIGUOUS = 'external_ambiguous'
    SD_CARD = 'sd_card'
    NETWORK = 'network'
    RAM = 'ram'
    UNKNOWN = 'unknown'

    @property
    def label(self) -> str:
        return _TIER_LABELS[self]


_TIER_LABELS = {
    Tier.NVME: 'NVMe SSD',
    Tier.SSD_INTERNAL: 'internal SSD',
    Tier.SSD_EXTERNAL: 'USB SSD',
    Tier.HDD_INTERNAL: 'internal HDD',
    Tier.HDD_EXTERNAL: 'external HDD',
    Tier.EXTERNAL_AMBIGUOUS: 'external drive',
    Tier.SD_CARD: 'SD card',
    Tier.NETWORK: 'network drive',
    Tier.RAM: 'RAM disk',
    Tier.UNKNOWN: 'unknown drive',
}


@dataclass(frozen=True)
class DriveProfile:
    """What is known about the storage behind a path."""
    device_key: str
    media: Media = Media.UNKNOWN
    bus: Bus = Bus.UNKNOWN
    is_remote: bool = False
    source: str = 'detected'   # 'detected' | 'override' | 'unknown'
    detail: str = ''

    @property
    def tier(self) -> Tier:
        media, bus = self.media, self.bus
        if bus is Bus.RAM:
            return Tier.RAM
        if self.is_remote or bus is Bus.NETWORK:
            return Tier.NETWORK
        if bus is Bus.SD:
            return Tier.SD_CARD
        if bus is Bus.VIRTUAL:
            return Tier.UNKNOWN
        if bus is Bus.RAID and media is Media.UNKNOWN:
            return Tier.UNKNOWN
        if bus is Bus.NVME or (bus is Bus.THUNDERBOLT and media is Media.SSD):
            return Tier.NVME
        if media is Media.SSD:
            return Tier.SSD_EXTERNAL if bus is Bus.USB else Tier.SSD_INTERNAL
        if media is Media.HDD:
            if bus in (Bus.USB, Bus.THUNDERBOLT):
                return Tier.HDD_EXTERNAL
            return Tier.HDD_INTERNAL
        if bus is Bus.USB:
            return Tier.EXTERNAL_AMBIGUOUS
        return Tier.UNKNOWN

    def as_dict(self) -> dict:
        return {
            'device': self.device_key,
            'media': self.media.value,
            'bus': self.bus.value,
            'tier': self.tier.value,
            'label': self.tier.label,
            'remote': self.is_remote,
            'source': self.source,
            'detail': self.detail,
        }


# Classifications accepted by --storage-profile / PIXSIEVE_STORAGE_OVERRIDE.
_OVERRIDE_SPECS: dict[str, tuple[Media, Bus]] = {
    'nvme': (Media.SSD, Bus.NVME),
    'ssd': (Media.SSD, Bus.SATA),
    'usb-ssd': (Media.SSD, Bus.USB),
    'hdd': (Media.HDD, Bus.SATA),
    'usb-hdd': (Media.HDD, Bus.USB),
    'usb': (Media.UNKNOWN, Bus.USB),
    'sd': (Media.UNKNOWN, Bus.SD),
    'network': (Media.UNKNOWN, Bus.NETWORK),
    'ram': (Media.SSD, Bus.RAM),
}

OVERRIDE_SPEC_NAMES = tuple(_OVERRIDE_SPECS)


def profile_from_spec(spec: str, device_key: str = '') -> DriveProfile:
    """Build a profile from a user-supplied classification like 'usb-hdd'."""
    key = spec.strip().lower()
    if key not in _OVERRIDE_SPECS:
        raise ValueError(
            f"Unknown storage profile '{spec}' "
            f"(expected one of: {', '.join(OVERRIDE_SPEC_NAMES)})"
        )
    media, bus = _OVERRIDE_SPECS[key]
    return DriveProfile(device_key, media, bus, is_remote=bus is Bus.NETWORK,
                        source='override', detail=key)


_cache: dict[str, DriveProfile] = {}
_inflight: dict[str, threading.Event] = {}
_cache_lock = threading.Lock()

# How long a caller waits for another thread already detecting the same drive
# (the PowerShell call itself times out after 10s).
_INFLIGHT_WAIT_S = 15


def _nearest_existing(path: str) -> str:
    """Walk up to the nearest existing ancestor (destination dirs may not exist yet)."""
    current = os.path.abspath(path)
    while current and not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def _cache_key(path: str) -> str:
    if os.name == 'nt':
        # The drive/share is all that matters; skip _nearest_existing, whose
        # exists() probes can each stall for seconds on an unreachable share.
        drive, _ = os.path.splitdrive(os.path.abspath(path))
        if drive.startswith(('\\\\', '//')):
            return drive.lower()
        return drive.upper() or 'C:'
    # One entry per filesystem, not per path
    try:
        return f'dev:{os.stat(_nearest_existing(path)).st_dev}'
    except OSError:
        return os.path.abspath(path)


def clear_cache() -> None:
    """Forget all detected profiles (e.g. at the start of each operation)."""
    with _cache_lock:
        _cache.clear()


def detect_drive(path: str) -> DriveProfile:
    """
    Return the DriveProfile for the drive backing `path`.

    Never raises: any failure yields an UNKNOWN profile, since this is a
    performance hint, not a correctness dependency.
    """
    key = _cache_key(path)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        waiting_on = _inflight.get(key)
        if waiting_on is None:
            _inflight[key] = threading.Event()

    if waiting_on is not None:
        # Another thread (e.g. detect_drive_async) is already running the
        # platform query for this drive - don't pay for a second one.
        waiting_on.wait(_INFLIGHT_WAIT_S)
        with _cache_lock:
            cached = _cache.get(key)
        return cached if cached is not None else DriveProfile(key, source='unknown')

    profile = DriveProfile(key, source='unknown')
    try:
        system = platform_module.system()
        if system == 'Windows':
            # Only the drive letter/share is used, so no need to resolve
            profile = _detect_windows(os.path.abspath(path))
        elif system == 'Darwin':
            profile = _detect_macos(_nearest_existing(path))
        elif system == 'Linux':
            profile = _detect_linux(_nearest_existing(path))
        profile = replace(profile, device_key=key)
    except Exception as e:
        _logger.debug(f"Drive detection failed for {path}: {e}")
        profile = DriveProfile(key, source='unknown')
    finally:
        with _cache_lock:
            _cache[key] = profile
            event = _inflight.pop(key, None)
        if event is not None:
            event.set()
    return profile


def detect_drive_async(paths: Iterable[str]) -> threading.Thread:
    """
    Warm the detection cache for `paths` on a daemon thread, so the platform
    query (PowerShell startup alone is 0.3-1s) overlaps other work like file
    discovery. Later detect_drive() calls wait on it instead of re-querying.
    """
    path_list = [str(p) for p in paths]

    def _warm():
        for p in path_list:
            detect_drive(p)

    thread = threading.Thread(target=_warm, name='pixsieve-drive-detect', daemon=True)
    thread.start()
    return thread


def same_device(a: str, b: str) -> bool | None:
    """True if both paths live on the same filesystem; None if that can't be determined."""
    try:
        return os.stat(_nearest_existing(a)).st_dev == os.stat(_nearest_existing(b)).st_dev
    except OSError:
        return None


def detect_media_type(path: str) -> str:
    """Return 'ssd', 'hdd', or 'unknown' for the drive backing `path`."""
    return detect_drive(path).media.value


def is_rotational(path: str) -> bool:
    """True only when the drive backing `path` is confidently identified as an HDD."""
    return detect_media_type(path) == 'hdd'


def tailor_workers(path: str, default_workers: int, hdd_cap: int) -> int:
    """
    Return `hdd_cap` in place of `default_workers` when `path`'s drive is a
    confirmed HDD and the cap is actually lower; otherwise return
    `default_workers` unchanged (including whenever detection is 'unknown').

    Deprecated: use worker_policy.resolve_workers(), which also accounts for
    the drive's bus and the kind of operation.
    """
    if hdd_cap < default_workers and is_rotational(path):
        return hdd_cap
    return default_workers


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------

_DRIVE_REMOTE = 4
_DRIVE_RAMDISK = 6

# MSFT_Disk.BusType, by name (spaces removed, lowercased) or numeric value
_WINDOWS_BUS = {
    'scsi': Bus.SAS, '1': Bus.SAS,
    'atapi': Bus.SATA, '2': Bus.SATA,
    'ata': Bus.SATA, '3': Bus.SATA,
    'fibrechannel': Bus.NETWORK, '6': Bus.NETWORK,
    'usb': Bus.USB, '7': Bus.USB,
    'raid': Bus.RAID, '8': Bus.RAID,
    'iscsi': Bus.NETWORK, '9': Bus.NETWORK,
    'sas': Bus.SAS, '10': Bus.SAS,
    'sata': Bus.SATA, '11': Bus.SATA,
    'sd': Bus.SD, '12': Bus.SD,
    'mmc': Bus.SD, '13': Bus.SD,
    'virtual': Bus.VIRTUAL, '14': Bus.VIRTUAL,
    'filebackedvirtual': Bus.VIRTUAL, '15': Bus.VIRTUAL,
    'nvme': Bus.NVME, '17': Bus.NVME,
}

# MSFT_PhysicalDisk.MediaType
_WINDOWS_MEDIA = {
    'hdd': Media.HDD, '3': Media.HDD,
    'ssd': Media.SSD, '4': Media.SSD,
    'scm': Media.SSD, '5': Media.SSD,
}


def _win_drive_type(root: str) -> int:
    """GetDriveTypeW for a root like 'X:\\'; 0 when unavailable (e.g. not on Windows)."""
    windll = getattr(ctypes, 'windll', None)
    if windll is None:
        return 0
    try:
        return int(windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
    except Exception:
        return 0


def _parse_windows_ps(stdout: str) -> tuple[Media, Bus]:
    """
    Parse 'MediaType|BusType|SpindleSpeed' (names or numbers). A bare
    'SSD'/'HDD' line, as printed by older versions of the query, also works.
    """
    fields = [f.strip() for f in stdout.strip().split('|')]
    fields += [''] * (3 - len(fields))
    media_raw, bus_raw, spindle_raw = (f.replace(' ', '').lower() for f in fields[:3])

    bus = _WINDOWS_BUS.get(bus_raw, Bus.UNKNOWN)
    media = _WINDOWS_MEDIA.get(media_raw, Media.UNKNOWN)
    if media is Media.UNKNOWN and spindle_raw.isdigit():
        # SpindleSpeed: 0 = non-rotational, an RPM for HDDs, 0xFFFFFFFF = unknown
        rpm = int(spindle_raw)
        if rpm == 0:
            media = Media.SSD
        elif rpm <= 20000:
            media = Media.HDD
    if media is Media.UNKNOWN and bus is Bus.NVME:
        media = Media.SSD
    return media, bus


def _detect_windows(path: str) -> DriveProfile:
    # ntpath rather than os.path: only ntpath parses drive letters, and this
    # must behave the same wherever it runs (e.g. its tests on Linux/macOS CI).
    drive, _ = ntpath.splitdrive(path)
    if drive.startswith(('\\\\', '//')):
        # UNC path: a network share. Its "drive" must never reach the
        # PowerShell script below - it's interpolated into the command string.
        return DriveProfile('', Media.UNKNOWN, Bus.NETWORK, is_remote=True, detail='UNC path')

    drive_letter = drive.rstrip(':').upper()
    if len(drive_letter) != 1 or not ('A' <= drive_letter <= 'Z'):
        return DriveProfile('', source='unknown')

    drive_type = _win_drive_type(f'{drive_letter}:\\')
    if drive_type == _DRIVE_REMOTE:
        return DriveProfile('', Media.UNKNOWN, Bus.NETWORK, is_remote=True, detail='mapped network drive')
    if drive_type == _DRIVE_RAMDISK:
        return DriveProfile('', Media.SSD, Bus.RAM, detail='RAM disk')

    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$p = Get-Partition -DriveLetter '{drive_letter}'; "
        "$k = Get-Disk -Number $p.DiskNumber; "
        "$d = Get-PhysicalDisk -DeviceNumber $p.DiskNumber -ErrorAction SilentlyContinue; "
        "Write-Output ('{0}|{1}|{2}' -f $d.MediaType, $k.BusType, $d.SpindleSpeed)"
    )
    proc = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return DriveProfile('', source='unknown', detail='PowerShell query failed')
    media, bus = _parse_windows_ps(proc.stdout)
    return DriveProfile('', media, bus, detail=proc.stdout.strip())


# --------------------------------------------------------------------------
# macOS
# --------------------------------------------------------------------------

_MACOS_NETWORK_FS = {'smbfs', 'nfs', 'afpfs', 'webdav', 'macfuse', 'osxfuse', 'cifs', 'ftp'}

_MACOS_BUS = {
    'pci-express': Bus.NVME,
    'pci': Bus.NVME,
    'nvme': Bus.NVME,
    'applefabric': Bus.NVME,
    'sata': Bus.SATA,
    'sas': Bus.SAS,
    'usb': Bus.USB,
    'thunderbolt': Bus.THUNDERBOLT,
    'securedigital': Bus.SD,
    'diskimage': Bus.VIRTUAL,
    'fibrechannel': Bus.NETWORK,
}


def _macos_mount_fstype(path: str) -> str | None:
    """Filesystem type of the mount containing `path`, from `mount` output."""
    proc = subprocess.run(['mount'], capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        return None
    target_dev = os.stat(path).st_dev
    best: tuple[int, str] | None = None
    for line in proc.stdout.splitlines():
        # "//user@server/share on /Volumes/share (smbfs, nodev, nosuid, mounted by me)"
        match = re.match(r'^.+? on (.+) \(([^,)]+)', line)
        if not match:
            continue
        mount_point, fstype = match.group(1), match.group(2).strip()
        try:
            if os.stat(mount_point).st_dev != target_dev:
                continue
        except OSError:
            continue
        if best is None or len(mount_point) > best[0]:
            best = (len(mount_point), fstype)
    return best[1] if best else None


def _diskutil_info(target: str) -> dict | None:
    proc = subprocess.run(
        ['diskutil', 'info', '-plist', target],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return None
    try:
        return plistlib.loads(proc.stdout.encode('utf-8'))
    except Exception:
        # Not a plist - fall back to the human-readable "Solid State:" line
        match = re.search(r'Solid State:\s*(Yes|No)', proc.stdout)
        if match:
            return {'SolidState': match.group(1) == 'Yes'}
        return None


def _parse_diskutil(info: dict) -> tuple[Media, Bus]:
    solid = info.get('SolidState')
    media = Media.UNKNOWN if solid is None else (Media.SSD if solid else Media.HDD)
    protocol = str(info.get('BusProtocol', '')).replace(' ', '').lower()
    return media, _MACOS_BUS.get(protocol, Bus.UNKNOWN)


def _detect_macos(path: str) -> DriveProfile:
    fstype = _macos_mount_fstype(path)
    if fstype and fstype.lower() in _MACOS_NETWORK_FS:
        return DriveProfile('', Media.UNKNOWN, Bus.NETWORK, is_remote=True, detail=fstype)

    info = _diskutil_info(path)
    if info is None:
        return DriveProfile('', source='unknown', detail='diskutil failed')
    media, bus = _parse_diskutil(info)

    if bus is Bus.UNKNOWN:
        # APFS volumes live in a synthesized container; the bus (and often
        # the media) is only reported for the physical store behind it.
        stores = info.get('APFSPhysicalStores') or []
        parent = None
        if stores and isinstance(stores[0], dict):
            parent = stores[0].get('APFSPhysicalStore')
        parent = parent or info.get('ParentWholeDisk')
        if parent and parent != info.get('DeviceIdentifier'):
            parent_info = _diskutil_info(str(parent))
            if parent_info:
                parent_media, bus = _parse_diskutil(parent_info)
                if media is Media.UNKNOWN:
                    media = parent_media

    return DriveProfile('', media, bus, detail=str(info.get('DeviceIdentifier', '')))


# --------------------------------------------------------------------------
# Linux
# --------------------------------------------------------------------------

# Overridable in tests to point at a fake sysfs/procfs/udev tree
_SYSFS = '/sys'
_PROC_MOUNTS = '/proc/mounts'
_UDEV_DB = '/run/udev/data'


def _realpath(path: str) -> str:
    return os.path.realpath(path)


_LINUX_NETWORK_FS = {
    'nfs', 'nfs4', 'cifs', 'smb3', 'smbfs', 'sshfs', '9p', 'ceph', 'glusterfs',
    'davfs', 'drvfs', 'afs', 'lustre', 'beegfs',
}
_LINUX_NETWORK_FUSE = ('fuse.sshfs', 'fuse.rclone', 'fuse.s3fs', 'fuse.gcsfuse', 'fuse.glusterfs')
_LINUX_RAM_FS = {'tmpfs', 'ramfs'}

_UDEV_BUS = {'usb': Bus.USB, 'ata': Bus.SATA, 'nvme': Bus.NVME, 'mmc': Bus.SD}

# Slowest first: when a volume spans several disks, the slowest one bounds it
_BUS_SPEED_RANK = [
    Bus.SD, Bus.USB, Bus.VIRTUAL, Bus.UNKNOWN, Bus.SATA, Bus.SAS, Bus.RAID,
    Bus.THUNDERBOLT, Bus.NVME, Bus.RAM, Bus.NETWORK,
]


def _unescape_mount(field: str) -> str:
    """/proc/mounts octal-escapes whitespace in paths (e.g. '\\040' for a space)."""
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), field)


def _linux_mount_entry(path: str) -> tuple[str, str, str] | None:
    """(source, mount_point, fstype) of the mount containing `path`."""
    target_dev = os.stat(path).st_dev
    best: tuple[str, str, str] | None = None
    with open(_PROC_MOUNTS) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 3:
                continue
            source, mount_point, fstype = parts[0], _unescape_mount(parts[1]), parts[2]
            try:
                if os.stat(mount_point).st_dev != target_dev:
                    continue
            except OSError:
                continue
            # Longest matching mount point wins (most specific bind/submount)
            if best is None or len(mount_point) > len(best[1]):
                best = (source, mount_point, fstype)
    return best


def _linux_block_device(path: str) -> str | None:
    """Find the /dev/... block device backing `path`'s mount point."""
    entry = _linux_mount_entry(path)
    if entry and entry[0].startswith('/dev/'):
        return entry[0]
    return None


def _linux_physical_disks(device: str, _depth: int = 0) -> list[str]:
    """
    Resolve a block device (partition, /dev/mapper/*, md array) to the
    whole physical disk(s) behind it, as /sys/block names.
    """
    if _depth > 8:
        return []
    name = os.path.basename(_realpath(device)) if _depth == 0 else device
    class_dir = os.path.join(_SYSFS, 'class', 'block', name)

    if not os.path.exists(class_dir):
        # No /sys/class/block entry: strip a partition suffix by name
        if name.startswith(('nvme', 'mmcblk')):
            base = re.sub(r'p\d+$', '', name)
        else:
            base = re.sub(r'\d+$', '', name)
        return [base] if os.path.exists(os.path.join(_SYSFS, 'block', base)) else []

    if os.path.exists(os.path.join(class_dir, 'partition')):
        parent = os.path.basename(os.path.dirname(_realpath(class_dir)))
        return _linux_physical_disks(parent, _depth + 1)

    slaves_dir = os.path.join(_SYSFS, 'block', name, 'slaves')
    if os.path.isdir(slaves_dir):
        slaves = sorted(os.listdir(slaves_dir))
        if slaves:
            disks: list[str] = []
            for slave in slaves:
                disks.extend(_linux_physical_disks(slave, _depth + 1))
            return disks
    return [name]


def _read_sys(*parts: str) -> str | None:
    try:
        with open(os.path.join(_SYSFS, *parts)) as f:
            return f.read().strip()
    except OSError:
        return None


def _linux_media(disk: str) -> Media:
    value = _read_sys('block', disk, 'queue', 'rotational')
    if value == '1':
        return Media.HDD
    if value == '0':
        return Media.SSD
    return Media.UNKNOWN


def _linux_bus(disk: str) -> Bus:
    if disk.startswith('nvme'):
        return Bus.NVME
    if disk.startswith('mmcblk'):
        return Bus.SD
    if disk.startswith(('zram', 'ram')):
        return Bus.RAM
    if disk.startswith(('loop', 'vd', 'xvd')):
        return Bus.VIRTUAL

    real = _realpath(os.path.join(_SYSFS, 'block', disk)).replace('\\', '/').lower()
    if '/usb' in real:
        return Bus.USB
    if '/virtio' in real or 'vmbus' in real:
        return Bus.VIRTUAL
    if '/ata' in real:
        return Bus.SATA
    if '/nvme' in real:
        return Bus.NVME
    if '/mmc' in real:
        return Bus.SD

    # Fall back to udev's record of the device, keyed by major:minor
    dev_numbers = _read_sys('block', disk, 'dev')
    if dev_numbers:
        try:
            with open(os.path.join(_UDEV_DB, f'b{dev_numbers}')) as f:
                for line in f:
                    if line.startswith('E:ID_BUS='):
                        return _UDEV_BUS.get(line.strip().split('=', 1)[1].lower(), Bus.UNKNOWN)
        except OSError:
            pass
    return Bus.UNKNOWN


def _detect_linux(path: str) -> DriveProfile:
    entry = _linux_mount_entry(path)
    if not entry:
        return DriveProfile('', source='unknown')
    source, mount_point, fstype = entry
    fstype = fstype.lower()

    if fstype in _LINUX_NETWORK_FS or fstype.startswith(_LINUX_NETWORK_FUSE):
        return DriveProfile('', Media.UNKNOWN, Bus.NETWORK, is_remote=True, detail=fstype)
    if fstype in _LINUX_RAM_FS:
        return DriveProfile('', Media.SSD, Bus.RAM, detail=fstype)
    if not source.startswith('/dev/'):
        # overlay, zfs datasets, etc. - no single block device to inspect
        return DriveProfile('', source='unknown', detail=fstype)

    disks = _linux_physical_disks(source)
    if not disks:
        return DriveProfile('', source='unknown', detail=source)

    medias = [_linux_media(d) for d in disks]
    buses = [_linux_bus(d) for d in disks]

    if Media.HDD in medias:
        media = Media.HDD
    elif Media.UNKNOWN in medias:
        media = Media.UNKNOWN
    else:
        media = Media.SSD
    bus = min(buses, key=_BUS_SPEED_RANK.index)

    if bus is Bus.USB and media is Media.HDD:
        # Many USB-SATA bridges report rotational=1 for every drive, SSDs
        # included, so the flag can't be trusted behind USB.
        media = Media.UNKNOWN
    return DriveProfile('', media, bus, detail=f"{source} ({','.join(disks)})")


__all__ = [
    'Media', 'Bus', 'Tier', 'DriveProfile', 'OVERRIDE_SPEC_NAMES',
    'profile_from_spec', 'detect_drive', 'detect_drive_async', 'same_device',
    'clear_cache', 'detect_media_type', 'is_rotational', 'tailor_workers',
]
