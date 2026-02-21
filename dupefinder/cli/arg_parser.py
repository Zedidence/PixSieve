"""
Argument parsing for the CLI interface.

Provides functions to create and configure the argument parser for the
duplicate finder command-line interface, including subcommands for
media file operations.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import DEFAULT_THRESHOLD, DEFAULT_WORKERS


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments shared across operation subcommands."""
    parser.add_argument(
        'directory',
        type=Path,
        help='Target directory'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=True,
        help='Simulate without making changes (default: True)'
    )
    parser.add_argument(
        '--no-dry-run',
        action='store_true',
        help='Actually perform the operation'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output'
    )


def _add_recursive_arg(parser: argparse.ArgumentParser) -> None:
    """Add recursive scanning argument."""
    parser.add_argument(
        '--no-recursive',
        action='store_true',
        help='Do not process subdirectories'
    )


def _build_duplicates_parser(subparsers) -> None:
    """Build the 'duplicates' subcommand (original behavior)."""
    dup = subparsers.add_parser(
        'duplicates',
        help='Find and manage duplicate images (default command)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/photos
      Scan for duplicates (report only, no changes)

  %(prog)s /path/to/photos --action move --trash-dir ./duplicates
      Move duplicates to a separate folder

  %(prog)s /path/to/photos --action delete --no-dry-run
      Actually delete duplicates (BE CAREFUL!)

  %(prog)s /path/to/photos --threshold 5 --exact-only
      Strict matching: exact duplicates + very similar perceptual matches

Platform Notes:
  --action hardlink: Requires same filesystem. On Windows, needs admin privileges.
  --action symlink:  On Windows, needs admin privileges or Developer Mode.
        """
    )

    dup.add_argument(
        'directory',
        type=Path,
        nargs='?',
        default=None,
        help='Directory to scan for duplicate images'
    )

    # Scanning options
    dup.add_argument(
        '-r', '--no-recursive',
        action='store_true',
        help='Do not scan subdirectories'
    )
    dup.add_argument(
        '-t', '--threshold',
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f'Perceptual hash threshold (0-64, lower=stricter). Default: {DEFAULT_THRESHOLD}'
    )
    dup.add_argument(
        '--exact-only',
        action='store_true',
        help='Only find exact duplicates (skip perceptual matching)'
    )
    dup.add_argument(
        '--perceptual-only',
        action='store_true',
        help='Only find perceptual duplicates (skip exact matching)'
    )

    # LSH control
    lsh_group = dup.add_mutually_exclusive_group()
    lsh_group.add_argument(
        '--lsh',
        action='store_true',
        dest='force_lsh',
        help='Force LSH acceleration on (useful for 1K-5K images)'
    )
    lsh_group.add_argument(
        '--no-lsh',
        action='store_true',
        dest='no_lsh',
        help='Force brute-force comparison (disable LSH auto-selection)'
    )

    # Caching
    dup.add_argument(
        '--no-cache',
        action='store_true',
        help='Disable SQLite caching (analyze all images fresh)'
    )

    # Action options
    dup.add_argument(
        '-a', '--action',
        choices=['report', 'delete', 'move', 'hardlink', 'symlink'],
        default='report',
        help='Action to take on duplicates. Default: report'
    )
    dup.add_argument(
        '--trash-dir',
        type=Path,
        help='Directory to move duplicates to (for --action move)'
    )
    dup.add_argument(
        '--no-dry-run',
        action='store_true',
        help='Actually perform the action (default is dry-run)'
    )

    # Performance
    dup.add_argument(
        '-w', '--workers',
        type=int,
        default=DEFAULT_WORKERS,
        help=f'Number of parallel workers. Default: {DEFAULT_WORKERS}'
    )

    # Export
    dup.add_argument(
        '-e', '--export',
        type=Path,
        help='Export results to file'
    )
    dup.add_argument(
        '--export-format',
        choices=['txt', 'csv'],
        default='txt',
        help='Export format. Default: txt'
    )

    # Output
    dup.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output'
    )
    dup.add_argument(
        '--no-progress',
        action='store_true',
        help='Disable progress bars (useful for piping output)'
    )


def _build_move_to_parent_parser(subparsers) -> None:
    """Build the 'move-to-parent' subcommand."""
    p = subparsers.add_parser(
        'move-to-parent',
        help='Move all files from subdirectories to the parent directory'
    )
    _add_common_args(p)
    p.add_argument(
        '--extensions',
        nargs='+',
        help='Only move files with these extensions (e.g., .jpg .png)'
    )


def _build_move_parser(subparsers) -> None:
    """Build the 'move' subcommand."""
    p = subparsers.add_parser(
        'move',
        help='Move files preserving directory structure'
    )
    _add_common_args(p)
    p.add_argument(
        'destination',
        type=Path,
        help='Destination directory'
    )
    p.add_argument(
        '--overwrite',
        action='store_true',
        help='Overwrite existing files at destination'
    )


def _build_rename_parser(subparsers) -> None:
    """Build the 'rename' subcommand with sub-subcommands."""
    p = subparsers.add_parser(
        'rename',
        help='Rename files using various strategies'
    )
    rename_sub = p.add_subparsers(dest='rename_mode', help='Rename strategy')
    rename_sub.required = True

    # rename random
    rr = rename_sub.add_parser('random', help='Rename to random alphanumeric names')
    _add_common_args(rr)
    _add_recursive_arg(rr)
    rr.add_argument(
        '--length',
        type=int,
        default=12,
        help='Length of random name. Default: 12'
    )
    rr.add_argument(
        '-w', '--workers',
        type=int,
        default=4,
        help='Number of parallel workers. Default: 4'
    )
    rr.add_argument(
        '--extensions',
        nargs='+',
        help='Only rename files with these extensions (e.g., .jpg .png)'
    )

    # rename parent
    rp = rename_sub.add_parser('parent', help='Rename based on parent folder name')
    _add_common_args(rp)


def _build_sort_parser(subparsers) -> None:
    """Build the 'sort' subcommand with sub-subcommands."""
    p = subparsers.add_parser(
        'sort',
        help='Sort files into organized folders'
    )
    sort_sub = p.add_subparsers(dest='sort_mode', help='Sort strategy')
    sort_sub.required = True

    # sort alpha
    sa = sort_sub.add_parser('alpha', help='Sort alphabetically into A-G, H-N, O-T, U-Z, 0-9')
    _add_common_args(sa)

    # sort color
    sc = sort_sub.add_parser('color', help='Sort images by color properties')
    _add_common_args(sc)
    sc.add_argument(
        '--method',
        choices=['dominant', 'bw', 'palette', 'analyze'],
        default='dominant',
        help='Color sort method. Default: dominant'
    )
    sc.add_argument(
        '--copy',
        action='store_true',
        help='Copy files instead of moving them'
    )
    sc.add_argument(
        '--n-colors',
        type=int,
        default=3,
        help='Number of palette colors (for palette method). Default: 3'
    )


def _build_fix_extensions_parser(subparsers) -> None:
    """Build the 'fix-extensions' subcommand."""
    p = subparsers.add_parser(
        'fix-extensions',
        help='Fix wrong file extensions based on actual file format'
    )
    _add_common_args(p)
    _add_recursive_arg(p)


def _build_convert_parser(subparsers) -> None:
    """Build the 'convert' subcommand."""
    p = subparsers.add_parser(
        'convert',
        help='Convert images to JPG format'
    )
    _add_common_args(p)
    _add_recursive_arg(p)
    p.add_argument(
        '--quality',
        type=int,
        default=95,
        help='JPG quality (1-100). Default: 95'
    )
    p.add_argument(
        '--delete-originals',
        action='store_true',
        help='Delete original files after conversion'
    )


def _build_metadata_parser(subparsers) -> None:
    """Build the 'metadata' subcommand with sub-subcommands."""
    p = subparsers.add_parser(
        'metadata',
        help='Manipulate file metadata and timestamps'
    )
    meta_sub = p.add_subparsers(dest='metadata_mode', help='Metadata operation')
    meta_sub.required = True

    # metadata randomize-exif
    me = meta_sub.add_parser('randomize-exif', help='Randomize EXIF date metadata')
    _add_common_args(me)
    _add_recursive_arg(me)
    me.add_argument(
        '--start',
        required=True,
        help='Start date (YYYY-MM-DD)'
    )
    me.add_argument(
        '--end',
        required=True,
        help='End date (YYYY-MM-DD)'
    )

    # metadata randomize-dates
    md = meta_sub.add_parser('randomize-dates', help='Randomize file system timestamps')
    _add_common_args(md)
    _add_recursive_arg(md)
    md.add_argument(
        '--start',
        required=True,
        help='Start date (YYYY-MM-DD)'
    )
    md.add_argument(
        '--end',
        required=True,
        help='End date (YYYY-MM-DD)'
    )


def _build_cleanup_parser(subparsers) -> None:
    """Build the 'cleanup' subcommand."""
    p = subparsers.add_parser(
        'cleanup',
        help='Delete empty folders'
    )
    _add_common_args(p)


def _build_pipeline_parser(subparsers) -> None:
    """Build the 'pipeline' subcommand."""
    p = subparsers.add_parser(
        'pipeline',
        help='Run a multi-step workflow',
        epilog='Available steps: random_rename, convert_jpg, randomize_exif, randomize_dates, cleanup_empty'
    )
    _add_common_args(p)
    _add_recursive_arg(p)
    p.add_argument(
        '--steps',
        required=True,
        help='Comma-separated list of steps (e.g., "random_rename,convert_jpg,cleanup_empty")'
    )
    p.add_argument(
        '--start',
        help='Start date for date operations (YYYY-MM-DD)'
    )
    p.add_argument(
        '--end',
        help='End date for date operations (YYYY-MM-DD)'
    )
    p.add_argument(
        '--length',
        type=int,
        default=12,
        help='Random name length (for random_rename step). Default: 12'
    )
    p.add_argument(
        '--quality',
        type=int,
        default=95,
        help='JPG quality (for convert_jpg step). Default: 95'
    )
    p.add_argument(
        '--delete-originals',
        action='store_true',
        help='Delete originals after conversion (for convert_jpg step)'
    )


def create_parser() -> argparse.ArgumentParser:
    """
    Create and configure the argument parser for the CLI.

    Returns:
        Configured ArgumentParser instance with subcommands for all
        operations. When no subcommand is given, defaults to 'duplicates'.
    """
    parser = argparse.ArgumentParser(
        description='Find and manage duplicate images, plus media file operations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  duplicates       Find and manage duplicate images (default)
  move-to-parent   Move files from subdirs to parent directory
  move             Move files preserving directory structure
  rename           Rename files (random or parent-based)
  sort             Sort files (alphabetical or by color)
  fix-extensions   Fix wrong file extensions
  convert          Convert images to JPG
  metadata         Manipulate EXIF data and timestamps
  cleanup          Delete empty folders
  pipeline         Run multi-step workflow

Examples:
  %(prog)s /path/to/photos
      Scan for duplicates (backward compatible)

  %(prog)s duplicates /path/to/photos --action move --trash-dir ./dupes
      Move duplicates to a separate folder

  %(prog)s move-to-parent /photos --no-dry-run
      Flatten directory structure

  %(prog)s rename random /photos --length 16
      Preview random rename (dry-run by default)

  %(prog)s sort color /photos --method dominant --no-dry-run
      Sort images by dominant color

  %(prog)s pipeline /photos --steps "random_rename,convert_jpg,cleanup_empty"
      Run a multi-step workflow
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    _build_duplicates_parser(subparsers)
    _build_move_to_parent_parser(subparsers)
    _build_move_parser(subparsers)
    _build_rename_parser(subparsers)
    _build_sort_parser(subparsers)
    _build_fix_extensions_parser(subparsers)
    _build_convert_parser(subparsers)
    _build_metadata_parser(subparsers)
    _build_cleanup_parser(subparsers)
    _build_pipeline_parser(subparsers)

    return parser


_VALID_COMMANDS = {
    'duplicates', 'move-to-parent', 'move', 'rename', 'sort',
    'fix-extensions', 'convert', 'metadata', 'cleanup', 'pipeline',
}


def parse_arguments(argv=None) -> argparse.Namespace:
    """
    Parse command-line arguments with backward compatibility.

    When no subcommand is provided but a directory path is given,
    treats the invocation as the 'duplicates' command for backward
    compatibility.

    Args:
        argv: List of argument strings (default: sys.argv)

    Returns:
        Parsed arguments as Namespace object
    """
    import sys
    parser = create_parser()

    # Get the actual argv list
    raw_argv = argv if argv is not None else sys.argv[1:]

    # Backward compatibility: if the first arg is not a known subcommand,
    # prepend 'duplicates' so the old usage pattern still works.
    if raw_argv and raw_argv[0] not in _VALID_COMMANDS and not raw_argv[0].startswith('-'):
        raw_argv = ['duplicates'] + raw_argv

    if not raw_argv:
        # No arguments at all - default to duplicates with no directory
        # (will trigger interactive prompt)
        args = argparse.Namespace(
            command='duplicates',
            directory=None,
            no_recursive=False,
            threshold=DEFAULT_THRESHOLD,
            exact_only=False,
            perceptual_only=False,
            force_lsh=False,
            no_lsh=False,
            no_cache=False,
            action='report',
            trash_dir=None,
            no_dry_run=False,
            workers=DEFAULT_WORKERS,
            export=None,
            export_format='txt',
            verbose=False,
            no_progress=False,
        )
        return args

    return parser.parse_args(raw_argv)


__all__ = [
    'create_parser',
    'parse_arguments',
]
