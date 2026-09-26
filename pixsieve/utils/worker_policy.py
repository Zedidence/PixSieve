"""
Decide how many parallel workers an operation should use, based on the
storage it touches (utils/disk_type.py) and the kind of I/O it does.

Why per-drive and per-operation:
- A spinning disk loses throughput past a few concurrent random reads
  (seek thrashing); behind a USB bridge it's worse still.
- USB bridges and SD readers have shallow command queues, so extra workers
  just queue up behind each other.
- Internal SSDs and especially NVMe drives keep scaling with queue depth,
  so metadata-heavy operations (renames, stats) benefit from *more* workers
  than the old fixed default of 4.
- Network shares are latency-bound: many small metadata calls overlap
  well, bulk copies don't.

resolve_workers() is the single entry point every call site uses. Precedence:
  1. an explicit count from the user (CLI -w, API `workers`, UI field)
  2. PIXSIEVE_WORKERS
  3. auto: a per-drive storage override (--storage-profile /
     PIXSIEVE_STORAGE_OVERRIDE) or the detected profile, looked up in
     WORKER_TABLE (and refined by utils/io_probe.py for ambiguous drives)
  4. the caller's legacy default - also used for every drive that can't be
     classified, and for everything when PIXSIEVE_AUTO_WORKERS=0.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Iterable, Mapping, Sequence, Union

from .. import config
from . import disk_type, io_probe
from .disk_type import DriveProfile, Tier

_logger = logging.getLogger(__name__)

MAX_WORKERS = 32

# Steps the I/O probe and the adaptive controller move along
LADDER = (1, 2, 4, 6, 8, 12, 16, 24, 32)


class OpKind(str, Enum):
    SCAN = 'scan'          # read + hash + decode (mixed I/O and CPU)
    METADATA = 'metadata'  # renames / same-volume moves (directory updates only)
    COPY = 'copy'          # cross-volume move (bulk read + write, per side)
    REWRITE = 'rewrite'    # small in-place rewrites (EXIF date randomization)
    REPAIR = 'repair'      # decode/verify (CPU) + read + occasional write
    STAT = 'stat'          # cache validation stat() fan-out


_Count = Union[int, Callable[[int], int]]


def _cpu_times(factor: int, cap: int) -> Callable[[int], int]:
    return lambda cpu: min(cpu * factor, cap)


# Tier -> operation -> worker count (an int, or a function of the CPU count).
# Tier.UNKNOWN is deliberately absent: an unclassified drive always keeps the
# caller's legacy default.
WORKER_TABLE: dict[Tier, dict[OpKind, _Count]] = {
    Tier.NVME: {
        OpKind.SCAN: _cpu_times(2, 32), OpKind.METADATA: 16, OpKind.COPY: 8,
        OpKind.REWRITE: 8, OpKind.REPAIR: _cpu_times(1, 16), OpKind.STAT: 32,
    },
    Tier.SSD_INTERNAL: {
        OpKind.SCAN: _cpu_times(2, 16), OpKind.METADATA: 8, OpKind.COPY: 4,
        OpKind.REWRITE: 6, OpKind.REPAIR: _cpu_times(1, 12), OpKind.STAT: 16,
    },
    Tier.SSD_EXTERNAL: {
        OpKind.SCAN: _cpu_times(2, 6), OpKind.METADATA: 4, OpKind.COPY: 2,
        OpKind.REWRITE: 3, OpKind.REPAIR: _cpu_times(1, 4), OpKind.STAT: 8,
    },
    # Scan and repair decode images, so even the slow-drive rows scale with
    # the CPU count on small machines (never more workers than a faster
    # drive would get).
    Tier.HDD_INTERNAL: {
        OpKind.SCAN: _cpu_times(2, config.HDD_ANALYSIS_WORKERS), OpKind.METADATA: config.HDD_WRITE_WORKERS,
        OpKind.COPY: 1, OpKind.REWRITE: 2, OpKind.REPAIR: _cpu_times(1, 3), OpKind.STAT: 4,
    },
    Tier.HDD_EXTERNAL: {
        OpKind.SCAN: _cpu_times(2, 2), OpKind.METADATA: 2, OpKind.COPY: 1,
        OpKind.REWRITE: 1, OpKind.REPAIR: _cpu_times(1, 2), OpKind.STAT: 2,
    },
    Tier.EXTERNAL_AMBIGUOUS: {
        OpKind.SCAN: _cpu_times(2, 3), OpKind.METADATA: 2, OpKind.COPY: 1,
        OpKind.REWRITE: 2, OpKind.REPAIR: _cpu_times(1, 3), OpKind.STAT: 4,
    },
    Tier.SD_CARD: {
        OpKind.SCAN: _cpu_times(2, 2), OpKind.METADATA: 1, OpKind.COPY: 1,
        OpKind.REWRITE: 1, OpKind.REPAIR: _cpu_times(1, 2), OpKind.STAT: 2,
    },
    Tier.NETWORK: {
        OpKind.SCAN: _cpu_times(2, 8), OpKind.METADATA: 8, OpKind.COPY: 2,
        OpKind.REWRITE: 4, OpKind.REPAIR: _cpu_times(1, 6), OpKind.STAT: 16,
    },
    Tier.RAM: {
        OpKind.SCAN: _cpu_times(2, 32), OpKind.METADATA: 16, OpKind.COPY: 8,
        OpKind.REWRITE: 8, OpKind.REPAIR: _cpu_times(1, 16), OpKind.STAT: 32,
    },
}

# Tiers fast enough that a huge scan may use the large-library worker count
_LARGE_LIBRARY_TIERS = {Tier.NVME, Tier.SSD_INTERNAL, Tier.NETWORK, Tier.RAM}

# Tiers where an explicit count far above the recommendation is worth a warning
_SLOW_TIERS = {
    Tier.HDD_INTERNAL, Tier.HDD_EXTERNAL, Tier.EXTERNAL_AMBIGUOUS,
    Tier.SD_CARD, Tier.SSD_EXTERNAL,
}
_HDD_TIERS = {Tier.HDD_INTERNAL, Tier.HDD_EXTERNAL}


def _plural(n: int) -> str:
    return f"{n} worker{'' if n == 1 else 's'}"


def reset_caches() -> None:
    """Forget detected drive profiles and probe results (call at the start of an operation)."""
    disk_type.clear_cache()
    io_probe.clear_cache()


def warm_up(paths: Iterable[str]) -> None:
    """
    Start detecting the drives behind `paths` in the background (so the
    platform query overlaps other work), but only when resolve_workers()
    will actually consult them.
    """
    if not config.AUTO_WORKERS or config.ENV_WORKERS is not None:
        return
    merged = dict(config.STORAGE_OVERRIDES)
    to_detect = [str(p) for p in paths if not _override_for(str(p), merged)]
    if to_detect:
        disk_type.detect_drive_async(to_detect)


def _clamp(n: int, upper: int = MAX_WORKERS) -> int:
    return max(1, min(int(n), upper))


def ladder_step(n: int, direction: int) -> int:
    """The LADDER value one step above (+1) or below (-1) `n`."""
    if direction > 0:
        return next((v for v in LADDER if v > n), LADDER[-1])
    return next((v for v in reversed(LADDER) if v < n), LADDER[0])


def recommend(
    op: OpKind,
    profile: DriveProfile,
    *,
    legacy: int,
    cpu: int | None = None,
    file_count: int | None = None,
) -> int:
    """Worker count for `op` on a single drive, before any cross-drive combination."""
    tier = profile.tier
    row = WORKER_TABLE.get(tier)
    if row is None:
        return _clamp(legacy)
    cpu = cpu or os.cpu_count() or 1
    value = row[op]
    n = value(cpu) if callable(value) else value
    if (op is OpKind.SCAN and tier in _LARGE_LIBRARY_TIERS
            and file_count is not None and file_count >= config.LARGE_LIBRARY_THRESHOLD):
        n = max(n, config.LARGE_LIBRARY_WORKERS)
    if op is OpKind.SCAN:
        # Once the drive keeps up, scanning is CPU-bound - never exceed the
        # CPU-scaled default the caller would otherwise have used.
        n = min(n, legacy)
    return _clamp(n)


@dataclass(frozen=True)
class WorkerDecision:
    workers: int
    source: str   # 'user' | 'env' | 'auto' | 'probe' | 'fallback'
    reason: str
    op: OpKind
    profiles: tuple[DriveProfile, ...] = ()
    floor: int = 0
    ceiling: int = 0
    probe: dict | None = None

    @property
    def label(self) -> str:
        """Short description of the storage, e.g. 'USB SSD' or 'NVMe SSD -> external HDD'."""
        labels = []
        for p in self.profiles:
            if p.tier.label not in labels:
                labels.append(p.tier.label)
        return ' -> '.join(labels) if labels else 'unknown drive'

    def as_dict(self) -> dict:
        return {
            'workers': self.workers,
            'source': self.source,
            'reason': self.reason,
            'op': self.op.value,
            'label': self.label,
            'floor': self.floor or self.workers,
            'ceiling': self.ceiling or self.workers,
            'drives': [p.as_dict() for p in self.profiles],
            'probe': self.probe,
        }


def parse_storage_overrides(specs: Iterable[str]) -> dict[str, str]:
    """
    Parse override entries into {path_prefix: spec}. Each entry is either
    'PATH=SPEC' (e.g. 'E:=usb-hdd', '/mnt/nas=network') or a bare 'SPEC',
    which applies to every path ('*'). Entries may also be ';'-separated.
    Raises ValueError on an unknown spec.
    """
    result: dict[str, str] = {}
    for entry in specs:
        for part in str(entry).split(';'):
            part = part.strip()
            if not part:
                continue
            if '=' in part:
                prefix, spec = part.rsplit('=', 1)
                prefix = prefix.strip()
            else:
                prefix, spec = '*', part
            disk_type.profile_from_spec(spec)   # validate
            result[prefix or '*'] = spec.strip().lower()
    return result


def _normalize_prefix(path: str) -> str:
    if re.fullmatch(r'[A-Za-z]:', path):
        path += os.sep
    return os.path.normcase(os.path.abspath(path))


def _override_for(path: str, overrides: Mapping[str, str]) -> str | None:
    if not overrides:
        return None
    target = os.path.normcase(os.path.abspath(path))
    best: tuple[int, str] | None = None
    for prefix, spec in overrides.items():
        if prefix == '*':
            continue
        norm = _normalize_prefix(prefix)
        if target == norm or target.startswith(norm.rstrip(os.sep) + os.sep):
            if best is None or len(norm) > best[0]:
                best = (len(norm), spec)
    if best:
        return best[1]
    return overrides.get('*')


def profile_for(path: str, overrides: Mapping[str, str] | None = None) -> DriveProfile:
    """The storage override for `path` if one applies, else the detected profile."""
    merged = dict(config.STORAGE_OVERRIDES)
    if overrides:
        merged.update(overrides)
    spec = _override_for(path, merged)
    if spec:
        try:
            return disk_type.profile_from_spec(spec, device_key=str(path))
        except ValueError as e:
            _logger.warning(str(e))
    return disk_type.detect_drive(str(path))


def _combine(
    op: OpKind,
    sources: Sequence[DriveProfile],
    dest: DriveProfile | None,
    dest_op: OpKind | None,
    legacy: int,
    file_count: int | None,
) -> int:
    counts = [recommend(op, p, legacy=legacy, file_count=file_count) for p in sources]
    if dest is not None and dest_op is not None:
        counts.append(recommend(dest_op, dest, legacy=legacy, file_count=file_count))
    return min(counts) if counts else _clamp(legacy)


def resolve_workers(
    op: OpKind,
    sources: Sequence[str] | str,
    *,
    legacy_default: int,
    destination: str | None = None,
    requested: int | None = None,
    file_count: int | None = None,
    sample_files: Sequence[str] | None = None,
    overrides: Mapping[str, str] | None = None,
    allow_probe: bool = True,
    upper: int = MAX_WORKERS,
) -> WorkerDecision:
    """
    Decide the worker count for an operation.

    Args:
        op: What kind of I/O the operation does.
        sources: Path(s) the operation reads from / works in.
        legacy_default: The count this call site used before drive-aware
            tuning - kept for unclassifiable drives and when auto is off.
        destination: For COPY, the destination root (a same-volume "copy"
            is really a rename and is sized as METADATA). For REPAIR, the
            quarantine folder.
        requested: An explicit count from the user; always used as-is.
        file_count: Number of files, when known (large-library scans).
        sample_files: Files the I/O probe may read (utils/io_probe.py).
        overrides: Extra {path_prefix: spec} storage overrides for this call.
        allow_probe: Permit the I/O probe for ambiguous drives.
        upper: Maximum the caller's API accepts.
    """
    source_paths = [sources] if isinstance(sources, (str, os.PathLike)) else list(sources)
    source_paths = [str(p) for p in source_paths]
    legacy = _clamp(legacy_default, upper)

    if requested is not None:
        requested = _clamp(requested, upper)
        decision = WorkerDecision(requested, 'user', f'{_plural(requested)} (set explicitly)', op)
        if config.AUTO_WORKERS:
            decision = _warn_if_excessive(decision, source_paths, destination, legacy,
                                          file_count, overrides)
        return decision

    if config.ENV_WORKERS is not None:
        n = _clamp(config.ENV_WORKERS, upper)
        return WorkerDecision(n, 'env', f'{_plural(n)} (PIXSIEVE_WORKERS)', op)

    if not config.AUTO_WORKERS:
        return WorkerDecision(legacy, 'fallback', f'{_plural(legacy)} (drive-aware tuning off)', op)

    # One profile per distinct drive
    source_profiles: list[DriveProfile] = []
    for path in source_paths:
        profile = profile_for(path, overrides)
        if all(p.device_key != profile.device_key for p in source_profiles):
            source_profiles.append(profile)

    effective_op = op
    dest_profile: DriveProfile | None = None
    dest_op: OpKind | None = None
    if destination is not None and source_paths and op in (OpKind.COPY, OpKind.REPAIR):
        same = disk_type.same_device(source_paths[0], destination)
        if op is OpKind.COPY and same is True:
            effective_op = OpKind.METADATA   # same-volume move = rename
        elif same is not True:
            # Different or undeterminable volume: the slower side bounds it
            dest_profile = profile_for(destination, overrides)
            dest_op = OpKind.COPY

    profiles = tuple(source_profiles) + ((dest_profile,) if dest_profile else ())

    # The probe reads `sample_files`, so it can only speak for their drive -
    # skip it when the sources span several drives.
    probe_info = None
    if allow_probe and config.IO_PROBE_ENABLED and sample_files and len(source_profiles) == 1:
        refined, probe_info = io_probe.refine_profile(source_profiles[0], sample_files)
        source_profiles = [refined]
        profiles = tuple(source_profiles) + ((dest_profile,) if dest_profile else ())

    if all(p.tier is Tier.UNKNOWN for p in profiles):
        return WorkerDecision(
            legacy, 'fallback', f'{_plural(legacy)} (drive type unknown)', effective_op,
            profiles=profiles,
        )

    n = _clamp(_combine(effective_op, source_profiles, dest_profile, dest_op, legacy, file_count), upper)
    if probe_info and probe_info.get('step', 0) < 0:
        n = ladder_step(n, -1) if n > 1 else 1

    floor = ladder_step(n, -1) if n > 1 else 1
    ceiling = min(ladder_step(n, +1), upper)
    if effective_op is OpKind.SCAN:
        ceiling = min(ceiling, max(n, legacy))
    if any(p.tier in _HDD_TIERS for p in profiles):
        ceiling = min(ceiling, max(n, config.HDD_ANALYSIS_WORKERS))
    ceiling = max(ceiling, n)
    floor = min(floor, n)

    decision = WorkerDecision(
        n, 'probe' if probe_info and probe_info.get('applied') else 'auto', '',
        effective_op, profiles=profiles, floor=floor, ceiling=ceiling, probe=probe_info,
    )
    return replace(decision, reason=f'{decision.label} -> {_plural(n)} ({effective_op.value})')


def _warn_if_excessive(
    decision: WorkerDecision,
    source_paths: Sequence[str],
    destination: str | None,
    legacy: int,
    file_count: int | None,
    overrides: Mapping[str, str] | None,
) -> WorkerDecision:
    """Log (but honor) an explicit count far above what a slow drive handles well."""
    try:
        auto = resolve_workers(
            decision.op, source_paths, legacy_default=legacy, destination=destination,
            file_count=file_count, overrides=overrides, allow_probe=False,
        )
    except Exception:
        return decision
    slow = [p for p in auto.profiles if p.tier in _SLOW_TIERS]
    if slow and decision.workers > 2 * auto.workers:
        _logger.warning(
            f"{decision.workers} workers requested on a {auto.label}; "
            f"{auto.workers} is usually faster there (more workers cause thrashing)"
        )
    return replace(decision, profiles=auto.profiles, op=auto.op)


__all__ = [
    'OpKind', 'WorkerDecision', 'WORKER_TABLE', 'LADDER', 'MAX_WORKERS',
    'recommend', 'resolve_workers', 'profile_for', 'parse_storage_overrides',
    'ladder_step', 'reset_caches', 'warm_up',
]
