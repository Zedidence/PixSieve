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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..config import IMAGE_EXTENSIONS
from ..utils import get_unique_path

logger = logging.getLogger(__name__)


def move_to_parent(
    parent_path: str | Path,
    extensions: set[str] | None = None,
    dry_run: bool = False,
    max_workers: int = 4,
) -> dict[str, int]:
    """
    Move all images from subdirectories into the parent folder.

    This flattens the directory structure by moving all images from
    nested subdirectories into the top-level parent folder.

    Args:
        parent_path: Parent directory containing subdirectories with images
        extensions: Set of file extensions to move (default: all IMAGE_EXTENSIONS)
        dry_run: If True, only report what would be moved (default: False)
        max_workers: G2 - number of parallel move workers (default: 4).
            Set to 1 to disable parallelism (e.g., for network drives).

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
    lock = threading.Lock()

    if not parent.is_dir():
        logger.error(f"Invalid path: {parent}")
        return stats

    # Collect work items first (walk is single-threaded)
    tasks: list[tuple[Path, Path]] = []  # (src, dest)
    for root, _dirs, files in os.walk(parent):
        for filename in files:
            file_path = Path(root) / filename

            if file_path.suffix.lower() not in exts:
                continue

            if file_path.parent == parent:
                with lock:
                    stats['skipped'] += 1
                continue

            dest = get_unique_path(parent, filename)

            if dry_run:
                logger.info(f"[DRY RUN] Would move: {file_path} -> {dest}")
                with lock:
                    stats['moved'] += 1
                continue

            tasks.append((file_path, dest))

    if dry_run or not tasks:
        return stats

    # G2: parallelize the actual file moves
    def _do_move(src: Path, dest: Path) -> str:
        """Return 'ok', 'error'."""
        try:
            shutil.move(str(src), str(dest))
            logger.info(f"Moved: {src} -> {dest}")
            return 'ok'
        except Exception as exc:
            logger.error(f"Failed to move {src}: {exc}")
            return 'error'

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_do_move, src, dest): (src, dest) for src, dest in tasks}
        for future in as_completed(futures):
            result = future.result()
            with lock:
                if result == 'ok':
                    stats['moved'] += 1
                else:
                    stats['errors'] += 1

    return stats


def move_with_structure(
    source: str | Path,
    destination: str | Path,
    overwrite: bool = False,
    dry_run: bool = False,
    max_workers: int = 4,
) -> dict[str, int]:
    """
    Move files from source to destination preserving directory structure.

    Args:
        source: Source directory to move files from
        destination: Destination directory to move files to
        overwrite: If True, overwrite existing files (default: False)
        dry_run: If True, only report what would be moved (default: False)
        max_workers: G2 - number of parallel move workers (default: 4).
            Set to 1 to disable parallelism (e.g., for network drives).

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
    lock = threading.Lock()

    if not source.is_dir():
        logger.error(f"Source does not exist: {source}")
        return stats

    destination.mkdir(parents=True, exist_ok=True)

    # Collect work items; create destination dirs synchronously to avoid races
    tasks: list[tuple[Path, Path, bool]] = []  # (src, dest, will_overwrite)
    for root, _dirs, files in os.walk(source):
        rel_path = os.path.relpath(root, source)
        dest_dir = destination / rel_path

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

            if dest_file.exists() and not overwrite:
                logger.info(f"Skipped (exists): {dest_file}")
                with lock:
                    stats['skipped'] += 1
                continue

            tasks.append((src_file, dest_file, dest_file.exists() and overwrite))

    if dry_run or not tasks:
        return stats

    # G2: parallelize the actual file moves
    def _do_move(src: Path, dest: Path, will_overwrite: bool) -> str:
        """Return 'ok', 'error'."""
        try:
            if will_overwrite:
                dest.unlink()
                shutil.move(str(src), str(dest))
                logger.info(f"Overwritten: {dest}")
            else:
                shutil.move(str(src), str(dest))
                logger.info(f"Moved: {src} -> {dest}")
            return 'ok'
        except PermissionError:
            logger.error(f"Permission denied: {src}")
            return 'error'
        except Exception as exc:
            logger.error(f"Failed to move {src}: {exc}")
            return 'error'

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_do_move, src, dest, wo): (src, dest)
            for src, dest, wo in tasks
        }
        for future in as_completed(futures):
            result = future.result()
            with lock:
                if result == 'ok':
                    stats['moved'] += 1
                else:
                    stats['errors'] += 1

    # Clean up empty source directories (bottom-up)
    for root, dirs, _files in os.walk(source, topdown=False):
        for d in dirs:
            dir_path = os.path.join(root, d)
            try:
                os.rmdir(dir_path)  # Only removes if empty
            except OSError:
                pass  # Directory not empty, skip silently

    return stats


__all__ = ['move_to_parent', 'move_with_structure']
