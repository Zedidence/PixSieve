"""
Unit tests for pixsieve/operations/ratings.py.
"""

import json
import shutil
from unittest.mock import patch, MagicMock

import pytest

from pixsieve.operations.ratings import strip_favorite_ratings, _ExifToolSession


def _make_images(temp_dir, names):
    paths = []
    for name in names:
        p = temp_dir / name
        p.write_bytes(b"fake")  # content irrelevant; the exiftool session is mocked
        paths.append(p)
    return paths


def _mock_session(execute_side_effect):
    """
    Build a fake `_ExifToolSession` class for patching.

    Returns (session_class, session) where `session_class` is what gets
    patched in for `pixsieve.operations.ratings._ExifToolSession` (calling
    it returns `session`), and `session` is a MagicMock whose `.execute()`
    yields `execute_side_effect` in order and whose `__enter__`/`__exit__`
    are no-ops so tests never touch the real process-management logic.
    """
    session = MagicMock()
    session.execute.side_effect = execute_side_effect
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session_class = MagicMock(return_value=session)
    return session_class, session


class TestExiftoolMissing:
    """When exiftool isn't on PATH, fail cleanly without touching the session."""

    def test_returns_zero_stats_when_exiftool_missing(self, temp_dir):
        _make_images(temp_dir, ["a.jpg"])
        session_class, _session = _mock_session([])
        with patch(
            "pixsieve.operations.ratings.check_exiftool_available",
            return_value=(False, "not found"),
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir)

        session_class.assert_not_called()
        assert stats == {'scanned': 0, 'favorited': 0, 'success': 0, 'failed': 0, 'files': []}


class TestNoImagesFound:
    def test_no_images_returns_zero_stats(self, temp_dir):
        session_class, _session = _mock_session([])
        with patch(
            "pixsieve.operations.ratings.check_exiftool_available",
            return_value=(True, ""),
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir)
        assert stats['scanned'] == 0
        assert stats['favorited'] == 0
        session_class.assert_not_called()  # no files -> no session is ever spun up


class TestDryRun:
    def test_dry_run_lists_but_does_not_call_removal(self, temp_dir):
        files = _make_images(temp_dir, ["a.jpg", "b.jpg"])
        detect_stdout = json.dumps([{"SourceFile": str(files[0]), "Rating": 5}])
        session_class, session = _mock_session([(detect_stdout, "")])

        with patch(
            "pixsieve.operations.ratings.check_exiftool_available", return_value=(True, "")
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir, dry_run=True)

        assert session.execute.call_count == 1  # detection only, no removal batch
        assert stats['scanned'] == 2
        assert stats['favorited'] == 1
        assert stats['success'] == 1
        assert stats['failed'] == 0
        assert str(files[0]) in stats['files']
        assert files[0].exists()  # nothing actually touched


class TestRealRun:
    def test_calls_exiftool_with_expected_removal_argv(self, temp_dir):
        files = _make_images(temp_dir, ["a.jpg"])
        detect_stdout = json.dumps([{"SourceFile": str(files[0]), "RatingPercent": 99}])
        remove_stdout = "1 image files updated\n"
        session_class, session = _mock_session([
            (detect_stdout, ""),
            (remove_stdout, ""),
        ])

        with patch(
            "pixsieve.operations.ratings.check_exiftool_available", return_value=(True, "")
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir, dry_run=False)

        removal_call = session.execute.call_args_list[1]
        expected_args = [
            "-Rating=", "-RatingPercent=",
            "-XMP:Rating=", "-EXIF:Rating=", "-overwrite_original",
            str(files[0]),
        ]
        assert removal_call.args[0] == expected_args
        assert stats['success'] == 1
        assert stats['failed'] == 0

    def test_parses_partial_updated_count_from_multi_file_batch(self, temp_dir):
        """
        Regression guard for the `updated` parsing in _remove_ratings: uses a
        batch of 3 files with a stdout count of 2, so the parsed value and
        the len(batch)-fallback are numerically different. A broken parser
        (typo in the substring match, off-by-one in `.split()[0]`, or an
        exception silently swallowed by the except clause) would report
        success == 3 (the fallback) instead of the correct 2, so this test
        -- unlike test_calls_exiftool_with_expected_removal_argv, whose
        single-file batch makes the fallback and the parsed value identical
        -- actually depends on the parsing succeeding.
        """
        files = _make_images(temp_dir, ["a.jpg", "b.jpg", "c.jpg"])
        detect_stdout = json.dumps([
            {"SourceFile": str(files[0]), "Rating": 5},
            {"SourceFile": str(files[1]), "Rating": 5},
            {"SourceFile": str(files[2]), "Rating": 5},
        ])
        remove_stdout = "2 image files updated\n"
        session_class, _session = _mock_session([
            (detect_stdout, ""),
            (remove_stdout, ""),
        ])

        with patch(
            "pixsieve.operations.ratings.check_exiftool_available", return_value=(True, "")
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir, dry_run=False)

        assert stats['success'] == 2  # parsed count, not len(batch) == 3
        assert stats['failed'] == 0

    def test_batch_failure_reflected_in_stats(self, temp_dir):
        files = _make_images(temp_dir, ["a.jpg", "b.jpg"])
        detect_stdout = json.dumps([
            {"SourceFile": str(files[0]), "Rating": 5},
            {"SourceFile": str(files[1]), "Rating": 5},
        ])
        session_class, _session = _mock_session([
            (detect_stdout, ""),
            ("", "exiftool error"),
        ])

        with patch(
            "pixsieve.operations.ratings.check_exiftool_available", return_value=(True, "")
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir, dry_run=False)

        assert stats['failed'] == 2
        assert stats['success'] == 0

    def test_no_favorited_images_short_circuits(self, temp_dir):
        _make_images(temp_dir, ["a.jpg"])
        session_class, session = _mock_session([(json.dumps([]), "")])

        with patch(
            "pixsieve.operations.ratings.check_exiftool_available", return_value=(True, "")
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir, dry_run=False)

        assert session.execute.call_count == 1  # detection only
        assert stats['favorited'] == 0
        assert stats['success'] == 0

    def test_session_death_marks_all_remaining_remove_batches_failed(self, temp_dir):
        """
        Regression guard: when the persistent exiftool session dies mid-
        removal, every batch after the one that failed must still be
        counted (as failed), not silently dropped from both `success` and
        `failed` while still appearing in `stats['files']`.
        """
        files = _make_images(temp_dir, ["a.jpg", "b.jpg", "c.jpg"])
        detect_stdout = json.dumps([{"SourceFile": str(f), "Rating": 5} for f in files])
        session_class, session = _mock_session([
            (detect_stdout, ""),           # detection: one batch, all 3 favorited
            RuntimeError("session died"),  # removal batch 1 of 2: session dies
            # No entry for removal batch 2: once the session is known dead,
            # it must not be attempted again -- if it were, this missing
            # side effect would raise StopIteration and fail the test.
        ])

        with patch("pixsieve.operations.ratings._REMOVE_BATCH_SIZE", 2), patch(
            "pixsieve.operations.ratings.check_exiftool_available", return_value=(True, "")
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir, dry_run=False)

        assert stats['favorited'] == 3
        assert stats['success'] == 0
        assert stats['failed'] == 3  # both batches accounted for, none dropped
        assert stats['success'] + stats['failed'] == stats['favorited']
        assert session.execute.call_count == 2  # detect + first removal batch only

    def test_session_death_mid_scan_reports_accurate_scanned_count(self, temp_dir):
        """
        Regression guard: when the persistent exiftool session dies mid-
        detection, stats['scanned'] must reflect only the files actually
        scanned, not the full pre-scan file count -- files in unattempted
        batches were never checked for a favorite rating.
        """
        files = _make_images(temp_dir, ["a.jpg", "b.jpg", "c.jpg"])
        detect_stdout_batch1 = json.dumps([{"SourceFile": str(files[0]), "Rating": 5}])
        session_class, session = _mock_session([
            (detect_stdout_batch1, ""),    # detect batch 1 of 2 (2 files): succeeds
            RuntimeError("session died"),  # detect batch 2 (1 file): session dies
        ])

        with patch("pixsieve.operations.ratings._DETECT_BATCH_SIZE", 2), patch(
            "pixsieve.operations.ratings.check_exiftool_available", return_value=(True, "")
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir, dry_run=True)

        assert stats['scanned'] == 2  # only the first batch was actually scanned
        assert stats['favorited'] == 1
        assert session.execute.call_count == 2  # both detect batches attempted


class TestExtensionsParam:
    """A caller-supplied `extensions` set (e.g. video-inclusive) overrides
    the RATING_EXTENSIONS default; exiftool's commands are extension-agnostic
    so no other code path changes."""

    def test_custom_extensions_includes_video_file(self, temp_dir):
        from pixsieve.config import RATING_EXTENSIONS, resolve_extensions

        video = temp_dir / "clip.mp4"
        video.write_bytes(b"placeholder, not a real video")

        session_class, _session = _mock_session([(json.dumps([]), "")])
        extensions = resolve_extensions(RATING_EXTENSIONS, include_videos=True)

        with patch(
            "pixsieve.operations.ratings.check_exiftool_available", return_value=(True, "")
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ), patch("pixsieve.operations.ratings._ExifToolSession", session_class):
            stats = strip_favorite_ratings(temp_dir, extensions=extensions)

        assert stats['scanned'] == 1  # the .mp4 was included in the scan

    def test_default_extensions_excludes_video(self, temp_dir):
        (temp_dir / "clip.mp4").write_bytes(b"placeholder, not a real video")

        with patch(
            "pixsieve.operations.ratings.check_exiftool_available", return_value=(True, "")
        ), patch(
            "pixsieve.operations.ratings.shutil.which", return_value="exiftool"
        ):
            stats = strip_favorite_ratings(temp_dir)

        assert stats['scanned'] == 0


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
class TestExifToolSessionReal:
    """
    Real-binary tests exercising the actual `-stay_open` persistent-process
    protocol end-to-end (no mocking) -- only run where exiftool is on PATH.
    """

    def test_execute_twice_on_same_session(self):
        """Two calls to execute() on one live session both succeed via the
        -execute<N>/{readyN} handshake, proving the protocol (and the
        stderr-draining thread that keeps it from deadlocking) actually
        work against real exiftool, not just against a mock that assumes
        the protocol is right."""
        exiftool_path = shutil.which("exiftool")
        with _ExifToolSession(exiftool_path) as session:
            stdout1, _stderr1 = session.execute(["-ver"])
            stdout2, _stderr2 = session.execute(["-ver"])

        assert stdout1.strip()
        assert stdout2.strip()
        assert stdout1.strip() == stdout2.strip()

    def test_round_trip_strip(self, temp_dir):
        """A real detect+remove round trip through strip_favorite_ratings,
        which reuses one _ExifToolSession across both phases."""
        from PIL import Image
        import subprocess

        img_path = temp_dir / "rated.jpg"
        Image.new("RGB", (10, 10)).save(img_path, "JPEG")
        subprocess.run(
            ["exiftool", "-overwrite_original", "-XMP:Rating=5", str(img_path)],
            check=True,
        )

        stats = strip_favorite_ratings(temp_dir, dry_run=False)
        assert stats['favorited'] == 1
        assert stats['success'] == 1
