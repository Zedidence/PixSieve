"""
Flask routes for media file operations.

Provides API endpoints for all file management operations accessible
from the web GUI. Each endpoint validates inputs, runs the operation
(optionally in a background thread), and returns JSON results.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from ..operations import (
    delete_empty_folders,
    move_to_parent,
    move_with_structure,
    rename_random,
    rename_by_parent,
    fix_extensions,
    batch_convert_to_jpg,
    randomize_exif_dates,
    randomize_file_dates,
    sort_alphabetical,
    ColorImageSorter,
    run_pipeline,
    AVAILABLE_STEPS,
)

# Blueprint for operations routes
operations_bp = Blueprint('operations', __name__)

# Module logger
_logger = logging.getLogger(__name__)

# Background operation state
_operation_state = {
    'status': 'idle',       # idle | running | complete | error
    'operation': None,      # Name of current operation
    'result': None,         # Result dict from operation
    'error': None,          # Error message if failed
}
_operation_lock = threading.Lock()


def _validate_directory(directory: str) -> tuple[bool, str | None]:
    """Validate a directory path from request data."""
    if not directory:
        return False, 'Directory path is required'
    if not os.path.isabs(directory):
        return False, 'Directory must be an absolute path'
    if not os.path.isdir(directory):
        return False, f'Directory not found: {directory}'
    return True, None


def _parse_date(date_str: str) -> datetime | None:
    """Parse a date string in YYYY-MM-DD format."""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None


def _parse_extensions(ext_list: list[str] | None) -> set[str] | None:
    """Normalize extension list to set with leading dots."""
    if not ext_list:
        return None
    return {ext if ext.startswith('.') else f'.{ext}' for ext in ext_list}


def _run_operation(name: str, func, *args, **kwargs):
    """Run an operation in a background thread and update state."""
    def _worker():
        with _operation_lock:
            _operation_state['status'] = 'running'
            _operation_state['operation'] = name
            _operation_state['result'] = None
            _operation_state['error'] = None

        try:
            result = func(*args, **kwargs)
            with _operation_lock:
                _operation_state['status'] = 'complete'
                _operation_state['result'] = result
        except Exception as exc:
            _logger.exception(f"Operation '{name}' failed: {exc}")
            with _operation_lock:
                _operation_state['status'] = 'error'
                _operation_state['error'] = str(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def _make_serializable(obj: Any) -> Any:
    """Ensure operation results are JSON-serializable."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


# =============================================================================
# Status endpoint
# =============================================================================

@operations_bp.route('/api/operations/status')
def operations_status():
    """Return current operation status."""
    with _operation_lock:
        return jsonify({
            'status': _operation_state['status'],
            'operation': _operation_state['operation'],
            'result': _make_serializable(_operation_state['result']),
            'error': _operation_state['error'],
        })


@operations_bp.route('/api/operations/available')
def operations_available():
    """Return list of available operations and pipeline steps."""
    return jsonify({
        'pipeline_steps': {
            k: v['label'] for k, v in AVAILABLE_STEPS.items()
        },
    })


# =============================================================================
# Move operations
# =============================================================================

@operations_bp.route('/api/operations/move-to-parent', methods=['POST'])
def api_move_to_parent():
    """Move all images from subdirectories into the parent folder."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    dry_run = data.get('dryRun', True)
    extensions = _parse_extensions(data.get('extensions'))

    _run_operation(
        'move-to-parent',
        move_to_parent,
        directory,
        extensions=extensions,
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'move-to-parent'})


@operations_bp.route('/api/operations/move', methods=['POST'])
def api_move():
    """Move files preserving directory structure."""
    data = request.json or {}
    source = data.get('directory', '').strip()
    destination = data.get('destination', '').strip()

    valid, error = _validate_directory(source)
    if not valid:
        return jsonify({'error': error}), 400
    if not destination:
        return jsonify({'error': 'Destination path is required'}), 400
    if not os.path.isabs(destination):
        return jsonify({'error': 'Destination must be an absolute path'}), 400

    dry_run = data.get('dryRun', True)
    overwrite = data.get('overwrite', False)

    _run_operation(
        'move',
        move_with_structure,
        source,
        destination,
        overwrite=overwrite,
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'move'})


# =============================================================================
# Rename operations
# =============================================================================

@operations_bp.route('/api/operations/rename/random', methods=['POST'])
def api_rename_random():
    """Rename files to random alphanumeric names."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    dry_run = data.get('dryRun', True)
    name_length = data.get('nameLength', 12)
    extensions = _parse_extensions(data.get('extensions'))
    recursive = data.get('recursive', True)
    workers = max(1, min(data.get('workers', 4), 16))

    _run_operation(
        'rename-random',
        rename_random,
        directory,
        name_length=name_length,
        extensions=extensions,
        recursive=recursive,
        dry_run=dry_run,
        workers=workers,
    )
    return jsonify({'status': 'started', 'operation': 'rename-random'})


@operations_bp.route('/api/operations/rename/parent', methods=['POST'])
def api_rename_parent():
    """Rename files based on parent folder names."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    dry_run = data.get('dryRun', True)

    _run_operation(
        'rename-parent',
        rename_by_parent,
        directory,
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'rename-parent'})


# =============================================================================
# Sort operations
# =============================================================================

@operations_bp.route('/api/operations/sort/alpha', methods=['POST'])
def api_sort_alpha():
    """Sort files into alphabetical group folders."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    dry_run = data.get('dryRun', True)

    _run_operation(
        'sort-alpha',
        sort_alphabetical,
        directory,
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'sort-alpha'})


@operations_bp.route('/api/operations/sort/color', methods=['POST'])
def api_sort_color():
    """Sort images by color using K-means clustering."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    dry_run = data.get('dryRun', True)
    method = data.get('method', 'dominant')
    copy_files = data.get('copyFiles', False)
    n_colors = data.get('nColors', 3)

    sorter = ColorImageSorter(directory)

    if method == 'dominant':
        _run_operation(
            'sort-color-dominant',
            sorter.sort_by_dominant_color,
            copy_files=copy_files,
            dry_run=dry_run,
        )
    elif method == 'bw':
        _run_operation(
            'sort-color-bw',
            sorter.sort_by_color_bw,
            copy_files=copy_files,
            dry_run=dry_run,
        )
    elif method == 'palette':
        _run_operation(
            'sort-color-palette',
            sorter.sort_by_palette,
            copy_files=copy_files,
            n_colors=n_colors,
            dry_run=dry_run,
        )
    elif method == 'analyze':
        _run_operation(
            'sort-color-analyze',
            sorter.analyze_colors,
        )
    else:
        return jsonify({'error': f'Unknown color sort method: {method}'}), 400

    return jsonify({'status': 'started', 'operation': f'sort-color-{method}'})


# =============================================================================
# Convert operations
# =============================================================================

@operations_bp.route('/api/operations/fix-extensions', methods=['POST'])
def api_fix_extensions():
    """Fix file extensions that don't match actual image format."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    dry_run = data.get('dryRun', True)
    recursive = data.get('recursive', True)

    _run_operation(
        'fix-extensions',
        fix_extensions,
        directory,
        recursive=recursive,
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'fix-extensions'})


@operations_bp.route('/api/operations/convert', methods=['POST'])
def api_convert():
    """Convert PNG/BMP/WEBP images to JPG."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    dry_run = data.get('dryRun', True)
    quality = max(1, min(data.get('quality', 95), 100))
    delete_originals = data.get('deleteOriginals', False)
    recursive = data.get('recursive', True)

    _run_operation(
        'convert',
        batch_convert_to_jpg,
        directory,
        quality=quality,
        delete_originals=delete_originals,
        recursive=recursive,
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'convert'})


# =============================================================================
# Metadata operations
# =============================================================================

@operations_bp.route('/api/operations/metadata/randomize-exif', methods=['POST'])
def api_randomize_exif():
    """Randomize EXIF date metadata."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    start_date = _parse_date(data.get('startDate', ''))
    end_date = _parse_date(data.get('endDate', ''))

    if not start_date:
        return jsonify({'error': 'Valid start date required (YYYY-MM-DD)'}), 400
    if not end_date:
        return jsonify({'error': 'Valid end date required (YYYY-MM-DD)'}), 400
    if start_date >= end_date:
        return jsonify({'error': 'Start date must be before end date'}), 400

    dry_run = data.get('dryRun', True)
    recursive = data.get('recursive', True)

    _run_operation(
        'randomize-exif',
        randomize_exif_dates,
        directory,
        start_date=start_date,
        end_date=end_date,
        recursive=recursive,
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'randomize-exif'})


@operations_bp.route('/api/operations/metadata/randomize-dates', methods=['POST'])
def api_randomize_dates():
    """Randomize file system timestamps."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    start_date = _parse_date(data.get('startDate', ''))
    end_date = _parse_date(data.get('endDate', ''))

    if not start_date:
        return jsonify({'error': 'Valid start date required (YYYY-MM-DD)'}), 400
    if not end_date:
        return jsonify({'error': 'Valid end date required (YYYY-MM-DD)'}), 400
    if start_date >= end_date:
        return jsonify({'error': 'Start date must be before end date'}), 400

    dry_run = data.get('dryRun', True)
    recursive = data.get('recursive', True)

    _run_operation(
        'randomize-dates',
        randomize_file_dates,
        directory,
        start_date=start_date,
        end_date=end_date,
        recursive=recursive,
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'randomize-dates'})


# =============================================================================
# Cleanup operations
# =============================================================================

@operations_bp.route('/api/operations/cleanup', methods=['POST'])
def api_cleanup():
    """Delete empty folders recursively."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    dry_run = data.get('dryRun', True)

    _run_operation(
        'cleanup',
        delete_empty_folders,
        directory,
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'cleanup'})


# =============================================================================
# Pipeline operations
# =============================================================================

@operations_bp.route('/api/operations/pipeline', methods=['POST'])
def api_pipeline():
    """Run a multi-step operation pipeline."""
    data = request.json or {}
    directory = data.get('directory', '').strip()

    valid, error = _validate_directory(directory)
    if not valid:
        return jsonify({'error': error}), 400

    steps = data.get('steps', [])
    if not steps or not isinstance(steps, list):
        return jsonify({'error': 'Steps list is required'}), 400

    # Validate step names
    invalid = [s for s in steps if s not in AVAILABLE_STEPS]
    if invalid:
        return jsonify({
            'error': f'Unknown steps: {invalid}. Available: {list(AVAILABLE_STEPS.keys())}'
        }), 400

    dry_run = data.get('dryRun', True)

    # Parse dates if needed
    date_steps = {'randomize_exif', 'randomize_dates'}
    start_date = None
    end_date = None
    if date_steps & set(steps):
        start_date = _parse_date(data.get('startDate', ''))
        end_date = _parse_date(data.get('endDate', ''))
        if not start_date or not end_date:
            return jsonify({
                'error': 'Start and end dates required for date-related steps (YYYY-MM-DD)'
            }), 400
        if start_date >= end_date:
            return jsonify({'error': 'Start date must be before end date'}), 400

    _run_operation(
        'pipeline',
        run_pipeline,
        directory,
        steps=steps,
        start_date=start_date,
        end_date=end_date,
        name_length=data.get('nameLength', 12),
        jpg_quality=max(1, min(data.get('jpgQuality', 95), 100)),
        delete_originals=data.get('deleteOriginals', False),
        recursive=data.get('recursive', True),
        dry_run=dry_run,
    )
    return jsonify({'status': 'started', 'operation': 'pipeline'})
