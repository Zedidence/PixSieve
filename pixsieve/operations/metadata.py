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
        - Preserves image quality (saves at 95%)
    """
    try:
        from PIL import Image
        import piexif
    except ImportError as exc:
        logger.error(f"Missing dependency: {exc} (pip install Pillow piexif)")
        return False

    try:
        img = Image.open(image_path)

        # Load existing EXIF or create new
        try:
            exif_dict = piexif.load(img.info.get('exif', b''))
        except Exception:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

        # Set all EXIF date fields
        exif_str = new_datetime.strftime("%Y:%m:%d %H:%M:%S").encode('utf-8')
        exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = exif_str
        exif_dict['Exif'][piexif.ExifIFD.DateTimeDigitized] = exif_str
        exif_dict['0th'][piexif.ImageIFD.DateTime] = exif_str

        # Save with new EXIF
        exif_bytes = piexif.dump(exif_dict)
        img.save(image_path, exif=exif_bytes, quality=95)
        return True

    except Exception as exc:
        logger.error(f"EXIF error on {image_path.name}: {exc}")
        return False


def randomize_exif_dates(
    directory: str | Path,
    start_date: datetime,
    end_date: datetime,
    recursive: bool = True,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Randomize EXIF date metadata for all EXIF-compatible images.

    Args:
        directory: Directory to scan
        start_date: Start of random date range
        end_date: End of random date range
        recursive: Search subdirectories (default: True)
        dry_run: If True, only report what would be changed (default: False)

    Returns:
        Dictionary with statistics:
            - success: Number of files successfully updated
            - failed: Number of files that failed to update

    Examples:
        >>> from datetime import datetime
        >>> start = datetime(2020, 1, 1)
        >>> end = datetime(2023, 12, 31)
        >>> stats = randomize_exif_dates('/photos', start, end, dry_run=True)
        >>> print(f"Would update {stats['success']} files")

    Notes:
        - Only processes EXIF-compatible formats (JPG, TIFF)
        - Requires piexif library
        - Each file gets a unique random date
    """
    files = find_files(Path(directory), EXIF_EXTENSIONS, recursive)
    stats = {'success': 0, 'failed': 0}

    if not files:
        logger.info("No EXIF-compatible images found")
        return stats

    logger.info(f"Found {len(files)} EXIF-compatible image(s)")

    for f in make_progress_bar(files, desc="Randomizing EXIF dates"):
        rand_date = random_date_in_range(start_date, end_date)

        if dry_run:
            logger.info(f"[DRY RUN] {f.name} -> {rand_date}")
            stats['success'] += 1
            continue

        if set_exif_dates(f, rand_date):
            logger.info(f"{f.name} -> {rand_date}")
            stats['success'] += 1
        else:
            stats['failed'] += 1

    return stats


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
        - Sets mtime (modification time) and atime (access time) on all platforms
        - On Windows, also sets ctime (creation time) if pywin32 is available
        - Gracefully degrades if pywin32 is not installed on Windows
    """
    ts = timestamp.timestamp()
    os.utime(filepath, (ts, ts))

    # Set creation time on Windows if pywin32 available
    if platform.system() == 'Windows':
        try:
            import win32file
            import pywintypes

            wintime = pywintypes.Time(timestamp)
            handle = win32file.CreateFile(
                str(filepath),
                win32file.GENERIC_WRITE,
                0, None,
                win32file.OPEN_EXISTING,
                0, None,
            )
            win32file.SetFileTime(handle, wintime, None, None)
            handle.close()
        except ImportError:
            pass  # pywin32 not installed — skip creation time


def randomize_file_dates(
    directory: str | Path,
    start_date: datetime,
    end_date: datetime,
    recursive: bool = True,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Randomize file-system dates (mtime, atime, and ctime on Windows).

    Args:
        directory: Directory to scan
        start_date: Start of random date range
        end_date: End of random date range
        recursive: Search subdirectories (default: True)
        dry_run: If True, only report what would be changed (default: False)

    Returns:
        Dictionary with statistics:
            - success: Number of files successfully updated
            - failed: Number of files that failed to update

    Examples:
        >>> from datetime import datetime
        >>> start = datetime(2020, 1, 1)
        >>> end = datetime(2023, 12, 31)
        >>> stats = randomize_file_dates('/photos', start, end, dry_run=True)
        >>> print(f"Would update {stats['success']} files")

    Notes:
        - Updates file system timestamps (not EXIF)
        - Works with all image formats
        - On Windows, also updates creation time if pywin32 installed
        - Each file gets a unique random date
    """
    files = find_files(Path(directory), IMAGE_EXTENSIONS, recursive)
    stats = {'success': 0, 'failed': 0}

    if not files:
        logger.info("No image files found")
        return stats

    logger.info(f"Found {len(files)} image(s) for date randomization")

    for f in make_progress_bar(files, desc="Randomizing file dates"):
        rand_date = random_date_in_range(start_date, end_date)

        if dry_run:
            logger.info(f"[DRY RUN] {f.name} -> {rand_date}")
            stats['success'] += 1
            continue

        try:
            set_file_times(f, rand_date)
            logger.info(f"{f.name} -> {rand_date}")
            stats['success'] += 1
        except Exception as exc:
            logger.error(f"Error setting date for {f.name}: {exc}")
            stats['failed'] += 1

    return stats


__all__ = [
    'random_date_in_range',
    'randomize_exif_dates',
    'randomize_file_dates',
]
