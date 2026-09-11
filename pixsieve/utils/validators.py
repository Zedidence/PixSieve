"""
Input validation and security checks for the PixSieve.

Provides validators for path traversal prevention, file accessibility,
and scan parameter validation.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def validate_path_in_directory(filepath: str, base_directory: str) -> bool:
    """
    Validate that a file path is within the expected base directory.

    Prevents path traversal attacks where user input could access files
    outside the scanned directory.

    Args:
        filepath: Path to validate
        base_directory: Expected base directory

    Returns:
        True if path is within base_directory, False otherwise

    Examples:
        >>> validate_path_in_directory('/home/user/photos/img.jpg', '/home/user/photos')
        True
        >>> validate_path_in_directory('/etc/passwd', '/home/user/photos')
        False
    """
    try:
        file_resolved = Path(filepath).resolve()
        base_resolved = Path(base_directory).resolve()
        return str(file_resolved).startswith(str(base_resolved) + os.sep) or \
               str(file_resolved) == str(base_resolved)
    except OSError as exc:
        # This backs every path-traversal check in the app (via
        # validate_path_in_any_directory / is_path_reference_protected), so
        # log it: a resolve() failure for an unexpected reason (an exotic
        # malformed path) should be distinguishable in the logs from the
        # normal "resolved fine, just outside the directory" False above.
        logger.warning(f"Path validation failed for {filepath!r} against {base_directory!r}: {exc}")
        return False


def validate_file_accessible(filepath: str) -> tuple[bool, str]:
    """
    Validate that a file exists and is accessible before operations.

    Args:
        filepath: Path to validate

    Returns:
        Tuple of (is_valid, error_message)
        - (True, "") if file is accessible
        - (False, error_message) if file is not accessible

    Examples:
        >>> validate_file_accessible('/nonexistent/file.jpg')
        (False, 'File does not exist')
    """
    if not os.path.exists(filepath):
        return False, "File does not exist"

    if not os.path.isfile(filepath):
        return False, "Path is not a file"

    if not os.access(filepath, os.R_OK):
        return False, "File is not readable (permission denied)"

    try:
        with open(filepath, 'rb') as f:
            pass
    except PermissionError:
        return False, "File is locked by another process"
    except IOError as e:
        return False, f"Cannot access file: {e}"

    return True, ""


def validate_directory(directory: str) -> tuple[bool, str]:
    """
    Validate that a directory exists and is accessible.

    Args:
        directory: Directory path to validate

    Returns:
        Tuple of (is_valid, error_message)

    Examples:
        >>> validate_directory('/nonexistent/directory')
        (False, 'Directory not found: /nonexistent/directory')
    """
    if not directory:
        return False, "Directory path is required"

    if not os.path.isabs(directory):
        return False, "Directory must be an absolute path"

    if not os.path.exists(directory):
        return False, f"Directory not found: {directory}"

    if not os.path.isdir(directory):
        return False, f"Path is not a directory: {directory}"

    if not os.access(directory, os.R_OK):
        return False, f"Cannot read directory (permission denied): {directory}"

    return True, ""


def validate_threshold(threshold: int) -> tuple[bool, str]:
    """
    Validate that a threshold value is within acceptable range.

    Args:
        threshold: Threshold value to validate (should be 0-64 for pHash)

    Returns:
        Tuple of (is_valid, error_message)

    Examples:
        >>> validate_threshold(10)
        (True, '')
        >>> validate_threshold(100)
        (False, 'Threshold must be between 0 and 64')
    """
    try:
        threshold = int(threshold)
        if not 0 <= threshold <= 64:
            return False, "Threshold must be between 0 and 64"
        return True, ""
    except (ValueError, TypeError):
        return False, "Threshold must be an integer"


def validate_scan_params(
    directory: str,
    threshold: Optional[int] = None,
    exact_only: bool = False,
    perceptual_only: bool = False,
    workers: Optional[int] = None,
) -> tuple[bool, str]:
    """
    Validate all scan parameters.

    Args:
        directory: Directory to scan
        threshold: Perceptual hash threshold (optional)
        exact_only: Whether to only find exact duplicates
        perceptual_only: Whether to only find perceptual duplicates
        workers: Number of worker threads (optional)

    Returns:
        Tuple of (is_valid, error_message)

    Examples:
        >>> validate_scan_params('/home/user/photos', threshold=10)
        (True, '')
    """
    # Validate directory
    is_valid, error = validate_directory(directory)
    if not is_valid:
        return False, error

    # Validate threshold if provided
    if threshold is not None:
        is_valid, error = validate_threshold(threshold)
        if not is_valid:
            return False, error

    # Validate mutual exclusivity
    if exact_only and perceptual_only:
        return False, "Cannot use both exactOnly and perceptualOnly"

    # Validate workers if provided
    if workers is not None:
        try:
            workers = int(workers)
            if not 1 <= workers <= 32:
                return False, "Workers must be between 1 and 32"
        except (ValueError, TypeError):
            return False, "Workers must be an integer"

    return True, ""


def validate_directories(entries: list[dict]) -> tuple[bool, str]:
    """
    Validate a list of {'path': str, 'is_reference': bool} entries.

    Each path is validated independently via validate_directory(). At most
    one entry may have is_reference=True - this is re-checked here as
    defense in depth even though the API schema layer already enforces it,
    since this function may be called from other contexts (e.g. CLI) that
    bypass the Pydantic schema.

    Args:
        entries: List of directory entry dicts

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not entries:
        return False, "At least one directory is required"

    ref_count = 0
    for entry in entries:
        is_valid, error = validate_directory(entry.get('path', ''))
        if not is_valid:
            return False, error
        if entry.get('is_reference'):
            ref_count += 1

    if ref_count > 1:
        return False, "At most one directory may be marked as reference"

    return True, ""


def validate_path_in_any_directory(filepath: str, directories: list[dict]) -> bool:
    """
    Validate that a file path is within ANY of the given directories.

    Replaces validate_path_in_directory() for the multi-root scan case.

    Args:
        filepath: Path to validate
        directories: List of {'path': str, 'is_reference': bool} entries

    Returns:
        True if filepath resolves under any of the given directories
    """
    return any(
        validate_path_in_directory(filepath, entry['path'])
        for entry in directories
    )


def is_path_reference_protected(filepath: str, directories: list[dict]) -> bool:
    """
    Check whether a file path falls under the configured reference folder.

    This is the hard server-side guard: callers performing mutating
    operations (delete, move, convert, etc.) must reject any path for which
    this returns True, regardless of what the client submitted or what the
    session's selections claim.

    Args:
        filepath: Path to check
        directories: List of {'path': str, 'is_reference': bool} entries

    Returns:
        True if filepath resolves under the directory entry flagged
        is_reference=True. False if no reference directory is configured.
    """
    ref_entry = next((entry for entry in directories if entry.get('is_reference')), None)
    if ref_entry is None:
        return False
    return validate_path_in_directory(filepath, ref_entry['path'])


__all__ = [
    'validate_path_in_directory',
    'validate_file_accessible',
    'validate_directory',
    'validate_threshold',
    'validate_scan_params',
    'validate_directories',
    'validate_path_in_any_directory',
    'is_path_reference_protected',
]
