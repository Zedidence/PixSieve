"""
Unit tests for pixsieve/utils/selection.py.

This module directly decides which files get kept vs. deleted, so it had
zero test coverage before this file - see the duplicate-finding improvement
plan (Phase 1, item #12).
"""

import os
import time

import pytest

from pixsieve.models import DuplicateGroup, ImageInfo
from pixsieve.utils.selection import (
    SelectionStrategy,
    apply_selection_strategy,
    resolve_group_selections,
    stamp_group_selections,
    group_keep_and_delete,
)


def _img(path, **kwargs) -> ImageInfo:
    return ImageInfo(path=path, **kwargs)


def _group(gid, images, match_type="perceptual") -> DuplicateGroup:
    return DuplicateGroup(id=gid, images=images, match_type=match_type)


class TestApplySelectionStrategyQuality:
    def test_quality_keeps_highest_score(self):
        images = [
            _img("/a.jpg", quality_score=50.0),
            _img("/b.jpg", quality_score=90.0),
            _img("/c.jpg", quality_score=10.0),
        ]
        selections = apply_selection_strategy([_group(1, images)], "quality")
        assert selections["/b.jpg"] == "keep"
        assert selections["/a.jpg"] == "delete"
        assert selections["/c.jpg"] == "delete"

    def test_invalid_strategy_falls_back_to_quality(self):
        images = [
            _img("/a.jpg", quality_score=50.0),
            _img("/b.jpg", quality_score=90.0),
        ]
        selections = apply_selection_strategy([_group(1, images)], "not-a-real-strategy")
        assert selections["/b.jpg"] == "keep"
        assert selections["/a.jpg"] == "delete"

    def test_empty_group_contributes_no_selections(self):
        selections = apply_selection_strategy([_group(1, [])], "quality")
        assert selections == {}

    def test_multiple_groups_all_resolved(self):
        g1 = _group(1, [_img("/a.jpg", quality_score=10.0), _img("/b.jpg", quality_score=20.0)])
        g2 = _group(2, [_img("/c.jpg", quality_score=5.0), _img("/d.jpg", quality_score=1.0)])
        selections = apply_selection_strategy([g1, g2], "quality")
        assert selections["/b.jpg"] == "keep"
        assert selections["/c.jpg"] == "keep"
        assert len(selections) == 4


class TestApplySelectionStrategyLargestSmallest:
    def test_largest_keeps_biggest_file(self):
        images = [_img("/a.jpg", file_size=100), _img("/b.jpg", file_size=500)]
        selections = apply_selection_strategy([_group(1, images)], "largest")
        assert selections["/b.jpg"] == "keep"
        assert selections["/a.jpg"] == "delete"

    def test_smallest_keeps_smallest_file(self):
        images = [_img("/a.jpg", file_size=100), _img("/b.jpg", file_size=500)]
        selections = apply_selection_strategy([_group(1, images)], "smallest")
        assert selections["/a.jpg"] == "keep"
        assert selections["/b.jpg"] == "delete"


class TestApplySelectionStrategyNewestOldest:
    def test_newest_keeps_most_recently_modified(self, temp_dir):
        old_path = temp_dir / "old.jpg"
        new_path = temp_dir / "new.jpg"
        old_path.write_bytes(b"x")
        new_path.write_bytes(b"x")
        now = time.time()
        os.utime(old_path, (now - 1000, now - 1000))
        os.utime(new_path, (now, now))

        images = [_img(str(old_path)), _img(str(new_path))]
        selections = apply_selection_strategy([_group(1, images)], "newest")
        assert selections[str(new_path)] == "keep"
        assert selections[str(old_path)] == "delete"

    def test_oldest_keeps_least_recently_modified(self, temp_dir):
        old_path = temp_dir / "old.jpg"
        new_path = temp_dir / "new.jpg"
        old_path.write_bytes(b"x")
        new_path.write_bytes(b"x")
        now = time.time()
        os.utime(old_path, (now - 1000, now - 1000))
        os.utime(new_path, (now, now))

        images = [_img(str(old_path)), _img(str(new_path))]
        selections = apply_selection_strategy([_group(1, images)], "oldest")
        assert selections[str(old_path)] == "keep"
        assert selections[str(new_path)] == "delete"

    def test_capture_date_takes_priority_over_mtime(self, temp_dir):
        """A file's EXIF capture_date must win over filesystem mtime for
        NEWEST/OLDEST - mtime is frequently wrong (e.g. after a copy/sync)
        while EXIF DateTimeOriginal is the semantically correct field."""
        older_by_mtime_path = temp_dir / "a.jpg"
        newer_by_mtime_path = temp_dir / "b.jpg"
        older_by_mtime_path.write_bytes(b"x")
        newer_by_mtime_path.write_bytes(b"x")
        now = time.time()
        # Filesystem says "a" is older, "b" is newer...
        os.utime(older_by_mtime_path, (now - 1000, now - 1000))
        os.utime(newer_by_mtime_path, (now, now))

        # ...but EXIF capture_date says the opposite is true.
        images = [
            _img(str(older_by_mtime_path), capture_date=now),           # actually newest by EXIF
            _img(str(newer_by_mtime_path), capture_date=now - 1000),    # actually oldest by EXIF
        ]
        selections = apply_selection_strategy([_group(1, images)], "newest")
        assert selections[str(older_by_mtime_path)] == "keep"  # EXIF wins, not mtime
        assert selections[str(newer_by_mtime_path)] == "delete"

    def test_falls_back_to_mtime_when_capture_date_absent(self, temp_dir):
        old_path = temp_dir / "old.jpg"
        new_path = temp_dir / "new.jpg"
        old_path.write_bytes(b"x")
        new_path.write_bytes(b"x")
        now = time.time()
        os.utime(old_path, (now - 1000, now - 1000))
        os.utime(new_path, (now, now))

        images = [_img(str(old_path)), _img(str(new_path))]  # no capture_date on either
        selections = apply_selection_strategy([_group(1, images)], "newest")
        assert selections[str(new_path)] == "keep"

    def test_missing_file_does_not_crash_newest_or_oldest(self):
        """_get_capture_or_mtime/_get_capture_or_mtime_reverse fall back to
        0.0/inf on OSError rather than raising, for files with no
        capture_date that no longer exist on disk."""
        images = [_img("/does/not/exist/a.jpg"), _img("/does/not/exist/b.jpg")]
        newest = apply_selection_strategy([_group(1, images)], "newest")
        oldest = apply_selection_strategy([_group(2, images)], "oldest")
        assert set(newest.values()) == {"keep", "delete"}
        assert set(oldest.values()) == {"keep", "delete"}


class TestResolveGroupSelectionsReferenceAware:
    def test_no_reference_images_falls_back_to_strategy(self):
        images = [_img("/a.jpg", quality_score=10.0), _img("/b.jpg", quality_score=90.0)]
        selections = resolve_group_selections([_group(1, images)], "quality")
        assert selections["/b.jpg"] == "keep"
        assert selections["/a.jpg"] == "delete"

    def test_reference_image_wins_regardless_of_quality(self):
        """A low-quality reference image must still be kept over a
        higher-quality non-reference copy - the whole point of a reference
        folder is 'this is canonical truth', not 'this is the best copy'."""
        images = [
            _img("/ref/a.jpg", quality_score=5.0, is_reference=True),
            _img("/other/b.jpg", quality_score=95.0, is_reference=False),
        ]
        selections = resolve_group_selections([_group(1, images)], "quality")
        assert selections["/ref/a.jpg"] == "keep"
        assert selections["/other/b.jpg"] == "delete"

    def test_reference_image_wins_regardless_of_size_or_mtime_strategy(self):
        images = [
            _img("/ref/a.jpg", file_size=1, is_reference=True),
            _img("/other/b.jpg", file_size=999999, is_reference=False),
        ]
        for strategy in ("largest", "smallest", "newest", "oldest"):
            selections = resolve_group_selections([_group(1, images)], strategy)
            assert selections["/ref/a.jpg"] == "keep", f"failed for strategy={strategy}"
            assert selections["/other/b.jpg"] == "delete", f"failed for strategy={strategy}"

    def test_multiple_reference_images_all_kept(self):
        """A group made entirely of reference-folder duplicates (no
        non-reference copy present) has nothing to auto-delete - all
        reference images resolve to 'keep', by design."""
        images = [
            _img("/ref/a.jpg", is_reference=True),
            _img("/ref/b.jpg", is_reference=True),
        ]
        selections = resolve_group_selections([_group(1, images)], "quality")
        assert selections["/ref/a.jpg"] == "keep"
        assert selections["/ref/b.jpg"] == "keep"

    def test_mixed_reference_and_non_reference_all_non_reference_deleted(self):
        images = [
            _img("/ref/a.jpg", is_reference=True),
            _img("/other/b.jpg", is_reference=False),
            _img("/other/c.jpg", is_reference=False),
        ]
        selections = resolve_group_selections([_group(1, images)], "quality")
        assert selections["/ref/a.jpg"] == "keep"
        assert selections["/other/b.jpg"] == "delete"
        assert selections["/other/c.jpg"] == "delete"

    def test_empty_group_contributes_no_selections(self):
        assert resolve_group_selections([_group(1, [])], "quality") == {}


class TestGroupKeepAndDelete:
    def test_representative_is_the_kept_image(self):
        images = [_img("/a.jpg", quality_score=10.0), _img("/b.jpg", quality_score=90.0)]
        group = _group(1, images)
        selections = resolve_group_selections([group], "quality")
        keep, deletes = group_keep_and_delete(group, selections)
        assert keep.path == "/b.jpg"
        assert [d.path for d in deletes] == ["/a.jpg"]

    def test_reference_representative_is_first_reference_in_stored_order(self):
        """When multiple images are marked 'keep' (all-reference group), the
        representative is the FIRST reference image in group.images order,
        not necessarily the highest-quality one."""
        images = [
            _img("/ref/low_quality_first.jpg", quality_score=1.0, is_reference=True),
            _img("/ref/high_quality_second.jpg", quality_score=99.0, is_reference=True),
        ]
        group = _group(1, images)
        selections = resolve_group_selections([group], "quality")
        keep, deletes = group_keep_and_delete(group, selections)
        assert keep.path == "/ref/low_quality_first.jpg"
        assert deletes == []

    def test_fallback_to_best_image_when_selections_missing(self):
        """If a group's images aren't in the selections map at all (e.g. a
        caller-supplied selections dict from an unrelated/incomplete run),
        group_keep_and_delete falls back to the group's quality-based
        best_image - documenting this fallback seam explicitly, since it's
        the same quality-only path DuplicateGroup.to_dict() falls back to."""
        images = [_img("/a.jpg", quality_score=10.0), _img("/b.jpg", quality_score=90.0)]
        group = _group(1, images)
        keep, deletes = group_keep_and_delete(group, {})
        assert keep.path == "/b.jpg"  # best_image by quality_score
        assert deletes == []  # nothing is in the (empty) selections as 'delete' either


class TestStampGroupSelections:
    def test_stamps_selected_keep_from_selections(self):
        images = [_img("/a.jpg", quality_score=10.0), _img("/b.jpg", quality_score=90.0)]
        group = _group(1, images)
        selections = resolve_group_selections([group], "quality")
        stamp_group_selections([group], selections)
        assert group.selected_keep == "/b.jpg"

    def test_stamps_reference_keep_not_quality_keep(self):
        """This is the regression test for the DuplicateGroup vs
        resolve_group_selections divergence: to_dict()'s fallback picks the
        highest-quality image when selected_keep is unset, which is WRONG
        for a reference-anchored group. Stamping must make to_dict() return
        the reference image instead."""
        images = [
            _img("/ref/a.jpg", quality_score=1.0, is_reference=True),
            _img("/other/b.jpg", quality_score=99.0, is_reference=False),
        ]
        group = _group(1, images)
        selections = resolve_group_selections([group], "quality")
        stamp_group_selections([group], selections)

        assert group.selected_keep == "/ref/a.jpg"
        # And to_dict() must now agree - its fallback never gets a chance to run.
        assert group.to_dict()["selected_keep"] == "/ref/a.jpg"

    def test_stamps_first_keep_for_all_reference_group(self):
        images = [
            _img("/ref/a.jpg", quality_score=1.0, is_reference=True),
            _img("/ref/b.jpg", quality_score=99.0, is_reference=True),
        ]
        group = _group(1, images)
        selections = resolve_group_selections([group], "quality")
        stamp_group_selections([group], selections)
        assert group.selected_keep == "/ref/a.jpg"
