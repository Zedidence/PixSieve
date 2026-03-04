"""
File discovery module for the scanner package.

Provides functionality to find and enumerate image files in directories,
with support for recursive scanning and HEIC/HEIF format detection.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Generator

from ..config import IMAGE_EXTENSIONS, DISCOVERY_CHUNK_SIZE
from .dependencies import HAS_HEIF_SUPPORT


logger = logging.getLogger(__name__)


def iter_image_chunks(
    root_path: str | Path,
    recursive: bool = True,
    resolve_symlinks: bool = True,
    chunk_size: int = DISCOVERY_CHUNK_SIZE,
) -> Generator[list[str], None, None]:
    """
    Yield successive chunks of image file paths found in the given directory.

    Unlike find_image_files(), this generator yields results progressively as
    files are discovered rather than materialising the entire file tree first.
    This keeps peak memory low for very large libraries (500k+ files) and
    allows the frontend to receive discovered-count updates before analysis
    is complete.

    Args:
        root_path: Directory path to search for images
        recursive: If True, search subdirectories recursively
        resolve_symlinks: If True (default), resolve symlinks to canonical paths
            and deduplicate files reachable via multiple symlinks.
        chunk_size: Number of paths to accumulate before yielding a chunk

    Yields:
        Lists of absolute file paths (as strings), each of length <= chunk_size
    """
    root = Path(root_path)

    extensions_to_scan = IMAGE_EXTENSIONS
    if not HAS_HEIF_SUPPORT:
        extensions_to_scan = {ext for ext in IMAGE_EXTENSIONS if ext not in {'.heic', '.heif'}}

    seen: set[str] = set()
    chunk: list[str] = []
    total_discovered = 0

    iterator = root.rglob('*') if recursive else root.glob('*')

    for filepath in iterator:
        if not filepath.is_file():
            continue
        if filepath.suffix.lower() not in extensions_to_scan:
            continue

        if resolve_symlinks:
            try:
                resolved = str(filepath.resolve())
            except OSError:
                # filepath.resolve() raises OSError on broken symlinks in
                # Python 3.10+.  Fall back to the un-resolved absolute path so
                # the file is still discovered rather than silently dropped.
                resolved = str(filepath.absolute())
        else:
            resolved = str(filepath.absolute())

        if resolved in seen:
            continue

        seen.add(resolved)
        chunk.append(resolved)
        total_discovered += 1

        if len(chunk) >= chunk_size:
            logger.debug(f"Discovery: yielding chunk of {len(chunk)} files ({total_discovered} total so far)")
            yield chunk
            chunk = []

    if chunk:
        logger.debug(f"Discovery: yielding final chunk of {len(chunk)} files ({total_discovered} total)")
        yield chunk


def find_image_files(
    root_path: str | Path,
    recursive: bool = True,
    resolve_symlinks: bool = True,
) -> list[str]:
    """
    Find all image files in the given directory.

    Args:
        root_path: Directory path to search for images
        recursive: If True, search subdirectories recursively
        resolve_symlinks: If True (default), resolve symlinks to canonical paths
            and deduplicate files reachable via multiple symlinks.
            Set to False on local drives without symlinks for a 5-15% speedup.

    Returns:
        List of absolute file paths as strings

    Notes:
        - Automatically filters out HEIC/HEIF files if pillow-heif is not installed
        - Handles symlinks by resolving to canonical paths (when resolve_symlinks=True)
        - Deduplicates files that may be encountered via multiple paths
        - Memory trade-off: all paths are accumulated in a single list before
          returning. For very large libraries (500k+ files) this can use
          100–150 MB. Use iter_image_chunks() directly to process files in
          streaming fashion without materialising the full list.
    """
    images: list[str] = []
    for chunk in iter_image_chunks(root_path, recursive=recursive, resolve_symlinks=resolve_symlinks):
        images.extend(chunk)
    return images


__all__ = ['find_image_files', 'iter_image_chunks']
