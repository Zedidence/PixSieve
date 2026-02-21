"""
File renaming operations.

Provides two renaming strategies:
- Random alphanumeric names
- Parent-folder-based naming
"""

from __future__ import annotations

import os
import random
import string
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import IMAGE_EXTENSIONS
from ..utils import (
    find_files,
    sanitize_filename,
    truncate_path,
    make_progress_bar,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Random rename
# ---------------------------------------------------------------------------

def _generate_random_name(length: int = 12) -> str:
    """Generate random alphanumeric string of specified length."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


def _rename_single_file(
    file_path: Path,
    name_length: int,
    dry_run: bool,
    max_retries: int = 100,
) -> tuple[bool, str, str | None, str | None]:
    """
    Rename one file to random name.

    Returns:
        Tuple of (success, old_name, new_name, error_message)
    """
    try:
        if not file_path.is_file():
            return False, str(file_path), None, "Not a file"

        ext = file_path.suffix
        directory = file_path.parent

        # Try to generate unique random name
        for _ in range(max_retries):
            new_name = f"{_generate_random_name(name_length)}{ext}"
            new_path = directory / new_name
            if not new_path.exists():
                break
        else:
            return False, file_path.name, None, "Could not generate unique name"

        if dry_run:
            return True, file_path.name, new_name, None

        file_path.rename(new_path)
        return True, file_path.name, new_name, None

    except PermissionError:
        return False, file_path.name, None, "Permission denied"
    except Exception as exc:
        return False, file_path.name, None, str(exc)


def rename_random(
    root_dir: str | Path,
    name_length: int = 12,
    extensions: set[str] | None = None,
    recursive: bool = True,
    dry_run: bool = False,
    workers: int = 4,
) -> dict[str, int | list[str]]:
    """
    Rename all matching files to random alphanumeric names.

    Uses parallel processing for improved performance on large collections.

    Args:
        root_dir: Root directory to scan
        name_length: Length of random name (default: 12)
        extensions: Set of file extensions to rename (default: IMAGE_EXTENSIONS)
        recursive: Search subdirectories (default: True)
        dry_run: If True, only report what would be renamed (default: False)
        workers: Number of parallel workers (default: 4)

    Returns:
        Dictionary with statistics:
            - success: Number of files successfully renamed
            - failed: Number of files that failed to rename
            - errors: List of error messages

    Examples:
        >>> stats = rename_random('/photos', name_length=16, dry_run=True)
        >>> print(f"Would rename {stats['success']} files")

    Notes:
        - Preserves file extensions
        - Automatically generates unique names
        - Parallel processing for better performance
    """
    root = Path(root_dir)
    exts = extensions or IMAGE_EXTENSIONS
    files = find_files(root, exts, recursive)

    if not files:
        logger.info(f"No files found to rename in {root}")
        return {'success': 0, 'failed': 0, 'errors': []}

    logger.info(f"Found {len(files)} file(s) to rename")

    stats: dict = {'success': 0, 'failed': 0, 'errors': []}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_rename_single_file, f, name_length, dry_run): f
            for f in files
        }
        pbar = make_progress_bar(total=len(files), desc="Renaming")
        for future in as_completed(futures):
            success, old, new, err = future.result()
            if success:
                stats['success'] += 1
                tag = "[DRY RUN] " if dry_run else ""
                logger.info(f"{tag}{old} -> {new}")
            else:
                stats['failed'] += 1
                stats['errors'].append(f"{old}: {err}")
                logger.error(f"Failed: {old} — {err}")
            pbar.update(1)
        pbar.close()

    return stats


# ---------------------------------------------------------------------------
# Parent-folder rename
# ---------------------------------------------------------------------------

def rename_by_parent(
    root_dir: str | Path,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Rename files based on parent and grandparent folder names.

    Expects directory structure like:
        root_dir/
            ArtistA/
                AlbumX/
                    img.jpg  →  ArtistA_AlbumX_1.jpg

    Args:
        root_dir: Root directory containing organized folders
        dry_run: If True, only report what would be renamed (default: False)

    Returns:
        Dictionary with statistics:
            - renamed: Number of files renamed (or would be renamed)
            - skipped: Number of files skipped (already correctly named)
            - errors: Number of errors encountered

    Examples:
        >>> stats = rename_by_parent('/music', dry_run=True)
        >>> print(f"Would rename {stats['renamed']} files")

    Notes:
        - Produces names like: FolderName_SubFolder_1.jpg
        - Handles Windows path length limits
        - Sanitizes filenames for Windows compatibility
        - Resolves naming conflicts automatically
    """
    root = Path(root_dir)
    stats = {'renamed': 0, 'skipped': 0, 'errors': 0}

    if not root.is_dir():
        logger.error(f"Not a valid directory: {root}")
        return stats

    try:
        name_folders = sorted(os.listdir(root))
    except (PermissionError, OSError) as exc:
        logger.error(f"Cannot read {root}: {exc}")
        return stats

    for name_folder in name_folders:
        name_path = root / name_folder
        if not name_path.is_dir():
            continue

        safe_name = sanitize_filename(name_folder)

        try:
            subfolders = sorted(
                f for f in os.listdir(name_path)
                if (name_path / f).is_dir()
            )
        except (PermissionError, OSError) as exc:
            logger.error(f"Cannot read {name_path}: {exc}")
            stats['errors'] += 1
            continue

        # If no subfolders, treat the parent itself as the target
        if not subfolders:
            subfolders = [""]

        for subfolder_name in subfolders:
            target_dir = (name_path / subfolder_name) if subfolder_name else name_path
            safe_sub = sanitize_filename(subfolder_name) if subfolder_name else ""

            try:
                filenames = sorted(os.listdir(target_dir))
            except (PermissionError, OSError) as exc:
                logger.error(f"Cannot read {target_dir}: {exc}")
                stats['errors'] += 1
                continue

            index = 1
            for filename in filenames:
                file_path = target_dir / filename
                if not file_path.is_file():
                    continue

                ext = file_path.suffix

                # Generate new name
                if safe_sub:
                    base = f"{safe_name}_{safe_sub}_{index}{ext}"
                else:
                    base = f"{safe_name}_{index}{ext}"

                # Handle Windows path length limits
                new_path_str = truncate_path(str(target_dir / base))
                if new_path_str is None:
                    logger.error(f"Path too long, skipping: {filename}")
                    stats['errors'] += 1
                    index += 1
                    continue

                new_path = Path(new_path_str)

                # Skip if already correctly named
                if file_path.resolve() == new_path.resolve():
                    stats['skipped'] += 1
                    index += 1
                    continue

                # Resolve naming conflicts
                counter = 1
                while new_path.exists() and new_path.resolve() != file_path.resolve():
                    if safe_sub:
                        base = f"{safe_name}_{safe_sub}_{index}_{counter}{ext}"
                    else:
                        base = f"{safe_name}_{index}_{counter}{ext}"
                    candidate = truncate_path(str(target_dir / base))
                    if candidate is None:
                        break
                    new_path = Path(candidate)
                    counter += 1
                    if counter > 1000:  # Safety limit
                        break

                if counter > 1000 or new_path_str is None:
                    logger.error(f"Too many conflicts for {filename}")
                    stats['errors'] += 1
                    index += 1
                    continue

                if dry_run:
                    logger.info(f"[DRY RUN] {filename} -> {new_path.name}")
                    stats['renamed'] += 1
                else:
                    try:
                        os.rename(str(file_path), str(new_path))
                        logger.info(f"Renamed: {filename} -> {new_path.name}")
                        stats['renamed'] += 1
                    except Exception as exc:
                        logger.error(f"Error renaming {filename}: {exc}")
                        stats['errors'] += 1

                index += 1

    return stats


__all__ = ['rename_random', 'rename_by_parent']
