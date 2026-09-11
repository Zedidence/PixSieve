"""
Media file operations for PixSieve.

Provides comprehensive file management operations including:
- File movement and organization
- Renaming strategies
- Sorting (alphabetical and color-based)
- Format conversion
- Metadata manipulation
- Cleanup operations
- Pipeline workflows
"""

from __future__ import annotations

from .cleanup import delete_empty_folders
from .move import move_to_parent, move_with_structure
from .rename import rename_random, rename_by_parent
from .convert import fix_extensions, batch_convert_to_jpg
from .metadata import (
    randomize_dates,
    randomize_dates_per_folder,
)
from .sort import sort_alphabetical, ColorImageSorter, sort_by_resolution
from .pipeline import run_pipeline, AVAILABLE_STEPS
from .repair import scan_and_repair, RepairResult, CorruptionType, RepairStatus
from .ratings import strip_favorite_ratings
from .capabilities import OPERATION_VIDEO_SUPPORT, supports_video, needs_video_flag

__all__ = [
    # Cleanup
    'delete_empty_folders',
    # Move
    'move_to_parent',
    'move_with_structure',
    # Rename
    'rename_random',
    'rename_by_parent',
    # Convert
    'fix_extensions',
    'batch_convert_to_jpg',
    # Metadata
    'randomize_dates',
    'randomize_dates_per_folder',
    # Sort
    'sort_alphabetical',
    'ColorImageSorter',
    'sort_by_resolution',
    # Pipeline
    'run_pipeline',
    'AVAILABLE_STEPS',
    # Repair
    'scan_and_repair',
    'RepairResult',
    'CorruptionType',
    'RepairStatus',
    # Ratings
    'strip_favorite_ratings',
    # Capabilities
    'OPERATION_VIDEO_SUPPORT',
    'supports_video',
    'needs_video_flag',
]
