"""
Argument parsing for the CLI interface.

Provides functions to create and configure the argument parser for the
duplicate finder command-line interface, including subcommands for
media file operations.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import DEFAULT_THRESHOLD
from ..operations.capabilities import OPERATION_VIDEO_SUPPORT, needs_video_flag
from ..utils.disk_type import OVERRIDE_SPEC_NAMES
from ..utils.worker_policy import parse_storage_overrides


def _bounded_int(min_val: int, max_val: int):
    """
    Build an argparse `type=` callable that rejects an out-of-range integer
    with a clean usage error instead of silently clamping it (the previous
    behavior on the equivalent web-API fields, and unbounded here on the
    CLI -- both are now enforced consistently via the same bounds the API's
    pydantic schemas declare in api/schemas.py).
    """
    def _parse(value: str) -> int:
        ivalue = int(value)
        if not (min_val <= ivalue <= max_val):
            raise argparse.ArgumentTypeError(
                f"must be between {min_val} and {max_val} (got {ivalue})"
            )
        return ivalue
    return _parse


def _workers_arg(max_val: int):
    """`type=` for -w/--workers: an integer in range, or 'auto' (None)."""
    bounded = _bounded_int(1, max_val)

    def _parse(value: str):
        if value.strip().lower() == 'auto':
            return None
        return bounded(value)
    return _parse


def _storage_profile_arg(value: str) -> str:
    """`type=` for --storage-profile: validate '[PATH=]TYPE' up front."""
    try:
        parse_storage_overrides([value])
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))
    return value


def _add_storage_profile_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--storage-profile',
        action='append',
        type=_storage_profile_arg,
        metavar='[PATH=]TYPE',
        help=(
            'Override drive detection when it gets a drive wrong, e.g. "usb-hdd" '
            '(all paths) or "E:=usb-ssd". Repeatable. '
            f'Types: {", ".join(OVERRIDE_SPEC_NAMES)}'
        ),
    )


def _add_worker_args(parser: argparse.ArgumentParser, max_workers: int = 32) -> None:
    """Add -w/--workers (default: auto), --storage-profile and --no-io-probe."""
    parser.add_argument(
        '-w', '--workers',
        type=_workers_arg(max_workers),
        default=None,
        metavar='N',
        help=(
            f'Number of parallel workers (1-{max_workers}, or "auto"). Default: auto - '
            'chosen from the drive type (HDD/SSD/NVMe) and connection (SATA/USB/network)'
        ),
    )
    _add_storage_profile_arg(parser)
    parser.add_argument(
        '--no-io-probe',
        action='store_true',
        help='Skip the short read-only speed test used to classify ambiguous (e.g. USB) drives',
    )


def storage_options(args: argparse.Namespace) -> dict:
    """resolve_workers() keyword arguments from the storage-related CLI flags."""
    return {
        'overrides': parse_storage_overrides(getattr(args, 'storage_profile', None) or []),
        'allow_probe': not getattr(args, 'no_io_probe', False),
    }


def _add_include_videos_arg(parser: argparse.ArgumentParser, op_name: str) -> None:
    """
    Add --include-videos to `parser`, but only for operations whose
    capability registry entry (pixsieve/operations/capabilities.py) actually
    has a video toggle to expose. A no-op for every other op, so passing
    --include-videos to an unsupported operation is a normal argparse
    "unrecognized arguments" error rather than a silently ignored flag.
    """
    if not needs_video_flag(op_name):
        return
    note = OPERATION_VIDEO_SUPPORT[op_name].get('note')
    help_text = (
        'Also process video files (mp4, mov, avi, mkv, etc). '
        'Requires opencv-python-headless (pip install pixsieve[video]).'
    )
    if note:
        help_text += f' Note: {note}'
    parser.add_argument('--include-videos', action='store_true', help=help_text)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments shared across operation subcommands."""
    parser.add_argument(
        'directory',
        type=Path,
        help='Target directory'
    )
    parser.add_argument(
        '--no-dry-run',
        action='store_true',
        help='Actually perform the operation (default is dry-run/simulate only)'
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
        nargs='*',
        default=[],
        help='Directory/directories to scan for duplicate images (space-separated for multiple)'
    )
    dup.add_argument(
        '--reference-dir',
        type=Path,
        default=None,
        help=(
            'Mark one of the scanned directories as the reference/canonical folder. '
            'Images in it are never deleted/moved/modified; any duplicate group '
            'containing a reference image auto-keeps all reference copies and flags '
            'all other copies for removal. Must exactly match one of the given '
            'directory arguments.'
        )
    )
    dup.add_argument(
        '--auto-select-strategy',
        choices=['quality', 'largest', 'smallest', 'newest', 'oldest'],
        default='quality',
        help='Strategy for choosing which file to keep in groups with no reference image. Default: quality'
    )

    # Scanning options
    _add_recursive_arg(dup)
    dup.add_argument(
        '--no-resolve-symlinks',
        action='store_true',
        help=(
            'Skip symlink path canonicalization during discovery. Saves one '
            'extra filesystem round-trip per file (5-15%% faster on drives '
            'with no symlinks, especially over slower interfaces like USB). '
            'Files reachable via multiple symlinks (or hardlinks) to the '
            'same underlying file are still deduplicated by file identity '
            'regardless of this flag; disabling it only means the reported '
            'path for a symlinked file stays as the symlink path instead of '
            'its resolved target.'
        )
    )
    dup.add_argument(
        '-t', '--threshold',
        type=_bounded_int(0, 64),
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
    dup.add_argument(
        '--include-videos',
        action='store_true',
        help=(
            'Also scan and deduplicate video files (mp4, mov, avi, mkv, etc). '
            'Requires opencv-python-headless (pip install pixsieve[video]). '
            'Off by default - video decoding is slower than image analysis.'
        )
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
    _add_worker_args(dup, 32)

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
    dup.add_argument(
        '--log-file',
        type=str,
        default=None,
        help='Also write logs to this file (recommended for long, unattended scans)'
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
    _add_include_videos_arg(p, 'move-to-parent')
    _add_worker_args(p, 32)


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
    _add_include_videos_arg(p, 'move')
    _add_worker_args(p, 32)


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
        type=_bounded_int(4, 64),
        default=12,
        help='Length of random name (4-64). Default: 12'
    )
    _add_worker_args(rr, 16)
    rr.add_argument(
        '--extensions',
        nargs='+',
        help='Only rename files with these extensions (e.g., .jpg .png)'
    )
    _add_include_videos_arg(rr, 'rename-random')

    # rename parent
    rp = rename_sub.add_parser('parent', help='Rename based on parent folder name')
    _add_common_args(rp)
    _add_include_videos_arg(rp, 'rename-parent')


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
    _add_include_videos_arg(sa, 'sort-alpha')

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
    _add_include_videos_arg(sc, 'sort-color')


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
        type=_bounded_int(1, 100),
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

    # metadata randomize-dates
    md = meta_sub.add_parser(
        'randomize-dates',
        help='Randomize image dates: EXIF date taken (JPG/TIFF) + filesystem timestamps'
    )
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
    md.add_argument(
        '--no-exif',
        action='store_true',
        help='Skip EXIF date update; only update filesystem timestamps'
    )
    _add_include_videos_arg(md, 'randomize-dates')
    _add_worker_args(md, 32)


def _build_cleanup_parser(subparsers) -> None:
    """Build the 'cleanup' subcommand."""
    p = subparsers.add_parser(
        'cleanup',
        help='Delete empty folders'
    )
    _add_common_args(p)


def _build_strip_ratings_parser(subparsers) -> None:
    """Build the 'strip-ratings' subcommand."""
    p = subparsers.add_parser(
        'strip-ratings',
        help='Remove 5-star/favorite rating tags from images via exiftool'
    )
    _add_common_args(p)
    _add_recursive_arg(p)
    _add_include_videos_arg(p, 'strip-ratings')


def _build_pipeline_parser(subparsers) -> None:
    """Build the 'pipeline' subcommand."""
    p = subparsers.add_parser(
        'pipeline',
        help='Run a multi-step workflow',
        epilog=(
            'Available steps: random_rename, convert_jpg, randomize_dates, '
            'cleanup_empty, repair_corrupt'
        )
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
        type=_bounded_int(4, 64),
        default=12,
        help='Random name length (4-64, for random_rename step). Default: 12'
    )
    p.add_argument(
        '--quality',
        type=_bounded_int(1, 100),
        default=95,
        help='JPG quality (1-100, for convert_jpg step). Default: 95'
    )
    p.add_argument(
        '--delete-originals',
        action='store_true',
        help='Delete originals after conversion (for convert_jpg step)'
    )
    _add_include_videos_arg(p, 'pipeline')
    _add_worker_args(p, 16)


def _build_storage_parser(subparsers) -> None:
    """Build the 'storage' subcommand."""
    p = subparsers.add_parser(
        'storage',
        help='Show the detected drive type for paths and the worker counts PixSieve would use'
    )
    p.add_argument('paths', nargs='+', type=Path, help='Paths to inspect')
    _add_storage_profile_arg(p)
    p.add_argument(
        '--probe',
        action='store_true',
        help='Also run the read-only speed test on files under each path',
    )
    p.add_argument('--json', action='store_true', help='Print the result as JSON')


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
  strip-ratings    Remove 5-star/favorite rating tags (requires exiftool)
  pipeline         Run multi-step workflow
  storage          Show detected drive types and worker counts

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
    _build_strip_ratings_parser(subparsers)
    _build_pipeline_parser(subparsers)
    _build_storage_parser(subparsers)

    return parser


_VALID_COMMANDS = {
    'duplicates', 'move-to-parent', 'move', 'rename', 'sort',
    'fix-extensions', 'convert', 'metadata', 'cleanup', 'strip-ratings', 'pipeline',
    'storage',
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
            directory=[],
            reference_dir=None,
            auto_select_strategy='quality',
            no_recursive=False,
            threshold=DEFAULT_THRESHOLD,
            exact_only=False,
            perceptual_only=False,
            include_videos=False,
            force_lsh=False,
            no_lsh=False,
            no_cache=False,
            action='report',
            trash_dir=None,
            no_dry_run=False,
            workers=None,
            storage_profile=None,
            no_io_probe=False,
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
