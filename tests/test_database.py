"""
Unit tests for database/caching module.
"""

import atexit
import pytest
import time
import os
import threading
from pathlib import Path
from pixsieve.database import ImageCache, CacheStats
from pixsieve.database.connection import ConnectionManager, _BackgroundWriter
from pixsieve.models import ImageInfo


class TestCacheStats:
    """Test CacheStats dataclass."""

    def test_hit_rate_calculation(self):
        """Test hit rate percentage calculation."""
        stats = CacheStats(cache_hits=75, cache_misses=25, total_files=100)
        assert stats.hit_rate == 75.0

    def test_hit_rate_no_files(self):
        """Test hit rate when no files."""
        stats = CacheStats(cache_hits=0, cache_misses=0, total_files=0)
        assert stats.hit_rate == 0.0

    def test_hit_rate_all_misses(self):
        """Test hit rate with all cache misses."""
        stats = CacheStats(cache_hits=0, cache_misses=100, total_files=100)
        assert stats.hit_rate == 0.0


class TestImageCache:
    """Test ImageCache class."""

    def test_initialization(self, temp_cache_db):
        """Test cache initialization creates database."""
        cache = ImageCache(db_path=temp_cache_db)
        assert os.path.exists(temp_cache_db)

    def test_put_and_get(self, temp_cache_db, sample_images):
        """Test caching and retrieving an image."""
        cache = ImageCache(db_path=temp_cache_db)

        # Create ImageInfo for a real file
        img_path = sample_images['unique']
        original_info = ImageInfo(
            path=img_path,
            file_size=os.path.getsize(img_path),
            width=100,
            height=100,
            pixel_count=10000,
            format="PNG",
            file_hash="test_hash_123",
            perceptual_hash="abcdef123456",
            quality_score=75.5
        )

        # Cache it
        result = cache.put(original_info)
        assert result is True

        # Retrieve it
        cached_info = cache.get(img_path)
        assert cached_info is not None
        assert cached_info.path == original_info.path
        assert cached_info.file_hash == original_info.file_hash
        assert cached_info.perceptual_hash == original_info.perceptual_hash
        assert cached_info.quality_score == original_info.quality_score

    def test_put_and_get_round_trips_sharpness_and_capture_date(self, temp_cache_db, sample_images):
        """sharpness_score/capture_date (content-aware quality scoring and
        EXIF-date sorting) must survive a cache put()/get() round trip -
        otherwise these only work for freshly-analyzed files and silently
        stop working the moment a scan hits a warm cache."""
        cache = ImageCache(db_path=temp_cache_db)
        img_path = sample_images['unique']
        original_info = ImageInfo(
            path=img_path,
            file_size=os.path.getsize(img_path),
            file_hash="test_hash_456",
            sharpness_score=1234.5,
            capture_date=1560594600.0,
        )

        assert cache.put(original_info) is True
        cached_info = cache.get(img_path)
        assert cached_info is not None
        assert cached_info.sharpness_score == 1234.5
        assert cached_info.capture_date == 1560594600.0

    def test_get_defaults_sharpness_and_capture_date_when_absent(self, temp_cache_db, sample_images):
        """A row written without sharpness_score/capture_date (simulating a
        pre-this-feature cache entry) should read back with safe defaults,
        not crash - same non-destructive-migration fallback already proven
        for media_type/duration in tests/test_video.py."""
        cache = ImageCache(db_path=temp_cache_db)
        img_path = sample_images['unique']
        info = ImageInfo(path=img_path, file_size=os.path.getsize(img_path))
        cache.put(info)

        cached_info = cache.get(img_path)
        assert cached_info.sharpness_score == 0.0
        assert cached_info.capture_date is None

    def test_get_nonexistent(self, temp_cache_db):
        """Test getting non-existent entry returns None."""
        cache = ImageCache(db_path=temp_cache_db)
        result = cache.get("/nonexistent/file.jpg")
        assert result is None

    def test_cache_invalidation_on_modification(self, temp_cache_db, temp_dir):
        """Test that cache is invalidated when file is modified."""
        cache = ImageCache(db_path=temp_cache_db)

        # Create a test file
        test_file = temp_dir / "test.txt"
        test_file.write_text("original")

        original_info = ImageInfo(
            path=str(test_file),
            file_size=os.path.getsize(test_file),
            file_hash="hash1"
        )

        cache.put(original_info)

        # Modify the file
        time.sleep(0.01)  # Ensure mtime changes
        test_file.write_text("modified content")

        # Should not return cached version (mtime changed)
        cached_info = cache.get(str(test_file))
        assert cached_info is None

    def test_cache_key_coincidence_serves_stale_data(self, temp_cache_db, temp_dir):
        """
        Documents a known, unmitigated limitation (see
        database/utils.py::make_cache_key's docstring and the duplicate-
        finding improvement plan, item #5): the cache key is path+mtime+size
        only, with no content-hash fallback. If a file's content changes
        while both mtime and size happen to stay identical, the cache has no
        way to detect it and serves stale data indefinitely. This is NOT a
        fix - it's a characterization test pinning today's real behavior, so
        a future content-hash-fallback fix has a concrete "before" to flip.
        """
        cache = ImageCache(db_path=temp_cache_db)
        test_file = temp_dir / "test.txt"
        test_file.write_text("original!")  # same length as the replacement below

        original_mtime = os.path.getmtime(test_file)
        original_info = ImageInfo(
            path=str(test_file),
            file_size=os.path.getsize(test_file),
            file_hash="hash-of-original-content",
        )
        cache.put(original_info)

        # Overwrite with same-length (different) content, then force mtime
        # back to its original value - simulating a tool that preserves
        # mtime after an in-place edit.
        test_file.write_text("different")
        assert os.path.getsize(test_file) == original_info.file_size  # same size
        os.utime(test_file, (original_mtime, original_mtime))         # same mtime

        cached_info = cache.get(str(test_file))
        assert cached_info is not None  # cache key coincidentally matches
        assert cached_info.file_hash == "hash-of-original-content"    # stale - the real gap

    def test_put_batch(self, temp_cache_db, sample_images):
        """Test batch caching."""
        cache = ImageCache(db_path=temp_cache_db)

        images = [
            ImageInfo(path=sample_images['unique'], file_size=os.path.getsize(sample_images['unique']),
                     file_hash="hash1"),
            ImageInfo(path=sample_images['identical1'], file_size=os.path.getsize(sample_images['identical1']),
                     file_hash="hash2"),
        ]

        count = cache.put_batch(images)
        assert count == 2

    def test_get_batch(self, temp_cache_db, sample_images):
        """Test batch retrieval."""
        cache = ImageCache(db_path=temp_cache_db)

        # Cache some images
        images = [
            ImageInfo(path=sample_images['unique'], file_size=os.path.getsize(sample_images['unique']),
                     file_hash="hash1"),
            ImageInfo(path=sample_images['identical1'], file_size=os.path.getsize(sample_images['identical1']),
                     file_hash="hash2"),
        ]
        cache.put_batch(images)

        # Retrieve them
        paths = [sample_images['unique'], sample_images['identical1']]
        results = cache.get_batch(paths)

        assert results[sample_images['unique']] is not None
        assert results[sample_images['identical1']] is not None
        assert results[sample_images['unique']].file_hash == "hash1"

    def test_invalidate(self, temp_cache_db, sample_images):
        """Test invalidating a specific file."""
        cache = ImageCache(db_path=temp_cache_db)

        img_path = sample_images['unique']
        info = ImageInfo(path=img_path, file_size=os.path.getsize(img_path), file_hash="hash1")

        cache.put(info)
        assert cache.get(img_path) is not None

        cache.invalidate(img_path)
        # After re-caching with same data, should work again
        cache.put(info)
        cached = cache.get(img_path)
        assert cached is not None

    def test_invalidate_directory_does_not_match_sibling_prefix(self, temp_cache_db, temp_dir):
        """Invalidating .../photos must not also remove a sibling .../photosBackup entry."""
        cache = ImageCache(db_path=temp_cache_db)

        photos_dir = temp_dir / "photos"
        backup_dir = temp_dir / "photosBackup"
        photos_dir.mkdir()
        backup_dir.mkdir()
        target = photos_dir / "img.jpg"
        sibling = backup_dir / "img.jpg"
        target.write_bytes(b"x")
        sibling.write_bytes(b"x")

        assert cache.put(ImageInfo(path=str(target), file_size=1))
        assert cache.put(ImageInfo(path=str(sibling), file_size=1))

        cache.invalidate_directory(str(photos_dir))

        assert cache.get(str(target)) is None
        assert cache.get(str(sibling)) is not None

    def test_invalidate_directory_escapes_like_wildcards(self, temp_cache_db, temp_dir):
        """A directory name containing a literal '%' must be treated literally, not as a wildcard."""
        cache = ImageCache(db_path=temp_cache_db)

        wildcard_dir = temp_dir / "100%_done"
        other_dir = temp_dir / "other"
        wildcard_dir.mkdir()
        other_dir.mkdir()
        wildcard_file = wildcard_dir / "img.jpg"
        unrelated_file = other_dir / "img.jpg"
        wildcard_file.write_bytes(b"x")
        unrelated_file.write_bytes(b"x")

        assert cache.put(ImageInfo(path=str(wildcard_file), file_size=1))
        assert cache.put(ImageInfo(path=str(unrelated_file), file_size=1))

        cache.invalidate_directory(str(wildcard_dir))

        assert cache.get(str(wildcard_file)) is None
        assert cache.get(str(unrelated_file)) is not None

    def test_cleanup_missing(self, temp_cache_db, temp_dir):
        """Test cleanup of entries for deleted files."""
        cache = ImageCache(db_path=temp_cache_db)

        # Create and cache a temporary file
        temp_file = temp_dir / "temp.txt"
        temp_file.write_text("test")

        info = ImageInfo(path=str(temp_file), file_size=os.path.getsize(temp_file))
        cache.put(info)

        # Delete the file
        temp_file.unlink()

        # Cleanup should remove the entry
        removed = cache.cleanup_missing()
        assert removed >= 1

    def test_get_stats(self, temp_cache_db, sample_images):
        """Test getting cache statistics."""
        cache = ImageCache(db_path=temp_cache_db)

        # Add some entries
        info = ImageInfo(path=sample_images['unique'], file_size=os.path.getsize(sample_images['unique']))
        cache.put(info)

        stats = cache.get_stats()
        assert 'total_entries' in stats
        assert 'db_size_mb' in stats
        assert 'db_path' in stats
        assert stats['total_entries'] >= 1

    def test_clear(self, temp_cache_db, sample_images):
        """Test clearing all cache data."""
        cache = ImageCache(db_path=temp_cache_db)

        # Add entries
        info = ImageInfo(path=sample_images['unique'], file_size=os.path.getsize(sample_images['unique']))
        cache.put(info)

        stats_before = cache.get_stats()
        assert stats_before['total_entries'] > 0

        # Clear
        cache.clear()

        stats_after = cache.get_stats()
        assert stats_after['total_entries'] == 0

    def test_put_nonexistent_file(self, temp_cache_db):
        """Test that putting info for nonexistent file fails gracefully."""
        cache = ImageCache(db_path=temp_cache_db)

        info = ImageInfo(path="/nonexistent/file.jpg", file_size=1000)
        result = cache.put(info)
        assert result is False


class TestBackgroundWriterShutdown:
    """
    Regression tests for a hang discovered while investigating the
    duplicate-scan performance work: ConnectionManager.__del__ used to call
    _BackgroundWriter.close(), which blocked forever on an un-timed-out
    threading.Event().wait() if the daemon writer thread had already been
    abandoned by interpreter shutdown (which happens *before* __del__ fires
    for module-level singletons like the global cache). See
    connection.py's flush()/close()/ConnectionManager docstrings.
    """

    def test_flush_returns_false_on_timeout_instead_of_hanging(self, temp_cache_db):
        """If nothing will ever acknowledge a flush, it must time out, not hang."""
        writer = _BackgroundWriter(temp_cache_db)
        # Simulate the writer thread having already been abandoned: stop it
        # via the normal path, then ask it to flush again.
        writer.close()

        start = time.monotonic()
        acknowledged = writer.flush(timeout=0.5)
        elapsed = time.monotonic() - start

        assert acknowledged is False
        assert elapsed < 2.0  # bounded by the timeout, not hanging indefinitely

    def test_flush_succeeds_while_writer_is_alive(self, temp_cache_db):
        writer = _BackgroundWriter(temp_cache_db)
        try:
            assert writer.flush(timeout=5.0) is True
        finally:
            writer.close()

    def test_close_is_idempotent_and_fast_the_second_time(self, temp_cache_db):
        """A second close() (e.g. atexit firing, then __del__ as a fallback)
        must not re-attempt (and re-time-out) a flush - it should be a
        near-instant no-op."""
        writer = _BackgroundWriter(temp_cache_db)
        writer.close()

        start = time.monotonic()
        writer.close()
        elapsed = time.monotonic() - start

        assert elapsed < 1.0

    def test_connection_manager_registers_and_unregisters_atexit_hook(self, temp_cache_db):
        """ConnectionManager should register its writer's close() with atexit
        on creation, and remove that registration once explicitly cleaned up
        via __del__ - otherwise every instance created during a long-running
        process (or a large test suite) would be kept alive by atexit's
        internal reference forever."""
        before = atexit._ncallbacks()
        mgr = ConnectionManager(temp_cache_db)
        assert atexit._ncallbacks() == before + 1

        mgr.__del__()  # simulate GC-triggered cleanup
        assert atexit._ncallbacks() == before

        # A second __del__ (which real GC won't normally trigger, but the
        # method must tolerate) should not raise or hang.
        start = time.monotonic()
        mgr.__del__()
        assert time.monotonic() - start < 1.0
        assert atexit._ncallbacks() == before
