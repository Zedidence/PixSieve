"""
Database schema initialization and migrations.

Provides schema versioning and table creation for the cache database.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

_DUPLICATE_COLUMN_MARKER = "duplicate column name"


# Schema version - increment when changing table structure OR when a change
# alters the VALUE an existing column should hold for already-cached rows
# (structural changes need this to run migrations; value changes need it to
# force re-analysis, since there is no way to selectively recompute just the
# affected rows without re-decoding every cached file anyway).
#
# v2: scanner/hashing.py's _ensure_phash_mode() now applies EXIF-orientation
# normalization before hashing (ImageOps.exif_transpose()), which changes the
# perceptual_hash VALUE for any already-cached image whose EXIF Orientation
# tag was not 1. Serving those stale hashes from the cache would silently
# defeat the fix for every returning user with a warm cache - the whole
# reason this fix exists is to catch EXIF-rotated duplicate photos, and a
# warm cache is the common case for a returning user. A full re-analysis on
# next scan is a one-time, deliberate cost.
SCHEMA_VERSION = 2


def _add_column_if_missing(conn: sqlite3.Connection, alter_sql: str) -> None:
    """
    Run an ALTER TABLE ... ADD COLUMN statement, tolerating only the
    "column already exists" case.

    A bare `except Exception: pass` here would silently swallow genuine
    failures (disk full, permission denied, a locked or corrupted database)
    identically to the expected no-op case, leaving the schema in an
    unexpected state with no trace in the logs. Narrowing to
    OperationalError and checking the message keeps the no-op behavior for
    the one case it's meant for while surfacing everything else.
    """
    try:
        conn.execute(alter_sql)
    except sqlite3.OperationalError as exc:
        if _DUPLICATE_COLUMN_MARKER not in str(exc).lower():
            logger.warning(f"Schema migration '{alter_sql}' failed unexpectedly: {exc}")


def initialize_schema(conn: sqlite3.Connection) -> None:
    """
    Initialize database schema with versioning support.

    Creates tables and indexes if they don't exist. Drops and recreates
    tables if schema version has changed.

    Args:
        conn: Active database connection

    Tables created:
        - meta: Schema version tracking
        - images: Cached image analysis results
        - scan_history: Directory scan tracking
    """
    # Create meta table for schema versioning
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Check current schema version
    result = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()

    current_version = int(result['value']) if result else 0

    # Drop and recreate tables if schema changed
    if current_version < SCHEMA_VERSION:
        conn.execute("DROP TABLE IF EXISTS images")
        conn.execute("DROP TABLE IF EXISTS scan_history")

    # Main images table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            cache_key TEXT UNIQUE NOT NULL,

            -- Image metadata
            width INTEGER,
            height INTEGER,
            pixel_count INTEGER,
            bit_depth INTEGER,
            format TEXT,

            -- Hashes
            file_hash TEXT,
            perceptual_hash TEXT,

            -- Computed
            quality_score REAL,
            error TEXT,

            -- Timestamps
            created_at REAL DEFAULT (strftime('%s', 'now')),
            last_accessed REAL DEFAULT (strftime('%s', 'now'))
        )
    """)

    # Indexes for common queries
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_images_cache_key
        ON images(cache_key)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_images_path
        ON images(path)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_images_file_hash
        ON images(file_hash)
    """)
    # F4: extended prefix index — 16 hex chars (64 bits) instead of 8 (32 bits)
    # for better selectivity on phash queries. Drop the old 8-char index if it
    # exists so it is replaced non-destructively (no schema version bump needed).
    conn.execute("DROP INDEX IF EXISTS idx_images_phash_prefix")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_images_phash_prefix
        ON images(substr(perceptual_hash, 1, 16))
    """)

    # Scan history for tracking directories
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            directory TEXT NOT NULL,
            file_count INTEGER,
            scan_time REAL,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)

    # G1: add dominant_color column non-destructively (ALTER TABLE is safe on
    # existing databases; silently ignored if the column already exists).
    _add_column_if_missing(conn, "ALTER TABLE images ADD COLUMN dominant_color TEXT")

    # Video support: add media_type/duration columns non-destructively, same
    # as dominant_color above. Deliberately NOT a SCHEMA_VERSION bump - that
    # would DROP and rebuild the whole images table above, forcing a full
    # re-analysis of every cached file (including images) on next scan.
    _add_column_if_missing(conn, "ALTER TABLE images ADD COLUMN media_type TEXT DEFAULT 'image'")
    _add_column_if_missing(conn, "ALTER TABLE images ADD COLUMN duration REAL DEFAULT 0")

    # Content-aware quality scoring / EXIF-capture-date sorting: add
    # sharpness_score/capture_date non-destructively, same as above - purely
    # additive fields (absent = "not computed yet", not "wrong"), so unlike
    # the SCHEMA_VERSION bump above (which corrects an existing value), no
    # forced re-analysis is needed for these to take effect.
    _add_column_if_missing(conn, "ALTER TABLE images ADD COLUMN sharpness_score REAL DEFAULT 0")
    _add_column_if_missing(conn, "ALTER TABLE images ADD COLUMN capture_date REAL DEFAULT NULL")

    # Update schema version
    conn.execute("""
        INSERT OR REPLACE INTO meta (key, value)
        VALUES ('schema_version', ?)
    """, (str(SCHEMA_VERSION),))


__all__ = ['SCHEMA_VERSION', 'initialize_schema']
