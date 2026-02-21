"""
Maintenance operations for directory cleanup.

Provides functionality to recursively delete empty directories.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def delete_empty_folders(
    root_dir: str | Path,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Recursively delete all empty directories under root_dir.

    Walks the directory tree bottom-up so that nested empty trees
    are fully removed.

    Args:
        root_dir: Root directory to scan for empty folders
        dry_run: If True, only report what would be deleted (default: False)

    Returns:
        Dictionary with statistics:
            - deleted: Number of empty folders deleted (or would be deleted)
            - errors: Number of errors encountered

    Examples:
        >>> stats = delete_empty_folders('/path/to/photos', dry_run=True)
        >>> print(f"Would delete {stats['deleted']} empty folders")

    Notes:
        - The root directory itself is never deleted
        - Requires appropriate permissions to delete folders
        - In dry-run mode, no folders are actually deleted
    """
    root = Path(root_dir).resolve()
    stats = {'deleted': 0, 'errors': 0}

    if not root.is_dir():
        logger.error(f"Not a directory: {root}")
        return stats

    # Walk bottom-up so empty parent folders are detected after children removed
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        dir_path = Path(dirpath)

        # Don't remove the root itself
        if dir_path == root:
            continue

        # A directory is "empty" if it contains no files and no subdirectories
        # (after we've already removed empty children in bottom-up walk)
        try:
            remaining = list(dir_path.iterdir())
        except PermissionError:
            logger.error(f"Permission denied: {dir_path}")
            stats['errors'] += 1
            continue

        if not remaining:
            if dry_run:
                logger.info(f"[DRY RUN] Would delete empty folder: {dir_path}")
                stats['deleted'] += 1
            else:
                try:
                    dir_path.rmdir()
                    logger.info(f"Deleted empty folder: {dir_path}")
                    stats['deleted'] += 1
                except OSError as exc:
                    logger.error(f"Could not delete {dir_path}: {exc}")
                    stats['errors'] += 1

    return stats


__all__ = ['delete_empty_folders']
