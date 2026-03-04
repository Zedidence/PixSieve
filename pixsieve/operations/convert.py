"""
Image format conversion and extension fixing.

Provides functionality to:
- Fix file extensions that don't match actual image format
- Convert images to JPG format
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from ..config import FORMAT_TO_EXT, CONVERTIBLE_TO_JPG
from ..utils import find_files, make_progress_bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extension fixer
# ---------------------------------------------------------------------------

def fix_extensions(
    folder: str | Path,
    recursive: bool = True,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Scan images and fix file extensions that don't match the actual format.

    Uses PIL to detect the true image format and corrects the file extension
    if it doesn't match.

    Args:
        folder: Directory to scan
        recursive: Search subdirectories (default: True)
        dry_run: If True, only report what would be fixed (default: False)

    Returns:
        Dictionary with statistics:
            - total: Total image files scanned
            - valid: Files with correct extensions
            - fixed: Files with corrected extensions
            - unknown: Files with unknown/unsupported formats

    Examples:
        >>> stats = fix_extensions('/photos', dry_run=True)
        >>> print(f"Would fix {stats['fixed']} files")

    Notes:
        - Uses PIL to detect actual image format
        - Handles naming conflicts automatically
        - Skips non-image files silently
    """
    folder = Path(folder)
    stats = {'total': 0, 'valid': 0, 'fixed': 0, 'unknown': 0}

    iterator = folder.rglob('*') if recursive else folder.iterdir()

    for file_path in iterator:
        if not file_path.is_file():
            continue

        try:
            # Detect format then close file before any rename (Windows locks open files)
            with Image.open(file_path) as img:
                stats['total'] += 1
                fmt = img.format

            ext_info = FORMAT_TO_EXT.get(fmt)

            if ext_info is None:
                stats['unknown'] += 1
                logger.debug(f"Unknown format {fmt} for {file_path.name}")
                continue

            actual_ext = file_path.suffix.lower()
            if actual_ext in ext_info['valid']:
                stats['valid'] += 1
                continue

            # Extension needs fixing
            new_file = file_path.with_suffix(ext_info['preferred'])
            counter = 1
            while new_file.exists():
                new_file = file_path.with_name(
                    f"{file_path.stem}_{counter}{ext_info['preferred']}"
                )
                counter += 1

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would fix: {file_path.name} -> {new_file.name} (format: {fmt})"
                )
            else:
                file_path.rename(new_file)
                logger.info(
                    f"Fixed: {file_path.name} -> {new_file.name} (format: {fmt})"
                )
            stats['fixed'] += 1

        except Exception:
            # Not an image or unreadable — skip silently
            continue

    return stats


# ---------------------------------------------------------------------------
# Convert to JPG
# ---------------------------------------------------------------------------

def convert_to_jpg_single(
    image_path: Path,
    quality: int = 95,
    delete_original: bool = False,
) -> tuple[Path | None, bool]:
    """
    Convert a single image to JPG format.

    Args:
        image_path: Path to image file
        quality: JPG quality (1-100, default: 95)
        delete_original: Delete original after conversion (default: False)

    Returns:
        Tuple of (new_path, original_deleted) or (None, False) on failure

    Notes:
        - Handles transparency by converting to white background
        - Automatically handles palette and RGBA modes
        - Generates unique filenames to avoid conflicts
    """
    try:
        # Use context manager so the file handle is released even if an
        # exception occurs or if the original is deleted afterwards.
        # On Windows, an unclosed PIL image holds a file lock that prevents
        # the source file from being moved or deleted.
        with Image.open(image_path) as _src:
            # Handle transparency / palette modes.  _src may be reassigned to
            # an in-memory copy; the context manager still closes the original.
            if _src.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', _src.size, (255, 255, 255))
                if _src.mode == 'P':
                    _src = _src.convert('RGBA')
                alpha = _src.split()[-1] if _src.mode in ('RGBA', 'LA') else None
                background.paste(_src, mask=alpha)
                img = background
            elif _src.mode != 'RGB':
                img = _src.convert('RGB')
            else:
                img = _src.copy()

        # Generate unique output filename
        new_path = image_path.with_suffix('.jpg')
        counter = 1
        while new_path.exists() and new_path != image_path:
            new_path = image_path.with_name(
                f"{image_path.stem}_{counter}.jpg"
            )
            counter += 1

        img.save(new_path, 'JPEG', quality=quality, optimize=True)

        deleted = False
        if delete_original and image_path.suffix.lower() != '.jpg':
            image_path.unlink()
            deleted = True

        return new_path, deleted

    except Exception as exc:
        logger.error(f"Error converting {image_path.name}: {exc}")
        return None, False


def batch_convert_to_jpg(
    directory: str | Path,
    quality: int = 95,
    delete_originals: bool = False,
    recursive: bool = True,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Convert all PNG/BMP/WEBP images to JPG format.

    Args:
        directory: Directory to scan
        quality: JPG quality (1-100, default: 95)
        delete_originals: Delete originals after conversion (default: False)
        recursive: Search subdirectories (default: True)
        dry_run: If True, only report what would be converted (default: False)

    Returns:
        Dictionary with statistics:
            - converted: Number of files converted
            - deleted: Number of originals deleted
            - failed: Number of conversion failures

    Examples:
        >>> stats = batch_convert_to_jpg('/photos', quality=90, dry_run=True)
        >>> print(f"Would convert {stats['converted']} files")

    Notes:
        - Only converts PNG, BMP, and WEBP formats
        - Handles transparency with white background
        - Progress bar shown during conversion
    """
    path = Path(directory)
    files = find_files(path, CONVERTIBLE_TO_JPG, recursive)
    stats = {'converted': 0, 'deleted': 0, 'failed': 0}

    if not files:
        logger.info("No convertible files found")
        return stats

    logger.info(f"Found {len(files)} file(s) to convert")

    for f in make_progress_bar(files, desc="Converting to JPG"):
        if dry_run:
            logger.info(f"[DRY RUN] Would convert: {f.name}")
            stats['converted'] += 1
            continue

        new_path, deleted = convert_to_jpg_single(f, quality, delete_originals)
        if new_path:
            logger.info(f"Converted: {f.name} -> {new_path.name}")
            stats['converted'] += 1
            if deleted:
                stats['deleted'] += 1
        else:
            stats['failed'] += 1

    return stats


__all__ = ['fix_extensions', 'batch_convert_to_jpg']
