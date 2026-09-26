"""
Operations orchestrator for CLI media file operations.

Routes parsed CLI arguments to the appropriate operation module and
handles dry-run mode, logging, and result reporting.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from ..config import (
    DEFAULT_OP_WORKERS, DEFAULT_WORKERS, IMAGE_EXTENSIONS, RATING_EXTENSIONS,
    resolve_extensions,
)
from ..utils import io_probe
from ..utils.adaptive import make_tuner
from ..utils.worker_policy import (
    OpKind, WorkerDecision, parse_storage_overrides, profile_for, resolve_workers,
)
from .arg_parser import storage_options
from ..operations import (
    delete_empty_folders,
    move_to_parent,
    move_with_structure,
    rename_random,
    rename_by_parent,
    sort_alphabetical,
    ColorImageSorter,
    fix_extensions,
    batch_convert_to_jpg,
    randomize_dates,
    run_pipeline,
    strip_favorite_ratings,
)
from ..utils.operations import parse_date
from ..utils.platform import check_exiftool_available


class OperationsOrchestrator:
    """
    Routes parsed CLI arguments to the appropriate operation.

    Each handler method validates arguments, calls the operation function,
    and prints results. All operations default to dry-run mode.
    """

    def __init__(self, args, logger: logging.Logger):
        self.args = args
        self.logger = logger
        self.dry_run = not getattr(args, 'no_dry_run', False)

    def run(self) -> int:
        """
        Dispatch to the appropriate handler based on args.command.

        Returns:
            Exit code (0 for success, 1 for error)
        """
        command = self.args.command
        handlers = {
            'move-to-parent': self._handle_move_to_parent,
            'move': self._handle_move,
            'rename': self._handle_rename,
            'sort': self._handle_sort,
            'fix-extensions': self._handle_fix_extensions,
            'convert': self._handle_convert,
            'metadata': self._handle_metadata,
            'cleanup': self._handle_cleanup,
            'strip-ratings': self._handle_ratings,
            'pipeline': self._handle_pipeline,
            'storage': self._handle_storage,
        }

        handler = handlers.get(command)
        if handler is None:
            self.logger.error(f"Unknown command: {command}")
            return 1

        return handler()

    def _validate_directory(self) -> bool:
        """Check that the target directory exists."""
        if not self.args.directory.exists():
            self.logger.error(f"Directory not found: {self.args.directory}")
            return False
        if not self.args.directory.is_dir():
            self.logger.error(f"Not a directory: {self.args.directory}")
            return False
        return True

    def _decide_workers(
        self, op: OpKind, sources: list[str], *, destination: str | None = None, upper: int = 32,
    ) -> WorkerDecision:
        """Drive-aware worker count for this command (an explicit -w wins)."""
        options = storage_options(self.args)
        decision = resolve_workers(
            op, sources, legacy_default=DEFAULT_OP_WORKERS,
            requested=getattr(self.args, 'workers', None), destination=destination,
            upper=upper, overrides=options['overrides'],
        )
        self.logger.info(f"Workers: {decision.reason}")
        return decision

    def _media_extensions(self) -> set[str]:
        """Images, plus videos with --include-videos - the only files an operation may touch."""
        return resolve_extensions(IMAGE_EXTENSIONS, getattr(self.args, 'include_videos', False))

    def _print_dry_run_banner(self) -> None:
        """Print dry-run mode notice."""
        if self.dry_run:
            print("\n[DRY RUN MODE - No files will be modified]")

    def _print_stats(self, stats: dict) -> None:
        """Print operation statistics."""
        print("\nResults:")
        for key, value in stats.items():
            if isinstance(value, dict):
                print(f"  {key}:")
                for k, v in value.items():
                    print(f"    {k}: {v}")
            elif isinstance(value, list):
                print(f"  {key}: {len(value)} items")
                for item in value[:5]:
                    print(f"    - {item}")
                if len(value) > 5:
                    print(f"    ... and {len(value) - 5} more")
            else:
                print(f"  {key}: {value}")

    def _handle_move_to_parent(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        explicit = None
        if getattr(self.args, 'extensions', None):
            explicit = {ext if ext.startswith('.') else f'.{ext}'
                        for ext in self.args.extensions}
        extensions = resolve_extensions(
            IMAGE_EXTENSIONS,
            getattr(self.args, 'include_videos', False),
            extra=explicit,
        )

        self.logger.info(f"Moving files to parent: {self.args.directory}")
        # Moving within one directory tree is a rename on the same volume
        decision = self._decide_workers(OpKind.METADATA, [str(self.args.directory)])
        stats = move_to_parent(
            self.args.directory,
            extensions=extensions,
            dry_run=self.dry_run,
            max_workers=decision.workers,
        )
        self._print_stats(stats)
        return 0

    def _handle_move(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        self.logger.info(f"Moving files: {self.args.directory} -> {self.args.destination}")
        # Sized on both ends: a same-volume move is a rename, a cross-volume
        # one is bounded by the slower drive.
        decision = self._decide_workers(
            OpKind.COPY, [str(self.args.directory)], destination=str(self.args.destination),
        )
        stats = move_with_structure(
            self.args.directory,
            self.args.destination,
            overwrite=getattr(self.args, 'overwrite', False),
            dry_run=self.dry_run,
            max_workers=decision.workers,
            tuner=make_tuner(decision, None),
            extensions=self._media_extensions(),
        )
        self._print_stats(stats)
        return 0

    def _handle_rename(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        mode = self.args.rename_mode

        if mode == 'random':
            explicit = None
            if getattr(self.args, 'extensions', None):
                explicit = {ext if ext.startswith('.') else f'.{ext}'
                            for ext in self.args.extensions}
            extensions = resolve_extensions(
                IMAGE_EXTENSIONS,
                getattr(self.args, 'include_videos', False),
                extra=explicit,
            )

            self.logger.info(f"Random rename in: {self.args.directory}")
            workers = self._decide_workers(OpKind.METADATA, [str(self.args.directory)], upper=16).workers
            stats = rename_random(
                self.args.directory,
                name_length=getattr(self.args, 'length', 12),
                extensions=extensions,
                recursive=not getattr(self.args, 'no_recursive', False),
                dry_run=self.dry_run,
                workers=workers,
            )
        elif mode == 'parent':
            self.logger.info(f"Parent-based rename in: {self.args.directory}")
            stats = rename_by_parent(
                self.args.directory,
                dry_run=self.dry_run,
                extensions=self._media_extensions(),
            )
        else:
            self.logger.error(f"Unknown rename mode: {mode}")
            return 1

        self._print_stats(stats)
        return 0

    def _handle_sort(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        mode = self.args.sort_mode

        if mode == 'alpha':
            self.logger.info(f"Alphabetical sort in: {self.args.directory}")
            stats = sort_alphabetical(
                self.args.directory,
                dry_run=self.dry_run,
                extensions=self._media_extensions(),
            )
        elif mode == 'color':
            method = getattr(self.args, 'method', 'dominant')
            copy_files = getattr(self.args, 'copy', False)
            self.logger.info(f"Color sort ({method}) in: {self.args.directory}")

            sorter = ColorImageSorter(
                self.args.directory,
                include_videos=getattr(self.args, 'include_videos', False),
            )

            if method == 'dominant':
                stats = sorter.sort_by_dominant_color(
                    copy_files=copy_files,
                    dry_run=self.dry_run,
                )
            elif method == 'bw':
                stats = sorter.sort_by_color_bw(
                    copy_files=copy_files,
                    dry_run=self.dry_run,
                )
            elif method == 'palette':
                stats = sorter.sort_by_palette(
                    copy_files=copy_files,
                    n_colors=getattr(self.args, 'n_colors', 3),
                    dry_run=self.dry_run,
                )
            elif method == 'analyze':
                stats = sorter.analyze_colors()
            else:
                self.logger.error(f"Unknown color method: {method}")
                return 1
        else:
            self.logger.error(f"Unknown sort mode: {mode}")
            return 1

        self._print_stats(stats)
        return 0

    def _handle_fix_extensions(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        self.logger.info(f"Fixing extensions in: {self.args.directory}")
        stats = fix_extensions(
            self.args.directory,
            recursive=not getattr(self.args, 'no_recursive', False),
            dry_run=self.dry_run,
        )
        self._print_stats(stats)
        return 0

    def _handle_convert(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        self.logger.info(f"Converting to JPG in: {self.args.directory}")
        stats = batch_convert_to_jpg(
            self.args.directory,
            quality=getattr(self.args, 'quality', 95),
            delete_originals=getattr(self.args, 'delete_originals', False),
            recursive=not getattr(self.args, 'no_recursive', False),
            dry_run=self.dry_run,
        )
        self._print_stats(stats)
        return 0

    def _handle_metadata(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        mode = self.args.metadata_mode

        try:
            start_date = parse_date(self.args.start)
        except ValueError:
            self.logger.error(f"Invalid start date: {self.args.start}")
            return 1
        try:
            end_date = parse_date(self.args.end)
        except ValueError:
            self.logger.error(f"Invalid end date: {self.args.end}")
            return 1

        recursive = not getattr(self.args, 'no_recursive', False)

        if mode == 'randomize-dates':
            self.logger.info(f"Randomizing dates in: {self.args.directory}")
            decision = self._decide_workers(OpKind.REWRITE, [str(self.args.directory)])
            stats = randomize_dates(
                self.args.directory,
                start_date=start_date,
                end_date=end_date,
                recursive=recursive,
                dry_run=self.dry_run,
                sync_exif=not getattr(self.args, 'no_exif', False),
                extensions=resolve_extensions(
                    IMAGE_EXTENSIONS,
                    getattr(self.args, 'include_videos', False),
                ),
                max_workers=decision.workers,
                tuner=make_tuner(decision, None),
            )
        else:
            self.logger.error(f"Unknown metadata mode: {mode}")
            return 1

        self._print_stats(stats)
        return 0

    def _handle_cleanup(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        self.logger.info(f"Cleaning up empty folders in: {self.args.directory}")
        stats = delete_empty_folders(
            self.args.directory,
            dry_run=self.dry_run,
        )
        self._print_stats(stats)
        return 0

    def _handle_ratings(self) -> int:
        if not self._validate_directory():
            return 1

        available, reason = check_exiftool_available()
        if not available:
            self.logger.error(f"exiftool not available: {reason}")
            return 1

        self._print_dry_run_banner()
        self.logger.info(f"Scanning for favorite/5-star ratings in: {self.args.directory}")
        stats = strip_favorite_ratings(
            self.args.directory,
            recursive=not getattr(self.args, 'no_recursive', False),
            dry_run=self.dry_run,
            extensions=resolve_extensions(
                RATING_EXTENSIONS,
                getattr(self.args, 'include_videos', False),
            ),
        )
        self._print_stats(stats)
        return 0

    def _handle_pipeline(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()

        steps = [s.strip() for s in self.args.steps.split(',')]

        # Parse optional dates
        start_date = None
        end_date = None
        date_steps = {'randomize_dates'}
        if date_steps & set(steps):
            if not self.args.start or not self.args.end:
                self.logger.error(
                    "Date steps require --start and --end dates"
                )
                return 1
            try:
                start_date = parse_date(self.args.start)
                end_date = parse_date(self.args.end)
            except ValueError:
                self.logger.error("Invalid date format. Use YYYY-MM-DD.")
                return 1

        self.logger.info(f"Running pipeline: {', '.join(steps)}")
        results = run_pipeline(
            self.args.directory,
            steps=steps,
            start_date=start_date,
            end_date=end_date,
            name_length=getattr(self.args, 'length', 12),
            jpg_quality=getattr(self.args, 'quality', 95),
            delete_originals=getattr(self.args, 'delete_originals', False),
            recursive=not getattr(self.args, 'no_recursive', False),
            dry_run=self.dry_run,
            include_videos=getattr(self.args, 'include_videos', False),
            workers=getattr(self.args, 'workers', None),
            storage_overrides=storage_options(self.args)['overrides'],
        )

        print("\nPipeline Results:")
        for step_name, step_stats in results.items():
            print(f"\n  [{step_name}]")
            for key, value in step_stats.items():
                print(f"    {key}: {value}")

        return 0

    def _handle_storage(self) -> int:
        """Print what drive-aware tuning sees for each path, and the worker counts it would use."""
        overrides = parse_storage_overrides(getattr(self.args, 'storage_profile', None) or [])
        legacy = {OpKind.SCAN: DEFAULT_WORKERS, OpKind.STAT: 32}
        report = []
        for path in self.args.paths:
            if not path.exists():
                self.logger.error(f"Path not found: {path}")
                return 1
            profile = profile_for(str(path), overrides)
            entry = {'path': str(path), 'drive': profile.as_dict(), 'workers': {}, 'probe': None}

            if getattr(self.args, 'probe', False):
                files = io_probe.sample_directory(str(path)) if path.is_dir() else [str(path)]
                result = io_probe.probe_device(files) if files else None
                entry['probe'] = result.as_dict() if result else {
                    'error': 'not enough large files to measure (needs several >= 512 KiB), or timed out'
                }

            for op in OpKind:
                decision = resolve_workers(
                    op, [str(path)], legacy_default=legacy.get(op, DEFAULT_OP_WORKERS),
                    overrides=overrides, allow_probe=False,
                )
                entry['workers'][op.value] = decision.workers
            report.append(entry)

        if getattr(self.args, 'json', False):
            print(json.dumps(report, indent=2))
            return 0

        for entry in report:
            drive = entry['drive']
            print(f"\n{entry['path']}")
            print(f"  Drive:   {drive['label']} (media: {drive['media']}, bus: {drive['bus']}, "
                  f"{drive['source']})")
            if drive['detail']:
                print(f"  Detail:  {drive['detail']}")
            print("  Workers: " + ' | '.join(f"{op} {n}" for op, n in entry['workers'].items()))
            probe = entry['probe']
            if probe and 'error' in probe:
                print(f"  Probe:   {probe['error']}")
            elif probe:
                print(
                    f"  Probe:   random 4K read {probe['mean_read_ms']:.2f} ms avg "
                    f"({probe['read_latency_ms']:.2f} median), "
                    f"{probe['rand_iops_qd1']:.0f} IOPS x1 -> {probe['rand_iops_qd8']:.0f} IOPS x8 "
                    f"(gain {probe['concurrency_gain']:.1f}x), stat {probe['meta_latency_ms']:.2f} ms"
                    + (' [served from cache - not representative]' if probe['cached_suspect'] else '')
                )
        return 0


__all__ = ['OperationsOrchestrator']
