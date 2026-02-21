"""
Unit tests for pixsieve/utils/operations.py utility functions.
"""

import pytest
from pathlib import Path
from datetime import datetime

from pixsieve.utils.operations import (
    get_unique_path,
    sanitize_filename,
    truncate_path,
    find_files,
    make_progress_bar,
    parse_date,
)


class TestGetUniquePath:
    """Test get_unique_path function."""

    def test_unique_path_no_conflict(self, temp_dir):
        """Returns base path when no conflict exists."""
        result = get_unique_path(temp_dir, "newfile.jpg")
        assert result == temp_dir / "newfile.jpg"

    def test_unique_path_with_conflict(self, temp_dir):
        """Appends _1 when file already exists."""
        (temp_dir / "photo.jpg").write_text("exists")
        result = get_unique_path(temp_dir, "photo.jpg")
        assert result == temp_dir / "photo_1.jpg"

    def test_unique_path_multiple_conflicts(self, temp_dir):
        """Increments suffix when multiple conflicts exist."""
        (temp_dir / "photo.jpg").write_text("exists")
        (temp_dir / "photo_1.jpg").write_text("exists")
        (temp_dir / "photo_2.jpg").write_text("exists")
        result = get_unique_path(temp_dir, "photo.jpg")
        assert result == temp_dir / "photo_3.jpg"

    def test_unique_path_preserves_extension(self, temp_dir):
        """Extension is preserved through suffix appending."""
        (temp_dir / "file.png").write_text("exists")
        result = get_unique_path(temp_dir, "file.png")
        assert result.suffix == ".png"


class TestSanitizeFilename:
    """Test sanitize_filename function."""

    def test_clean_name_unchanged(self):
        """Clean filename passes through unchanged."""
        assert sanitize_filename("photo.jpg") == "photo.jpg"

    def test_removes_invalid_chars(self):
        """Invalid Windows characters replaced with underscores."""
        result = sanitize_filename('file<>:"/\\|?*.jpg')
        assert '<' not in result
        assert '>' not in result
        assert ':' not in result
        assert '"' not in result
        assert '?' not in result
        assert '*' not in result

    def test_reserved_name_prefixed(self):
        """Windows reserved names get underscore prefix."""
        result = sanitize_filename("CON")
        assert result.startswith("_")

    def test_reserved_name_with_extension(self):
        """Reserved name with extension is also handled."""
        result = sanitize_filename("NUL.txt")
        assert result.startswith("_")

    def test_empty_becomes_unnamed(self):
        """Empty/whitespace string becomes 'unnamed'."""
        assert sanitize_filename("   ") == "unnamed"
        assert sanitize_filename("...") == "unnamed"

    def test_strips_leading_trailing_dots_spaces(self):
        """Leading/trailing spaces and dots are stripped."""
        result = sanitize_filename("  file.jpg  ")
        assert not result.startswith(" ")
        assert not result.endswith(" ")


class TestTruncatePath:
    """Test truncate_path function."""

    def test_short_path_unchanged(self):
        """Path shorter than max is returned unchanged."""
        short_path = "/home/user/photo.jpg"
        assert truncate_path(short_path) == short_path

    def test_long_path_truncated(self):
        """Path exceeding max_length is truncated."""
        long_name = "a" * 300 + ".jpg"
        long_path = f"/home/{long_name}"
        result = truncate_path(long_path, max_length=250)
        assert result is not None
        assert len(result) <= 250

    def test_preserves_extension(self):
        """Truncation preserves file extension."""
        long_name = "a" * 300 + ".png"
        long_path = f"/home/{long_name}"
        result = truncate_path(long_path, max_length=250)
        assert result is not None
        assert result.endswith(".png")

    def test_impossible_truncation_returns_none(self):
        """Returns None when directory alone exceeds max length."""
        long_dir = "/" + "d" * 260
        path = f"{long_dir}/file.jpg"
        result = truncate_path(path, max_length=250)
        assert result is None


class TestFindFiles:
    """Test find_files function."""

    def test_finds_matching_extensions(self, ops_temp_dir):
        """Finds files matching the specified extensions."""
        files = find_files(ops_temp_dir, {'.jpg', '.jpeg'})
        names = {f.name for f in files}
        assert 'alpha.jpg' in names
        assert 'charlie.jpg' in names

    def test_recursive_finds_nested(self, ops_temp_dir):
        """Recursive mode finds files in subdirectories."""
        files = find_files(ops_temp_dir, {'.jpg'}, recursive=True)
        names = {f.name for f in files}
        assert 'alpha.jpg' in names
        assert 'charlie.jpg' in names

    def test_non_recursive_skips_nested(self, ops_temp_dir):
        """Non-recursive mode only finds top-level files."""
        files = find_files(ops_temp_dir, {'.jpg'}, recursive=False)
        names = {f.name for f in files}
        assert 'alpha.jpg' in names
        assert 'charlie.jpg' not in names

    def test_empty_directory(self, temp_dir):
        """Returns empty list for directory with no matching files."""
        empty = temp_dir / "empty"
        empty.mkdir()
        files = find_files(empty, {'.jpg'})
        assert files == []

    def test_case_insensitive(self, temp_dir):
        """Extension matching is case-insensitive."""
        (temp_dir / "photo.JPG").write_bytes(b"\xff\xd8\xff")
        files = find_files(temp_dir, {'.jpg'}, recursive=False)
        assert len(files) == 1


class TestMakeProgressBar:
    """Test make_progress_bar function."""

    def test_returns_iterable(self):
        """Returns an iterable that can be looped over."""
        items = [1, 2, 3]
        result = make_progress_bar(items, desc="Test")
        collected = list(result)
        assert collected == [1, 2, 3]

    def test_with_total_only(self):
        """Works with total parameter and no iterable."""
        result = make_progress_bar(total=5, desc="Test")
        assert hasattr(result, '__iter__')


class TestParseDate:
    """Test parse_date function."""

    def test_date_only(self):
        """Parses YYYY-MM-DD format."""
        result = parse_date("2023-06-15")
        assert result == datetime(2023, 6, 15, 0, 0, 0)

    def test_datetime_format(self):
        """Parses YYYY-MM-DD HH:MM:SS format."""
        result = parse_date("2023-06-15 14:30:00")
        assert result == datetime(2023, 6, 15, 14, 30, 0)

    def test_invalid_raises_valueerror(self):
        """Invalid format raises ValueError."""
        with pytest.raises(ValueError):
            parse_date("not-a-date")

    def test_empty_string_raises_valueerror(self):
        """Empty string raises ValueError."""
        with pytest.raises(ValueError):
            parse_date("")
