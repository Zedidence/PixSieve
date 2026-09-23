"""
Detect whether a filesystem path lives on a rotational (HDD) or solid-state
(SSD) drive, so read/write concurrency can be tailored to the underlying
media's actual I/O characteristics instead of just CPU count.

Concurrent random-access I/O against a spinning disk causes seek thrashing
that *reduces* throughput past a small number of simultaneous operations -
unlike CPU-bound work or SSDs, where more workers keep helping (up to the
device's queue depth). Callers use tailor_workers() to cap worker counts
accordingly, but only ever pull a count *down* for a confirmed HDD - a
detection failure (unknown filesystem, virtualized/network drive, missing
platform tool) always falls back to leaving the caller's chosen default
untouched.

Detection results are cached per drive - the platform calls involved
(PowerShell/WMI on Windows, /sys/block on Linux, diskutil on macOS) cost
tens to hundreds of milliseconds, and a drive's media type never changes
during a run.
"""

from __future__ import annotations

import logging
import os
import platform as platform_module
import re
import subprocess
import threading

_logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}
_cache_lock = threading.Lock()


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
        drive, _ = os.path.splitdrive(_nearest_existing(path))
        return drive.upper() or 'C:'
    return path


def detect_media_type(path: str) -> str:
    """
    Return 'ssd', 'hdd', or 'unknown' for the drive backing `path`.

    Best-effort: any failure (missing tool, permissions, virtualized/network
    drive with no meaningful answer) falls back to 'unknown' rather than
    raising, since this is a performance hint, not a correctness dependency.
    """
    key = _cache_key(path)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    resolved = _nearest_existing(path)
    result = 'unknown'
    try:
        system = platform_module.system()
        if system == 'Windows':
            result = _detect_windows(resolved)
        elif system == 'Darwin':
            result = _detect_macos(resolved)
        elif system == 'Linux':
            result = _detect_linux(resolved)
    except Exception as e:
        _logger.debug(f"Disk type detection failed for {path}: {e}")
        result = 'unknown'

    with _cache_lock:
        _cache[key] = result
    return result


def is_rotational(path: str) -> bool:
    """True only when the drive backing `path` is confidently identified as an HDD."""
    return detect_media_type(path) == 'hdd'


def tailor_workers(path: str, default_workers: int, hdd_cap: int) -> int:
    """
    Return `hdd_cap` in place of `default_workers` when `path`'s drive is a
    confirmed HDD and the cap is actually lower; otherwise return
    `default_workers` unchanged (including whenever detection is 'unknown').
    """
    if hdd_cap < default_workers and is_rotational(path):
        return hdd_cap
    return default_workers


def _detect_windows(path: str) -> str:
    drive, _ = os.path.splitdrive(path)
    drive_letter = drive.rstrip(':').upper()
    if not drive_letter:
        return 'unknown'

    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$p = Get-Partition -DriveLetter '{drive_letter}'; "
        "$d = Get-PhysicalDisk -DeviceNumber $p.DiskNumber; "
        "Write-Output $d.MediaType"
    )
    proc = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return 'unknown'
    normalized = proc.stdout.strip().upper()
    if normalized == 'SSD':
        return 'ssd'
    if normalized == 'HDD':
        return 'hdd'
    return 'unknown'


def _detect_macos(path: str) -> str:
    proc = subprocess.run(
        ['diskutil', 'info', path],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return 'unknown'
    match = re.search(r'Solid State:\s*(Yes|No)', proc.stdout)
    if not match:
        return 'unknown'
    return 'ssd' if match.group(1) == 'Yes' else 'hdd'


def _linux_block_device(path: str) -> str | None:
    """Find the /dev/... block device backing `path`'s mount point."""
    target_dev = os.stat(path).st_dev
    best_match: tuple[int, str] | None = None
    with open('/proc/mounts') as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2 or not parts[0].startswith('/dev/'):
                continue
            device, mount_point = parts[0], parts[1]
            try:
                if os.stat(mount_point).st_dev != target_dev:
                    continue
            except OSError:
                continue
            # Longest matching mount point wins (most specific bind/submount)
            if best_match is None or len(mount_point) > best_match[0]:
                best_match = (len(mount_point), device)
    return best_match[1] if best_match else None


def _detect_linux(path: str) -> str:
    device = _linux_block_device(path)
    if not device:
        return 'unknown'

    base = os.path.basename(device)
    if base.startswith(('nvme', 'mmcblk')):
        base = re.sub(r'p\d+$', '', base)
    else:
        base = re.sub(r'\d+$', '', base)

    rotational_path = f'/sys/block/{base}/queue/rotational'
    if not os.path.exists(rotational_path):
        return 'unknown'
    with open(rotational_path) as f:
        value = f.read().strip()
    return 'hdd' if value == '1' else 'ssd'


__all__ = ['detect_media_type', 'is_rotational', 'tailor_workers']
