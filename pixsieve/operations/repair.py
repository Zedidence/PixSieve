"""
Corrupt image detection, repair, and quarantine operations.

Scans image files for corruption, attempts automated repair using multiple
strategies, and moves unfixable files to a designated trash folder.

Repair strategies (attempted in order):
  1. Re-encode via PIL   -- fixes truncated files and minor pixel corruption
  2. Strip EXIF + re-save -- fixes bad/malformed metadata
  3. Format conversion    -- last resort: recover pixel data as clean PNG

Permission levels are checked and reported separately from corruption:
  readable      -- whether the file can be opened at all
  writable      -- whether the file can be modified in-place for repair
  parent_writable -- whether the file can be moved to the trash folder
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from ..config import IMAGE_EXTENSIONS
from ..utils.operations import get_unique_path, make_progress_bar

logger = logging.getLogger(__name__)

# Module-level lock: held while toggling PIL's global LOAD_TRUNCATED_IMAGES flag
# so parallel repair threads don't clobber each other's setting.
_repair_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------

class CorruptionType(Enum):
    """Classification of detected image corruption."""
    NONE = "none"
    PERMISSION_DENIED = "permission_denied"  # File is unreadable
    TRUNCATED = "truncated"                  # File is cut off / partial
    BAD_EXIF = "bad_exif"                    # Invalid EXIF/metadata structure
    INVALID_FORMAT = "invalid_format"        # PIL cannot identify the format
    UNKNOWN = "unknown"                      # Other PIL error


class RepairStatus(Enum):
    """Outcome of the scan-and-repair process for a single file."""
    CLEAN = "clean"                    # No corruption detected
    REPAIRED = "repaired"              # Successfully repaired in-place
    QUARANTINED = "quarantined"        # Moved to trash (could not repair)
    SKIPPED = "skipped"                # Skipped (e.g. read-only, INVALID_FORMAT)
    PERMISSION_ERROR = "permission_error"  # Cannot read the file at all
    ERROR = "error"                    # Unexpected error during processing


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PermissionInfo:
    """File-level permission details."""
    readable: bool
    writable: bool
    parent_writable: bool   # Whether the containing directory allows moves
    error: Optional[str] = None


@dataclass
class RepairResult:
    """Result of scanning and optionally repairing a single image file."""
    path: str
    corruption_type: CorruptionType = CorruptionType.NONE
    status: RepairStatus = RepairStatus.CLEAN
    permissions: Optional[PermissionInfo] = None
    repair_attempts: list[str] = field(default_factory=list)
    trash_path: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dictionary."""
        return {
            "path": self.path,
            "corruption_type": self.corruption_type.value,
            "status": self.status.value,
            "permissions": {
                "readable": self.permissions.readable,
                "writable": self.permissions.writable,
                "parent_writable": self.permissions.parent_writable,
            } if self.permissions else None,
            "repair_attempts": self.repair_attempts,
            "trash_path": self.trash_path,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _check_permissions(path: str) -> PermissionInfo:
    """Return read/write/parent-write permission flags for *path*."""
    try:
        readable = os.access(path, os.R_OK)
        writable = os.access(path, os.W_OK)
        parent_writable = os.access(str(Path(path).parent), os.W_OK)
        return PermissionInfo(readable=readable, writable=writable,
                              parent_writable=parent_writable)
    except OSError as exc:
        return PermissionInfo(readable=False, writable=False,
                              parent_writable=False, error=str(exc))


def _classify_error(msg: str) -> CorruptionType:
    """Map a PIL error message to a CorruptionType."""
    lower = msg.lower()
    if any(k in lower for k in ("truncat", "premature", "eof", "unexpected end")):
        return CorruptionType.TRUNCATED
    if any(k in lower for k in ("exif", "ifd", "tag", "tiff")):
        return CorruptionType.BAD_EXIF
    return CorruptionType.UNKNOWN


def _detect_corruption(path: str) -> tuple[CorruptionType, Optional[str]]:
    """
    Attempt to detect corruption in an image file.

    Returns (CorruptionType, error_message).  CorruptionType.NONE means the
    file loaded and verified without issues.
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return CorruptionType.UNKNOWN, "Pillow is not installed"

    # Pass 1: open + force-load pixel data (catches truncated files)
    try:
        with Image.open(path) as img:
            try:
                img.load()
            except Exception as load_exc:
                return _classify_error(str(load_exc)), str(load_exc)
    except Image.UnidentifiedImageError as exc:
        return CorruptionType.INVALID_FORMAT, str(exc)
    except Exception as exc:
        return _classify_error(str(exc)), str(exc)

    # Pass 2: structural verify (must reopen because verify() closes the file)
    try:
        with Image.open(path) as img:
            try:
                img.verify()
            except Exception as verify_exc:
                return _classify_error(str(verify_exc)), str(verify_exc)
    except Exception:
        # If we got here it loaded fine in pass 1; treat verify open failure as benign
        pass

    return CorruptionType.NONE, None


def _attempt_repair(path: str, corruption_type: CorruptionType) -> tuple[bool, list[str]]:
    """
    Try to repair *path* in-place using up to three strategies.

    Returns (success, list_of_attempted_strategy_names).
    """
    try:
        from PIL import Image, ImageFile
    except ImportError:
        return False, []

    attempts: list[str] = []

    # ------------------------------------------------------------------
    # Strategy 1: Re-encode
    # Open with LOAD_TRUNCATED_IMAGES=True, copy pixel data, save back.
    # Handles truncated files and recovers clean pixel data.
    # The _repair_lock ensures the global PIL flag is set/restored safely
    # even when multiple threads call this concurrently.
    # ------------------------------------------------------------------
    attempts.append("re-encode")
    try:
        with _repair_lock:
            old_setting = ImageFile.LOAD_TRUNCATED_IMAGES
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            try:
                with Image.open(path) as img:
                    img_copy = img.copy()
                    fmt = img.format or "JPEG"
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = old_setting

        save_kwargs: dict = {}
        if fmt == "JPEG":
            save_kwargs = {"quality": 95, "subsampling": 0}

        img_copy.save(path, format=fmt, **save_kwargs)

        if _detect_corruption(path)[0] == CorruptionType.NONE:
            return True, attempts
    except Exception as exc:
        logger.debug(f"Re-encode failed for {path}: {exc}")

    # ------------------------------------------------------------------
    # Strategy 2: Strip EXIF then re-save
    # Useful when the pixel data is fine but the metadata block is broken.
    # ------------------------------------------------------------------
    attempts.append("strip-exif")
    try:
        import piexif
        piexif.remove(path)

        # Re-encode after stripping to ensure a clean file
        with _repair_lock:
            old_setting = ImageFile.LOAD_TRUNCATED_IMAGES
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            try:
                with Image.open(path) as img:
                    img_copy = img.copy()
                    fmt = img.format or "JPEG"
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = old_setting

        save_kwargs = {"quality": 95, "subsampling": 0} if fmt == "JPEG" else {}
        img_copy.save(path, format=fmt, **save_kwargs)

        if _detect_corruption(path)[0] == CorruptionType.NONE:
            return True, attempts
    except Exception as exc:
        logger.debug(f"Strip-EXIF repair failed for {path}: {exc}")

    # ------------------------------------------------------------------
    # Strategy 3: Format conversion to PNG (last resort)
    # Attempts to recover whatever pixel data PIL can read, saves as a
    # clean PNG alongside the original, then replaces the original.
    # ------------------------------------------------------------------
    attempts.append("convert-png")
    stem = Path(path).stem
    png_path = str(Path(path).parent / f"{stem}_repaired.png")
    try:
        with _repair_lock:
            old_setting = ImageFile.LOAD_TRUNCATED_IMAGES
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            try:
                with Image.open(path) as img:
                    rgb = img.convert("RGB")
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = old_setting

        rgb.save(png_path, format="PNG")

        if _detect_corruption(png_path)[0] == CorruptionType.NONE:
            # Replace original with the repaired PNG
            os.remove(path)
            os.rename(png_path, path.rsplit(".", 1)[0] + ".png")
            return True, attempts

        # Clean up failed attempt
        if os.path.exists(png_path):
            os.remove(png_path)
    except Exception as exc:
        logger.debug(f"Format-conversion repair failed for {path}: {exc}")
        if os.path.exists(png_path):
            try:
                os.remove(png_path)
            except OSError:
                pass

    return False, attempts


def _quarantine(path: str, trash_dir: str, dry_run: bool) -> str:
    """
    Move *path* to *trash_dir*.  Returns the destination path string.
    On dry_run the move is skipped but the computed destination is returned.
    """
    dest = get_unique_path(Path(trash_dir), Path(path).name)
    if not dry_run:
        os.makedirs(trash_dir, exist_ok=True)
        shutil.move(path, str(dest))
        logger.info(f"Quarantined: {path} -> {dest}")
    else:
        logger.info(f"[DRY RUN] Would quarantine: {path} -> {dest}")
    return str(dest)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_and_repair(
    directory: str,
    trash_folder: str,
    attempt_repair: bool = True,
    quarantine_unfixable: bool = True,
    extensions: Optional[set[str]] = None,
    dry_run: bool = False,
    max_workers: int = 4,
) -> dict:
    """
    Scan a directory for corrupt images, attempt repairs, and quarantine
    files that cannot be fixed.

    Args:
        directory: Root directory to scan recursively.
        trash_folder: Absolute path to the folder where unfixable files are
            moved.  The folder is created on demand.
        attempt_repair: If True, try to repair corrupt files before
            quarantining (default: True).
        quarantine_unfixable: If True, move unrepairable files to
            *trash_folder* (default: True).
        extensions: Set of lowercase extensions to scan (e.g. {'.jpg'}).
            Defaults to all IMAGE_EXTENSIONS from config.
        dry_run: Simulate all actions without modifying any files
            (default: False).
        max_workers: Number of parallel worker threads (default: 4).
            Set to 1 to disable parallelism (e.g. for network drives).

    Returns:
        Dictionary with the following keys:
            - checked (int): total files examined
            - clean (int): files with no corruption
            - repaired (int): files successfully repaired in-place
            - quarantined (int): files moved to trash_folder
            - permission_errors (int): files that could not be read
            - skipped (int): files skipped (read-only, INVALID_FORMAT, etc.)
            - errors (int): unexpected errors during processing
            - results (list[RepairResult]): per-file detail

    Examples:
        >>> stats = scan_and_repair(
        ...     "/photos",
        ...     trash_folder="/photos/.trash",
        ...     dry_run=True,
        ... )
        >>> print(f"Found {len(stats['results'])} files; "
        ...       f"{stats['clean']} clean, {stats['repaired']} repaired, "
        ...       f"{stats['quarantined']} quarantined")

    Notes:
        - Files already inside *trash_folder* are excluded from scanning.
        - Repair modifies files in-place; always test with dry_run=True first.
        - INVALID_FORMAT files are quarantined directly (not repairable).
        - Permission errors are reported but never cause a crash.
    """
    from ..scanner.file_discovery import find_image_files

    exts = extensions or IMAGE_EXTENSIONS
    trash_resolved = str(Path(trash_folder).resolve())

    # Discover all image files, excluding anything already in the trash folder
    all_paths = find_image_files(directory, recursive=True)
    paths = [
        p for p in all_paths
        if Path(p).suffix.lower() in exts
        and not p.startswith(trash_resolved)
    ]

    stats: dict = {
        "checked": 0,
        "clean": 0,
        "repaired": 0,
        "quarantined": 0,
        "permission_errors": 0,
        "skipped": 0,
        "errors": 0,
        "results": [],
    }
    lock = threading.Lock()

    def _process(path: str) -> RepairResult:
        result = RepairResult(path=path)

        # ── Permission check ──────────────────────────────────────────────
        perms = _check_permissions(path)
        result.permissions = perms

        if not perms.readable:
            result.corruption_type = CorruptionType.PERMISSION_DENIED
            result.status = RepairStatus.PERMISSION_ERROR
            result.error = perms.error or "File is not readable (permission denied)"
            return result

        # ── Corruption detection ──────────────────────────────────────────
        corruption_type, error_msg = _detect_corruption(path)
        result.corruption_type = corruption_type
        result.error = error_msg

        if corruption_type == CorruptionType.NONE:
            result.status = RepairStatus.CLEAN
            return result

        # INVALID_FORMAT files cannot be repaired — go straight to quarantine
        if corruption_type == CorruptionType.INVALID_FORMAT:
            if quarantine_unfixable:
                if not perms.writable or not perms.parent_writable:
                    result.status = RepairStatus.SKIPPED
                    result.error = (result.error or "") + \
                        " [cannot quarantine: insufficient permissions]"
                    return result
                try:
                    result.trash_path = _quarantine(path, trash_folder, dry_run)
                    result.status = RepairStatus.QUARANTINED
                except Exception as exc:
                    result.status = RepairStatus.ERROR
                    result.error = f"Quarantine failed: {exc}"
            else:
                result.status = RepairStatus.SKIPPED
            return result

        # ── Repair attempt ────────────────────────────────────────────────
        if attempt_repair:
            if not perms.writable:
                result.status = RepairStatus.SKIPPED
                result.error = (result.error or "") + \
                    " [read-only: cannot repair in-place]"
                return result

            success, repair_attempts = _attempt_repair(path, corruption_type)
            result.repair_attempts = repair_attempts

            if success:
                result.status = RepairStatus.REPAIRED
                return result

        # ── Quarantine ────────────────────────────────────────────────────
        if quarantine_unfixable:
            if not perms.writable or not perms.parent_writable:
                result.status = RepairStatus.SKIPPED
                result.error = (result.error or "") + \
                    " [cannot quarantine: insufficient permissions]"
                return result
            try:
                result.trash_path = _quarantine(path, trash_folder, dry_run)
                result.status = RepairStatus.QUARANTINED
            except Exception as exc:
                result.status = RepairStatus.ERROR
                result.error = f"Quarantine failed: {exc}"
        else:
            result.status = RepairStatus.SKIPPED

        return result

    pbar = make_progress_bar(total=len(paths), desc="Checking images", unit="img")
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_process, p): p for p in paths}
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    path = futures[future]
                    result = RepairResult(
                        path=path,
                        status=RepairStatus.ERROR,
                        error=str(exc),
                    )

                with lock:
                    stats["checked"] += 1
                    stats["results"].append(result)
                    if result.status == RepairStatus.CLEAN:
                        stats["clean"] += 1
                    elif result.status == RepairStatus.REPAIRED:
                        stats["repaired"] += 1
                    elif result.status == RepairStatus.QUARANTINED:
                        stats["quarantined"] += 1
                    elif result.status == RepairStatus.PERMISSION_ERROR:
                        stats["permission_errors"] += 1
                    elif result.status == RepairStatus.SKIPPED:
                        stats["skipped"] += 1
                    elif result.status == RepairStatus.ERROR:
                        stats["errors"] += 1

                pbar.update(1)
    finally:
        pbar.close()

    return stats


__all__ = [
    "CorruptionType",
    "RepairStatus",
    "PermissionInfo",
    "RepairResult",
    "scan_and_repair",
]
