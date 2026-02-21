"""
Operations orchestrator for CLI media file operations.

Routes parsed CLI arguments to the appropriate operation module and
handles dry-run mode, logging, and result reporting.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

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
    randomize_exif_dates,
    randomize_file_dates,
    run_pipeline,
)
from ..utils.operations import parse_date


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
            'pipeline': self._handle_pipeline,
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
        extensions = None
        if getattr(self.args, 'extensions', None):
            extensions = {ext if ext.startswith('.') else f'.{ext}'
                          for ext in self.args.extensions}

        self.logger.info(f"Moving files to parent: {self.args.directory}")
        stats = move_to_parent(
            self.args.directory,
            extensions=extensions,
            dry_run=self.dry_run,
        )
        self._print_stats(stats)
        return 0

    def _handle_move(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        self.logger.info(f"Moving files: {self.args.directory} -> {self.args.destination}")
        stats = move_with_structure(
            self.args.directory,
            self.args.destination,
            overwrite=getattr(self.args, 'overwrite', False),
            dry_run=self.dry_run,
        )
        self._print_stats(stats)
        return 0

    def _handle_rename(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()
        mode = self.args.rename_mode

        if mode == 'random':
            extensions = None
            if getattr(self.args, 'extensions', None):
                extensions = {ext if ext.startswith('.') else f'.{ext}'
                              for ext in self.args.extensions}

            self.logger.info(f"Random rename in: {self.args.directory}")
            stats = rename_random(
                self.args.directory,
                name_length=getattr(self.args, 'length', 12),
                extensions=extensions,
                recursive=not getattr(self.args, 'no_recursive', False),
                dry_run=self.dry_run,
                workers=getattr(self.args, 'workers', 4),
            )
        elif mode == 'parent':
            self.logger.info(f"Parent-based rename in: {self.args.directory}")
            stats = rename_by_parent(
                self.args.directory,
                dry_run=self.dry_run,
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
            )
        elif mode == 'color':
            method = getattr(self.args, 'method', 'dominant')
            copy_files = getattr(self.args, 'copy', False)
            self.logger.info(f"Color sort ({method}) in: {self.args.directory}")

            sorter = ColorImageSorter(self.args.directory)

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

        start_date = parse_date(self.args.start)
        end_date = parse_date(self.args.end)

        if start_date is None:
            self.logger.error(f"Invalid start date: {self.args.start}")
            return 1
        if end_date is None:
            self.logger.error(f"Invalid end date: {self.args.end}")
            return 1

        recursive = not getattr(self.args, 'no_recursive', False)

        if mode == 'randomize-exif':
            self.logger.info(f"Randomizing EXIF dates in: {self.args.directory}")
            stats = randomize_exif_dates(
                self.args.directory,
                start_date=start_date,
                end_date=end_date,
                recursive=recursive,
                dry_run=self.dry_run,
            )
        elif mode == 'randomize-dates':
            self.logger.info(f"Randomizing file dates in: {self.args.directory}")
            stats = randomize_file_dates(
                self.args.directory,
                start_date=start_date,
                end_date=end_date,
                recursive=recursive,
                dry_run=self.dry_run,
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

    def _handle_pipeline(self) -> int:
        if not self._validate_directory():
            return 1

        self._print_dry_run_banner()

        steps = [s.strip() for s in self.args.steps.split(',')]

        # Parse optional dates
        start_date = None
        end_date = None
        date_steps = {'randomize_exif', 'randomize_dates'}
        if date_steps & set(steps):
            if not self.args.start or not self.args.end:
                self.logger.error(
                    "Date steps require --start and --end dates"
                )
                return 1
            start_date = parse_date(self.args.start)
            end_date = parse_date(self.args.end)
            if start_date is None or end_date is None:
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
        )

        print("\nPipeline Results:")
        for step_name, step_stats in results.items():
            print(f"\n  [{step_name}]")
            for key, value in step_stats.items():
                print(f"    {key}: {value}")

        return 0


__all__ = ['OperationsOrchestrator']
