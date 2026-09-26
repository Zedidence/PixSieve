"""
Scan orchestration for the PixSieve.

Provides the ScanOrchestrator class that coordinates the scanning process,
including file discovery, image analysis, duplicate detection, and progress tracking.
"""

from __future__ import annotations

import time
import logging
from datetime import datetime
from typing import Callable, Optional

from ..state import ScanState, HistoryManager
from ..scanner import (
    iter_image_chunks_multi,
    analyze_images_streaming,
    analyze_media,
    find_exact_duplicates,
    find_perceptual_duplicates,
    find_video_perceptual_duplicates,
)
from ..models import ImageInfo, DuplicateGroup
from ..database import CacheStats
from ..config import (
    LSH_AUTO_THRESHOLD,
    LARGE_LIBRARY_WORKERS,
    PERCEPTUAL_AUTO_DISABLE_THRESHOLD,
)
from ..utils import formatters, selection
from ..utils.adaptive import make_tuner
from ..utils.worker_policy import OpKind, resolve_workers, reset_caches, warm_up

# Module logger
_logger = logging.getLogger(__name__)


class ProgressTracker:
    """
    Tracks and updates scan progress through various stages.

    Handles progress callbacks, cancel/pause checks, and state updates
    with optimized update frequency to reduce overhead.
    """

    def __init__(self, scan_state: ScanState, save_callback: Callable[[], None]):
        """
        Initialize the progress tracker.

        Args:
            scan_state: Shared scan state object
            save_callback: Callback to save state (thread-safe)
        """
        self.scan_state = scan_state
        self.save_callback = save_callback
        self.last_save_time = time.time()
        self.last_progress_update = time.time()

    def check_cancelled(self) -> bool:
        """Check if scan has been cancelled."""
        return self.scan_state.cancel_requested

    def handle_pause(self) -> None:
        """Block while scan is paused."""
        while self.scan_state.paused and not self.scan_state.cancel_requested:
            time.sleep(0.5)

    def update_analysis_progress(
        self,
        current: int,
        total: int,
        analysis_start_time: float
    ) -> None:
        """
        Update progress during image analysis phase.

        Args:
            current: Current image number
            total: Total images to analyze
            analysis_start_time: Timestamp when analysis started
        """
        # Check for cancel
        if self.check_cancelled():
            return

        # Handle pause
        self.handle_pause()

        current_time = time.time()

        # Optimized: Only update when 0.5 seconds have passed or final update
        should_update = (current_time - self.last_progress_update >= 0.5) or (current == total)

        if should_update:
            self.scan_state.analyzed = current
            self.scan_state.progress = int(current / total * 50)  # 0-50% for analysis
            self.scan_state.stage_progress = int(current / total * 100)

            elapsed = current_time - analysis_start_time

            # Update progress details
            self.scan_state.progress_details['elapsed_seconds'] = current_time - self.scan_state.progress_details['start_time']

            # Update message every 2 seconds
            if current_time - self.last_progress_update >= 2:
                rate = current / elapsed if elapsed > 0 else 0
                remaining = total - current

                self.scan_state.progress_details['rate'] = round(rate, 1)

                if rate > 0:
                    eta_seconds = remaining / rate
                    self.scan_state.progress_details['eta_seconds'] = int(eta_seconds)
                    eta_str = formatters.format_time_estimate(eta_seconds)
                    self.scan_state.message = (
                        f'Analyzing images: {formatters.format_number(current)}/{formatters.format_number(total)} '
                        f'({int(rate)}/sec, ~{eta_str} remaining)'
                    )
                else:
                    self.scan_state.message = (
                        f'Analyzing images: {formatters.format_number(current)}/{formatters.format_number(total)}'
                    )
                self.last_progress_update = current_time

            # Save state every 5 seconds
            if current_time - self.last_save_time > 5:
                self.save_callback()
                self.last_save_time = current_time

    def update_comparison_progress(
        self,
        current: int,
        total: int,
        comparison_start_time: float
    ) -> None:
        """
        Update progress during comparison phase.

        Args:
            current: Current comparison number
            total: Total comparisons to perform
            comparison_start_time: Timestamp when comparisons started
        """
        # Check for cancel
        if self.check_cancelled():
            return

        # Handle pause
        self.handle_pause()

        current_time = time.time()

        # Optimized: Only update when 0.5 seconds have passed or final update
        should_update = (current_time - self.last_progress_update >= 0.5) or (current == total)

        if should_update:
            self.scan_state.progress = 60 + int(current / total * 35)
            self.scan_state.stage_progress = int(current / total * 100)
            self.scan_state.progress_details['comparisons_done'] = current
            self.scan_state.progress_details['elapsed_seconds'] = time.time() - self.scan_state.progress_details['start_time']

            # Update message every 2 seconds with progress
            if current_time - self.last_progress_update >= 2:
                elapsed = current_time - comparison_start_time
                rate = current / elapsed if elapsed > 0 else 0
                remaining = total - current

                self.scan_state.progress_details['rate'] = round(rate, 1)

                if rate > 0:
                    eta_seconds = remaining / rate
                    self.scan_state.progress_details['eta_seconds'] = int(eta_seconds)
                    eta_str = formatters.format_time_estimate(eta_seconds)
                    self.scan_state.message = (
                        f'Comparing images: {formatters.format_number(current)}/{formatters.format_number(total)} '
                        f'({formatters.format_number(int(rate))}/sec, ~{eta_str} remaining)'
                    )
                self.last_progress_update = current_time


class ScanOrchestrator:
    """
    Orchestrates the complete scanning process.

    Coordinates file discovery, image analysis, duplicate detection,
    and result finalization with progress tracking and error handling.
    """

    def __init__(
        self,
        scan_state: ScanState,
        directories: list[dict],
        threshold: int,
        exact_only: bool,
        perceptual_only: bool,
        recursive: bool = True,
        use_cache: bool = True,
        use_lsh: Optional[bool] = None,
        workers: Optional[int] = None,
        resolve_symlinks: bool = True,
        auto_select_strategy: str = 'quality',
        include_videos: bool = False,
        save_callback: Optional[Callable[[], None]] = None,
    ):
        """
        Initialize the scan orchestrator.

        Args:
            scan_state: Shared scan state object
            directories: List of {'path': str, 'is_reference': bool} entries,
                already validated by the caller. At most one entry may have
                is_reference=True.
            threshold: Perceptual hash threshold
            exact_only: Only find exact duplicates
            perceptual_only: Only find perceptual duplicates
            recursive: Scan subdirectories
            use_cache: Use SQLite caching
            use_lsh: Force LSH on/off (None = auto)
            workers: Number of parallel workers; None = pick from the
                drive type of the scanned directories (utils/worker_policy.py)
            resolve_symlinks: Canonicalize each discovered path and dedupe
                files reachable via multiple symlinks. Costs one extra
                filesystem round-trip per file during discovery - disabling
                it is a real speedup on drives with no symlinks (most
                external/removable media), especially over slower interfaces
                like USB where per-syscall latency is higher.
            auto_select_strategy: Selection strategy
            include_videos: Also discover/analyze/deduplicate video files
                (requires opencv-python-headless; falls back to images-only
                with a warning if not installed)
            save_callback: Callback to save state (thread-safe)
        """
        self.scan_state = scan_state
        self.directories = directories
        self.directory = directories[0]['path']  # back-compat: primary directory
        self.threshold = threshold
        self.exact_only = exact_only
        self.perceptual_only = perceptual_only
        self.recursive = recursive
        self.use_cache = use_cache
        self.use_lsh = use_lsh
        self.workers = workers
        self.resolve_symlinks = resolve_symlinks
        self.auto_select_strategy = auto_select_strategy
        self.include_videos = include_videos
        self.save_callback = save_callback or (lambda: None)

        # Track if we auto-disabled perceptual
        self.auto_disabled_perceptual = False

        # Side-channel populated by _discover_and_analyze(): path -> is_reference
        self._path_is_reference: dict[str, bool] = {}

    def run(self) -> None:
        """
        Execute the complete scan process.

        This is the main entry point that orchestrates all scan phases.
        """
        _logger.info(
            f"Scan starting - dirs={[d['path'] for d in self.directories]} "
            f"threshold={self.threshold} exact_only={self.exact_only} "
            f"perceptual_only={self.perceptual_only} cache={self.use_cache} workers={self.workers}"
        )
        try:
            # Initialize scan state
            self.scan_state.reset()
            self.scan_state.status = 'scanning'
            self.scan_state.stage = 'scanning'
            self.scan_state.directory = self.directory
            self.scan_state.directories = self.directories
            self.scan_state.message = 'Scanning for image files...'
            self.scan_state.settings = {
                'threshold': self.threshold,
                'exact_only': self.exact_only,
                'perceptual_only': self.perceptual_only,
                'recursive': self.recursive,
                'use_cache': self.use_cache,
                'use_lsh': self.use_lsh,
                'workers': self.workers,
                'resolve_symlinks': self.resolve_symlinks,
                'auto_select_strategy': self.auto_select_strategy,
                'include_videos': self.include_videos,
            }
            self.scan_state.progress_details['start_time'] = time.time()

            # Save to history
            for d in self.directories:
                HistoryManager.save_directory(d['path'])

            if self.workers is None:
                # Detect drive types while discovery starts; a long-running
                # server re-detects per scan in case a different drive now
                # sits at the same letter/mount point.
                reset_caches()
                warm_up(d['path'] for d in self.directories)

            # Phases 1-2: Discover and analyze images (overlapped)
            result = self._discover_and_analyze()
            if result is None:
                return  # Cancelled, no images found, or error
            images, cache_stats = result

            # Phase 3: Find exact duplicates
            exact_groups, exact_hashes = self._find_exact_dupes(images)
            if exact_groups is None:
                return  # Cancelled

            # Phase 4: Find perceptual duplicates
            perceptual_groups = self._find_perceptual_dupes(images, exact_hashes, len(exact_groups))
            if perceptual_groups is None:
                return  # Cancelled

            # Phase 5: Finalize results
            self._finalize_results(exact_groups, perceptual_groups)

        except Exception as e:
            self.scan_state.status = 'error'
            self.scan_state.message = f'Error: {str(e)}'
            _logger.exception(f"Scan error: {e}")
            self.save_callback()

    def _discover_and_analyze(self) -> Optional[tuple[list[ImageInfo], CacheStats]]:
        """
        Phases 1-2: Discover and analyze images, overlapped rather than
        sequential.

        Directory-walking (discovery) and image analysis now run
        concurrently via analyze_images_streaming() instead of one fully
        finishing before the other starts - for a very large or slow
        (e.g. external/USB) source, the old sequential order meant the whole
        directory walk had to complete before a single image was analyzed.

        Returns:
            Tuple of (valid images, cache stats), or None if cancelled/empty
        """
        # Check for cancel
        if self.scan_state.cancel_requested:
            self.scan_state.status = 'cancelled'
            self.scan_state.message = 'Scan cancelled by user'
            self.save_callback()
            return None

        _logger.info(
            f"Phase: discover+analyze - scanning {[d['path'] for d in self.directories]} "
            f"(cache={'on' if self.use_cache else 'off'}, workers={self.workers})"
        )
        _phase_start = time.time()
        analysis_start_time = _phase_start

        # Reference root (if any) is walked first so files under a nested or
        # overlapping reference folder are always attributed as reference.
        roots = [
            (d['path'], d['is_reference'])
            for d in sorted(self.directories, key=lambda d: not d['is_reference'])
        ]

        def _chunks():
            for chunk in iter_image_chunks_multi(
                roots, recursive=self.recursive, resolve_symlinks=self.resolve_symlinks,
                include_videos=self.include_videos,
            ):
                paths = []
                for path, is_reference in chunk:
                    self._path_is_reference[path] = is_reference
                    paths.append(path)
                yield paths

        progress_tracker = ProgressTracker(self.scan_state, self.save_callback)
        last_discovery_update = [time.time()]
        entered_analyzing = [False]

        def discovered_callback(count: int) -> None:
            now = time.time()

            # First chunk in: analysis of it has already begun by the time we
            # find out about it, so flip to the 'analyzing' stage here rather
            # than waiting for discovery to fully finish (which is exactly
            # the wait this refactor removes).
            if not entered_analyzing[0]:
                entered_analyzing[0] = True
                self.scan_state.status = 'analyzing'
                self.scan_state.stage = 'analyzing'

            if now - last_discovery_update[0] >= 0.5:
                self.scan_state.total_files = count
                self.scan_state.message = (
                    f'Analyzing images... {formatters.format_number(count)} found so far'
                )
                self.scan_state.progress_details['elapsed_seconds'] = (
                    now - self.scan_state.progress_details['start_time']
                )
                last_discovery_update[0] = now

            # Auto-disable perceptual matching once the running discovered
            # count crosses the threshold, unless LSH was explicitly forced
            # on. Edge-triggered (checked via auto_disabled_perceptual) so it
            # only ever fires once, and calculate_phash below (re-evaluated
            # per file) picks up the flip immediately for every file
            # submitted from this point on - no need to know the final total.
            if (not self.auto_disabled_perceptual and not self.exact_only and not self.perceptual_only
                    and count > PERCEPTUAL_AUTO_DISABLE_THRESHOLD and self.use_lsh is not True):
                self.auto_disabled_perceptual = True
                self.exact_only = True

                warning_msg = (
                    f'⚠️ LARGE COLLECTION DETECTED ({formatters.format_number(count)}+ images). '
                    f'Perceptual matching has been automatically disabled. '
                    f'Enable LSH in Advanced Options to process large collections with perceptual matching.'
                )
                self.scan_state.message = warning_msg
                self.scan_state.settings['exact_only'] = True
                self.scan_state.settings['auto_disabled_perceptual'] = True
                self.save_callback()
                time.sleep(3)

        def analysis_progress_callback(current: int, total: int) -> None:
            if total <= 0:
                return
            progress_tracker.update_analysis_progress(current, total, analysis_start_time)

        # Drive-aware worker count. Discovery and analysis overlap, so the
        # file count isn't known before the pool starts: the large-library
        # count is the ceiling, and each drive's type decides how much of it
        # to use (an HDD or USB drive gets far fewer). The adaptive tuner
        # then adjusts during long scans. An explicit `workers` is used as-is.
        scan_dirs = [d['path'] for d in self.directories]
        decision = resolve_workers(
            OpKind.SCAN, scan_dirs, legacy_default=LARGE_LIBRARY_WORKERS, requested=self.workers,
        )
        effective_workers = decision.workers
        stat_workers = resolve_workers(OpKind.STAT, scan_dirs, legacy_default=32).workers
        tuner = make_tuner(decision, None)
        _logger.info(f"Workers: {decision.reason}")
        self.scan_state.settings['storage'] = decision.as_dict()
        if any(p.media.value == 'hdd' for p in decision.profiles):
            self.scan_state.settings['detected_media_type'] = 'hdd'

        images, cache_stats = analyze_images_streaming(
            _chunks(),
            max_workers=effective_workers,
            progress_callback=analysis_progress_callback,
            show_progress=False,
            use_cache=self.use_cache,
            calculate_phash=lambda: not self.exact_only,
            discovered_callback=discovered_callback,
            cancel_check=lambda: self.scan_state.cancel_requested,
            logger=_logger,
            analyze_fn=analyze_media,
            stat_workers=stat_workers,
            tuner=tuner,
        )
        if tuner is not None:
            self.scan_state.settings['storage']['adaptive'] = tuner.summary()

        self.scan_state.total_files = cache_stats.total_files
        self.scan_state.progress_details['elapsed_seconds'] = (
            time.time() - self.scan_state.progress_details['start_time']
        )
        self.scan_state.progress_details['cache_hits'] = cache_stats.cache_hits
        self.scan_state.progress_details['cache_misses'] = cache_stats.cache_misses

        _logger.info(
            f"Phase: discover+analyze done - {len(images):,} files in {time.time() - _phase_start:.1f}s"
        )

        if self.scan_state.cancel_requested:
            self.scan_state.status = 'cancelled'
            self.scan_state.message = f'Scan cancelled (analyzed {formatters.format_number(len(images))} images)'
            self.save_callback()
            return None

        if not images:
            self.scan_state.status = 'complete'
            self.scan_state.message = 'No images found in directory'
            self.save_callback()
            return None

        if cache_stats.cache_hits > 0:
            _logger.info(f"Cache: {cache_stats.cache_hits:,} hits, {cache_stats.cache_misses:,} misses "
                         f"({cache_stats.hit_rate:.1f}% hit rate)")

        # Stamp is_reference on every image (cache-returned objects always
        # default to False, since reference status is scan-local and never
        # cached). Done before the valid/error split so error images are
        # tagged too.
        for img in images:
            img.is_reference = self._path_is_reference.get(img.path, False)

        # Separate valid images from errors
        valid_images = [img for img in images if not img.error]
        error_images = [img for img in images if img.error]
        error_count = len(error_images)

        self.scan_state.error_images = error_images

        if not valid_images:
            self.scan_state.status = 'complete'
            self.scan_state.message = f'No valid images could be analyzed ({error_count} errors)'
            self.save_callback()
            return None

        if error_count > 0:
            _logger.warning(f"Warning: {error_count} images could not be analyzed")

        return valid_images, cache_stats

    def _find_exact_dupes(self, images: list[ImageInfo]) -> Optional[tuple[list[DuplicateGroup], set[str]]]:
        """
        Phase 3: Find exact duplicates.

        Args:
            images: List of valid images

        Returns:
            Tuple of (exact groups, exact hashes), or None if cancelled
        """
        exact_groups = []
        exact_hashes = set()

        if not self.perceptual_only:
            self.scan_state.status = 'comparing'
            self.scan_state.stage = 'exact_matching'
            self.scan_state.progress = 52  # Show movement before instant O(n) hash pass
            self.scan_state.message = f'Finding exact duplicates among {formatters.format_number(len(images))} images...'
            self.scan_state.stage_progress = 0
            self.save_callback()

            # Check for cancel
            if self.scan_state.cancel_requested:
                self.scan_state.status = 'cancelled'
                self.scan_state.message = 'Scan cancelled by user'
                self.save_callback()
                return None

            _logger.info(f"Phase: exact-match - {len(images):,} images")
            _phase_start = time.time()

            exact_groups = find_exact_duplicates(images)
            exact_hashes = {img.file_hash for g in exact_groups for img in g.images}

            _logger.info(
                f"Phase: exact-match done - {len(exact_groups):,} groups "
                f"in {time.time() - _phase_start:.1f}s"
            )

            self.scan_state.progress_details['exact_groups'] = len(exact_groups)
            exact_dupe_count = sum(len(g.images) - 1 for g in exact_groups)
            self.scan_state.message = (
                f'Found {formatters.format_number(exact_dupe_count)} exact duplicates '
                f'in {formatters.format_number(len(exact_groups))} groups'
            )
            self.scan_state.stage_progress = 100

        self.scan_state.progress = 60
        self.save_callback()

        return exact_groups, exact_hashes

    def _find_perceptual_dupes(
        self,
        images: list[ImageInfo],
        exclude_hashes: set[str],
        exact_group_count: int = 0
    ) -> Optional[list[DuplicateGroup]]:
        """
        Phase 4: Find perceptual duplicates.

        Args:
            images: List of valid images
            exclude_hashes: File hashes to exclude (exact duplicates)

        Returns:
            List of perceptual duplicate groups, or None if cancelled
        """
        perceptual_groups = []

        if not self.exact_only:
            # Videos carry a "|"-joined multi-frame hash that image
            # perceptual matching doesn't understand - matched separately
            # via find_video_perceptual_duplicates() below.
            image_candidates = [img for img in images if img.media_type != 'video']
            video_candidates = [img for img in images if img.media_type == 'video'
                                 and img.file_hash not in exclude_hashes]

            # Calculate expected comparisons for progress
            candidates_count = len([img for img in image_candidates
                                   if img.perceptual_hash and img.file_hash not in exclude_hashes])

            # Determine if we'll use LSH
            if self.use_lsh is None:
                # Auto-select based on collection size
                will_use_lsh = candidates_count >= LSH_AUTO_THRESHOLD
            else:
                will_use_lsh = self.use_lsh

            self.scan_state.progress_details['using_lsh'] = will_use_lsh

            if will_use_lsh:
                self.scan_state.message = (
                    f'Finding visually similar images using LSH ({formatters.format_number(candidates_count)} candidates)...'
                )
            else:
                total_comparisons = (candidates_count * (candidates_count - 1)) // 2
                self.scan_state.progress_details['total_comparisons'] = total_comparisons
                self.scan_state.message = (
                    f'Finding visually similar images ({formatters.format_number(candidates_count)} candidates, '
                    f'{formatters.format_number(total_comparisons)} comparisons)...'
                )

            self.scan_state.stage = 'perceptual_matching'
            self.scan_state.stage_progress = 0

            # Check for cancel
            if self.scan_state.cancel_requested:
                self.scan_state.status = 'cancelled'
                self.scan_state.message = 'Scan cancelled by user'
                self.save_callback()
                return None

            _logger.info(
                f"Phase: perceptual-match - {candidates_count:,} candidates "
                f"(lsh={'on' if will_use_lsh else 'off'}, threshold={self.threshold})"
            )
            comparison_start_time = time.time()

            # Create progress tracker
            progress_tracker = ProgressTracker(self.scan_state, self.save_callback)

            def comparison_progress_callback(current: int, total: int):
                progress_tracker.update_comparison_progress(current, total, comparison_start_time)

            perceptual_groups = find_perceptual_duplicates(
                image_candidates,
                threshold=self.threshold,
                exclude_hashes=exclude_hashes,
                start_id=exact_group_count + 1 if exact_group_count else 1,
                progress_callback=comparison_progress_callback,
                show_progress=False,
                use_lsh=self.use_lsh,
                logger=_logger,
            )

            _logger.info(
                f"Phase: perceptual-match done - {len(perceptual_groups):,} groups "
                f"in {time.time() - comparison_start_time:.1f}s"
            )

            if video_candidates:
                video_groups = find_video_perceptual_duplicates(
                    video_candidates,
                    threshold=self.threshold,
                    start_id=exact_group_count + len(perceptual_groups) + 1,
                )
                perceptual_groups.extend(video_groups)
                _logger.info(f"Phase: video-perceptual-match done - {len(video_groups):,} groups")

            self.scan_state.progress_details['perceptual_groups'] = len(perceptual_groups)

        return perceptual_groups

    def _finalize_results(
        self,
        exact_groups: list[DuplicateGroup],
        perceptual_groups: list[DuplicateGroup]
    ) -> None:
        """
        Phase 5: Finalize results and build summary.

        Args:
            exact_groups: List of exact duplicate groups
            perceptual_groups: List of perceptual duplicate groups
        """
        # Check for cancel one last time
        if self.scan_state.cancel_requested:
            self.scan_state.status = 'cancelled'
            self.scan_state.message = 'Scan cancelled by user'
            self.save_callback()
            return

        self.scan_state.groups = exact_groups + perceptual_groups
        self.scan_state.progress = 100
        self.scan_state.stage = 'complete'
        self.scan_state.stage_progress = 100
        self.scan_state.status = 'complete'

        # Apply auto-selection strategy (reference-aware)
        self.scan_state.selections = selection.resolve_group_selections(
            self.scan_state.groups,
            self.auto_select_strategy
        )
        # Stamp the resolved keep choice back onto each group so
        # DuplicateGroup.to_dict()'s quality-only fallback never gets a
        # chance to disagree with what was actually resolved above.
        selection.stamp_group_selections(self.scan_state.groups, self.scan_state.selections)

        # Build final summary message
        total_dupes = sum(len(g.images) - 1 for g in self.scan_state.groups)
        exact_count = sum(len(g.images) - 1 for g in exact_groups)
        perceptual_count = sum(len(g.images) - 1 for g in perceptual_groups)

        elapsed_total = time.time() - self.scan_state.progress_details['start_time']
        self.scan_state.progress_details['elapsed_seconds'] = elapsed_total
        elapsed_str = formatters.format_time_estimate(elapsed_total)

        summary_parts = [f'Found {formatters.format_number(total_dupes)} duplicates in {formatters.format_number(len(self.scan_state.groups))} groups']

        if exact_count > 0 and perceptual_count > 0:
            summary_parts.append(f'({formatters.format_number(exact_count)} exact, {formatters.format_number(perceptual_count)} similar)')
        elif exact_count > 0:
            summary_parts.append('(exact matches)')
        elif perceptual_count > 0:
            summary_parts.append('(visually similar)')

        summary_parts.append(f'• Completed in {elapsed_str}')

        # Show cache reuse stats
        cache_hits = self.scan_state.progress_details.get('cache_hits', 0)
        cache_misses = self.scan_state.progress_details.get('cache_misses', 0)
        cache_total = cache_hits + cache_misses
        if cache_hits > 0 and cache_total > 0:
            pct = cache_hits / cache_total * 100
            summary_parts.append(
                f'• {formatters.format_number(cache_hits)}/{formatters.format_number(cache_total)} '
                f'reused from cache ({pct:.0f}%)'
            )

        error_count = len(self.scan_state.error_images) if self.scan_state.error_images else 0
        if error_count > 0:
            summary_parts.append(f'• {formatters.format_number(error_count)} files had errors')

        if self.auto_disabled_perceptual:
            summary_parts.append('• ⚠️ Perceptual matching was skipped for large collection')

        self.scan_state.message = ' '.join(summary_parts)
        self.scan_state.last_updated = datetime.now().isoformat()

        # Plain-ASCII log line (the UI message above uses bullets/emoji that
        # can mangle on non-UTF-8 Windows consoles when piped through logging)
        _logger.info(
            f"Scan complete - {total_dupes:,} duplicates in {len(self.scan_state.groups):,} groups, "
            f"{elapsed_str} elapsed, {error_count} errors, "
            f"cache {cache_hits:,}/{cache_total:,}"
        )

        self.save_callback()


__all__ = ['ScanOrchestrator', 'ProgressTracker', 'PERCEPTUAL_AUTO_DISABLE_THRESHOLD']
