"""
File movement operations.

Provides functions to move image files with different strategies:
- Flatten directory hierarchy by moving all to parent
- Preserve directory structure while moving
"""

from __future__ import annotations

import os
import shutil
import logging
from pathlib import Path

from ..config import IMAGE_EXTENSIONS
from ..utils import get_unique_path

logger = logging.getLogger(__name__)


def move_to_parent(
    parent_path: str | Path,
    extensions: set[str] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Move all images from subdirectories into the parent folder.

    This flattens the directory structure by moving all images from
    nested subdirectories into the top-level parent folder.

    Args:
        parent_path: Parent directory containing subdirectories with images
        extensions: Set of file extensions to move (default: all IMAGE_EXTENSIONS)
        dry_run: If True, only report what would be moved (default: False)

    Returns:
        Dictionary with statistics:
            - moved: Number of files moved (or would be moved)
            - skipped: Number of files already in parent (skipped)
            - errors: Number of errors encountered

    Examples:
        >>> stats = move_to_parent('/photos', dry_run=True)
        >>> print(f"Would move {stats['moved']} files")

    Notes:
        - Files already in parent directory are skipped
        - Duplicate filenames get _1, _2, etc. appended
        - Preserves file extensions
    """
    parent = Path(parent_path).resolve()
    exts = extensions or IMAGE_EXTENSIONS
    stats = {'moved': 0, 'skipped': 0, 'errors': 0}

    if not parent.is_dir():
        logger.error(f"Invalid path: {parent}")
        return stats

    for root, _dirs, files in os.walk(parent):
        for filename in files:
            file_path = Path(root) / filename

            # Skip if not matching extension
            if file_path.suffix.lower() not in exts:
                continue

            # Skip if already in parent
            if file_path.parent == parent:
                stats['skipped'] += 1
                continue

            # Get unique destination path
            dest = get_unique_path(parent, filename)

            if dry_run:
                logger.info(f"[DRY RUN] Would move: {file_path} -> {dest}")
                stats['moved'] += 1
                continue

            try:
                shutil.move(str(file_path), str(dest))
                logger.info(f"Moved: {file_path} -> {dest}")
                stats['moved'] += 1
            except Exception as exc:
                logger.error(f"Failed to move {file_path}: {exc}")
                stats['errors'] += 1

    return stats


def move_with_structure(
    source: str | Path,
    destination: str | Path,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Move files from source to destination preserving directory structure.

    Args:
        source: Source directory to move files from
        destination: Destination directory to move files to
        overwrite: If True, overwrite existing files (default: False)
        dry_run: If True, only report what would be moved (default: False)

    Returns:
        Dictionary with statistics:
            - moved: Number of files moved (or would be moved)
            - skipped: Number of files skipped (already exist, overwrite=False)
            - errors: Number of errors encountered

    Examples:
        >>> stats = move_with_structure('/photos', '/backup', dry_run=True)
        >>> print(f"Would move {stats['moved']} files, skip {stats['skipped']}")

    Notes:
        - Automatically creates necessary directories in destination
        - Cleans up empty source directories after moving (unless dry-run)
        - If overwrite=False, existing files are skipped
    """
    source = Path(source)
    destination = Path(destination)
    stats = {'moved': 0, 'skipped': 0, 'errors': 0}

    if not source.is_dir():
        logger.error(f"Source does not exist: {source}")
        return stats

    destination.mkdir(parents=True, exist_ok=True)

    for root, _dirs, files in os.walk(source):
        # Calculate relative path from source
        rel_path = os.path.relpath(root, source)
        dest_dir = destination / rel_path

        # Create destination directory structure
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)

        for filename in files:
            src_file = Path(root) / filename
            dest_file = dest_dir / filename

            if dry_run:
                if dest_file.exists() and not overwrite:
                    logger.info(f"[DRY RUN] Would skip (exists): {dest_file}")
                    stats['skipped'] += 1
                else:
                    action = "overwrite" if dest_file.exists() and overwrite else "move"
                    logger.info(f"[DRY RUN] Would {action}: {src_file} -> {dest_file}")
                    stats['moved'] += 1
                continue

            try:
                if dest_file.exists():
                    if overwrite:
                        dest_file.unlink()
                        shutil.move(str(src_file), str(dest_file))
                        logger.info(f"Overwritten: {dest_file}")
                        stats['moved'] += 1
                    else:
                        logger.info(f"Skipped (exists): {dest_file}")
                        stats['skipped'] += 1
                else:
                    shutil.move(str(src_file), str(dest_file))
                    logger.info(f"Moved: {src_file} -> {dest_file}")
                    stats['moved'] += 1
            except PermissionError:
                logger.error(f"Permission denied: {src_file}")
                stats['errors'] += 1
            except Exception as exc:
                logger.error(f"Failed to move {src_file}: {exc}")
                stats['errors'] += 1

    # Clean up empty source directories (bottom-up)
    if not dry_run:
        for root, dirs, _files in os.walk(source, topdown=False):
            for d in dirs:
                dir_path = os.path.join(root, d)
                try:
                    os.rmdir(dir_path)  # Only removes if empty
                except OSError:
                    pass  # Directory not empty, skip silently

    return stats


__all__ = ['move_to_parent', 'move_with_structure']
