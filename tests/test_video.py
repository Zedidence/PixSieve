"""
Unit tests for video duplicate detection support.

Tests that require actually decoding a video (analyze_video) are skipped
when opencv-python-headless isn't installed (HAS_VIDEO_SUPPORT=False) -
everything else (config, model round-trips, hash-comparison logic, cache
schema) exercises real code without needing opencv at all.
"""

import os
import pytest

from pixsieve.config import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from pixsieve.models import ImageInfo
from pixsieve.scanner.dependencies import HAS_VIDEO_SUPPORT
from pixsieve.scanner.video_analysis import VIDEO_HASH_SEPARATOR
from pixsieve.scanner.video_deduplication import find_video_perceptual_duplicates
from pixsieve.database import ImageCache


class TestVideoConfig:
    def test_video_extensions_disjoint_from_image_extensions(self):
        """VIDEO_EXTENSIONS must never overlap IMAGE_EXTENSIONS - image-only
        operations (EXIF, color sort, repair) filter by IMAGE_EXTENSIONS and
        rely on video files never appearing in that set."""
        assert IMAGE_EXTENSIONS.isdisjoint(VIDEO_EXTENSIONS)

    def test_video_extensions_nonempty(self):
        assert '.mp4' in VIDEO_EXTENSIONS
        assert '.mov' in VIDEO_EXTENSIONS


class TestOperationVideoSupportRegistry:
    """Registry-consistency invariants for OPERATION_VIDEO_SUPPORT - the
    single source of truth cli/arg_parser.py, api/operations_routes.py, and
    the GUI template all read to decide which ops expose a video toggle."""

    _KNOWN_OPS = {
        'move-to-parent', 'move', 'rename-random', 'rename-parent',
        'sort-alpha', 'sort-color', 'sort-resolution', 'fix-extensions',
        'convert', 'randomize-dates', 'strip-ratings', 'cleanup',
        'repair', 'pipeline',
    }

    # op_name -> CLI argv that should accept --include-videos. sort-resolution
    # is intentionally excluded - it has no CLI subcommand at all (Web-GUI/API
    # only, per docs/cli.md).
    _CLI_ARGV = {
        'move-to-parent': ['move-to-parent', '.'],
        'rename-random': ['rename', 'random', '.'],
        'strip-ratings': ['strip-ratings', '.'],
        'randomize-dates': [
            'metadata', 'randomize-dates', '.',
            '--start', '2020-01-01', '--end', '2020-12-31',
        ],
        'sort-color': ['sort', 'color', '.'],
        'pipeline': ['pipeline', '.', '--steps', 'cleanup_empty'],
    }

    def test_registry_keys_name_real_operations(self):
        from pixsieve.operations.capabilities import OPERATION_VIDEO_SUPPORT
        assert set(OPERATION_VIDEO_SUPPORT.keys()) == self._KNOWN_OPS

    def test_needs_flag_ops_have_a_live_cli_flag(self):
        from pixsieve.cli.arg_parser import create_parser
        from pixsieve.operations.capabilities import OPERATION_VIDEO_SUPPORT, needs_video_flag

        parser = create_parser()
        for op_name, argv in self._CLI_ARGV.items():
            assert needs_video_flag(op_name), op_name
            assert OPERATION_VIDEO_SUPPORT[op_name]['support'] != 'none'
            args = parser.parse_args(argv + ['--include-videos'])
            assert getattr(args, 'include_videos', None) is True, op_name

    def test_none_support_op_rejects_the_cli_flag(self):
        from pixsieve.cli.arg_parser import create_parser

        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(['convert', '.', '--include-videos'])


class TestImageInfoVideoFields:
    def test_defaults_are_image(self):
        info = ImageInfo(path="/x/photo.jpg")
        assert info.media_type == "image"
        assert info.duration == 0.0

    def test_to_dict_from_dict_round_trip(self):
        info = ImageInfo(
            path="/x/clip.mp4",
            media_type="video",
            duration=125.4,
            width=1920,
            height=1080,
        )
        data = info.to_dict()
        assert data['media_type'] == 'video'
        assert data['duration'] == 125.4
        assert data['duration_formatted'] == '2:05'

        restored = ImageInfo.from_dict(data)
        assert restored.media_type == 'video'
        assert restored.duration == 125.4

    def test_duration_formatted_zero(self):
        assert ImageInfo(path="/x/photo.jpg").duration_formatted == ""

    def test_duration_formatted_seconds_only(self):
        assert ImageInfo(path="/x/clip.mp4", duration=9).duration_formatted == "0:09"


def _video_info(path: str, frame_hashes: list, file_hash: str = "") -> ImageInfo:
    return ImageInfo(
        path=path,
        media_type="video",
        file_hash=file_hash,
        perceptual_hash=VIDEO_HASH_SEPARATOR.join(frame_hashes),
    )


class TestFindVideoPerceptualDuplicates:
    # Two distinct 256-bit (64 hex char) hex strings used as stand-ins for
    # real frame pHashes - what matters for this module is Hamming distance
    # between them, not that they came from a real decoded frame.
    HASH_A = "0" * 64
    HASH_B = "f" * 64  # maximally different from HASH_A

    def test_identical_frame_hashes_group(self):
        videos = [
            _video_info("/a.mp4", [self.HASH_A, self.HASH_A, self.HASH_A]),
            _video_info("/b.mp4", [self.HASH_A, self.HASH_A, self.HASH_A]),
        ]
        groups = find_video_perceptual_duplicates(videos, threshold=10)
        assert len(groups) == 1
        assert groups[0].match_type == "video-perceptual"
        assert groups[0].image_count == 2

    def test_dissimilar_frame_hashes_dont_group(self):
        videos = [
            _video_info("/a.mp4", [self.HASH_A, self.HASH_A, self.HASH_A]),
            _video_info("/b.mp4", [self.HASH_B, self.HASH_B, self.HASH_B]),
        ]
        groups = find_video_perceptual_duplicates(videos, threshold=10)
        assert groups == []

    def test_fewer_than_two_candidates_returns_empty(self):
        assert find_video_perceptual_duplicates([], threshold=10) == []
        assert find_video_perceptual_duplicates(
            [_video_info("/a.mp4", [self.HASH_A])], threshold=10
        ) == []

    def test_videos_without_perceptual_hash_are_skipped(self):
        videos = [
            _video_info("/a.mp4", [self.HASH_A]),
            ImageInfo(path="/b.mp4", media_type="video", perceptual_hash=""),
        ]
        assert find_video_perceptual_duplicates(videos, threshold=10) == []

    def test_start_id_offset(self):
        videos = [
            _video_info("/a.mp4", [self.HASH_A]),
            _video_info("/b.mp4", [self.HASH_A]),
        ]
        groups = find_video_perceptual_duplicates(videos, threshold=10, start_id=42)
        assert groups[0].id == 42


class TestVideoCacheRoundTrip:
    """media_type/duration should survive a put()/get() cache round trip."""

    def test_put_and_get_video_info(self, temp_cache_db, tmp_path):
        video_path = tmp_path / "clip.mp4"
        video_path.write_bytes(b"not a real video, just needs to exist for os.stat")

        cache = ImageCache(db_path=temp_cache_db)
        original = ImageInfo(
            path=str(video_path),
            file_size=os.path.getsize(video_path),
            media_type="video",
            duration=42.5,
            file_hash="deadbeef",
            perceptual_hash="aa" * 32 + VIDEO_HASH_SEPARATOR + "bb" * 32,
        )
        assert cache.put(original) is True

        cached = cache.get(str(video_path))
        assert cached is not None
        assert cached.media_type == "video"
        assert cached.duration == 42.5
        assert cached.perceptual_hash == original.perceptual_hash

    def test_existing_image_rows_default_to_image_media_type(self, temp_cache_db, tmp_path):
        """A row written without media_type (simulating a pre-video-support
        cache entry) should read back as media_type='image', not crash."""
        img_path = tmp_path / "photo.jpg"
        img_path.write_bytes(b"fake jpeg bytes")

        cache = ImageCache(db_path=temp_cache_db)
        info = ImageInfo(path=str(img_path), file_size=os.path.getsize(img_path))
        cache.put(info)

        cached = cache.get(str(img_path))
        assert cached.media_type == "image"
        assert cached.duration == 0.0


@pytest.mark.skipif(not HAS_VIDEO_SUPPORT, reason="opencv-python-headless not installed")
class TestAnalyzeVideoWithOpenCV:
    """Only runs where opencv is actually installed (pip install pixsieve[video])."""

    def _make_test_clip(self, path, num_frames=15, size=(64, 48), color=(0, 0, 255)):
        import numpy as np
        from pixsieve.scanner.dependencies import cv2

        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, size)
        try:
            frame = np.full((size[1], size[0], 3), color, dtype=np.uint8)
            for _ in range(num_frames):
                writer.write(frame)
        finally:
            writer.release()

    def _make_test_clip_from_frame(self, path, frame, num_frames=15):
        """Write `frame` (an HxWx3 uint8 array) repeated num_frames times."""
        from pixsieve.scanner.dependencies import cv2

        height, width = frame.shape[:2]
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (width, height))
        try:
            for _ in range(num_frames):
                writer.write(frame)
        finally:
            writer.release()

    def test_analyze_video_basic_metadata(self, tmp_path):
        from pixsieve.scanner.video_analysis import analyze_video

        clip_path = tmp_path / "clip.mp4"
        self._make_test_clip(clip_path)

        info = analyze_video(str(clip_path))
        assert info.error is None
        assert info.media_type == "video"
        assert info.width == 64
        assert info.height == 48
        assert info.file_hash
        assert info.perceptual_hash
        assert VIDEO_HASH_SEPARATOR in info.perceptual_hash or info.perceptual_hash

    def test_analyze_video_missing_file(self, tmp_path):
        from pixsieve.scanner.video_analysis import analyze_video

        info = analyze_video(str(tmp_path / "does_not_exist.mp4"))
        assert info.error == "File not found"

    def test_end_to_end_exact_dedup(self, tmp_path):
        """Full pipeline, real opencv decoding: two byte-identical clips are
        caught by find_exact_duplicates; a visually distinct clip is not."""
        from pixsieve.scanner.video_analysis import analyze_video
        from pixsieve.scanner.deduplication import find_exact_duplicates
        import shutil

        clip_a = tmp_path / "a.mp4"
        clip_a_copy = tmp_path / "a_copy.mp4"
        clip_b = tmp_path / "b.mp4"

        self._make_test_clip(clip_a, color=(0, 0, 255))
        shutil.copyfile(clip_a, clip_a_copy)  # byte-identical -> exact duplicate
        self._make_test_clip(clip_b, color=(255, 0, 0))  # different -> no match

        infos = [analyze_video(str(p)) for p in (clip_a, clip_a_copy, clip_b)]
        for info in infos:
            assert info.error is None

        exact_groups = find_exact_duplicates(infos)
        assert len(exact_groups) == 1
        assert {img.path for img in exact_groups[0].images} == {str(clip_a), str(clip_a_copy)}

    def test_end_to_end_perceptual_dedup_across_resolutions(self, tmp_path):
        """A clip re-encoded at a different resolution (same visual content,
        NOT byte-identical) should still be caught by
        find_video_perceptual_duplicates - a visually distinct clip should
        not match either.

        Uses smooth gradients rather than flat colors or noise: a flat color
        has ~no high-frequency DCT content (pHash is near color-blind to it),
        and raw noise is heavily distorted by lossy video compression -
        both are bad fixtures for proving perceptual matching actually
        discriminates content. A gradient survives resize + lossy re-encode
        while still differing sharply by orientation.
        """
        import numpy as np
        from pixsieve.scanner.video_analysis import analyze_video
        from pixsieve.scanner.dependencies import cv2

        horizontal_gradient = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (48, 1))
        base_frame = np.stack([horizontal_gradient] * 3, axis=-1)
        small_frame = cv2.resize(base_frame, (32, 24))

        yy, xx = np.meshgrid(np.arange(48), np.arange(64), indexing='ij')
        checkerboard = (((xx // 8) + (yy // 8)) % 2 * 255).astype(np.uint8)
        distinct_frame = np.stack([checkerboard] * 3, axis=-1)

        clip_a = tmp_path / "a.mp4"
        clip_a_small = tmp_path / "a_small.mp4"
        clip_b = tmp_path / "b.mp4"

        self._make_test_clip_from_frame(clip_a, base_frame)
        self._make_test_clip_from_frame(clip_a_small, small_frame)  # same content, resized
        self._make_test_clip_from_frame(clip_b, distinct_frame)  # different content

        infos = [analyze_video(str(p)) for p in (clip_a, clip_a_small, clip_b)]
        for info in infos:
            assert info.error is None
        assert infos[0].file_hash != infos[1].file_hash  # not byte-identical

        groups = find_video_perceptual_duplicates(infos, threshold=10)
        assert len(groups) == 1
        assert {img.path for img in groups[0].images} == {str(clip_a), str(clip_a_small)}
