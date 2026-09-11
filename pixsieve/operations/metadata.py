"""
EXIF and file-system date manipulation.

Provides functionality to:
- Randomize EXIF metadata dates
- Randomize file system timestamps
"""

from __future__ import annotations

import os
import random
import logging
import platform
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from ..config import IMAGE_EXTENSIONS, EXIF_EXTENSIONS
from ..utils import find_files, make_progress_bar

logger = logging.getLogger(__name__)


def random_date_in_range(start: datetime, end: datetime) -> datetime:
    """
    Generate a random datetime between start and end.

    Args:
        start: Start of date range
        end: End of date range

    Returns:
        Random datetime within range

    Examples:
        >>> from datetime import datetime
        >>> start = datetime(2020, 1, 1)
        >>> end = datetime(2023, 12, 31)
        >>> random_dt = random_date_in_range(start, end)
        >>> start <= random_dt <= end
        True
    """
    delta = end - start
    random_days = random.randint(0, max(0, delta.days))
    random_seconds = random.randint(0, 86399)
    return start + timedelta(days=random_days, seconds=random_seconds)


# ---------------------------------------------------------------------------
# EXIF dates
# ---------------------------------------------------------------------------

def _write_bytes_shared(filepath: Path, data: bytes) -> None:
    """
    Write bytes to filepath using a win32 handle with shared-access flags on Windows.

    OneDrive holds files open with FILE_SHARE_* flags during sync. Python's
    built-in open() doesn't request matching share flags, which causes [Errno 22].
    win32file.CreateFile with explicit share flags avoids the conflict.
    Falls back to a plain write on non-Windows or when pywin32 is absent.
    """
    if platform.system() == 'Windows':
        try:
            import win32file
            import win32con
            handle = win32file.CreateFile(
                str(filepath),
                win32con.GENERIC_WRITE,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                None,
                win32con.OPEN_EXISTING,
                0, None,
            )
            try:
                win32file.SetEndOfFile(handle)   # truncate at position 0
                win32file.WriteFile(handle, data)
            finally:
                handle.close()
            return
        except ImportError:
            pass
    filepath.write_bytes(data)


def set_exif_dates(image_path: Path, new_datetime: datetime) -> bool:
    """
    Write EXIF date metadata to an image file.

    Sets DateTimeOriginal, DateTimeDigitized, and DateTime fields.

    Args:
        image_path: Path to image file
        new_datetime: Datetime to set in EXIF

    Returns:
        True if successful, False otherwise

    Notes:
        - Requires piexif library
        - Only works with EXIF-compatible formats (JPG, TIFF)
        - New file bytes are built in a temp file then written back via a
          shared-access win32 handle to avoid [Errno 22] on OneDrive files.
    """
    try:
        import piexif
    except ImportError as exc:
        logger.error(f"Missing dependency: {exc} (pip install piexif)")
        return False

    _blank = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    exif_str = new_datetime.strftime("%Y:%m:%d %H:%M:%S").encode('ascii')

    try:
        try:
            exif_dict = piexif.load(str(image_path))
        except Exception:
            exif_dict = _blank

        # EXIF spec (JEITA CP-3451) requires 7-bit ASCII for date strings.
        exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = exif_str
        exif_dict['Exif'][piexif.ExifIFD.DateTimeDigitized] = exif_str
        exif_dict['0th'][piexif.ImageIFD.DateTime] = exif_str

        exif_bytes = piexif.dump(exif_dict)
        suffix = image_path.suffix.lower()

        # Write to a temp file in the same directory first — new files have no
        # OneDrive lock — then read the bytes back and write to the original
        # through a shared-access win32 handle.
        tmp_fd, tmp_str = tempfile.mkstemp(suffix=suffix, dir=image_path.parent)
        os.close(tmp_fd)
        tmp_path = Path(tmp_str)
        try:
            if suffix in ('.jpg', '.jpeg'):
                piexif.insert(exif_bytes, str(image_path), new_file=tmp_str)
            else:
                from PIL import Image
                img = Image.open(image_path)
                img.save(tmp_str, exif=exif_bytes)
                img.close()
            _write_bytes_shared(image_path, tmp_path.read_bytes())
        finally:
            tmp_path.unlink(missing_ok=True)

        return True

    except Exception as exc:
        logger.error(f"EXIF error on {image_path.name}: {exc}")
        return False


def randomize_dates(
    directory: str | Path,
    start_date: datetime,
    end_date: datetime,
    recursive: bool = True,
    dry_run: bool = False,
    sync_exif: bool = True,
    max_workers: int = 4,
    extensions: set[str] | None = None,
) -> dict[str, int]:
    """
    Randomize all date fields for images in a directory.

    Sets filesystem timestamps (mtime/atime/ctime on Windows) for every image
    file, and optionally also writes EXIF date metadata (DateTimeOriginal,
    DateTimeDigitized, DateTime) for EXIF-compatible formats (JPG, TIFF).

    This covers every date field Windows Photos can sort by:
      - "Date taken"    -> EXIF DateTimeOriginal (JPG/TIFF only)
      - "Date modified" -> filesystem mtime
      - "Date created"  -> filesystem ctime (Windows only)

    Args:
        directory: Directory to scan
        start_date: Start of random date range
        end_date: End of random date range
        recursive: Search subdirectories (default: True)
        dry_run: If True, only report what would be changed (default: False)
        sync_exif: Also write EXIF date tags for JPG/TIFF files (default: True)
        max_workers: Parallel workers for file processing (default: 4)
        extensions: Set of file extensions to scan (default: IMAGE_EXTENSIONS).
            Passing a video-inclusive set (e.g. via
            config.resolve_extensions(IMAGE_EXTENSIONS, include_videos=True))
            only randomizes filesystem timestamps for video files - sync_exif
            still gates on EXIF_EXTENSIONS (piexif has no video support), so
            video files never reach set_exif_dates() regardless of this param.

    Returns:
        Dictionary with statistics:
            - success: Number of files successfully updated
            - failed: Number of files that failed to update
    """
    exts = extensions or IMAGE_EXTENSIONS
    files = find_files(Path(directory), exts, recursive)
    stats = {'success': 0, 'failed': 0}
    lock = threading.Lock()

    if not files:
        logger.info("No image files found")
        return stats

    logger.info(f"Found {len(files)} image(s)")

    if dry_run:
        for f in make_progress_bar(files, desc="Randomizing dates"):
            rand_date = random_date_in_range(start_date, end_date)
            logger.info(f"[DRY RUN] {f.name} -> {rand_date}")
            stats['success'] += 1
        return stats

    # Pre-assign random dates so each file gets a deterministic date
    # even when tasks execute out of order.
    file_dates = [(f, random_date_in_range(start_date, end_date)) for f in files]

    def _process(f: Path, rand_date: datetime) -> bool:
        try:
            exif_ok = True
            if sync_exif and f.suffix.lower() in EXIF_EXTENSIONS:
                exif_ok = set_exif_dates(f, rand_date)
            set_file_times(f, rand_date)
            logger.info(f"{f.name} -> {rand_date}")
            return exif_ok
        except Exception as exc:
            logger.error(f"Error processing {f.name}: {exc}")
            return False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process, f, d): f
            for f, d in file_dates
        }
        for future in make_progress_bar(
            as_completed(futures),
            desc="Randomizing dates",
            total=len(futures),
        ):
            ok = future.result()
            with lock:
                if ok:
                    stats['success'] += 1
                else:
                    stats['failed'] += 1

    return stats


def randomize_dates_per_folder(
    folder_ranges: list[dict],
    dry_run: bool = False,
    sync_exif: bool = True,
    max_workers: int = 4,
    extensions: set[str] | None = None,
) -> dict[str, object]:
    """
    Randomize all date fields with a separate date range per folder.

    Sets filesystem timestamps for every image and optionally EXIF date tags
    for JPG/TIFF files, using the date range specified for each folder.

    Args:
        folder_ranges: List of dicts with keys:
            - folder: Absolute path to folder
            - startDate: datetime start of range
            - endDate: datetime end of range
        dry_run: If True, only report what would be changed
        sync_exif: Also write EXIF date tags for JPG/TIFF files (default: True)
        max_workers: Parallel workers for file processing
        extensions: Set of file extensions to scan (default: IMAGE_EXTENSIONS).
            See randomize_dates() - sync_exif still gates on EXIF_EXTENSIONS
            regardless of this param, so video files never get EXIF writes.

    Returns:
        Dict with per-folder stats and totals.
    """
    total_stats = {'success': 0, 'failed': 0, 'folders': {}}
    lock = threading.Lock()
    exts = extensions or IMAGE_EXTENSIONS

    for entry in folder_ranges:
        folder = Path(entry['folder'])
        start_date = entry['startDate']
        end_date = entry['endDate']
        folder_name = folder.name

        files = find_files(folder, exts, recursive=True)
        folder_stats = {'success': 0, 'failed': 0, 'total': len(files)}

        if not files:
            logger.info(f"No image files in {folder_name}")
            total_stats['folders'][folder_name] = folder_stats
            continue

        logger.info(f"[{folder_name}] Found {len(files)} image(s) "
                    f"(range: {start_date.date()} to {end_date.date()})")

        if dry_run:
            for f in make_progress_bar(files, desc=f"Dates {folder_name}"):
                rand_date = random_date_in_range(start_date, end_date)
                logger.info(f"[DRY RUN] {f.name} -> {rand_date}")
                folder_stats['success'] += 1
        else:
            file_dates = [(f, random_date_in_range(start_date, end_date)) for f in files]

            def _process(f: Path, rand_date: datetime) -> bool:
                try:
                    exif_ok = True
                    if sync_exif and f.suffix.lower() in EXIF_EXTENSIONS:
                        exif_ok = set_exif_dates(f, rand_date)
                    set_file_times(f, rand_date)
                    logger.info(f"{f.name} -> {rand_date}")
                    return exif_ok
                except Exception as exc:
                    logger.error(f"Error processing {f.name}: {exc}")
                    return False

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_process, f, d): f for f, d in file_dates}
                for future in make_progress_bar(
                    as_completed(futures),
                    desc=f"Dates {folder_name}",
                    total=len(futures),
                ):
                    ok = future.result()
                    with lock:
                        if ok:
                            folder_stats['success'] += 1
                        else:
                            folder_stats['failed'] += 1

        total_stats['folders'][folder_name] = folder_stats
        total_stats['success'] += folder_stats['success']
        total_stats['failed'] += folder_stats['failed']

    return total_stats


# ---------------------------------------------------------------------------
# File-system dates
# ---------------------------------------------------------------------------

def set_file_times(filepath: Path, timestamp: datetime) -> None:
    """
    Set file modification/access time (and creation time on Windows).

    Args:
        filepath: Path to file
        timestamp: Datetime to set

    Notes:
        - On Windows, uses win32file API with shared access flags to handle
          OneDrive / cloud-synced files that reject os.utime() with errno 22.
        - Falls back to os.utime() on non-Windows or when pywin32 is missing.
    """
    if platform.system() == 'Windows':
        try:
            import win32file
            import win32con
            import pywintypes

            wintime = pywintypes.Time(timestamp)
            handle = win32file.CreateFile(
                str(filepath),
                win32con.GENERIC_WRITE,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                None,
                win32con.OPEN_EXISTING,
                0, None,
            )
            try:
                win32file.SetFileTime(handle, wintime, wintime, wintime)
            finally:
                handle.close()
            return
        except ImportError:
            pass  # pywin32 not installed — fall through to os.utime

    ts = timestamp.timestamp()
    os.utime(filepath, (ts, ts))


__all__ = [
    'random_date_in_range',
    'set_exif_dates',
    'set_file_times',
    'randomize_dates',
    'randomize_dates_per_folder',
]
