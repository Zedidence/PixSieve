"""
Utilities package for the PixSieve.

Provides:
- formatters: Human-readable formatting for numbers, time, and file sizes
- validators: Input validation and security checks
- selection: Selection strategies for duplicate handling
- platform: Platform-specific capability checks
- exporters: Export duplicate results to files
- operations: Shared utilities for media operations
- disk_type: HDD/SSD detection for concurrency tuning
"""

from __future__ import annotations

# Import submodules for convenient access
from . import formatters
from . import validators
from . import selection
from . import platform
from . import exporters
from . import operations
from . import disk_type

# Export commonly used functions and classes
from .formatters import format_number, format_time_estimate, format_size
from .validators import (
    validate_path_in_directory,
    validate_file_accessible,
    validate_directory,
    validate_threshold,
    validate_scan_params,
    validate_directories,
    validate_path_in_any_directory,
    is_path_reference_protected,
)
from .selection import (
    SelectionStrategy,
    apply_selection_strategy,
    resolve_group_selections,
    group_keep_and_delete,
)
from .platform import (
    is_windows_admin,
    check_hardlink_support,
    check_symlink_support,
    check_exiftool_available,
)
from .exporters import export_results
from .operations import (
    get_unique_path,
    sanitize_filename,
    truncate_path,
    find_files,
    make_progress_bar,
    parse_date,
)
from .disk_type import detect_media_type, is_rotational, tailor_workers

__all__ = [
    # Submodules
    'formatters',
    'validators',
    'selection',
    'platform',
    'exporters',
    'operations',
    'disk_type',
    # Formatters
    'format_number',
    'format_time_estimate',
    'format_size',
    # Validators
    'validate_path_in_directory',
    'validate_file_accessible',
    'validate_directory',
    'validate_threshold',
    'validate_scan_params',
    'validate_directories',
    'validate_path_in_any_directory',
    'is_path_reference_protected',
    # Selection
    'SelectionStrategy',
    'apply_selection_strategy',
    'resolve_group_selections',
    'group_keep_and_delete',
    # Platform
    'is_windows_admin',
    'check_hardlink_support',
    'check_symlink_support',
    'check_exiftool_available',
    # Exporters
    'export_results',
    # Operations
    'get_unique_path',
    'sanitize_filename',
    'truncate_path',
    'find_files',
    'make_progress_bar',
    'parse_date',
    # Disk type
    'detect_media_type',
    'is_rotational',
    'tailor_workers',
]
