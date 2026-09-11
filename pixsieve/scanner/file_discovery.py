"""
File discovery module for the scanner package.

Provides functionality to find and enumerate image files in directories,
with support for recursive scanning and HEIC/HEIF format detection.
"""

from __future__ import annotations

import logging
import stat as stat_module
from pathlib import Path
from typing import Generator

from ..config import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, DISCOVERY_CHUNK_SIZE
from .dependencies import HAS_HEIF_SUPPORT, HAS_VIDEO_SUPPORT


logger = logging.getLogger(__name__)


def _scan_extensions(include_videos: bool = False) -> set[str]:
    extensions = IMAGE_EXTENSIONS if HAS_HEIF_SUPPORT else {
        ext for ext in IMAGE_EXTENSIONS if ext not in {'.heic', '.heif'}
    }
    if include_videos:
        if HAS_VIDEO_SUPPORT:
            extensions = extensions | VIDEO_EXTENSIONS
        else:
            logger.warning(
                "include_videos requested but opencv-python-headless is not "
                "installed - scanning images only. Install with: "
                "pip install opencv-python-headless (or pip install pixsieve[video])"
            )
    return extensions


def _stat_regular_file(filepath: Path) -> object | None:
    """
    Return the os.stat_result for filepath if it exists and is a regular
    file, else None (covers missing files, broken symlinks, directories,
    and other non-file entries - same cases Path.is_file() would filter,
    but via a single stat() call we can also reuse for identity below).
    """
    try:
        st = filepath.stat()
    except OSError:
        return None
    return st if stat_module.S_ISREG(st.st_mode) else None


def _file_identity(st) -> tuple[int, int] | None:
    """
    Return (st_dev, st_ino) for an already-stat()'d file, or None if the
    platform/filesystem doesn't report a usable inode (st_ino == 0 - some
    filesystems/platforms use 0 to mean "unknown", and treating it as a real
    shared identity would incorrectly merge unrelated files).

    This exists to dedupe HARDLINKS: multiple directory entries pointing at
    the same inode. Path.resolve() (used for symlink-path dedup above) does
    NOT collapse hardlinks - a hardlinked path is already a distinct,
    non-symlink path pointing at the same underlying file - so without this
    check, a hardlinked photo reachable via two paths is discovered/analyzed
    twice and unconditionally reported as a false "exact duplicate" (its
    SHA-256 naturally matches itself). On Windows, Python populates
    st_dev/st_ino via GetFileInformationByHandle (reliable for local NTFS
    volumes since Python 3.5); network shares or unusual filesystems may
    still report 0, in which case this safely falls back to the pre-existing
    path-based dedup only.

    As a side effect, this also catches multiple SYMLINKS pointing at the
    same target file even when resolve_symlinks=False: filepath.stat()
    follows symlinks by default (like os.stat), so the identity captured is
    always the ultimate target's, independent of the resolve_symlinks flag
    or how many symlink hops were involved.
    """
    if st.st_ino == 0:
        return None
    return (st.st_dev, st.st_ino)


def _resolve_or_absolute(filepath: Path, resolve_symlinks: bool) -> str:
    if resolve_symlinks:
        try:
            return str(filepath.resolve())
        except OSError:
            # filepath.resolve() raises OSError on broken symlinks in
            # Python 3.10+.  Fall back to the un-resolved absolute path so
            # the file is still discovered rather than silently dropped.
            return str(filepath.absolute())
    return str(filepath.absolute())


def iter_image_chunks(
    root_path: str | Path,
    recursive: bool = True,
    resolve_symlinks: bool = True,
    chunk_size: int = DISCOVERY_CHUNK_SIZE,
    include_videos: bool = False,
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
        include_videos: If True, also discover VIDEO_EXTENSIONS files (requires
            opencv-python-headless; falls back to images-only with a warning
            if not installed).

    Yields:
        Lists of absolute file paths (as strings), each of length <= chunk_size

    Notes:
        - Deduplicates by resolved path (when resolve_symlinks=True) AND,
          always regardless of resolve_symlinks, by underlying file identity
          - which also covers symlinked and hardlinked paths pointing at the
          same file. See _file_identity().
    """
    root = Path(root_path)
    extensions_to_scan = _scan_extensions(include_videos=include_videos)

    seen: set[str] = set()
    seen_identities: set[tuple[int, int]] = set()
    chunk: list[str] = []
    total_discovered = 0

    iterator = root.rglob('*') if recursive else root.glob('*')

    for filepath in iterator:
        st = _stat_regular_file(filepath)
        if st is None:
            continue
        if filepath.suffix.lower() not in extensions_to_scan:
            continue

        resolved = _resolve_or_absolute(filepath, resolve_symlinks)
        if resolved in seen:
            continue

        identity = _file_identity(st)
        if identity is not None and identity in seen_identities:
            continue

        seen.add(resolved)
        if identity is not None:
            seen_identities.add(identity)
        chunk.append(resolved)
        total_discovered += 1

        if len(chunk) >= chunk_size:
            logger.debug(f"Discovery: yielding chunk of {len(chunk)} files ({total_discovered} total so far)")
            yield chunk
            chunk = []

    if chunk:
        logger.debug(f"Discovery: yielding final chunk of {len(chunk)} files ({total_discovered} total)")
        yield chunk


def iter_image_chunks_multi(
    roots: list[tuple[str | Path, bool]],
    recursive: bool = True,
    resolve_symlinks: bool = True,
    chunk_size: int = DISCOVERY_CHUNK_SIZE,
    include_videos: bool = False,
) -> Generator[list[tuple[str, bool]], None, None]:
    """
    Like iter_image_chunks(), but walks multiple roots with ONE shared `seen`
    set so a file reachable from two overlapping/nested roots is only yielded
    once - attributed to whichever root's walk reaches it first.

    Args:
        roots: List of (root_path, is_reference) tuples. Callers should place
            the reference root (if any) first so that files under a nested or
            overlapping reference folder are always attributed as reference,
            never claimed first by an outer non-reference root.
        recursive: If True, search subdirectories recursively
        resolve_symlinks: If True (default), resolve symlinks to canonical paths
        chunk_size: Number of paths to accumulate before yielding a chunk
        include_videos: If True, also discover VIDEO_EXTENSIONS files (requires
            opencv-python-headless; falls back to images-only with a warning
            if not installed).

    Yields:
        Lists of (absolute_path, is_reference) tuples, each of length <= chunk_size

    Notes:
        - Deduplicates by resolved path (when resolve_symlinks=True) AND,
          always regardless of resolve_symlinks, by underlying file identity
          - which also covers symlinked and hardlinked paths pointing at the
          same file - shared across ALL roots the same way path-based dedup
          already is. See _file_identity().
    """
    extensions_to_scan = _scan_extensions(include_videos=include_videos)

    # Hardening for the "reference root must be listed first" contract
    # described above: it's currently enforced only by caller discipline, not
    # structurally. A non-reference root appearing before a reference root
    # can't corrupt anything within a single root's own files, but for
    # overlapping/nested roots it silently mis-attributes is_reference to
    # whichever root's walk reaches a shared file first - exactly backwards
    # from the intended "reference wins" guarantee. This can't be fixed
    # generically here (checking real overlap would need a filesystem walk
    # of its own), so just warn when the ordering itself looks wrong.
    seen_non_reference = False
    for _root_path, _is_reference in roots:
        if not _is_reference:
            seen_non_reference = True
        elif seen_non_reference:
            logger.warning(
                "iter_image_chunks_multi: a reference root appears after a "
                "non-reference root in `roots` - if they overlap or nest, "
                "files may be mis-attributed as non-reference. Callers "
                "should list the reference root first."
            )
            break

    seen: set[str] = set()
    seen_identities: set[tuple[int, int]] = set()
    chunk: list[tuple[str, bool]] = []
    total_discovered = 0

    for root_path, is_reference in roots:
        root = Path(root_path)
        iterator = root.rglob('*') if recursive else root.glob('*')

        for filepath in iterator:
            st = _stat_regular_file(filepath)
            if st is None:
                continue
            if filepath.suffix.lower() not in extensions_to_scan:
                continue

            resolved = _resolve_or_absolute(filepath, resolve_symlinks)
            if resolved in seen:
                continue

            identity = _file_identity(st)
            if identity is not None and identity in seen_identities:
                continue

            seen.add(resolved)
            if identity is not None:
                seen_identities.add(identity)
            chunk.append((resolved, is_reference))
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
    include_videos: bool = False,
) -> list[str]:
    """
    Find all image files in the given directory.

    Args:
        root_path: Directory path to search for images
        recursive: If True, search subdirectories recursively
        resolve_symlinks: If True (default), resolve symlinks to canonical paths
            and deduplicate files reachable via multiple symlinks.
            Set to False on local drives without symlinks for a 5-15% speedup.
        include_videos: If True, also discover VIDEO_EXTENSIONS files (requires
            opencv-python-headless; falls back to images-only with a warning
            if not installed).

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
    for chunk in iter_image_chunks(
        root_path, recursive=recursive, resolve_symlinks=resolve_symlinks, include_videos=include_videos
    ):
        images.extend(chunk)
    return images


def find_image_files_multi(
    roots: list[tuple[str | Path, bool]],
    recursive: bool = True,
    resolve_symlinks: bool = True,
    include_videos: bool = False,
) -> list[tuple[str, bool]]:
    """
    Non-streaming wrapper around iter_image_chunks_multi() - materializes all
    (path, is_reference) tuples across all roots into a single list.

    Args:
        roots: List of (root_path, is_reference) tuples; reference root first.
        recursive: If True, search subdirectories recursively
        resolve_symlinks: If True (default), resolve symlinks to canonical paths
        include_videos: If True, also discover VIDEO_EXTENSIONS files (requires
            opencv-python-headless; falls back to images-only with a warning
            if not installed).

    Returns:
        List of (absolute_path, is_reference) tuples
    """
    images: list[tuple[str, bool]] = []
    for chunk in iter_image_chunks_multi(
        roots, recursive=recursive, resolve_symlinks=resolve_symlinks, include_videos=include_videos
    ):
        images.extend(chunk)
    return images


__all__ = [
    'find_image_files',
    'find_image_files_multi',
    'iter_image_chunks',
    'iter_image_chunks_multi',
]
