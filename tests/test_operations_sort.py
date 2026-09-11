"""
Unit tests for pixsieve/operations/sort.py.
"""

import pytest
from pathlib import Path
from PIL import Image

from pixsieve.operations.sort import sort_alphabetical, ColorImageSorter, sort_by_resolution
from pixsieve.scanner.dependencies import HAS_VIDEO_SUPPORT

requires_video = pytest.mark.skipif(
    not HAS_VIDEO_SUPPORT, reason="opencv-python-headless not installed"
)


class TestSortAlphabetical:
    """Test sort_alphabetical function."""

    def test_stats_keys(self, temp_dir):
        """Returns dict with expected keys."""
        stats = sort_alphabetical(temp_dir, dry_run=True)
        assert 'moved' in stats
        assert 'skipped' in stats
        assert 'errors' in stats

    def test_dry_run_no_folders_created(self, temp_dir):
        """Dry-run doesn't create group folders."""
        (temp_dir / "apple.txt").write_text("data")

        stats = sort_alphabetical(temp_dir, dry_run=True)
        assert stats['moved'] == 1
        assert not (temp_dir / "A-G").exists()

    def test_actual_sorts_into_groups(self, temp_dir):
        """Files are moved into correct alphabetical groups."""
        (temp_dir / "apple.txt").write_text("data")
        (temp_dir / "banana.txt").write_text("data")
        (temp_dir / "mango.txt").write_text("data")
        (temp_dir / "zebra.txt").write_text("data")

        stats = sort_alphabetical(temp_dir, dry_run=False)
        assert stats['moved'] == 4

        assert (temp_dir / "A-G" / "apple.txt").exists()
        assert (temp_dir / "A-G" / "banana.txt").exists()
        assert (temp_dir / "H-N" / "mango.txt").exists()
        assert (temp_dir / "U-Z" / "zebra.txt").exists()

    def test_digits_go_to_0_9(self, temp_dir):
        """Files starting with digits go to 0-9 group."""
        (temp_dir / "1file.txt").write_text("data")
        (temp_dir / "9photo.txt").write_text("data")

        stats = sort_alphabetical(temp_dir, dry_run=False)
        assert stats['moved'] == 2
        assert (temp_dir / "0-9" / "1file.txt").exists()
        assert (temp_dir / "0-9" / "9photo.txt").exists()

    def test_special_chars_skipped(self, temp_dir):
        """Files starting with special characters are skipped."""
        (temp_dir / "_hidden.txt").write_text("data")

        stats = sort_alphabetical(temp_dir, dry_run=False)
        assert stats['skipped'] == 1

    def test_case_insensitive(self, temp_dir):
        """Grouping is case-insensitive."""
        (temp_dir / "Apple.txt").write_text("data")
        (temp_dir / "apple2.txt").write_text("data")

        stats = sort_alphabetical(temp_dir, dry_run=False)
        assert stats['moved'] == 2
        assert (temp_dir / "A-G" / "Apple.txt").exists()
        assert (temp_dir / "A-G" / "apple2.txt").exists()


class TestColorImageSorterGetColorName:
    """Test ColorImageSorter.get_color_name static method."""

    def test_black(self):
        assert ColorImageSorter.get_color_name((0, 0, 0)) == "black"

    def test_white(self):
        assert ColorImageSorter.get_color_name((255, 255, 255)) == "white"

    def test_red(self):
        result = ColorImageSorter.get_color_name((255, 0, 0))
        assert result == "red"

    def test_green(self):
        result = ColorImageSorter.get_color_name((0, 255, 0))
        assert result == "green"

    def test_blue(self):
        result = ColorImageSorter.get_color_name((0, 0, 255))
        assert result == "blue"

    def test_gray(self):
        result = ColorImageSorter.get_color_name((80, 80, 80))
        assert result == "gray"


class TestColorImageSorterIsGrayscale:
    """Test ColorImageSorter.is_grayscale method."""

    def test_grayscale_image(self, temp_dir):
        """Grayscale image is detected."""
        img = Image.new('L', (50, 50), color=128)
        path = temp_dir / "gray.png"
        img.save(path, 'PNG')

        sorter = ColorImageSorter(temp_dir)
        assert sorter.is_grayscale(path)

    def test_color_image(self, temp_dir):
        """Color image with varied colors is not detected as grayscale."""
        # Solid colors have zero std in channel differences, so we need
        # an image with actual color variation to trigger non-grayscale
        import numpy as np
        arr = np.zeros((50, 50, 3), dtype=np.uint8)
        arr[:25, :, 0] = 255  # Top half red
        arr[25:, :, 2] = 255  # Bottom half blue
        img = Image.fromarray(arr, 'RGB')
        path = temp_dir / "color.png"
        img.save(path, 'PNG')

        sorter = ColorImageSorter(temp_dir)
        assert not sorter.is_grayscale(path)


class TestColorImageSorterGetImageFiles:
    """Test ColorImageSorter.get_image_files method."""

    def test_finds_supported_files(self, temp_dir):
        """Finds files with supported extensions."""
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "a.jpg", 'JPEG')
        Image.new('RGB', (10, 10), 'blue').save(temp_dir / "b.png", 'PNG')
        (temp_dir / "c.txt").write_text("not an image")

        sorter = ColorImageSorter(temp_dir)
        files = sorter.get_image_files()
        names = {f.name for f in files}
        assert 'a.jpg' in names
        assert 'b.png' in names
        assert 'c.txt' not in names


class TestColorImageSorterSortByColorBW:
    """Test ColorImageSorter.sort_by_color_bw method."""

    def test_dry_run_classifies_only(self, temp_dir):
        """Dry-run classifies but doesn't create folders or move files."""
        Image.new('RGB', (50, 50), (255, 0, 0)).save(
            temp_dir / "color.jpg", 'JPEG'
        )
        Image.new('L', (50, 50), 128).save(
            temp_dir / "bw.jpg", 'JPEG'
        )

        sorter = ColorImageSorter(temp_dir)
        stats = sorter.sort_by_color_bw(dry_run=True)

        assert stats['color'] + stats['bw'] == 2
        assert not (temp_dir / "sorted_by_color_type").exists()

    def test_stats_keys(self, temp_dir):
        """Returns dict with expected keys."""
        sorter = ColorImageSorter(temp_dir)
        stats = sorter.sort_by_color_bw(dry_run=True)
        assert 'color' in stats
        assert 'bw' in stats
        assert 'skipped' in stats


class TestSortByResolution:
    """Test sort_by_resolution function."""

    def test_default_excludes_video(self, temp_dir, make_video_clip):
        """Without include_videos, video files are ignored entirely."""
        Image.new('RGB', (1920, 1080), 'red').save(temp_dir / "photo.jpg", 'JPEG')
        make_video_clip(temp_dir / "clip.mp4")

        stats = sort_by_resolution(temp_dir, dry_run=True)
        assert stats['processed'] == 1  # only the image

    def test_dry_run_categorizes_image_by_resolution(self, temp_dir):
        Image.new('RGB', (1920, 1080), 'red').save(temp_dir / "hd.jpg", 'JPEG')
        stats = sort_by_resolution(temp_dir, dry_run=True)
        assert stats['processed'] == 1
        assert 'hd/landscape' in stats['by_category']

    @requires_video
    def test_include_videos_categorizes_video_by_resolution(self, temp_dir, make_video_clip):
        """A video included via include_videos gets its dimensions read via
        OpenCV (not PIL) and is sorted into the matching category."""
        make_video_clip(temp_dir / "clip.mp4", size=(1920, 1080))

        stats = sort_by_resolution(temp_dir, dry_run=True, include_videos=True)
        assert stats['processed'] == 1
        assert stats['skipped'] == 0
        assert 'hd/landscape' in stats['by_category']

    @requires_video
    def test_actual_move_places_video_in_category_folder(self, temp_dir, make_video_clip):
        clip = make_video_clip(temp_dir / "clip.mp4", size=(640, 480))

        stats = sort_by_resolution(temp_dir, dry_run=False, include_videos=True)
        assert stats['processed'] == 1
        assert not clip.exists()
        assert (temp_dir / "sorted_by_resolution" / "medium" / "landscape" / "clip.mp4").exists()


class TestColorImageSorterVideoSupport:
    """ColorImageSorter with include_videos=True extracts a representative
    frame via OpenCV instead of opening the file with PIL."""

    @requires_video
    def test_get_image_files_includes_video_when_enabled(self, temp_dir, make_video_clip):
        Image.new('RGB', (10, 10), 'red').save(temp_dir / "a.jpg", 'JPEG')
        make_video_clip(temp_dir / "clip.mp4")

        sorter = ColorImageSorter(temp_dir, include_videos=True)
        names = {f.name for f in sorter.get_image_files()}
        assert 'clip.mp4' in names

    def test_get_image_files_excludes_video_by_default(self, temp_dir, make_video_clip):
        make_video_clip(temp_dir / "clip.mp4")
        sorter = ColorImageSorter(temp_dir)
        names = {f.name for f in sorter.get_image_files()}
        assert 'clip.mp4' not in names

    @requires_video
    def test_get_dominant_color_reads_video_frame(self, temp_dir, make_video_clip):
        clip = make_video_clip(temp_dir / "clip.mp4", color=(255, 0, 0))  # BGR red-ish
        sorter = ColorImageSorter(temp_dir, include_videos=True, use_cache=False)
        color = sorter.get_dominant_color(clip)
        assert color is not None

    @requires_video
    def test_is_grayscale_reads_video_frame(self, temp_dir, make_video_clip):
        clip = make_video_clip(temp_dir / "clip.mp4", color=(128, 128, 128))
        sorter = ColorImageSorter(temp_dir, include_videos=True)
        assert sorter.is_grayscale(clip)
