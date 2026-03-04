"""
Core CRUD operations for the image cache.

Provides CacheOperations class for single and batch operations.
"""

from __future__ import annotations

import os
import logging
from typing import Optional

from ..config import WRITE_BATCH_SIZE
from ..models import ImageInfo
from .connection import ConnectionManager
from .utils import make_cache_key, get_file_stats, row_to_imageinfo, CHUNK_SIZE


logger = logging.getLogger(__name__)

# Shared INSERT statement used by put(), put_batch(), and put_async()
_INSERT_SQL = """
    INSERT OR REPLACE INTO images (
        path, file_size, mtime, cache_key,
        width, height, pixel_count, bit_depth, format,
        file_hash, perceptual_hash, quality_score,
        dominant_color, error
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class CacheOperations:
    """
    Handles CRUD operations for the image cache.

    Provides single and batch operations for retrieving, storing, and
    invalidating cached image analysis results.
    """

    def __init__(self, connection_manager: ConnectionManager):
        """
        Initialize cache operations.

        Args:
            connection_manager: ConnectionManager instance for database access
        """
        self.conn_mgr = connection_manager

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, filepath: str) -> Optional[ImageInfo]:
        """
        Get cached image info if available and still valid.

        Args:
            filepath: Path to the image file

        Returns:
            ImageInfo if cached and valid, None otherwise
        """
        try:
            if not os.path.exists(filepath):
                return None

            mtime, size = get_file_stats(filepath)
            cache_key = make_cache_key(filepath, mtime, size)

            # Read operations don't need exclusive lock
            with self.conn_mgr.connection(exclusive=False) as conn:
                row = conn.execute("""
                    SELECT * FROM images WHERE cache_key = ?
                """, (cache_key,)).fetchone()

                if row:
                    info = row_to_imageinfo(row)

            if row:
                # F1: async last_accessed update — non-critical, no need to block
                def _touch(conn, _key=cache_key):
                    conn.execute(
                        "UPDATE images SET last_accessed = strftime('%s', 'now') WHERE cache_key = ?",
                        (_key,),
                    )
                self.conn_mgr.enqueue_write(_touch)
                return info

            return None

        except Exception as e:
            logger.debug(f"Failed to get cached info for {filepath}: {e}")
            return None

    def get_batch(self, filepaths: list[str]) -> dict[str, Optional[ImageInfo]]:
        """
        Get cached info for multiple files efficiently.

        Flushes any pending background writes first so callers always see
        a consistent view regardless of whether put_batch() / put_async()
        was used (F1 guarantee).

        Args:
            filepaths: List of file paths

        Returns:
            Dict mapping filepath to ImageInfo (or None if not cached)
        """
        # F1: ensure all async writes are visible before reading
        self.conn_mgr.flush_writes()

        results: dict[str, Optional[ImageInfo]] = {fp: None for fp in filepaths}

        try:
            # Build cache keys for files that exist
            cache_keys = {}
            for fp in filepaths:
                try:
                    if os.path.exists(fp):
                        mtime, size = get_file_stats(fp)
                        cache_keys[make_cache_key(fp, mtime, size)] = fp
                except Exception:
                    continue

            if not cache_keys:
                return results

            # Process in chunks to avoid SQLite variable limit
            cache_key_list = list(cache_keys.keys())
            all_hit_keys = []

            with self.conn_mgr.connection(exclusive=False) as conn:
                for i in range(0, len(cache_key_list), CHUNK_SIZE):
                    chunk = cache_key_list[i:i + CHUNK_SIZE]
                    placeholders = ','.join('?' * len(chunk))

                    rows = conn.execute(f"""
                        SELECT * FROM images WHERE cache_key IN ({placeholders})
                    """, chunk).fetchall()

                    for row in rows:
                        filepath = cache_keys.get(row['cache_key'])
                        if filepath is not None:
                            results[filepath] = row_to_imageinfo(row)
                            all_hit_keys.append(row['cache_key'])

        except Exception as e:
            logger.warning(f"Error during batch retrieval: {e}")
            return results

        # F1: async last_accessed updates — batch into one write per CHUNK_SIZE
        if all_hit_keys:
            for i in range(0, len(all_hit_keys), CHUNK_SIZE):
                chunk = all_hit_keys[i:i + CHUNK_SIZE]

                def _touch_batch(conn, _chunk=chunk):
                    placeholders = ','.join('?' * len(_chunk))
                    conn.execute(
                        f"UPDATE images SET last_accessed = strftime('%s', 'now') "
                        f"WHERE cache_key IN ({placeholders})",
                        _chunk,
                    )

                self.conn_mgr.enqueue_write(_touch_batch)

        return results

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def put(self, info: ImageInfo) -> bool:
        """
        Cache an ImageInfo object (synchronous).

        Args:
            info: ImageInfo to cache

        Returns:
            True if successfully cached
        """
        try:
            if not os.path.exists(info.path):
                return False

            mtime, size = get_file_stats(info.path)
            cache_key = make_cache_key(info.path, mtime, size)

            # Write operations use exclusive lock
            with self.conn_mgr.connection(exclusive=True) as conn:
                conn.execute(_INSERT_SQL, (
                    info.path, info.file_size, mtime, cache_key,
                    info.width, info.height, info.pixel_count,
                    info.bit_depth, info.format,
                    info.file_hash, info.perceptual_hash,
                    info.quality_score, info.dominant_color, info.error
                ))

            return True

        except Exception as e:
            logger.debug(f"Failed to cache image info for {info.path}: {e}")
            return False

    def put_async(self, info: ImageInfo) -> None:
        """
        I1: Non-blocking cache write via the F1 background writer.

        Pre-computes file stats in the calling thread (cheap os.stat call),
        then hands the row data to the background writer. Returns immediately.
        Call flush_writes() (or get_batch()) when the written data must be
        visible to subsequent reads.

        Args:
            info: ImageInfo to cache
        """
        try:
            if not os.path.exists(info.path):
                return

            mtime, size = get_file_stats(info.path)
            cache_key = make_cache_key(info.path, mtime, size)
            row = (
                info.path, info.file_size, mtime, cache_key,
                info.width, info.height, info.pixel_count,
                info.bit_depth, info.format,
                info.file_hash, info.perceptual_hash,
                info.quality_score, info.dominant_color, info.error,
            )

            def _write(conn, _row=row):
                conn.execute(_INSERT_SQL, _row)

            self.conn_mgr.enqueue_write(_write)

        except Exception as e:
            logger.debug(f"Failed to queue async write for {info.path}: {e}")

    def put_batch(self, images: list[ImageInfo]) -> int:
        """
        F1: Cache multiple ImageInfo objects via the background writer.

        Rows are pre-computed in the calling thread (os.stat calls) and
        handed off as write callables — one per WRITE_BATCH_SIZE chunk.
        The background writer batches them into a single transaction,
        eliminating per-chunk lock acquisition overhead at high worker counts.

        Args:
            images: List of ImageInfo objects

        Returns:
            Number of rows successfully queued for writing
        """
        total_queued = 0

        for batch_start in range(0, len(images), WRITE_BATCH_SIZE):
            chunk = images[batch_start:batch_start + WRITE_BATCH_SIZE]
            rows = self._precompute_rows(chunk)
            if not rows:
                continue

            def _write(conn, _rows=rows):
                for row in _rows:
                    try:
                        conn.execute(_INSERT_SQL, row)
                    except Exception:
                        pass

            self.conn_mgr.enqueue_write(_write)
            total_queued += len(rows)

        return total_queued

    def set_dominant_color(self, filepath: str, color_str: str) -> bool:
        """
        G1: Update just the dominant_color field for a cached image.

        Used by sort.py to store K-means results so subsequent sorts skip
        re-computation. Safe to call even if the image is not yet cached;
        in that case it silently does nothing.

        Args:
            filepath: Path to the image file
            color_str: Dominant color as "R,G,B" string

        Returns:
            True if a row was updated (approximate — update is async)
        """
        def _write(conn, _path=filepath, _color=color_str):
            conn.execute(
                "UPDATE images SET dominant_color = ? WHERE path = ?",
                (_color, _path),
            )

        self.conn_mgr.enqueue_write(_write)
        return True  # optimistically true; async so we can't check rowcount here

    def set_dominant_color_batch(self, updates: list[tuple[str, str]]) -> bool:
        """
        Batch-update dominant_color for multiple images in a single transaction.

        Reduces N round-trips to the background writer queue down to one
        executemany call, which is more efficient for large sort operations.

        Args:
            updates: List of (color_str, filepath) tuples

        Returns:
            True (optimistic; update is async)
        """
        if not updates:
            return True

        def _write(conn, _updates=updates):
            conn.executemany(
                "UPDATE images SET dominant_color = ? WHERE path = ?",
                _updates,
            )

        self.conn_mgr.enqueue_write(_write)
        return True

    def invalidate(self, filepath: str):
        """
        Remove a specific file from the cache.

        Args:
            filepath: Path to the file to invalidate
        """
        try:
            with self.conn_mgr.connection(exclusive=True) as conn:
                conn.execute("DELETE FROM images WHERE path = ?", (filepath,))
        except Exception as e:
            logger.debug(f"Failed to invalidate cache for {filepath}: {e}")

    def invalidate_directory(self, directory: str):
        """
        Remove all cached entries for files in a directory.

        Args:
            directory: Directory path
        """
        try:
            with self.conn_mgr.connection(exclusive=True) as conn:
                conn.execute(
                    "DELETE FROM images WHERE path LIKE ?",
                    (f"{directory}%",)
                )
        except Exception as e:
            logger.debug(f"Failed to invalidate cache for directory {directory}: {e}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _precompute_rows(self, images: list[ImageInfo]) -> list[tuple]:
        """
        Pre-compute DB row tuples for a batch of ImageInfo objects.

        Called in the submitting thread so os.stat / make_cache_key work
        against the current filesystem state before the background writer
        picks up the callable.
        """
        rows = []
        for info in images:
            try:
                if not os.path.exists(info.path):
                    continue
                mtime, size = get_file_stats(info.path)
                cache_key = make_cache_key(info.path, mtime, size)
                rows.append((
                    info.path, info.file_size, mtime, cache_key,
                    info.width, info.height, info.pixel_count,
                    info.bit_depth, info.format,
                    info.file_hash, info.perceptual_hash,
                    info.quality_score, info.dominant_color, info.error,
                ))
            except Exception:
                continue
        return rows


__all__ = ['CacheOperations']
