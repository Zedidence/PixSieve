"""
Unit tests for dupefinder/operations/move.py.
"""

import pytest
from pathlib import Path
from PIL import Image

from dupefinder.operations.move import move_to_parent, move_with_structure


class TestMoveToParent:
    """Test move_to_parent function."""

    def test_stats_keys(self, ops_temp_dir):
        """Returns dict with expected keys."""
        stats = move_to_parent(ops_temp_dir, dry_run=True)
        assert 'moved' in stats
        assert 'skipped' in stats
        assert 'errors' in stats

    def test_dry_run_no_movement(self, ops_temp_dir):
        """Dry-run reports files but doesn't move them."""
        stats = move_to_parent(ops_temp_dir, dry_run=True)
        assert stats['moved'] > 0
        # Files should still be in subdirectories
        assert (ops_temp_dir / "sub1" / "charlie.jpg").exists()

    def test_actual_moves_to_parent(self, ops_temp_dir):
        """Files from subdirectories are moved to parent."""
        stats = move_to_parent(ops_temp_dir, dry_run=False)
        assert stats['moved'] > 0
        # charlie.jpg should now be in parent (or parent with unique name)
        parent_files = list(ops_temp_dir.glob("charlie*.jpg"))
        assert len(parent_files) >= 1

    def test_skips_files_already_in_parent(self, ops_temp_dir):
        """Files already in the parent directory are skipped."""
        stats = move_to_parent(ops_temp_dir, dry_run=True)
        assert stats['skipped'] > 0  # alpha.jpg and beta.png are in parent

    def test_extension_filter(self, ops_temp_dir):
        """Only moves files matching specified extensions."""
        stats = move_to_parent(
            ops_temp_dir, extensions={'.bmp'}, dry_run=True
        )
        assert stats['moved'] == 1  # Only echo.bmp

    def test_invalid_path(self, temp_dir):
        """Invalid path returns zero stats."""
        stats = move_to_parent(temp_dir / "nonexistent")
        assert stats['moved'] == 0
        assert stats['errors'] == 0


class TestMoveWithStructure:
    """Test move_with_structure function."""

    def test_stats_keys(self, ops_temp_dir, temp_dir):
        """Returns dict with expected keys."""
        dest = temp_dir / "destination"
        stats = move_with_structure(ops_temp_dir, dest, dry_run=True)
        assert 'moved' in stats
        assert 'skipped' in stats
        assert 'errors' in stats

    def test_dry_run_no_files_moved(self, ops_temp_dir, temp_dir):
        """Dry-run doesn't actually move files."""
        dest = temp_dir / "destination"
        stats = move_with_structure(ops_temp_dir, dest, dry_run=True)
        assert stats['moved'] > 0
        # Source files should still exist
        assert (ops_temp_dir / "alpha.jpg").exists()

    def test_actual_moves_with_structure(self, temp_dir):
        """Files are moved preserving directory structure."""
        src = temp_dir / "src"
        sub = src / "subdir"
        sub.mkdir(parents=True)
        Image.new('RGB', (10, 10), 'red').save(src / "root.jpg", 'JPEG')
        Image.new('RGB', (10, 10), 'blue').save(sub / "nested.jpg", 'JPEG')

        dest = temp_dir / "dest"
        stats = move_with_structure(src, dest, dry_run=False)

        assert stats['moved'] == 2
        assert (dest / "root.jpg").exists()
        assert (dest / "subdir" / "nested.jpg").exists()

    def test_overwrite_false_skips(self, temp_dir):
        """Overwrite=False skips existing files at destination."""
        src = temp_dir / "src"
        src.mkdir()
        (src / "file.txt").write_text("source")

        dest = temp_dir / "dest"
        dest.mkdir()
        (dest / "file.txt").write_text("existing")

        stats = move_with_structure(src, dest, overwrite=False, dry_run=False)
        assert stats['skipped'] == 1
        assert (dest / "file.txt").read_text() == "existing"

    def test_overwrite_true_replaces(self, temp_dir):
        """Overwrite=True replaces existing files at destination."""
        src = temp_dir / "src"
        src.mkdir()
        (src / "file.txt").write_text("source")

        dest = temp_dir / "dest"
        dest.mkdir()
        (dest / "file.txt").write_text("existing")

        stats = move_with_structure(src, dest, overwrite=True, dry_run=False)
        assert stats['moved'] == 1
        assert (dest / "file.txt").read_text() == "source"

    def test_invalid_source(self, temp_dir):
        """Invalid source returns zero stats."""
        stats = move_with_structure(
            temp_dir / "nonexistent", temp_dir / "dest"
        )
        assert stats['moved'] == 0
