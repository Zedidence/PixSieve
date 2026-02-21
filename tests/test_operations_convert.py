"""
Unit tests for pixsieve/operations/convert.py.
"""

import pytest
from pathlib import Path
from PIL import Image

from pixsieve.operations.convert import fix_extensions, batch_convert_to_jpg


class TestFixExtensions:
    """Test fix_extensions function."""

    def test_stats_keys(self, temp_dir):
        """Returns dict with expected keys."""
        stats = fix_extensions(temp_dir, dry_run=True)
        assert 'total' in stats
        assert 'valid' in stats
        assert 'fixed' in stats
        assert 'unknown' in stats

    def test_dry_run_no_rename(self, temp_dir):
        """Dry-run detects wrong extension but doesn't rename."""
        # Create a proper JPEG file, then rename it to .png
        img = Image.new('RGB', (10, 10), 'red')
        correct = temp_dir / "photo.jpg"
        img.save(correct, 'JPEG')
        wrong_ext = temp_dir / "photo.png"
        correct.rename(wrong_ext)

        stats = fix_extensions(temp_dir, dry_run=True)
        assert stats['fixed'] >= 1
        assert wrong_ext.exists(), "File should not be renamed in dry-run"

    def test_actual_fixes_extension(self, temp_dir):
        """Actually renames file to correct extension."""
        # Create a proper JPEG file, then rename it to .png
        img = Image.new('RGB', (10, 10), 'red')
        correct = temp_dir / "photo.jpg"
        img.save(correct, 'JPEG')
        wrong_ext = temp_dir / "photo.png"
        correct.rename(wrong_ext)

        stats = fix_extensions(temp_dir, dry_run=False)
        assert stats['fixed'] >= 1
        assert not wrong_ext.exists()
        assert (temp_dir / "photo.jpg").exists()

    def test_valid_extension_counted(self, temp_dir):
        """Correctly-extensioned files counted as valid."""
        img = Image.new('RGB', (10, 10), 'red')
        img.save(temp_dir / "correct.jpg", 'JPEG')

        stats = fix_extensions(temp_dir, dry_run=False)
        assert stats['valid'] >= 1

    def test_non_image_skipped(self, temp_dir):
        """Non-image files are skipped silently."""
        (temp_dir / "readme.txt").write_text("not an image")

        stats = fix_extensions(temp_dir, dry_run=False)
        assert stats['total'] == 0

    def test_recursive(self, temp_dir):
        """Recursive mode finds files in subdirectories."""
        sub = temp_dir / "sub"
        sub.mkdir()
        img = Image.new('RGB', (10, 10), 'red')
        img.save(sub / "nested.jpg", 'JPEG')

        stats = fix_extensions(temp_dir, recursive=True, dry_run=True)
        assert stats['total'] >= 1


class TestBatchConvertToJpg:
    """Test batch_convert_to_jpg function."""

    def test_stats_keys(self, temp_dir):
        """Returns dict with expected keys."""
        stats = batch_convert_to_jpg(temp_dir, dry_run=True)
        assert 'converted' in stats
        assert 'deleted' in stats
        assert 'failed' in stats

    def test_dry_run_no_conversion(self, temp_dir):
        """Dry-run counts files but doesn't convert them."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "photo.png", 'PNG')

        stats = batch_convert_to_jpg(temp_dir, dry_run=True)
        assert stats['converted'] == 1
        # Original should still exist, no jpg created
        assert (temp_dir / "photo.png").exists()
        assert not (temp_dir / "photo.jpg").exists()

    def test_actual_converts_png_to_jpg(self, temp_dir):
        """Converts PNG to JPG."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "photo.png", 'PNG')

        stats = batch_convert_to_jpg(temp_dir, dry_run=False)
        assert stats['converted'] == 1
        assert (temp_dir / "photo.jpg").exists()

    def test_delete_originals(self, temp_dir):
        """Delete originals option removes source files."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "photo.png", 'PNG')

        stats = batch_convert_to_jpg(
            temp_dir, delete_originals=True, dry_run=False
        )
        assert stats['converted'] == 1
        assert stats['deleted'] == 1
        assert not (temp_dir / "photo.png").exists()
        assert (temp_dir / "photo.jpg").exists()

    def test_keeps_originals_by_default(self, temp_dir):
        """Originals are kept by default."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "photo.png", 'PNG')

        batch_convert_to_jpg(temp_dir, dry_run=False)
        assert (temp_dir / "photo.png").exists()

    def test_skips_non_convertible(self, temp_dir):
        """Non-convertible extensions (e.g., .jpg) are skipped."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "photo.jpg", 'JPEG')

        stats = batch_convert_to_jpg(temp_dir, dry_run=True)
        assert stats['converted'] == 0

    def test_converts_bmp(self, temp_dir):
        """Converts BMP to JPG."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "photo.bmp", 'BMP')

        stats = batch_convert_to_jpg(temp_dir, dry_run=False)
        assert stats['converted'] == 1
        assert (temp_dir / "photo.jpg").exists()

    def test_handles_rgba_transparency(self, temp_dir):
        """Handles RGBA images by compositing onto white background."""
        img = Image.new('RGBA', (10, 10), (255, 0, 0, 128))
        img.save(temp_dir / "transparent.png", 'PNG')

        stats = batch_convert_to_jpg(temp_dir, dry_run=False)
        assert stats['converted'] == 1
        assert stats['failed'] == 0
