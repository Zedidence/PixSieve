"""
Favorite/star rating tag operations.

Provides functionality to find and remove 5-star / favorite rating tags
(Rating, RatingPercent, XMP:Rating, EXIF:Rating) from images via the
external `exiftool` binary. Ported from the standalone faveRemover script.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
from pathlib import Path

from ..config import RATING_EXTENSIONS
from ..utils import find_files, make_progress_bar
from ..utils.platform import check_exiftool_available

logger = logging.getLogger(__name__)

_DETECT_BATCH_SIZE = 200
_REMOVE_BATCH_SIZE = 100
_RATING_KEYS = ("Rating", "XMP:Rating", "EXIF:Rating", "XMP-xmp:Rating")


class _ExifToolSession:
    """
    A persistent `exiftool -stay_open` process shared across many batches.

    A plain `subprocess.run([exiftool_path, ...])` call pays exiftool's
    process-startup cost (Perl interpreter init, ~100-400ms on Windows,
    often worse under antivirus real-time scanning of the freshly-launched
    exe) every time it's invoked. For an operation that issues one command
    per batch of files, that cost is paid once per batch and dominates
    runtime on large libraries.

    This class launches exiftool once in `-stay_open True -@ -` mode and
    feeds it commands over its stdin/stdout pipes for the whole session,
    so the process-launch cost is paid exactly once. Use it as a context
    manager:

        with _ExifToolSession(exiftool_path) as session:
            stdout, stderr = session.execute(["-ver"])
    """

    def __init__(self, exiftool_path: str) -> None:
        """
        Launch the persistent exiftool process and start draining its stderr.

        `-charset filename=utf8` is required here: in `-stay_open` mode,
        filenames are sent to exiftool as UTF-8 text over the `-@ -` argfile
        pipe rather than passed as native OS argv, so exiftool must be told
        that encoding explicitly. Without it, non-ASCII filenames (accented
        characters, CJK, emoji -- common in real photo libraries) get
        mangled and produce spurious "file not found" failures.

        Args:
            exiftool_path: Path (or PATH-resolved name) of the exiftool
                executable to launch.

        Raises:
            OSError: If the process could not be started.
        """
        self._proc = subprocess.Popen(
            [exiftool_path, "-stay_open", "True", "-@", "-", "-charset", "filename=utf8"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._tag = 0
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        """
        Continuously read exiftool's stderr into a locked buffer.

        This must run for the entire lifetime of the session. exiftool's
        "{readyN}" completion sentinel is written only to stdout, never to
        stderr, so nothing else synchronizes on stderr. If it were left
        unread and exiftool wrote enough warning/error text to fill the OS
        pipe buffer, exiftool would block writing to stderr while the main
        thread is simultaneously blocked in `execute()` waiting for the
        stdout sentinel -- a permanent deadlock, since neither side would
        ever unblock the other.
        """
        try:
            for line in self._proc.stderr:
                with self._stderr_lock:
                    self._stderr_lines.append(line)
        except (ValueError, OSError):
            # Pipe closed out from under us during shutdown; nothing to do.
            pass

    def execute(self, args: list[str]) -> tuple[str, str]:
        """
        Run one exiftool command against the persistent process.

        Args:
            args: exiftool arguments for this command, one per list item
                (each is written as its own line of the `-@` argfile, so no
                shell-style splitting or quoting is needed or applied).

        Returns:
            Tuple of (stdout_text, stderr_text) produced by this command.
            stderr_text is a best-effort snapshot taken right after this
            command's completion sentinel is seen; it may occasionally
            include a stray trailing line from an adjacent call, which is
            acceptable since it is only used for coarse batch-level logging.

        Raises:
            RuntimeError: If the exiftool process has died (a broken pipe
                on write, or EOF while waiting for the completion sentinel).
        """
        self._tag += 1
        tag = self._tag
        sentinel = f"{{ready{tag}}}"

        try:
            for arg in args:
                self._proc.stdin.write(f"{arg}\n")
            self._proc.stdin.write(f"-execute{tag}\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise RuntimeError("exiftool session is no longer running") from exc

        lines: list[str] = []
        while True:
            line = self._proc.stdout.readline()
            if line == "":
                raise RuntimeError("exiftool session ended unexpectedly (EOF)")
            if line.rstrip("\r\n") == sentinel:
                break
            lines.append(line)

        with self._stderr_lock:
            stderr_text = "".join(self._stderr_lines)
            self._stderr_lines.clear()

        return "".join(lines), stderr_text

    def close(self) -> None:
        """
        Shut down the persistent exiftool process.

        Asks exiftool to exit cleanly (`-stay_open False`); if it doesn't
        exit promptly, kills it. Safe to call even if the process already
        died on its own.
        """
        proc = self._proc
        if proc.poll() is None:
            try:
                proc.stdin.write("-stay_open\nFalse\n")
                proc.stdin.flush()
                proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                try:
                    # Bounded even here: kill() only requests termination,
                    # and a process stuck in uninterruptible I/O (a stalled
                    # network share, a filter/AV driver holding the file
                    # open) may not be reapable promptly. An unbounded
                    # wait() here would hang close() -- and every caller
                    # blocked in its `with` block's __exit__ -- forever.
                    proc.wait(timeout=5)
                except Exception:
                    pass
        self._stderr_thread.join(timeout=2)

    def __enter__(self) -> "_ExifToolSession":
        """Return self, so the session can be used as a context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close the session on exit from the `with` block."""
        self.close()


def _is_favorited(item: dict) -> bool:
    """Return True if an exiftool JSON record indicates a 5-star / favorite rating."""
    for key in _RATING_KEYS:
        val = item.get(key)
        if val is not None:
            try:
                if int(val) >= 5:
                    return True
            except (ValueError, TypeError):
                pass

    val = item.get("RatingPercent")
    if val is not None:
        try:
            if int(val) >= 99:
                return True
        except (ValueError, TypeError):
            pass

    return False


def _find_favorited(files: list[Path], session: _ExifToolSession) -> tuple[list[Path], int]:
    """
    Batch-scan files with exiftool and return those carrying a favorite rating.

    Returns:
        Tuple of (favorited_paths, files_scanned). `files_scanned` is the
        count of files whose batches were actually sent to exiftool; it is
        less than len(files) if the persistent session died partway through
        (a permanent condition -- once it happens, no further batches are
        attempted), so callers can report an accurate scanned count instead
        of assuming every discovered file was checked.
    """
    favorited: list[Path] = []
    scanned = 0
    session_died = False

    batches = [files[i:i + _DETECT_BATCH_SIZE] for i in range(0, len(files), _DETECT_BATCH_SIZE)]
    for batch in make_progress_bar(batches, desc="Scanning ratings", unit="batch"):
        if session_died:
            # The exiftool process is gone for good (broken pipe / EOF);
            # retrying would just fail again. Leave these files unscanned
            # rather than silently counting them as checked.
            continue

        args = [
            "-json", "-fast2",
            "-Rating", "-RatingPercent",
            "-XMP:Rating", "-EXIF:Rating",
        ] + [str(p) for p in batch]

        try:
            stdout_text, stderr_text = session.execute(args)
        except RuntimeError as exc:
            logger.warning(f"Rating scan batch failed, exiftool session died: {exc}")
            session_died = True
            continue

        scanned += len(batch)

        if stderr_text.strip():
            # Detection is read-only and best-effort: a warning on one file
            # in a batch shouldn't discard the JSON results for the rest.
            logger.debug(f"exiftool scan batch stderr: {stderr_text.strip()}")

        if stdout_text.strip():
            try:
                for item in json.loads(stdout_text):
                    if _is_favorited(item):
                        favorited.append(Path(item["SourceFile"]))
            except json.JSONDecodeError as exc:
                logger.warning(f"Rating scan batch failed: {exc}")

    return favorited, scanned


def _remove_ratings(favorited: list[Path], session: _ExifToolSession) -> tuple[int, list[Path]]:
    """
    Batch-remove favorite rating tags via exiftool -overwrite_original.

    Note: exiftool errors are reported per-batch, not per-file; a single bad
    file in a batch of up to 100 marks the whole batch failed.

    Returns:
        Tuple of (success_count, failed_paths).
    """
    success = 0
    failed: list[Path] = []
    session_died = False

    batches = [
        favorited[i:i + _REMOVE_BATCH_SIZE]
        for i in range(0, len(favorited), _REMOVE_BATCH_SIZE)
    ]
    for batch in make_progress_bar(batches, desc="Removing ratings", unit="batch"):
        if session_died:
            # The session is permanently gone; every remaining batch is
            # unattempted and must still be accounted for as failed so
            # success + failed keeps reconciling against len(favorited)
            # instead of silently dropping files from both tallies.
            failed.extend(batch)
            continue

        args = [
            "-Rating=", "-RatingPercent=",
            "-XMP:Rating=", "-EXIF:Rating=",
            "-overwrite_original",
        ] + [str(p) for p in batch]

        try:
            stdout_text, stderr_text = session.execute(args)
        except RuntimeError as exc:
            logger.error(f"exiftool batch removal failed, session died: {exc}")
            failed.extend(batch)
            session_died = True
            continue

        if stderr_text.strip():
            logger.error(f"exiftool batch removal failed: {stderr_text.strip()}")
            failed.extend(batch)
            continue

        updated = len(batch)
        for line in stdout_text.splitlines():
            if "image files updated" in line or "image file updated" in line:
                try:
                    updated = int(line.strip().split()[0])
                except (ValueError, IndexError):
                    pass
                break
        success += updated

    return success, failed


def strip_favorite_ratings(
    directory: str | Path,
    recursive: bool = True,
    dry_run: bool = False,
    extensions: set[str] | None = None,
) -> dict[str, int]:
    """
    Find and remove 5-star/favorite rating tags from images in a directory.

    A file counts as "favorited" if its Rating/XMP:Rating/EXIF:Rating/
    XMP-xmp:Rating tag is >= 5, or its RatingPercent is >= 99. Removal uses
    exiftool's -overwrite_original, so no backup copy is created (consistent
    with every other PixSieve operation, which rely on dry-run as the sole
    safety net rather than creating backups).

    Args:
        directory: Directory to scan for images
        recursive: Search subdirectories (default: True)
        dry_run: If True, only report what would be changed (default: False)
        extensions: Set of file extensions to scan (default: RATING_EXTENSIONS).
            exiftool's rating tags apply equally to video containers, so
            callers can pass config.resolve_extensions(RATING_EXTENSIONS,
            include_videos=True) to also strip ratings from video files.

    Returns:
        Dictionary with statistics:
            - scanned: Number of image files scanned
            - favorited: Number of files with a favorite rating found
            - success: Number of ratings successfully stripped (or would be, in dry-run)
            - failed: Number of files that failed removal
            - files: List of affected file paths (as strings)

    Notes:
        - Requires the exiftool binary on PATH; returns zeroed stats and logs
          an error if it is not available.
        - Uses a single persistent exiftool process (-stay_open) for every
          batch in the operation, rather than launching a new process per
          batch, to avoid paying exiftool's startup cost repeatedly.
        - Destructive: files are modified in place with no backup.
    """
    stats: dict = {'scanned': 0, 'favorited': 0, 'success': 0, 'failed': 0, 'files': []}

    available, reason = check_exiftool_available()
    if not available:
        logger.error(f"exiftool not available: {reason}")
        return stats

    exiftool_path = shutil.which("exiftool")

    exts = extensions or RATING_EXTENSIONS
    files = find_files(Path(directory), exts, recursive)
    stats['scanned'] = len(files)
    if not files:
        logger.info("No image files found")
        return stats

    try:
        with _ExifToolSession(exiftool_path) as session:
            logger.info(f"Found {len(files)} image(s); checking for favorite ratings...")
            favorited, scanned = _find_favorited(files, session)
            stats['scanned'] = scanned
            stats['favorited'] = len(favorited)
            stats['files'] = [str(p) for p in favorited]

            if not favorited:
                logger.info("No favorited images found")
                return stats

            if dry_run:
                for p in favorited:
                    logger.info(f"[DRY RUN] Would strip favorite rating: {p}")
                stats['success'] = len(favorited)
                return stats

            success, failed = _remove_ratings(favorited, session)
            stats['success'] = success
            stats['failed'] = len(failed)
            for p in failed:
                logger.error(f"Failed to strip rating: {p}")
    except OSError as exc:
        logger.error(f"Failed to start exiftool session: {exc}")
        return stats

    return stats


__all__ = ['strip_favorite_ratings']
