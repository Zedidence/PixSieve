"""
Unit tests for pixsieve/operations/metadata.py.
"""

import pytest
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from PIL import Image

from pixsieve.operations.metadata import (
    random_date_in_range,
    randomize_dates,
    randomize_dates_per_folder,
)


class TestRandomDateInRange:
    """Test random_date_in_range function."""

    def test_date_within_range(self):
        """Generated date falls within the specified range."""
        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)

        for _ in range(20):
            result = random_date_in_range(start, end)
            assert start <= result <= end + timedelta(days=1)

    def test_same_start_end(self):
        """Same start and end returns a date on that day."""
        day = datetime(2023, 6, 15)
        result = random_date_in_range(day, day)
        assert result.date() == day.date()


class TestRandomizeDates:
    """Test randomize_dates function."""

    def test_stats_keys(self, temp_dir):
        """Returns dict with expected keys."""
        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_dates(temp_dir, start, end, dry_run=True)
        assert 'success' in stats
        assert 'failed' in stats

    def test_dry_run_no_change(self, temp_dir):
        """Dry-run counts files but doesn't modify EXIF or file timestamps."""
        path = temp_dir / "photo.jpg"
        Image.new('RGB', (10, 10), 'red').save(path, 'JPEG')
        original_mtime = os.path.getmtime(path)

        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_dates(temp_dir, start, end, dry_run=True)
        assert stats['success'] == 1
        assert os.path.getmtime(path) == original_mtime

    def test_empty_directory(self, temp_dir):
        """Empty directory returns zero stats."""
        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_dates(temp_dir, start, end, dry_run=True)
        assert stats['success'] == 0
        assert stats['failed'] == 0

    def test_actual_writes_exif_and_mtime(self, temp_dir):
        """With sync_exif=True, writes EXIF dates and file timestamps for JPEGs."""
        path = temp_dir / "photo.jpg"
        Image.new('RGB', (10, 10), 'red').save(path, 'JPEG')

        start = datetime(2000, 1, 1)
        end = datetime(2000, 12, 31)
        stats = randomize_dates(temp_dir, start, end, dry_run=False, sync_exif=True)
        assert stats['success'] == 1

        import piexif
        exif_data = piexif.load(str(path))
        assert piexif.ExifIFD.DateTimeOriginal in exif_data['Exif']

        new_dt = datetime.fromtimestamp(os.path.getmtime(path))
        assert start <= new_dt <= datetime(2001, 1, 1)

    def test_sync_exif_false_only_changes_file_times(self, temp_dir):
        """With sync_exif=False, only filesystem timestamps change, not EXIF."""
        path = temp_dir / "photo.jpg"
        Image.new('RGB', (10, 10), 'red').save(path, 'JPEG')
        original_mtime = os.path.getmtime(path)

        start = datetime(2000, 1, 1)
        end = datetime(2000, 12, 31)
        stats = randomize_dates(temp_dir, start, end, dry_run=False, sync_exif=False)
        assert stats['success'] == 1
        assert os.path.getmtime(path) != original_mtime

        import piexif
        exif_data = piexif.load(str(path))
        assert piexif.ExifIFD.DateTimeOriginal not in exif_data['Exif']

    def test_non_exif_formats_only_get_file_times(self, temp_dir):
        """Non-EXIF-compatible formats (e.g. PNG) still get file timestamps updated."""
        jpg_path = temp_dir / "photo.jpg"
        png_path = temp_dir / "photo.png"
        Image.new('RGB', (10, 10), 'red').save(jpg_path, 'JPEG')
        Image.new('RGB', (10, 10), 'blue').save(png_path, 'PNG')

        start = datetime(2000, 1, 1)
        end = datetime(2000, 12, 31)
        stats = randomize_dates(temp_dir, start, end, dry_run=False, sync_exif=True)
        assert stats['success'] == 2  # both files processed (file times)

        new_dt = datetime.fromtimestamp(os.path.getmtime(png_path))
        assert start <= new_dt <= datetime(2001, 1, 1)


class TestRandomizeDatesVideoExtensions:
    """extensions param can include video files; EXIF sync must still skip them."""

    def test_default_extensions_excludes_video(self, temp_dir):
        (temp_dir / "clip.mp4").write_bytes(b"placeholder, not a real video")

        start = datetime(2000, 1, 1)
        end = datetime(2000, 12, 31)
        stats = randomize_dates(temp_dir, start, end, dry_run=True)
        assert stats['success'] == 0  # video not scanned by default

    def test_include_videos_updates_file_times_but_never_writes_exif(self, temp_dir):
        """A video file included via `extensions` gets its mtime randomized,
        but sync_exif=True must never route it through set_exif_dates
        (piexif has no video support)."""
        from pixsieve.config import IMAGE_EXTENSIONS, resolve_extensions

        video_path = temp_dir / "clip.mp4"
        video_path.write_bytes(b"placeholder, not a real video")
        original_mtime = os.path.getmtime(video_path)

        start = datetime(2000, 1, 1)
        end = datetime(2000, 12, 31)
        extensions = resolve_extensions(IMAGE_EXTENSIONS, include_videos=True)

        with mock.patch("pixsieve.operations.metadata.set_exif_dates") as mock_set_exif:
            stats = randomize_dates(
                temp_dir, start, end, dry_run=False, sync_exif=True, extensions=extensions,
            )

        mock_set_exif.assert_not_called()
        assert stats['success'] == 1
        assert stats['failed'] == 0
        assert os.path.getmtime(video_path) != original_mtime


class TestRandomizeDatesPerFolder:
    """Test randomize_dates_per_folder function."""

    def test_stats_structure(self, temp_dir):
        """Returns dict with totals and a per-folder breakdown."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "photo.jpg", 'JPEG')

        folder_ranges = [{
            'folder': str(temp_dir),
            'startDate': datetime(2000, 1, 1),
            'endDate': datetime(2000, 12, 31),
        }]
        stats = randomize_dates_per_folder(folder_ranges, dry_run=True)
        assert stats['success'] == 1
        assert stats['failed'] == 0
        assert temp_dir.name in stats['folders']
        assert stats['folders'][temp_dir.name]['total'] == 1

    def test_empty_folder(self, temp_dir):
        """A folder with no images contributes zero stats."""
        folder_ranges = [{
            'folder': str(temp_dir),
            'startDate': datetime(2000, 1, 1),
            'endDate': datetime(2000, 12, 31),
        }]
        stats = randomize_dates_per_folder(folder_ranges, dry_run=True)
        assert stats['success'] == 0
        assert stats['folders'][temp_dir.name]['total'] == 0

    def test_exif_write_failure_counted_as_failed(self, temp_dir):
        """A False from set_exif_dates must propagate to failed, not be silently
        over-reported as success (mirrors randomize_dates()'s behavior)."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "photo.jpg", 'JPEG')

        folder_ranges = [{
            'folder': str(temp_dir),
            'startDate': datetime(2000, 1, 1),
            'endDate': datetime(2000, 12, 31),
        }]

        with mock.patch("pixsieve.operations.metadata.set_exif_dates", return_value=False):
            stats = randomize_dates_per_folder(folder_ranges, dry_run=False, sync_exif=True)

        assert stats['success'] == 0
        assert stats['failed'] == 1
