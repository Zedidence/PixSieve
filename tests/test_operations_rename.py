"""
Unit tests for pixsieve/operations/rename.py.
"""

import pytest
from pathlib import Path
from PIL import Image

from pixsieve.operations.rename import rename_random, rename_by_parent


class TestRenameRandom:
    """Test rename_random function."""

    def test_stats_keys(self, ops_temp_dir):
        """Returns dict with expected keys."""
        stats = rename_random(ops_temp_dir, dry_run=True)
        assert 'success' in stats
        assert 'failed' in stats
        assert 'errors' in stats

    def test_dry_run_no_rename(self, ops_temp_dir):
        """Dry-run reports files but doesn't rename them."""
        stats = rename_random(ops_temp_dir, dry_run=True)
        assert stats['success'] > 0
        # Original files should still exist
        assert (ops_temp_dir / "alpha.jpg").exists()

    def test_actual_renames(self, temp_dir):
        """Files are actually renamed with random names."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "test.jpg", 'JPEG')

        stats = rename_random(
            temp_dir, extensions={'.jpg'}, dry_run=False, workers=1
        )
        assert stats['success'] == 1
        # Original name should be gone
        assert not (temp_dir / "test.jpg").exists()
        # A new .jpg file should exist
        jpg_files = list(temp_dir.glob("*.jpg"))
        assert len(jpg_files) == 1

    def test_preserves_extension(self, temp_dir):
        """Renamed files keep their original extension."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "test.png", 'PNG')

        rename_random(
            temp_dir, extensions={'.png'}, dry_run=False, workers=1
        )
        png_files = list(temp_dir.glob("*.png"))
        assert len(png_files) == 1
        assert png_files[0].suffix == ".png"

    def test_empty_directory(self, temp_dir):
        """Empty directory returns zero stats."""
        empty = temp_dir / "empty"
        empty.mkdir()
        stats = rename_random(empty, extensions={'.jpg'})
        assert stats['success'] == 0
        assert stats['failed'] == 0

    def test_name_length(self, temp_dir):
        """Renamed files use the specified name length."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "test.jpg", 'JPEG')

        rename_random(
            temp_dir, name_length=20, extensions={'.jpg'},
            dry_run=False, workers=1,
        )
        jpg_files = list(temp_dir.glob("*.jpg"))
        assert len(jpg_files) == 1
        assert len(jpg_files[0].stem) == 20


class TestRenameByParent:
    """Test rename_by_parent function."""

    def test_stats_keys(self, temp_dir):
        """Returns dict with expected keys."""
        stats = rename_by_parent(temp_dir, dry_run=True)
        assert 'renamed' in stats
        assert 'skipped' in stats
        assert 'errors' in stats

    def test_dry_run_no_rename(self, temp_dir):
        """Dry-run counts but doesn't rename."""
        artist = temp_dir / "ArtistA"
        album = artist / "AlbumX"
        album.mkdir(parents=True)
        (album / "song.jpg").write_text("data")

        stats = rename_by_parent(temp_dir, dry_run=True)
        assert stats['renamed'] > 0
        assert (album / "song.jpg").exists()

    def test_actual_renames_with_parent_names(self, temp_dir):
        """Files are renamed using parent/subfolder naming scheme."""
        artist = temp_dir / "ArtistA"
        album = artist / "AlbumX"
        album.mkdir(parents=True)
        (album / "photo.jpg").write_text("data")

        stats = rename_by_parent(temp_dir, dry_run=False)
        assert stats['renamed'] == 1

        # Should be renamed to ArtistA_AlbumX_1.jpg
        renamed = list(album.glob("ArtistA_AlbumX_*.jpg"))
        assert len(renamed) == 1

    def test_no_subfolders(self, temp_dir):
        """Works when parent has files but no subfolders."""
        folder = temp_dir / "MyFolder"
        folder.mkdir()
        (folder / "image.jpg").write_text("data")

        stats = rename_by_parent(temp_dir, dry_run=False)
        assert stats['renamed'] == 1

        renamed = list(folder.glob("MyFolder_*.jpg"))
        assert len(renamed) == 1

    def test_invalid_directory(self, temp_dir):
        """Invalid directory returns zero stats."""
        stats = rename_by_parent(temp_dir / "nonexistent")
        assert stats['renamed'] == 0
        assert stats['errors'] == 0

    def test_skips_already_named_file(self, temp_dir):
        """A file already following the naming convention is left alone."""
        artist = temp_dir / "ArtistA"
        album = artist / "AlbumX"
        album.mkdir(parents=True)
        (album / "ArtistA_AlbumX_1.jpg").write_text("data")
        (album / "new_photo.jpg").write_text("data")

        stats = rename_by_parent(temp_dir, dry_run=False)

        assert stats['skipped'] == 1
        assert stats['renamed'] == 1
        # Already-correct file untouched, no "_1" suffix appended to it
        assert (album / "ArtistA_AlbumX_1.jpg").exists()
        # The new file got a distinct index, not colliding with index 1
        renamed = list(album.glob("ArtistA_AlbumX_*.jpg"))
        assert len(renamed) == 2
        new_names = {p.name for p in renamed}
        assert "ArtistA_AlbumX_1.jpg" in new_names
        assert "ArtistA_AlbumX_1_1.jpg" not in new_names

    def test_skips_already_named_file_not_first_alphabetically(self, temp_dir):
        """Correctly-named file isn't first alphabetically but is still skipped."""
        artist = temp_dir / "ArtistA"
        album = artist / "AlbumX"
        album.mkdir(parents=True)
        # "aaa_new.jpg" sorts before "ArtistA_AlbumX_1.jpg" alphabetically,
        # so it would previously have claimed index 1 for itself.
        (album / "aaa_new.jpg").write_text("data")
        (album / "ArtistA_AlbumX_1.jpg").write_text("data")

        stats = rename_by_parent(temp_dir, dry_run=False)

        assert stats['skipped'] == 1
        assert stats['renamed'] == 1
        assert (album / "ArtistA_AlbumX_1.jpg").exists()
        renamed = list(album.glob("ArtistA_AlbumX_*.jpg"))
        new_names = {p.name for p in renamed}
        assert "ArtistA_AlbumX_1_1.jpg" not in new_names

    def test_skips_already_named_file_case_insensitive(self, temp_dir):
        """Existing correct name is recognized regardless of case."""
        artist = temp_dir / "ArtistA"
        album = artist / "AlbumX"
        album.mkdir(parents=True)
        (album / "artista_albumx_1.jpg").write_text("data")
        (album / "new_photo.jpg").write_text("data")

        stats = rename_by_parent(temp_dir, dry_run=False)

        assert stats['skipped'] == 1
        assert stats['renamed'] == 1
        assert (album / "artista_albumx_1.jpg").exists()
