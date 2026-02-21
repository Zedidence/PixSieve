"""
Unit tests for dupefinder/operations/cleanup.py.
"""

import pytest
from pathlib import Path

from dupefinder.operations.cleanup import delete_empty_folders


class TestDeleteEmptyFolders:
    """Test delete_empty_folders function."""

    def test_stats_keys(self, temp_dir):
        """Returns dict with expected keys."""
        stats = delete_empty_folders(temp_dir)
        assert 'deleted' in stats
        assert 'errors' in stats

    def test_dry_run_counts_but_keeps(self, temp_dir):
        """Dry-run counts empty folders but doesn't delete them."""
        empty = temp_dir / "empty"
        empty.mkdir()

        stats = delete_empty_folders(temp_dir, dry_run=True)
        assert stats['deleted'] == 1
        assert empty.exists(), "Folder should still exist after dry run"

    def test_actual_deletes_empty(self, temp_dir):
        """Actually removes empty directories."""
        empty = temp_dir / "empty"
        empty.mkdir()

        stats = delete_empty_folders(temp_dir, dry_run=False)
        assert stats['deleted'] == 1
        assert not empty.exists()

    def test_leaves_non_empty(self, temp_dir):
        """Does not delete directories with files."""
        non_empty = temp_dir / "has_files"
        non_empty.mkdir()
        (non_empty / "file.txt").write_text("data")

        stats = delete_empty_folders(temp_dir, dry_run=False)
        assert stats['deleted'] == 0
        assert non_empty.exists()

    def test_nested_empty_removed(self, temp_dir):
        """Nested empty directories are removed bottom-up."""
        a = temp_dir / "a"
        b = a / "b"
        c = b / "c"
        c.mkdir(parents=True)

        stats = delete_empty_folders(temp_dir, dry_run=False)
        assert stats['deleted'] == 3
        assert not a.exists()

    def test_root_never_deleted(self, temp_dir):
        """Root directory is never deleted even if empty."""
        stats = delete_empty_folders(temp_dir, dry_run=False)
        assert temp_dir.exists()

    def test_non_directory_input(self, temp_dir):
        """Non-directory input returns zero stats."""
        fake_path = temp_dir / "nonexistent"
        stats = delete_empty_folders(fake_path)
        assert stats['deleted'] == 0
        assert stats['errors'] == 0

    def test_mixed_empty_and_non_empty(self, temp_dir):
        """Only empty dirs removed in a mixed structure."""
        empty1 = temp_dir / "empty1"
        empty1.mkdir()
        empty2 = temp_dir / "empty2"
        empty2.mkdir()
        non_empty = temp_dir / "non_empty"
        non_empty.mkdir()
        (non_empty / "keep.txt").write_text("data")

        stats = delete_empty_folders(temp_dir, dry_run=False)
        assert stats['deleted'] == 2
        assert not empty1.exists()
        assert not empty2.exists()
        assert non_empty.exists()
