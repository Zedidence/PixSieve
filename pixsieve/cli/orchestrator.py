"""
CLI workflow orchestration for the PixSieve.

Provides the CLIOrchestrator class that coordinates the entire CLI scanning
workflow from argument parsing through final reporting, and routes operations
commands to the OperationsOrchestrator.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Optional

from ..scanner import (
    find_image_files_multi,
    analyze_images_parallel,
    analyze_videos_parallel,
    find_exact_duplicates,
    find_perceptual_duplicates,
    find_video_perceptual_duplicates,
)
from ..config import DEFAULT_WORKERS, LARGE_LIBRARY_THRESHOLD, LARGE_LIBRARY_WORKERS, VIDEO_EXTENSIONS
from ..models import format_size
from ..utils.exporters import export_results
from ..utils.platform import check_symlink_support
from ..utils.selection import resolve_group_selections, stamp_group_selections
from .arg_parser import parse_arguments
from .interactive import prompt_for_directories, confirm_action
from .reporting import print_duplicate_report
from .actions import handle_duplicates
from .operations_orchestrator import OperationsOrchestrator


# Commands that are handled by the OperationsOrchestrator
OPERATIONS_COMMANDS = {
    'move-to-parent', 'move', 'rename', 'sort',
    'fix-extensions', 'convert', 'metadata',
    'cleanup', 'strip-ratings', 'pipeline',
}


def setup_logging(verbose: bool = False, log_file: Optional[str] = None) -> logging.Logger:
    """
    Configure logging for the CLI.

    Args:
        verbose: Enable verbose (DEBUG level) logging
        log_file: Optional path to also write logs to. Useful for long,
            unattended scans — the console/terminal scrollback may be gone
            by the time you notice something took too long, but a log file
            survives and can be grepped for phase timings and slow-file
            warnings after the fact.

    Returns:
        Configured logger instance
    """
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S',
        handlers=handlers,
    )
    logger = logging.getLogger(__name__)
    if log_file:
        logger.info(f"Logging to file: {log_file}")
    return logger


class CLIOrchestrator:
    """
    Orchestrates the CLI scanning workflow.

    Manages the complete lifecycle from argument parsing through duplicate
    detection, reporting, and action execution. Also routes operations
    commands to the OperationsOrchestrator.
    """

    def __init__(self):
        """Initialize the orchestrator."""
        self.logger = None
        self.args = None
        self.image_files = []
        self.video_files = []
        self.images = []
        self.video_images = []
        self.exact_groups = []
        self.perceptual_groups = []
        self._path_is_reference = {}

    def run(self) -> int:
        """
        Execute the complete CLI workflow.

        Returns:
            Exit code (0 for success, 1 for error)
        """
        # Phase 1: Setup
        exit_code = self._setup_phase()
        if exit_code != 0:
            return exit_code

        # Route: if command is an operation, delegate to OperationsOrchestrator
        if self.args.command in OPERATIONS_COMMANDS:
            ops = OperationsOrchestrator(self.args, self.logger)
            return ops.run()

        # Otherwise, run the duplicate detection workflow
        return self._run_duplicates_workflow()

    def _run_duplicates_workflow(self) -> int:
        """
        Execute the duplicate detection workflow (original behavior).

        Returns:
            Exit code (0 for success, 1 for error)

        Workflow phases:
        2. Interactive prompts (if needed)
        3. Validation
        4. Configuration
        5. File scanning
        6. Image analysis
        7. Duplicate detection & reporting
        8. Action execution & cleanup
        """
        # Phase 2: Interactive prompts
        exit_code = self._interactive_phase()
        if exit_code != 0:
            return exit_code

        # Phase 3: Validation
        exit_code = self._validate_phase()
        if exit_code != 0:
            return exit_code

        # Phase 4: Configuration
        self._configure_phase()

        # Phase 5: Scanning
        exit_code = self._scan_phase()
        if exit_code != 0:
            return exit_code

        # Phase 6: Analysis
        exit_code = self._analyze_phase()
        if exit_code != 0:
            return exit_code

        # Phase 7: Detection & Reporting
        self._detect_phase()
        self._report_phase()

        # Phase 8: Actions
        if self.args.action != 'report':
            self._action_phase()

        return 0

    def _setup_phase(self) -> int:
        """
        Phase 1: Parse arguments and setup logging.

        Returns:
            0 for success, non-zero for error
        """
        self.args = parse_arguments()
        self.logger = setup_logging(getattr(self.args, 'verbose', False), getattr(self.args, 'log_file', None))
        return 0

    def _interactive_phase(self) -> int:
        """
        Phase 2: Handle interactive directory prompt if needed.

        Returns:
            0 for success, non-zero for error
        """
        if not self.args.directory:
            directories, reference_dir = prompt_for_directories()
            self.args.directory = directories
            self.args.reference_dir = reference_dir
        return 0

    def _validate_phase(self) -> int:
        """
        Phase 3: Validate arguments and check prerequisites.

        Returns:
            0 for success, 1 for validation error
        """
        # Validate every directory exists
        for d in self.args.directory:
            if not d.exists() or not d.is_dir():
                self.logger.error(f"Directory not found: {d}")
                return 1

        # Reject duplicate directories (after resolution, so trailing
        # slashes/case/relative-vs-absolute variants are caught too)
        resolved = [d.resolve() for d in self.args.directory]
        if len(resolved) != len(set(resolved)):
            self.logger.error("The same directory was specified more than once")
            return 1

        # Validate --reference-dir matches one of the given directories
        if self.args.reference_dir is not None:
            ref_resolved = self.args.reference_dir.resolve()
            if ref_resolved not in resolved:
                self.logger.error(
                    f"--reference-dir must be one of the scanned directories: {self.args.reference_dir}"
                )
                return 1

        # Validate trash-dir for move action
        if self.args.action == 'move' and not self.args.trash_dir:
            self.logger.error("--trash-dir required for 'move' action")
            return 1

        # Create trash directory if needed
        if self.args.trash_dir:
            try:
                if not self.args.trash_dir.exists():
                    self.args.trash_dir.mkdir(parents=True, exist_ok=True)
                    self.logger.info(f"Created trash directory: {self.args.trash_dir}")
            except PermissionError:
                self.logger.error(f"Cannot create trash directory (permission denied): {self.args.trash_dir}")
                return 1
            except OSError as e:
                self.logger.error(f"Cannot create trash directory: {e}")
                return 1

        # Platform-specific checks for hardlink/symlink
        dry_run = not self.args.no_dry_run

        if self.args.action == 'hardlink' and not dry_run:
            import platform as platform_module
            if platform_module.system() == 'Windows':
                self.logger.warning(
                    "Note: Hardlinks on Windows require administrator privileges "
                    "and source/destination must be on the same volume."
                )

        elif self.args.action == 'symlink' and not dry_run:
            import platform as platform_module
            if platform_module.system() == 'Windows':
                for d in self.args.directory:
                    supported, reason = check_symlink_support(d)
                    if not supported:
                        self.logger.error(f"Symlinks not supported for {d}: {reason}")
                        self.logger.info("Tip: Run as Administrator or enable Developer Mode in Windows Settings")
                        return 1

        return 0

    def _configure_phase(self) -> None:
        """Phase 4: Configure runtime options."""
        # Determine cache usage
        self.use_cache = not self.args.no_cache
        if self.args.no_cache:
            self.logger.info("Cache disabled - analyzing all images fresh")

        # Determine LSH mode
        self.use_lsh = None  # Auto-select by default
        if self.args.force_lsh:
            self.use_lsh = True
            self.logger.info("LSH acceleration forced on")
        elif self.args.no_lsh:
            self.use_lsh = False
            self.logger.info("LSH disabled (brute-force mode)")

        # Progress display
        self.show_progress = not self.args.no_progress

    def _scan_phase(self) -> int:
        """
        Phase 5: Scan for image files.

        Returns:
            0 for success, non-zero if no images found
        """
        dir_list = ', '.join(str(d) for d in self.args.directory)
        self.logger.info(f"Scanning {dir_list} for images...")
        if self.show_progress:
            print("Scanning for image files...", end=" ", flush=True)

        _phase_start = time.monotonic()
        recursive = not self.args.no_recursive
        resolve_symlinks = not self.args.no_resolve_symlinks
        ref_resolved = self.args.reference_dir.resolve() if self.args.reference_dir else None
        roots = sorted(
            ((d, d.resolve() == ref_resolved) for d in self.args.directory),
            key=lambda pair: not pair[1],
        )
        include_videos = getattr(self.args, 'include_videos', False)
        results = find_image_files_multi(
            roots, recursive=recursive, resolve_symlinks=resolve_symlinks, include_videos=include_videos
        )
        all_files = [path for path, _ in results]
        self._path_is_reference = {path: is_ref for path, is_ref in results}

        # Split discovered files by media type - image and video files run
        # through separate analysis functions (see _analyze_phase).
        self.image_files = [p for p in all_files if Path(p).suffix.lower() not in VIDEO_EXTENSIONS]
        self.video_files = [p for p in all_files if Path(p).suffix.lower() in VIDEO_EXTENSIONS]

        if self.show_progress:
            print("done!")

        self.logger.info(
            f"Found {len(self.image_files):,} image files"
            + (f" and {len(self.video_files):,} video files" if include_videos else "")
            + f" in {time.monotonic() - _phase_start:.1f}s"
        )

        if not self.image_files and not self.video_files:
            self.logger.info("No images found. Exiting.")
            return 1

        return 0

    def _analyze_phase(self) -> int:
        """
        Phase 6: Analyze images in parallel.

        Returns:
            0 for success
        """
        # Scale up workers for very large libraries, matching the
        # already-materialized-count case where we can make this decision
        # up front. Only applied when the user hasn't explicitly overridden
        # --workers, so an explicit choice (e.g. deliberately throttling
        # concurrency on a slow external drive) is never silently overridden.
        effective_workers = self.args.workers
        if len(self.image_files) >= LARGE_LIBRARY_THRESHOLD and self.args.workers == DEFAULT_WORKERS:
            effective_workers = LARGE_LIBRARY_WORKERS
            self.logger.info(
                f"Large library ({len(self.image_files):,} files >= {LARGE_LIBRARY_THRESHOLD:,}) - "
                f"scaling workers {self.args.workers} -> {effective_workers}"
            )

        self.logger.info(f"Analyzing {len(self.image_files):,} images (this may take a while)...")
        _phase_start = time.monotonic()
        self.images, cache_stats = analyze_images_parallel(
            self.image_files,
            max_workers=effective_workers,
            logger=self.logger,
            show_progress=self.show_progress,
            use_cache=self.use_cache,
            calculate_phash=not self.args.exact_only,
        )
        self.logger.info(f"Analysis done - {len(self.images):,} files in {time.monotonic() - _phase_start:.1f}s")

        # Show cache stats
        if self.use_cache and cache_stats.cache_hits > 0:
            self.logger.info(
                f"Cache: {cache_stats.cache_hits:,} hits, {cache_stats.cache_misses:,} misses "
                f"({cache_stats.hit_rate:.1f}% hit rate)"
            )

        if self.video_files:
            self.logger.info(f"Analyzing {len(self.video_files):,} videos (this may take a while)...")
            _video_phase_start = time.monotonic()
            self.video_images, video_cache_stats = analyze_videos_parallel(
                self.video_files,
                max_workers=effective_workers,
                logger=self.logger,
                show_progress=self.show_progress,
                use_cache=self.use_cache,
                calculate_phash=not self.args.exact_only,
            )
            self.logger.info(
                f"Video analysis done - {len(self.video_images):,} files "
                f"in {time.monotonic() - _video_phase_start:.1f}s"
            )
            if self.use_cache and video_cache_stats.cache_hits > 0:
                self.logger.info(
                    f"Video cache: {video_cache_stats.cache_hits:,} hits, "
                    f"{video_cache_stats.cache_misses:,} misses "
                    f"({video_cache_stats.hit_rate:.1f}% hit rate)"
                )

        # Stamp is_reference on every file (cache-returned objects always
        # default to False, since reference status is scan-local and never
        # cached). Done before the valid/error split so error files are
        # tagged too.
        for img in self.images + self.video_images:
            img.is_reference = self._path_is_reference.get(img.path, False)

        # Filter out errors
        valid_images = [img for img in self.images if not img.error]
        error_count = len(self.images) - len(valid_images)
        if error_count:
            self.logger.warning(f"Could not analyze {error_count:,} files")
        self.images = valid_images

        valid_videos = [v for v in self.video_images if not v.error]
        video_error_count = len(self.video_images) - len(valid_videos)
        if video_error_count:
            self.logger.warning(f"Could not analyze {video_error_count:,} video files")
        self.video_images = valid_videos

        return 0

    def _detect_phase(self) -> None:
        """Phase 7: Detect exact and perceptual duplicates."""
        exact_hashes = set()
        all_media = self.images + self.video_images

        # Find exact duplicates (across images and videos together - hashes
        # never collide across unrelated file types, so this is safe)
        if not self.args.perceptual_only:
            self.logger.info("Finding exact duplicates...")
            _phase_start = time.monotonic()
            self.exact_groups = find_exact_duplicates(all_media)
            exact_hashes = {img.file_hash for g in self.exact_groups for img in g.images}
            self.logger.info(
                f"Found {len(self.exact_groups):,} exact duplicate groups "
                f"in {time.monotonic() - _phase_start:.1f}s"
            )

        # Find perceptual duplicates
        if not self.args.exact_only:
            self.logger.info(f"Finding perceptual duplicates (threshold={self.args.threshold})...")
            _phase_start = time.monotonic()
            self.perceptual_groups = find_perceptual_duplicates(
                self.images,
                threshold=self.args.threshold,
                exclude_hashes=exact_hashes,
                start_id=len(self.exact_groups) + 1,
                show_progress=self.show_progress,
                use_lsh=self.use_lsh,
                logger=self.logger,
            )
            self.logger.info(
                f"Found {len(self.perceptual_groups):,} perceptual duplicate groups "
                f"in {time.monotonic() - _phase_start:.1f}s"
            )

            if self.video_images:
                self.logger.info(f"Finding video perceptual duplicates (threshold={self.args.threshold})...")
                _video_phase_start = time.monotonic()
                video_groups = find_video_perceptual_duplicates(
                    [v for v in self.video_images if v.file_hash not in exact_hashes],
                    threshold=self.args.threshold,
                    start_id=len(self.exact_groups) + len(self.perceptual_groups) + 1,
                )
                self.perceptual_groups.extend(video_groups)
                self.logger.info(
                    f"Found {len(video_groups):,} video perceptual duplicate groups "
                    f"in {time.monotonic() - _video_phase_start:.1f}s"
                )

    def _report_phase(self) -> None:
        """Phase 7b: Generate and display report, handle exports."""
        # Print report
        print_duplicate_report(
            self.exact_groups, self.perceptual_groups, self.logger,
            strategy=self.args.auto_select_strategy,
        )

        # Export if requested
        if self.args.export:
            all_groups = self.exact_groups + self.perceptual_groups
            selections = resolve_group_selections(all_groups, self.args.auto_select_strategy)
            stamp_group_selections(all_groups, selections)
            export_results(
                self.exact_groups,
                self.perceptual_groups,
                self.args.export,
                self.args.export_format,
                selections,
            )
            self.logger.info(f"Results exported to: {self.args.export}")

    def _action_phase(self) -> None:
        """Phase 8: Execute action on duplicates and show statistics."""
        all_groups = self.exact_groups + self.perceptual_groups
        dry_run = not self.args.no_dry_run
        strategy = self.args.auto_select_strategy

        if dry_run:
            self.logger.info("\n[DRY RUN MODE - No files will be modified]")
        else:
            # Confirmation
            selections = resolve_group_selections(all_groups, strategy)
            total_dupes = sum(1 for v in selections.values() if v == 'delete')
            if not confirm_action(self.args.action, total_dupes):
                self.logger.info("Aborted.")
                sys.exit(0)

        stats = handle_duplicates(
            all_groups,
            action=self.args.action,
            trash_dir=self.args.trash_dir,
            dry_run=dry_run,
            logger=self.logger,
            strategy=strategy,
        )

        self.logger.info(f"\nProcessed: {stats['processed']:,} files")
        if stats['skipped'] > 0:
            self.logger.info(f"Skipped: {stats['skipped']:,} files")
        self.logger.info(f"Errors: {stats['errors']}")
        self.logger.info(f"Space {'would be ' if dry_run else ''}saved: {format_size(stats['space_saved'])}")


__all__ = ['CLIOrchestrator', 'setup_logging']
