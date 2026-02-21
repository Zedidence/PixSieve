"""
Workflow pipeline engine.

Provides functionality to chain multiple operations together in a workflow.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .rename import rename_random
from .convert import batch_convert_to_jpg
from .metadata import randomize_exif_dates, randomize_file_dates
from .cleanup import delete_empty_folders

logger = logging.getLogger(__name__)

# Registry of available pipeline steps
AVAILABLE_STEPS = {
    'random_rename': {
        'label': 'Rename files to random names',
        'func': 'random_rename',
    },
    'convert_jpg': {
        'label': 'Convert PNG/BMP/WEBP to JPG',
        'func': 'convert_jpg',
    },
    'randomize_exif': {
        'label': 'Randomize EXIF dates (Date Taken)',
        'func': 'randomize_exif',
    },
    'randomize_dates': {
        'label': 'Randomize file system dates',
        'func': 'randomize_dates',
    },
    'cleanup_empty': {
        'label': 'Delete empty folders',
        'func': 'cleanup_empty',
    },
}


def run_pipeline(
    directory: str | Path,
    steps: list[str],
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    name_length: int = 12,
    jpg_quality: int = 95,
    delete_originals: bool = False,
    recursive: bool = True,
    dry_run: bool = False,
) -> dict[str, dict]:
    """
    Execute a sequence of operations on directory.

    Args:
        directory: Directory to process
        steps: List of step keys from AVAILABLE_STEPS
        start_date: Start date for date randomization (required for date steps)
        end_date: End date for date randomization (required for date steps)
        name_length: Length of random names (default: 12)
        jpg_quality: JPG conversion quality (default: 95)
        delete_originals: Delete originals after conversion (default: False)
        recursive: Process subdirectories (default: True)
        dry_run: Only report, don't modify files (default: False)

    Returns:
        Dictionary mapping step names to their result dictionaries

    Examples:
        >>> from datetime import datetime
        >>> steps = ['random_rename', 'convert_jpg', 'cleanup_empty']
        >>> results = run_pipeline('/photos', steps, dry_run=True)
        >>> print(results['random_rename'])

    Notes:
        - Steps are executed in order
        - Date steps (randomize_exif, randomize_dates) require start_date and end_date
        - Unknown steps will cause pipeline to abort
        - Each step's results are printed during execution
    """
    directory = Path(directory)
    if not directory.is_dir():
        logger.error(f"Directory not found: {directory}")
        return {}

    # Validate steps
    for step in steps:
        if step not in AVAILABLE_STEPS:
            logger.error(f"Unknown step: '{step}'. Available: {list(AVAILABLE_STEPS)}")
            return {}

    # Check date requirements
    date_steps = {'randomize_exif', 'randomize_dates'}
    if date_steps & set(steps):
        if start_date is None or end_date is None:
            logger.error("start_date and end_date are required for date-related steps")
            return {}
        if start_date >= end_date:
            logger.error("start_date must be before end_date")
            return {}

    results: dict[str, dict] = {}
    total = len(steps)

    for i, step in enumerate(steps, 1):
        label = AVAILABLE_STEPS[step]['label']
        print(f"\n[STEP {i}/{total}] {label}")
        print("-" * 70)

        if step == 'random_rename':
            results[step] = rename_random(
                directory,
                name_length=name_length,
                recursive=recursive,
                dry_run=dry_run,
            )

        elif step == 'convert_jpg':
            results[step] = batch_convert_to_jpg(
                directory,
                quality=jpg_quality,
                delete_originals=delete_originals,
                recursive=recursive,
                dry_run=dry_run,
            )

        elif step == 'randomize_exif':
            results[step] = randomize_exif_dates(
                directory,
                start_date,
                end_date,
                recursive=recursive,
                dry_run=dry_run,
            )

        elif step == 'randomize_dates':
            results[step] = randomize_file_dates(
                directory,
                start_date,
                end_date,
                recursive=recursive,
                dry_run=dry_run,
            )

        elif step == 'cleanup_empty':
            results[step] = delete_empty_folders(directory, dry_run=dry_run)

    # Summary
    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE")
    print("=" * 70)
    for step, result in results.items():
        label = AVAILABLE_STEPS[step]['label']
        print(f"  {label}: {result}")
    print("=" * 70)

    return results


__all__ = ['run_pipeline', 'AVAILABLE_STEPS']
