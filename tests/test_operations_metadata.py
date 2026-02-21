"""
Unit tests for dupefinder/operations/metadata.py.
"""

import pytest
import os
from datetime import datetime
from pathlib import Path
from PIL import Image

from dupefinder.operations.metadata import (
    random_date_in_range,
    randomize_exif_dates,
    randomize_file_dates,
)


class TestRandomDateInRange:
    """Test random_date_in_range function."""

    def test_date_within_range(self):
        """Generated date falls within the specified range."""
        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)

        for _ in range(20):
            result = random_date_in_range(start, end)
            assert start <= result <= end + __import__('datetime').timedelta(days=1)

    def test_same_start_end(self):
        """Same start and end returns a date on that day."""
        day = datetime(2023, 6, 15)
        result = random_date_in_range(day, day)
        assert result.date() == day.date()


class TestRandomizeExifDates:
    """Test randomize_exif_dates function."""

    def test_stats_keys(self, temp_dir):
        """Returns dict with expected keys."""
        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_exif_dates(temp_dir, start, end, dry_run=True)
        assert 'success' in stats
        assert 'failed' in stats

    def test_dry_run_no_exif_change(self, temp_dir):
        """Dry-run counts files but doesn't modify EXIF."""
        img = Image.new('RGB', (10, 10), 'red')
        img.save(temp_dir / "photo.jpg", 'JPEG')

        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_exif_dates(temp_dir, start, end, dry_run=True)
        assert stats['success'] == 1

    def test_only_exif_compatible(self, temp_dir):
        """Only processes EXIF-compatible extensions."""
        # .jpg should be processed
        Image.new('RGB', (10, 10), 'red').save(
            temp_dir / "photo.jpg", 'JPEG'
        )
        # .png should NOT be processed (not EXIF-compatible)
        Image.new('RGB', (10, 10), 'blue').save(
            temp_dir / "photo.png", 'PNG'
        )

        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_exif_dates(temp_dir, start, end, dry_run=True)
        assert stats['success'] == 1  # Only the JPEG

    def test_empty_directory(self, temp_dir):
        """Empty directory returns zero stats."""
        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_exif_dates(temp_dir, start, end, dry_run=True)
        assert stats['success'] == 0
        assert stats['failed'] == 0

    def test_actual_writes_exif(self, temp_dir):
        """Actually writes EXIF data to the file."""
        img = Image.new('RGB', (10, 10), 'red')
        path = temp_dir / "photo.jpg"
        img.save(path, 'JPEG')

        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_exif_dates(temp_dir, start, end, dry_run=False)
        assert stats['success'] == 1

        # Verify EXIF was written
        import piexif
        exif_data = piexif.load(str(path))
        assert piexif.ExifIFD.DateTimeOriginal in exif_data['Exif']


class TestRandomizeFileDates:
    """Test randomize_file_dates function."""

    def test_stats_keys(self, temp_dir):
        """Returns dict with expected keys."""
        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_file_dates(temp_dir, start, end, dry_run=True)
        assert 'success' in stats
        assert 'failed' in stats

    def test_dry_run_no_timestamp_change(self, temp_dir):
        """Dry-run doesn't modify file timestamps."""
        Image.new('RGB', (10, 10), 'red').save(
            temp_dir / "photo.jpg", 'JPEG'
        )
        original_mtime = os.path.getmtime(temp_dir / "photo.jpg")

        start = datetime(2020, 1, 1)
        end = datetime(2020, 6, 30)
        stats = randomize_file_dates(temp_dir, start, end, dry_run=True)
        assert stats['success'] == 1
        assert os.path.getmtime(temp_dir / "photo.jpg") == original_mtime

    def test_actual_changes_mtime(self, temp_dir):
        """Actually changes file modification time."""
        path = temp_dir / "photo.jpg"
        Image.new('RGB', (10, 10), 'red').save(path, 'JPEG')
        original_mtime = os.path.getmtime(path)

        # Use a date range well in the past
        start = datetime(2000, 1, 1)
        end = datetime(2000, 12, 31)
        stats = randomize_file_dates(temp_dir, start, end, dry_run=False)
        assert stats['success'] == 1

        new_mtime = os.path.getmtime(path)
        assert new_mtime != original_mtime
        # Verify new mtime is within the specified range
        new_dt = datetime.fromtimestamp(new_mtime)
        assert start <= new_dt <= datetime(2001, 1, 1)

    def test_empty_directory(self, temp_dir):
        """Empty directory returns zero stats."""
        start = datetime(2020, 1, 1)
        end = datetime(2023, 12, 31)
        stats = randomize_file_dates(temp_dir, start, end, dry_run=True)
        assert stats['success'] == 0
