"""
Unit tests for pixsieve/scanner/file_discovery.py.

Covers the hardlink/symlink identity-based dedup fix and the multi-root
reference-overlap ordering contract - see the duplicate-finding improvement
plan (Phase 1 item #1, Phase 4 items #14/#15/#20).
"""

import os
import logging

import pytest
from PIL import Image

from pixsieve.scanner.file_discovery import (
    find_image_files,
    find_image_files_multi,
    iter_image_chunks_multi,
)

_HARDLINK_SUPPORTED = hasattr(os, 'link')


def _make_image(path):
    Image.new('RGB', (10, 10), 'red').save(path, 'JPEG')


@pytest.mark.skipif(not _HARDLINK_SUPPORTED, reason="os.link not available on this platform")
class TestHardlinkDedup:
    def test_hardlinked_file_counted_once(self, temp_dir):
        original = temp_dir / "photo.jpg"
        hardlink = temp_dir / "photo_hardlink.jpg"
        _make_image(original)
        try:
            os.link(original, hardlink)
        except OSError as e:
            pytest.skip(f"could not create hardlink on this filesystem: {e}")

        files = find_image_files(temp_dir)
        assert len(files) == 1

    def test_hardlink_dedup_independent_of_resolve_symlinks(self, temp_dir):
        original = temp_dir / "photo.jpg"
        hardlink = temp_dir / "photo_hardlink.jpg"
        _make_image(original)
        try:
            os.link(original, hardlink)
        except OSError as e:
            pytest.skip(f"could not create hardlink on this filesystem: {e}")

        files = find_image_files(temp_dir, resolve_symlinks=False)
        assert len(files) == 1

    def test_two_distinct_files_both_counted(self, temp_dir):
        """Sanity control: hardlink dedup must not over-merge unrelated files."""
        _make_image(temp_dir / "a.jpg")
        _make_image(temp_dir / "b.jpg")
        files = find_image_files(temp_dir)
        assert len(files) == 2


class TestSymlinkDedup:
    def _make_symlink(self, target, link_path):
        try:
            os.symlink(target, link_path)
        except OSError as e:
            pytest.skip(f"symlinks not permitted in this environment: {e}")

    def test_symlink_deduped_when_resolve_symlinks_true(self, temp_dir):
        original = temp_dir / "photo.jpg"
        link = temp_dir / "photo_link.jpg"
        _make_image(original)
        self._make_symlink(original, link)

        files = find_image_files(temp_dir, resolve_symlinks=True)
        assert len(files) == 1

    def test_symlink_still_deduped_when_resolve_symlinks_false(self, temp_dir):
        """As a side effect of identity-based dedup (Path.stat() always
        follows symlinks to the target's identity), a symlink pointing at an
        already-discovered file is deduped even with resolve_symlinks=False -
        this used to be a documented gap; it no longer is."""
        original = temp_dir / "photo.jpg"
        link = temp_dir / "photo_link.jpg"
        _make_image(original)
        self._make_symlink(original, link)

        files = find_image_files(temp_dir, resolve_symlinks=False)
        assert len(files) == 1


class TestMultiRootReferenceOverlap:
    def test_reference_root_first_attributes_shared_file_as_reference(self, temp_dir):
        ref_dir = temp_dir / "reference"
        ref_dir.mkdir()
        _make_image(ref_dir / "shared.jpg")

        # outer_dir "overlaps" ref_dir by containing it as a subdirectory
        outer_dir = temp_dir

        results = find_image_files_multi(
            [(str(ref_dir), True), (str(outer_dir), False)],
        )
        shared_entries = [is_ref for path, is_ref in results if "shared.jpg" in path]
        assert shared_entries == [True]

    def test_wrong_order_misattributes_shared_file_as_non_reference(self, temp_dir, caplog):
        """Documents the current contract-violation behavior (not a bug fix -
        the docstring says callers must order roots correctly) and confirms
        the new hardening warning fires when they don't."""
        ref_dir = temp_dir / "reference"
        ref_dir.mkdir()
        _make_image(ref_dir / "shared.jpg")
        outer_dir = temp_dir

        with caplog.at_level(logging.WARNING, logger="pixsieve.scanner.file_discovery"):
            results = find_image_files_multi(
                [(str(outer_dir), False), (str(ref_dir), True)],  # wrong order
            )

        shared_entries = [is_ref for path, is_ref in results if "shared.jpg" in path]
        assert shared_entries == [False]  # mis-attributed, as documented
        assert any("reference root" in record.message for record in caplog.records)

    def test_correct_order_emits_no_warning(self, temp_dir, caplog):
        ref_dir = temp_dir / "reference"
        ref_dir.mkdir()
        _make_image(ref_dir / "shared.jpg")

        with caplog.at_level(logging.WARNING, logger="pixsieve.scanner.file_discovery"):
            list(iter_image_chunks_multi([(str(ref_dir), True), (str(temp_dir), False)]))

        assert not any("reference root" in record.message for record in caplog.records)

    def test_non_overlapping_roots_no_reference_needed(self, temp_dir):
        """A single non-reference root, or roots with no reference at all,
        must never trigger the ordering warning."""
        dir_a = temp_dir / "a"
        dir_b = temp_dir / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        _make_image(dir_a / "x.jpg")
        _make_image(dir_b / "y.jpg")

        results = find_image_files_multi([(str(dir_a), False), (str(dir_b), False)])
        assert len(results) == 2
