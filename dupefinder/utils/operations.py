"""
Shared utilities for media operations.

Provides helper functions for file management, Windows path handling,
progress tracking, and date parsing used across operation modules.
"""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from datetime import datetime
from typing import Iterator, Any

from ..config import WINDOWS_RESERVED_NAMES, WINDOWS_MAX_PATH

logger = logging.getLogger(__name__)


def get_unique_path(dest_folder: Path, filename: str) -> Path:
    """
    Generate a unique file path by appending _1, _2, etc. if file exists.

    Args:
        dest_folder: Destination directory
        filename: Target filename

    Returns:
        Unique Path object that doesn't exist

    Examples:
        >>> get_unique_path(Path("/dest"), "image.jpg")
        Path("/dest/image.jpg")  # if doesn't exist
        Path("/dest/image_1.jpg")  # if image.jpg exists
    """
    dest_folder = Path(dest_folder)
    base_path = dest_folder / filename

    if not base_path.exists():
        return base_path

    # Split name and extension
    stem = base_path.stem
    ext = base_path.suffix

    # Try appending _1, _2, etc.
    counter = 1
    while True:
        new_path = dest_folder / f"{stem}_{counter}{ext}"
        if not new_path.exists():
            return new_path
        counter += 1
        if counter > 9999:  # Safety limit
            raise ValueError(f"Too many duplicates for {filename}")


def sanitize_filename(name: str) -> str:
    """
    Remove invalid Windows characters from filename.

    Args:
        name: Filename to sanitize

    Returns:
        Sanitized filename safe for Windows/Unix

    Notes:
        - Removes: < > : " / \\ | ? *
        - Checks for Windows reserved names (CON, PRN, etc.)
        - Preserves file extension
    """
    # Remove invalid characters
    invalid_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(invalid_chars, '_', name)

    # Handle Windows reserved names
    name_upper = sanitized.upper()
    stem = Path(sanitized).stem.upper()

    if stem in WINDOWS_RESERVED_NAMES:
        # Prepend underscore to reserved names
        sanitized = f"_{sanitized}"

    # Remove leading/trailing spaces and dots
    sanitized = sanitized.strip(' .')

    # Ensure not empty
    if not sanitized:
        sanitized = "unnamed"

    return sanitized


def truncate_path(path: str | Path, max_length: int = WINDOWS_MAX_PATH) -> str | None:
    """
    Truncate path to fit Windows path length limits.

    Args:
        path: Path to truncate
        max_length: Maximum allowed length (default: 250)

    Returns:
        Truncated path string, or None if path too short to truncate

    Notes:
        - Windows MAX_PATH is 260, we use 250 for safety
        - Truncates filename, preserves directory and extension
    """
    path_str = str(path)

    if len(path_str) <= max_length:
        return path_str

    path_obj = Path(path_str)
    directory = path_obj.parent
    stem = path_obj.stem
    ext = path_obj.suffix

    # Calculate available space for filename
    dir_len = len(str(directory))
    ext_len = len(ext)
    separator_len = 1  # For the path separator

    available = max_length - dir_len - ext_len - separator_len

    if available < 1:
        logger.warning(f"Cannot truncate path (directory too long): {path}")
        return None

    # Truncate stem
    truncated_stem = stem[:available]
    truncated_path = directory / f"{truncated_stem}{ext}"

    return str(truncated_path)


def find_files(
    root: Path,
    extensions: set[str],
    recursive: bool = True
) -> list[Path]:
    """
    Find all files with specified extensions in directory.

    Args:
        root: Root directory to search
        extensions: Set of file extensions (e.g., {'.jpg', '.png'})
        recursive: Search subdirectories (default: True)

    Returns:
        List of Path objects for matching files

    Notes:
        - Extensions should include the dot (e.g., '.jpg')
        - Case-insensitive matching
        - Skips hidden files and directories
    """
    root = Path(root)
    files = []

    # Normalize extensions to lowercase
    extensions = {ext.lower() for ext in extensions}

    if recursive:
        # Recursive search
        for item in root.rglob('*'):
            if item.is_file() and not item.name.startswith('.'):
                if item.suffix.lower() in extensions:
                    files.append(item)
    else:
        # Non-recursive search
        for item in root.iterdir():
            if item.is_file() and not item.name.startswith('.'):
                if item.suffix.lower() in extensions:
                    files.append(item)

    return sorted(files)


def make_progress_bar(
    iterable: Iterator[Any] | None = None,
    total: int | None = None,
    desc: str = "Processing",
    unit: str = "file"
) -> Iterator[Any]:
    """
    Create a tqdm progress bar with graceful fallback.

    Args:
        iterable: Iterable to wrap
        total: Total count (if iterable is None)
        desc: Description text
        unit: Unit name (e.g., 'file', 'image')

    Returns:
        Progress bar iterator (or plain iterable if tqdm unavailable)

    Examples:
        >>> for file in make_progress_bar(files, desc="Scanning"):
        ...     process(file)
    """
    try:
        from tqdm import tqdm
        return tqdm(iterable, total=total, desc=desc, unit=unit)
    except ImportError:
        # Fallback to plain iterable if tqdm not available
        logger.debug("tqdm not available, progress bars disabled")
        return iterable if iterable is not None else range(total or 0)


def parse_date(date_str: str) -> datetime:
    """
    Parse date string in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format.

    Args:
        date_str: Date string to parse

    Returns:
        datetime object

    Raises:
        ValueError: If date format is invalid

    Examples:
        >>> parse_date("2023-12-25")
        datetime(2023, 12, 25, 0, 0, 0)
        >>> parse_date("2023-12-25 14:30:00")
        datetime(2023, 12, 25, 14, 30, 0)
    """
    # Try full datetime format first
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    # Try date-only format
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"Invalid date format: {date_str}. "
            "Expected YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
        )


__all__ = [
    'get_unique_path',
    'sanitize_filename',
    'truncate_path',
    'find_files',
    'make_progress_bar',
    'parse_date',
]
