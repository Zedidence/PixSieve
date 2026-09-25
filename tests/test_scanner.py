"""
Unit tests for scanner module functions.
"""

import pytest
from pathlib import Path
from pixsieve.scanner import (
    find_image_files,
    calculate_file_hash,
    calculate_perceptual_hash,
    calculate_quality_score,
    calculate_sharpness_score,
    analyze_image,
    find_exact_duplicates,
)
from pixsieve.scanner.parallel import analyze_images_parallel, analyze_images_streaming
from pixsieve.models import ImageInfo


class TestFindImageFiles:
    """Test find_image_files function."""

    def test_find_png_files(self, sample_images, temp_dir):
        """Test finding PNG files in directory."""
        files = find_image_files(temp_dir, recursive=False)
        # Should find PNG files but not TXT
        assert len(files) >= 5  # All our sample PNGs
        assert all(f.endswith('.png') for f in files)

    def test_recursive_search(self, temp_dir):
        """Test recursive directory search."""
        # Create subdirectory with images
        subdir = temp_dir / "subdir"
        subdir.mkdir()

        from PIL import Image
        img = Image.new('RGB', (10, 10), color='green')
        img.save(subdir / "test.png")

        # Recursive should find both
        files = find_image_files(temp_dir, recursive=True)
        assert len(files) >= 1

        # Non-recursive should only find root level (if any)
        files_non_recursive = find_image_files(temp_dir, recursive=False)
        assert (subdir / "test.png").resolve() not in [Path(f) for f in files_non_recursive]

    def test_empty_directory(self, temp_dir):
        """Test scanning empty directory."""
        empty_dir = temp_dir / "empty"
        empty_dir.mkdir()
        files = find_image_files(empty_dir)
        assert len(files) == 0


class TestCalculateFileHash:
    """Test calculate_file_hash function."""

    def test_identical_files_same_hash(self, sample_images):
        """Test that identical files have same hash."""
        hash1 = calculate_file_hash(sample_images['identical1'])
        hash2 = calculate_file_hash(sample_images['identical2'])
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex length

    def test_different_files_different_hash(self, sample_images):
        """Test that different files have different hashes."""
        hash1 = calculate_file_hash(sample_images['identical1'])
        hash2 = calculate_file_hash(sample_images['unique'])
        assert hash1 != hash2

    def test_nonexistent_file(self):
        """Test hash of nonexistent file returns empty string."""
        hash_val = calculate_file_hash("/nonexistent/file.jpg")
        assert hash_val == ""


class TestCalculatePerceptualHash:
    """Test calculate_perceptual_hash, including EXIF-orientation normalization."""

    def test_identical_images_same_phash(self, sample_images):
        h1 = calculate_perceptual_hash(sample_images['identical1'])
        h2 = calculate_perceptual_hash(sample_images['identical2'])
        assert h1 is not None
        assert h1 == h2

    def test_visually_different_images_different_phash(self, temp_dir):
        """
        Uses real spatial-frequency content (a gradient vs. a checkerboard),
        not flat solid colors - pHash's DCT nature makes it near-color-blind
        to flat colors (a red square and a blue square can hash identically),
        so a discriminating test needs actual texture/structure, matching the
        pattern already used for this reason in tests/test_video.py.
        """
        import numpy as np
        from PIL import Image

        gradient = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
        gradient_img = Image.fromarray(np.stack([gradient] * 3, axis=-1))

        yy, xx = np.meshgrid(np.arange(64), np.arange(64), indexing='ij')
        checkerboard = (((xx // 8) + (yy // 8)) % 2 * 255).astype(np.uint8)
        checkerboard_img = Image.fromarray(np.stack([checkerboard] * 3, axis=-1))

        gradient_path = temp_dir / "gradient.png"
        checkerboard_path = temp_dir / "checkerboard.png"
        gradient_img.save(gradient_path, 'PNG')
        checkerboard_img.save(checkerboard_path, 'PNG')

        h1 = calculate_perceptual_hash(str(gradient_path))
        h2 = calculate_perceptual_hash(str(checkerboard_path))
        assert h1 != h2

    def test_nonexistent_file_returns_none(self):
        assert calculate_perceptual_hash("/nonexistent/file.jpg") is None

    def test_exif_rotated_twin_hashes_identically(self, temp_dir):
        """
        Regression test for the EXIF-orientation normalization fix
        (scanner/hashing.py::_ensure_phash_mode). A photo and a copy whose
        pixels are IDENTICAL but which carries an EXIF Orientation tag (the
        common case for phone/camera photos that store rotation as metadata
        rather than physically rotating pixels) must now hash the same,
        since _ensure_phash_mode applies ImageOps.exif_transpose() before
        hashing. Before this fix, these would hash completely differently
        and never be caught as duplicates at any threshold.
        """
        import imagehash
        import numpy as np
        from PIL import Image

        plain_path = temp_dir / "plain.jpg"
        rotated_path = temp_dir / "exif_rotated.jpg"

        # Asymmetric gradient content - a flat color would hash identically
        # regardless of rotation, which wouldn't prove anything here.
        gradient = np.tile(np.linspace(0, 255, 120, dtype=np.uint8), (80, 1))
        display_img = Image.fromarray(np.stack([gradient] * 3, axis=-1))
        display_img.save(plain_path, 'JPEG', quality=95)

        # Per the EXIF spec (and PIL's ImageOps.exif_transpose implementation),
        # orientation=6 means the RAW stored pixels must be rotated 270
        # degrees (Image.Transpose.ROTATE_270) to reach the intended display
        # orientation. So the raw bytes we store are display_img rotated the
        # inverse way (ROTATE_90) - this simulates a camera that wrote pixels
        # in its native sensor orientation and recorded "rotate this to view
        # upright" in EXIF, rather than physically rotating the pixels.
        raw_for_rotated = display_img.transpose(Image.Transpose.ROTATE_90)
        exif = Image.Exif()
        exif[0x0112] = 6
        raw_for_rotated.save(rotated_path, 'JPEG', exif=exif, quality=95)

        h_plain = calculate_perceptual_hash(str(plain_path))
        h_rotated = calculate_perceptual_hash(str(rotated_path))
        assert h_plain is not None and h_rotated is not None

        distance = imagehash.hex_to_hash(h_plain) - imagehash.hex_to_hash(h_rotated)
        assert distance <= 4, f"Expected near-zero Hamming distance after EXIF normalization, got {distance}"

    def test_video_frame_without_exif_still_hashes(self):
        """_ensure_phash_mode's exif_transpose() call must be a safe no-op
        for images with no EXIF at all (e.g. PIL.Image.fromarray() video
        frames, which carry no EXIF data whatsoever)."""
        import numpy as np
        from PIL import Image
        from pixsieve.scanner.hashing import _ensure_phash_mode

        img = Image.fromarray(np.zeros((48, 64, 3), dtype='uint8'))
        result = _ensure_phash_mode(img)
        assert result is not None
        assert result.mode in ('RGB', 'L')


class TestCalculateQualityScore:
    """Test calculate_quality_score function."""

    def test_higher_resolution_better_score(self):
        """Test that higher resolution gets better score."""
        img1 = ImageInfo(
            path="/test1.jpg",
            pixel_count=1920*1080,  # 2MP
            file_size=1000000,
            bit_depth=24,
            format="JPEG"
        )
        img2 = ImageInfo(
            path="/test2.jpg",
            pixel_count=3840*2160,  # 8MP
            file_size=1000000,
            bit_depth=24,
            format="JPEG"
        )

        score1 = calculate_quality_score(img1)
        score2 = calculate_quality_score(img2)
        assert score2 > score1

    def test_larger_file_better_score(self):
        """Test that larger file size contributes to score."""
        img1 = ImageInfo(
            path="/test1.jpg",
            pixel_count=1000000,
            file_size=500000,  # 500KB
            bit_depth=24,
            format="JPEG"
        )
        img2 = ImageInfo(
            path="/test2.jpg",
            pixel_count=1000000,
            file_size=2000000,  # 2MB
            bit_depth=24,
            format="JPEG"
        )

        score1 = calculate_quality_score(img1)
        score2 = calculate_quality_score(img2)
        assert score2 > score1

    def test_raw_format_better_than_jpeg(self):
        """Test that RAW format scores higher than JPEG."""
        img_jpeg = ImageInfo(path="/test.jpeg", pixel_count=1000000, file_size=1000000, bit_depth=24)
        img_raw = ImageInfo(path="/test.cr2", pixel_count=1000000, file_size=1000000, bit_depth=24)

        score_jpeg = calculate_quality_score(img_jpeg)
        score_raw = calculate_quality_score(img_raw)
        assert score_raw > score_jpeg

    def test_sharpness_contributes_a_small_weight(self):
        """sharpness_score should be a modest tiebreaker (capped at 10 pts),
        not swamp the other factors."""
        img_no_sharpness = ImageInfo(path="/a.jpg", pixel_count=1000000, file_size=1000000, bit_depth=24)
        img_sharp = ImageInfo(
            path="/b.jpg", pixel_count=1000000, file_size=1000000, bit_depth=24, sharpness_score=3000,
        )
        score_plain = calculate_quality_score(img_no_sharpness)
        score_sharp = calculate_quality_score(img_sharp)
        assert score_sharp > score_plain
        assert score_sharp - score_plain <= 10  # capped contribution

    def test_sharpness_score_is_capped_even_for_extreme_variance(self):
        img_no_sharpness = ImageInfo(path="/a.jpg", sharpness_score=0)
        img_extreme_sharpness = ImageInfo(path="/a.jpg", sharpness_score=1_000_000)
        # The sharpness term's contribution (and only that term, since both
        # ImageInfos are otherwise identical) must be capped at 10 points
        # even for a wildly out-of-range variance value.
        delta = calculate_quality_score(img_extreme_sharpness) - calculate_quality_score(img_no_sharpness)
        assert delta == 10


class TestCalculateSharpnessScore:
    """Test calculate_sharpness_score - a cheap PIL-only edge-variance proxy."""

    def test_sharp_image_scores_higher_than_blurry_twin(self):
        import numpy as np
        from PIL import Image, ImageFilter

        size = 128
        yy, xx = np.meshgrid(np.arange(size), np.arange(size), indexing='ij')
        checkerboard = (((xx // 8) + (yy // 8)) % 2 * 255).astype(np.uint8)
        sharp_img = Image.fromarray(np.stack([checkerboard] * 3, axis=-1))
        blurry_img = sharp_img.filter(ImageFilter.GaussianBlur(radius=6))

        sharp_score = calculate_sharpness_score(sharp_img)
        blurry_score = calculate_sharpness_score(blurry_img)
        assert sharp_score > blurry_score

    def test_flat_color_scores_much_lower_than_real_texture(self):
        import numpy as np
        from PIL import Image

        flat_img = Image.new('RGB', (256, 256), 'red')

        size = 256
        yy, xx = np.meshgrid(np.arange(size), np.arange(size), indexing='ij')
        checkerboard = (((xx // 8) + (yy // 8)) % 2 * 255).astype(np.uint8)
        textured_img = Image.fromarray(np.stack([checkerboard] * 3, axis=-1))

        assert calculate_sharpness_score(flat_img) < calculate_sharpness_score(textured_img) / 10

    def test_never_raises_on_bad_input(self):
        assert calculate_sharpness_score(None) == 0.0


class TestAnalyzeImage:
    """Test analyze_image function."""

    def test_analyze_valid_image(self, sample_images):
        """Test analyzing a valid image file."""
        info = analyze_image(sample_images['unique'])

        assert info.path == sample_images['unique']
        assert info.width == 100
        assert info.height == 100
        assert info.pixel_count == 10000
        assert info.format == "PNG"
        assert info.file_hash != ""
        assert info.perceptual_hash != ""
        assert info.error is None

    def test_analyze_corrupted_file(self, sample_images):
        """Test analyzing corrupted/invalid file."""
        info = analyze_image(sample_images['corrupted'])

        assert info.path == sample_images['corrupted']
        assert info.error is not None  # Should have error message

    def test_analyze_nonexistent_file(self):
        """Test analyzing nonexistent file."""
        info = analyze_image("/nonexistent/image.jpg")
        assert info.error is not None

    def test_sharpness_score_populated_when_phash_enabled(self, sample_images):
        info = analyze_image(sample_images['unique'], calculate_phash=True)
        assert info.error is None
        # sample_images['unique'] is a flat color square - low but should at
        # least be a real (non-crashing) float, not left at the class default
        # by accident when phash succeeds.
        assert isinstance(info.sharpness_score, float)

    def test_sharpness_score_not_computed_when_phash_disabled(self, sample_images):
        """Sharpness reuses the phash thumbnail - if phash is skipped, there's
        no thumbnail to reuse, so sharpness stays at its 0.0 default."""
        info = analyze_image(sample_images['unique'], calculate_phash=False)
        assert info.error is None
        assert info.sharpness_score == 0.0

    def test_capture_date_extracted_from_exif(self, temp_dir):
        import piexif
        from PIL import Image

        path = temp_dir / "dated.jpg"
        img = Image.new('RGB', (20, 20), 'red')
        exif_dict = {'0th': {}, 'Exif': {}, 'GPS': {}, '1st': {}, 'thumbnail': None}
        exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = b'2019:06:15 10:30:00'
        img.save(path, 'JPEG', exif=piexif.dump(exif_dict))

        info = analyze_image(str(path))
        assert info.error is None
        assert info.capture_date is not None

        from datetime import datetime
        assert datetime.fromtimestamp(info.capture_date) == datetime(2019, 6, 15, 10, 30, 0)

    def test_capture_date_none_when_exif_absent(self, sample_images):
        info = analyze_image(sample_images['unique'])
        assert info.error is None
        assert info.capture_date is None


class TestFindExactDuplicates:
    """Test find_exact_duplicates function."""

    def test_find_exact_duplicates(self, sample_images):
        """Test finding exact duplicate files."""
        # Analyze the identical images
        img1 = analyze_image(sample_images['identical1'])
        img2 = analyze_image(sample_images['identical2'])
        img3 = analyze_image(sample_images['unique'])

        groups = find_exact_duplicates([img1, img2, img3])

        # Should find one group with 2 identical images
        assert len(groups) == 1
        assert groups[0].image_count == 2
        assert groups[0].match_type == "exact"

    def test_no_duplicates(self, sample_images):
        """Test when there are no duplicates."""
        img1 = analyze_image(sample_images['unique'])
        img2 = analyze_image(sample_images['red_large'])

        groups = find_exact_duplicates([img1, img2])
        assert len(groups) == 0

    def test_empty_list(self):
        """Test with empty image list."""
        groups = find_exact_duplicates([])
        assert len(groups) == 0

    def test_filters_errors(self, sample_images):
        """Test that images with errors are filtered out."""
        img1 = analyze_image(sample_images['identical1'])
        img2 = ImageInfo(path="/fake.jpg", error="Test error")

        groups = find_exact_duplicates([img1, img2])
        # Should not crash, img2 should be ignored
        assert len(groups) == 0


class TestCalculatePhashOption:
    """calculate_phash should be threaded all the way through to analyze_image."""

    def test_parallel_skips_phash_when_disabled(self, sample_images):
        """analyze_images_parallel(calculate_phash=False) should skip pHash but still hash."""
        results, stats = analyze_images_parallel(
            [sample_images['unique']],
            use_cache=False,
            show_progress=False,
            calculate_phash=False,
        )
        assert len(results) == 1
        info = results[0]
        assert info.error is None
        assert info.file_hash != ""
        assert info.perceptual_hash == ""

    def test_parallel_computes_phash_by_default(self, sample_images):
        """Default behaviour (calculate_phash=True) is unchanged."""
        results, stats = analyze_images_parallel(
            [sample_images['unique']],
            use_cache=False,
            show_progress=False,
        )
        assert results[0].perceptual_hash != ""


class TestAnalyzeImagesStreaming:
    """Test the chunk-generator variant used for very large libraries."""

    def _chunks(self, paths, chunk_size=2):
        for i in range(0, len(paths), chunk_size):
            yield paths[i:i + chunk_size]

    def test_analyzes_all_files_across_chunks(self, sample_images):
        paths = [sample_images['unique'], sample_images['identical1'], sample_images['identical2']]
        results, stats = analyze_images_streaming(
            self._chunks(paths, chunk_size=2),
            use_cache=False,
        )
        assert {r.path for r in results} == set(paths)
        assert all(r.error is None for r in results)
        assert stats.total_files == len(paths)
        assert stats.cache_misses == len(paths)

    def test_calculate_phash_false_skips_perceptual_hash(self, sample_images):
        paths = [sample_images['unique'], sample_images['identical1']]
        results, stats = analyze_images_streaming(
            self._chunks(paths, chunk_size=2),
            use_cache=False,
            calculate_phash=False,
        )
        assert all(r.perceptual_hash == "" for r in results)
        assert all(r.file_hash != "" for r in results)

    def test_reports_errors_without_crashing(self, sample_images):
        paths = [sample_images['unique'], sample_images['corrupted']]
        results, stats = analyze_images_streaming(
            self._chunks(paths, chunk_size=2),
            use_cache=False,
        )
        by_path = {r.path: r for r in results}
        assert by_path[sample_images['unique']].error is None
        assert by_path[sample_images['corrupted']].error is not None

    def test_discovered_callback_reflects_running_total(self, sample_images):
        paths = [sample_images['unique'], sample_images['identical1'], sample_images['identical2']]
        seen_counts = []
        analyze_images_streaming(
            self._chunks(paths, chunk_size=1),
            use_cache=False,
            discovered_callback=seen_counts.append,
        )
        assert seen_counts == [1, 2, 3]

    def test_calculate_phash_callable_can_flip_mid_scan(self, sample_images):
        """calculate_phash may be a zero-arg callable re-evaluated per file,
        so a caller can disable pHash partway through a scan once it decides
        the collection is too large for perceptual matching to be worthwhile
        - without having to know the final file count up front."""
        paths = [sample_images['unique'], sample_images['identical1'], sample_images['identical2']]
        disabled = {'flag': False}

        def _phash_flag():
            return not disabled['flag']

        def _chunks_that_disable_after_first():
            yield [paths[0]]
            disabled['flag'] = True
            yield [paths[1], paths[2]]

        results, stats = analyze_images_streaming(
            _chunks_that_disable_after_first(),
            use_cache=False,
            calculate_phash=_phash_flag,
        )
        by_path = {r.path: r for r in results}
        assert by_path[paths[0]].perceptual_hash != ""
        assert by_path[paths[1]].perceptual_hash == ""
        assert by_path[paths[2]].perceptual_hash == ""

    def test_uses_and_populates_cache(self, sample_images, temp_cache_db, monkeypatch):
        from pixsieve.database import ImageCache
        import pixsieve.scanner.parallel as parallel_mod

        cache = ImageCache(db_path=temp_cache_db)
        monkeypatch.setattr(parallel_mod, 'get_cache', lambda: cache)

        # Pre-warm the cache for one of the two files.
        precomputed = analyze_image(sample_images['unique'])
        cache.put(precomputed)

        paths = [sample_images['unique'], sample_images['identical1']]
        results, stats = analyze_images_streaming(
            self._chunks(paths, chunk_size=2),
            use_cache=True,
        )

        assert stats.cache_hits == 1
        assert stats.cache_misses == 1
        assert {r.path for r in results} == set(paths)

        # The freshly-analyzed file should now be persisted too. put_batch()
        # goes through the background writer and get() doesn't flush it
        # (unlike get_batch()), so flush first to avoid racing the write.
        cache.flush_writes()
        cached_now = cache.get(sample_images['identical1'])
        assert cached_now is not None
        assert cached_now.file_hash != ""

    def test_cancel_check_stops_discovery_early(self, sample_images):
        paths = [sample_images['unique'], sample_images['identical1'], sample_images['identical2']]
        seen = {'chunks': 0}

        def _cancel_after_first_chunk():
            return seen['chunks'] >= 1

        def _counting_chunks():
            for chunk in self._chunks(paths, chunk_size=1):
                seen['chunks'] += 1
                yield chunk

        results, stats = analyze_images_streaming(
            _counting_chunks(),
            use_cache=False,
            cancel_check=_cancel_after_first_chunk,
        )
        # Discovery should stop after (at most) the first couple of chunks
        # rather than walking the whole generator.
        assert len(results) < len(paths)
