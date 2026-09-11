"""
Unit tests for pixsieve/operations/repair.py.

Focus: scan_and_repair(dry_run=True) must never modify, rename, or delete
any file on disk. Prior to the fix this suite locks in, _attempt_repair()
ignored dry_run entirely and always wrote to disk.
"""

from pathlib import Path

from PIL import Image

from pixsieve.operations.repair import (
    CorruptionType,
    RepairStatus,
    _attempt_repair,
    scan_and_repair,
)


def _make_truncated_jpeg(path: Path) -> bytes:
    """
    Write a JPEG, then truncate it near the end to simulate corruption that
    PIL can recover from under LOAD_TRUNCATED_IMAGES=True (what the re-encode
    repair strategy relies on) while still failing plain detection (no flag).
    A large, low-detail image keeps the file small enough that even a 10% cut
    lands after the last full MCU rather than inside the header.
    """
    img = Image.new("RGB", (200, 200), color="blue")
    img.save(path, "JPEG", quality=90)
    original = path.read_bytes()
    truncated = original[: int(len(original) * 0.9)]
    path.write_bytes(truncated)
    return truncated


def _snapshot(directory: Path) -> dict[str, tuple[int, float, bytes]]:
    """Map every file under *directory* to (size, mtime_ns, content)."""
    return {
        str(p): (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
        for p in directory.rglob("*")
        if p.is_file()
    }


class TestAttemptRepairDryRun:
    """Unit-level: _attempt_repair() itself must honor dry_run."""

    def test_dry_run_does_not_touch_file_bytes(self, temp_dir):
        path = temp_dir / "truncated.jpg"
        before = _make_truncated_jpeg(path)

        success, attempts = _attempt_repair(str(path), CorruptionType.TRUNCATED, dry_run=True)

        assert path.read_bytes() == before
        assert attempts  # detection/would-repair logic still ran

    def test_dry_run_creates_no_extra_files(self, temp_dir):
        path = temp_dir / "truncated.jpg"
        _make_truncated_jpeg(path)

        _attempt_repair(str(path), CorruptionType.TRUNCATED, dry_run=True)

        assert sorted(p.name for p in temp_dir.iterdir()) == ["truncated.jpg"]

    def test_real_run_still_repairs(self, temp_dir):
        """Positive control: dry_run=False must still perform a real repair."""
        path = temp_dir / "truncated.jpg"
        before = _make_truncated_jpeg(path)

        success, attempts = _attempt_repair(str(path), CorruptionType.TRUNCATED, dry_run=False)

        assert success is True
        assert path.read_bytes() != before
        with Image.open(path) as img:
            img.verify()


class TestScanAndRepairDryRun:
    """Integration-level: scan_and_repair(dry_run=True) must be a true no-op."""

    def test_dry_run_leaves_directory_byte_identical(self, temp_dir):
        clean_path = temp_dir / "clean.jpg"
        Image.new("RGB", (32, 32), color="green").save(clean_path, "JPEG")
        _make_truncated_jpeg(temp_dir / "truncated.jpg")

        before = _snapshot(temp_dir)
        stats = scan_and_repair(
            str(temp_dir),
            trash_folder=str(temp_dir / ".trash"),
            dry_run=True,
        )
        after = _snapshot(temp_dir)

        assert after == before
        assert not (temp_dir / ".trash").exists()
        assert stats["checked"] == 2
        assert stats["clean"] == 1

    def test_dry_run_reports_repaired_without_writing(self, temp_dir):
        _make_truncated_jpeg(temp_dir / "truncated.jpg")
        before = _snapshot(temp_dir)

        stats = scan_and_repair(
            str(temp_dir),
            trash_folder=str(temp_dir / ".trash"),
            dry_run=True,
            max_workers=1,
        )

        result = stats["results"][0]
        assert result.status == RepairStatus.REPAIRED
        assert _snapshot(temp_dir) == before

    def test_real_run_repairs_in_place(self, temp_dir):
        """Positive control: dry_run=False end-to-end still repairs for real."""
        path = temp_dir / "truncated.jpg"
        before = _make_truncated_jpeg(path)

        stats = scan_and_repair(
            str(temp_dir),
            trash_folder=str(temp_dir / ".trash"),
            dry_run=False,
            max_workers=1,
        )

        assert stats["repaired"] == 1
        assert path.read_bytes() != before
        with Image.open(path) as img:
            img.verify()
