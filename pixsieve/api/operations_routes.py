"""
Flask routes for media file operations.

Provides API endpoints for all file management operations accessible
from the web GUI. Each endpoint validates inputs, runs the operation
(optionally in a background thread), and returns JSON results.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request, Response

from ..config import IMAGE_EXTENSIONS, RATING_EXTENSIONS, resolve_extensions
from ..operations import (
    delete_empty_folders,
    move_to_parent,
    move_with_structure,
    rename_random,
    rename_by_parent,
    fix_extensions,
    batch_convert_to_jpg,
    randomize_dates,
    randomize_dates_per_folder,
    sort_alphabetical,
    sort_by_resolution,
    ColorImageSorter,
    run_pipeline,
    AVAILABLE_STEPS,
    scan_and_repair,
    RepairStatus,
    strip_favorite_ratings,
    supports_video,
)
from ..utils.platform import check_exiftool_available
from ..utils.validators import validate_directory as _shared_validate_directory
from .schemas import (
    parse_request,
    DirectoryRequest,
    MoveRequest,
    MoveToParentRequest,
    RenameRandomRequest,
    ConvertRequest,
    DateRangeRequest,
    RandomizeDatesPerFolderRequest,
    RecursiveVideoRequest,
    SortColorRequest,
    SortResolutionRequest,
    PipelineRequest,
    RepairRequest,
)

# Blueprint for operations routes
operations_bp = Blueprint('operations', __name__)

# Module logger
_logger = logging.getLogger(__name__)

# Background operation state
_operation_state = {
    'status': 'idle',           # idle | running | complete | error
    'operation': None,          # Name of current operation
    'result': None,             # Result dict from operation
    'error': None,              # Error message if failed
    'progress': None,           # 0-100 integer or None (indeterminate)
    'progress_text': '',        # Human-readable progress description
}
_operation_lock = threading.Lock()


def _update_progress(pct: int | None, text: str = '') -> None:
    """Update the shared progress state from inside an operation worker."""
    with _operation_lock:
        _operation_state['progress'] = pct
        _operation_state['progress_text'] = text


def _validate_directory(directory: str) -> tuple[bool, str | None]:
    """
    Validate a directory path from request data.

    Delegates to the shared utils.validators.validate_directory() (also used
    by api/routes.py and api/schemas.py) instead of a separately-maintained,
    weaker copy -- the shared version additionally checks read permission
    (os.access(..., os.R_OK)), which this file's own copy used to omit, so an
    unreadable directory previously slipped past this check and only failed
    later as an unhandled exception inside the operation itself.
    """
    return _shared_validate_directory(directory)


def _parse_date(date_str: str) -> datetime | None:
    """Parse a date string in YYYY-MM-DD format."""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None


def _parse_extensions(ext_list: list[str] | None) -> set[str] | None:
    """
    Normalize extension list to set with leading dots.

    Treats anything other than a real list (e.g. a bare string like ".jpg"
    sent instead of [".jpg"]) as "no extensions provided" rather than
    iterating it -- a bare string would otherwise iterate character-by-
    character, silently producing a nonsensical extension set instead of
    the caller's intended override.
    """
    if not ext_list:
        return None
    if not isinstance(ext_list, list):
        _logger.warning(f"Ignoring non-list 'extensions' value: {ext_list!r}")
        return None
    return {ext if ext.startswith('.') else f'.{ext}' for ext in ext_list}


def _check_include_videos(include_videos: bool, op_name: str) -> tuple[bool, tuple | None]:
    """
    Validate an already-parsed includeVideos flag against `op_name`'s
    video-support capability.

    Rejects the request with a 400 (rather than silently ignoring the flag)
    if the operation doesn't support video files at all, per the capability
    registry in pixsieve/operations/capabilities.py.

    Returns:
        (include_videos, None) on success, or
        (False, (response, 400)) - return this tuple directly from the
        route to short-circuit it.
    """
    include_videos = bool(include_videos)
    if include_videos and not supports_video(op_name):
        return False, (jsonify({'error': f"'{op_name}' does not support video files"}), 400)
    return include_videos, None


def _run_operation(name: str, func, *args, **kwargs) -> bool:
    """
    Run an operation in a background thread and update state.

    Returns False without starting anything if another operation is already
    running (mirroring the 409-on-concurrent-start behavior /api/scan and
    /api/delete already have) instead of silently racing the shared
    _operation_state dict between two overlapping background threads.
    """
    with _operation_lock:
        if _operation_state['status'] == 'running':
            return False
        _operation_state['status'] = 'running'
        _operation_state['operation'] = name
        _operation_state['result'] = None
        _operation_state['error'] = None
        _operation_state['progress'] = None
        _operation_state['progress_text'] = ''

    def _worker():
        try:
            result = func(*args, **kwargs)
            with _operation_lock:
                _operation_state['status'] = 'complete'
                _operation_state['result'] = result
                _operation_state['progress'] = 100
        except Exception as exc:
            _logger.exception(f"Operation '{name}' failed: {exc}")
            with _operation_lock:
                _operation_state['status'] = 'error'
                _operation_state['error'] = str(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return True


def _busy_response():
    """Standard 409 response for '_run_operation refused, one is already running'."""
    return jsonify({'error': 'Another operation is already running. Wait for it to finish.'}), 409


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
            'progress': _operation_state['progress'],
            'progress_text': _operation_state['progress_text'],
        })


@operations_bp.route('/api/operations/stream')
def stream_status():
    """
    Server-Sent Events endpoint for real-time operation progress.

    Replaces the 1-second polling loop in the frontend with a push-based
    stream. The connection is closed automatically when the operation reaches
    a terminal state (complete, error, or idle).

    Keep the existing /api/operations/status endpoint for the initial
    page-load state check and for clients that do not support SSE.
    """
    def generate():
        while True:
            with _operation_lock:
                state = {
                    'status': _operation_state['status'],
                    'operation': _operation_state['operation'],
                    'result': _make_serializable(_operation_state['result']),
                    'error': _operation_state['error'],
                    'progress': _operation_state['progress'],
                    'progress_text': _operation_state['progress_text'],
                }
            yield f"data: {json.dumps(state)}\n\n"
            if state['status'] in ('complete', 'error', 'idle'):
                break
            time.sleep(0.5)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


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
    body, err = parse_request(MoveToParentRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    include_videos, err = _check_include_videos(body.includeVideos, 'move-to-parent')
    if err:
        return err

    extensions = resolve_extensions(
        IMAGE_EXTENSIONS, include_videos, extra=_parse_extensions(body.extensions),
    )

    if not _run_operation(
        'move-to-parent',
        move_to_parent,
        body.directory,
        extensions=extensions,
        dry_run=body.dryRun,
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'move-to-parent'})


@operations_bp.route('/api/operations/move', methods=['POST'])
def api_move():
    """Move files preserving directory structure."""
    body, err = parse_request(MoveRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400
    if not os.path.isabs(body.destination):
        return jsonify({'error': 'Destination must be an absolute path'}), 400

    if not _run_operation(
        'move',
        move_with_structure,
        body.directory,
        body.destination,
        overwrite=body.overwrite,
        dry_run=body.dryRun,
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'move'})


# =============================================================================
# Rename operations
# =============================================================================

@operations_bp.route('/api/operations/rename/random', methods=['POST'])
def api_rename_random():
    """Rename files to random alphanumeric names."""
    body, err = parse_request(RenameRandomRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    include_videos, err = _check_include_videos(body.includeVideos, 'rename-random')
    if err:
        return err

    extensions = resolve_extensions(
        IMAGE_EXTENSIONS, include_videos, extra=_parse_extensions(body.extensions),
    )

    if not _run_operation(
        'rename-random',
        rename_random,
        body.directory,
        name_length=body.nameLength,
        extensions=extensions,
        recursive=body.recursive,
        dry_run=body.dryRun,
        workers=body.workers,
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'rename-random'})


@operations_bp.route('/api/operations/rename/parent', methods=['POST'])
def api_rename_parent():
    """Rename files based on parent folder names."""
    body, err = parse_request(DirectoryRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    if not _run_operation(
        'rename-parent',
        rename_by_parent,
        body.directory,
        dry_run=body.dryRun,
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'rename-parent'})


# =============================================================================
# Sort operations
# =============================================================================

@operations_bp.route('/api/operations/sort/alpha', methods=['POST'])
def api_sort_alpha():
    """Sort files into alphabetical group folders."""
    body, err = parse_request(DirectoryRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    if not _run_operation(
        'sort-alpha',
        sort_alphabetical,
        body.directory,
        dry_run=body.dryRun,
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'sort-alpha'})


@operations_bp.route('/api/operations/sort/color', methods=['POST'])
def api_sort_color():
    """Sort images by color using K-means clustering."""
    body, err = parse_request(SortColorRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    include_videos, err = _check_include_videos(body.includeVideos, 'sort-color')
    if err:
        return err

    sorter = ColorImageSorter(body.directory, include_videos=include_videos)
    method = body.method

    if method == 'dominant':
        started = _run_operation(
            'sort-color-dominant',
            sorter.sort_by_dominant_color,
            copy_files=body.copyFiles,
            dry_run=body.dryRun,
        )
    elif method == 'bw':
        started = _run_operation(
            'sort-color-bw',
            sorter.sort_by_color_bw,
            copy_files=body.copyFiles,
            dry_run=body.dryRun,
        )
    elif method == 'palette':
        started = _run_operation(
            'sort-color-palette',
            sorter.sort_by_palette,
            copy_files=body.copyFiles,
            n_colors=body.nColors,
            dry_run=body.dryRun,
        )
    else:
        started = _run_operation(
            'sort-color-analyze',
            sorter.analyze_colors,
        )

    if not started:
        return _busy_response()
    return jsonify({'status': 'started', 'operation': f'sort-color-{method}'})


@operations_bp.route('/api/operations/sort/resolution', methods=['POST'])
def api_sort_resolution():
    """Sort images by resolution category and orientation into sub-folders."""
    body, err = parse_request(SortResolutionRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    include_videos, err = _check_include_videos(body.includeVideos, 'sort-resolution')
    if err:
        return err

    def _with_progress():
        return sort_by_resolution(
            body.directory,
            copy_files=body.copyFiles,
            dry_run=body.dryRun,
            on_progress=_update_progress,
            include_videos=include_videos,
        )

    if not _run_operation('sort-resolution', _with_progress):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'sort-resolution'})


# =============================================================================
# Convert operations
# =============================================================================

@operations_bp.route('/api/operations/fix-extensions', methods=['POST'])
def api_fix_extensions():
    """Fix file extensions that don't match actual image format."""
    body, err = parse_request(RecursiveVideoRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    _, err = _check_include_videos(body.includeVideos, 'fix-extensions')
    if err:
        return err

    if not _run_operation(
        'fix-extensions',
        fix_extensions,
        body.directory,
        recursive=body.recursive,
        dry_run=body.dryRun,
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'fix-extensions'})


@operations_bp.route('/api/operations/convert', methods=['POST'])
def api_convert():
    """Convert PNG/BMP/WEBP images to JPG."""
    body, err = parse_request(ConvertRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    _, err = _check_include_videos(body.includeVideos, 'convert')
    if err:
        return err

    if not _run_operation(
        'convert',
        batch_convert_to_jpg,
        body.directory,
        quality=body.quality,
        delete_originals=body.deleteOriginals,
        recursive=body.recursive,
        dry_run=body.dryRun,
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'convert'})


# =============================================================================
# Metadata operations
# =============================================================================

@operations_bp.route('/api/operations/metadata/randomize-dates', methods=['POST'])
def api_randomize_dates():
    """Randomize image dates: EXIF metadata (JPG/TIFF) and filesystem timestamps."""
    body, err = parse_request(DateRangeRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    # The schema already checked shape (10 chars) and lexicographic
    # ordering; _parse_date() still does the real calendar-validity parse
    # (e.g. "9999-99-99" is 10 chars and would sort fine, but isn't a date).
    start_date = _parse_date(body.startDate)
    end_date = _parse_date(body.endDate)
    if not start_date:
        return jsonify({'error': 'Valid start date required (YYYY-MM-DD)'}), 400
    if not end_date:
        return jsonify({'error': 'Valid end date required (YYYY-MM-DD)'}), 400

    include_videos, err = _check_include_videos(body.includeVideos, 'randomize-dates')
    if err:
        return err

    if not _run_operation(
        'randomize-dates',
        randomize_dates,
        body.directory,
        start_date=start_date,
        end_date=end_date,
        recursive=body.recursive,
        dry_run=body.dryRun,
        sync_exif=body.syncExif,
        extensions=resolve_extensions(IMAGE_EXTENSIONS, include_videos),
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'randomize-dates'})


@operations_bp.route('/api/operations/metadata/randomize-dates-per-folder', methods=['POST'])
def api_randomize_dates_per_folder():
    """Randomize image dates with a per-folder date range."""
    body, err = parse_request(RandomizeDatesPerFolderRequest, request.json)
    if err:
        return err

    folder_ranges = []
    for entry in body.folderRanges:
        folder = entry.folder.strip()
        label = entry.name or folder
        valid, error = _validate_directory(folder)
        if not valid:
            return jsonify({'error': f'{label}: {error}'}), 400

        # The schema already checked shape (10 chars); _parse_date() still
        # does the real calendar-validity parse and the ordering check
        # (unlike DateRangeRequest, FolderDateRange has no start<end model
        # validator, since garbage strings must be parsed per-entry first
        # to even compare them meaningfully).
        start_date = _parse_date(entry.startDate)
        end_date = _parse_date(entry.endDate)
        if not start_date or not end_date:
            return jsonify({'error': f'{label}: Valid dates required'}), 400
        if start_date >= end_date:
            return jsonify({'error': f'{label}: Start date must be before end date'}), 400

        folder_ranges.append({
            'folder': folder,
            'startDate': start_date,
            'endDate': end_date,
        })

    include_videos, err = _check_include_videos(body.includeVideos, 'randomize-dates')
    if err:
        return err

    if not _run_operation(
        'randomize-dates',
        randomize_dates_per_folder,
        folder_ranges,
        dry_run=body.dryRun,
        sync_exif=body.syncExif,
        extensions=resolve_extensions(IMAGE_EXTENSIONS, include_videos),
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'randomize-dates'})


@operations_bp.route('/api/operations/metadata/strip-ratings', methods=['POST'])
def api_strip_ratings():
    """Remove 5-star/favorite rating tags from images via exiftool."""
    body, err = parse_request(RecursiveVideoRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    available, reason = check_exiftool_available()
    if not available:
        return jsonify({'error': f'exiftool not available: {reason}'}), 400

    include_videos, err = _check_include_videos(body.includeVideos, 'strip-ratings')
    if err:
        return err

    if not _run_operation(
        'strip-ratings',
        strip_favorite_ratings,
        body.directory,
        recursive=body.recursive,
        dry_run=body.dryRun,
        extensions=resolve_extensions(RATING_EXTENSIONS, include_videos),
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'strip-ratings'})


# =============================================================================
# Cleanup operations
# =============================================================================

@operations_bp.route('/api/operations/cleanup', methods=['POST'])
def api_cleanup():
    """Delete empty folders recursively."""
    body, err = parse_request(DirectoryRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    if not _run_operation(
        'cleanup',
        delete_empty_folders,
        body.directory,
        dry_run=body.dryRun,
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'cleanup'})


# =============================================================================
# Pipeline operations
# =============================================================================

@operations_bp.route('/api/operations/pipeline', methods=['POST'])
def api_pipeline():
    """Run a multi-step operation pipeline."""
    body, err = parse_request(PipelineRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    steps = body.steps

    # Step names are dynamic business data (the pipeline registry), not
    # schema-expressible without coupling schemas.py to operations.pipeline.
    invalid = [s for s in steps if s not in AVAILABLE_STEPS]
    if invalid:
        return jsonify({
            'error': f'Unknown steps: {invalid}. Available: {list(AVAILABLE_STEPS.keys())}'
        }), 400

    include_videos, err = _check_include_videos(body.includeVideos, 'pipeline')
    if err:
        return err

    # Dates are only required for certain steps, so this stays a post-schema
    # conditional check rather than a plain required schema field; the
    # schema already checked shape (10 chars) when startDate/endDate are
    # given, but _parse_date() still does the real calendar-validity parse.
    date_steps = {'randomize_dates'}
    start_date = None
    end_date = None
    if date_steps & set(steps):
        start_date = _parse_date(body.startDate or '')
        end_date = _parse_date(body.endDate or '')
        if not start_date or not end_date:
            return jsonify({
                'error': 'Start and end dates required for date-related steps (YYYY-MM-DD)'
            }), 400
        if start_date >= end_date:
            return jsonify({'error': 'Start date must be before end date'}), 400

    # trash_dir is required when repair_corrupt step is included
    trash_dir = (body.trashDir or '').strip() or None
    if 'repair_corrupt' in steps and not trash_dir:
        from ..config import DEFAULT_TRASH_DIR
        trash_dir = DEFAULT_TRASH_DIR

    if not _run_operation(
        'pipeline',
        run_pipeline,
        body.directory,
        steps=steps,
        start_date=start_date,
        end_date=end_date,
        name_length=body.nameLength,
        jpg_quality=body.jpgQuality,
        delete_originals=body.deleteOriginals,
        recursive=body.recursive,
        dry_run=body.dryRun,
        trash_dir=trash_dir,
        include_videos=include_videos,
    ):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'pipeline'})


# =============================================================================
# Repair operations
# =============================================================================

@operations_bp.route('/api/operations/repair', methods=['POST'])
def api_repair():
    """Scan for corrupt images, attempt repair, quarantine unfixable files."""
    body, err = parse_request(RepairRequest, request.json)
    if err:
        return err

    valid, error = _validate_directory(body.directory)
    if not valid:
        return jsonify({'error': error}), 400

    _, err = _check_include_videos(body.includeVideos, 'repair')
    if err:
        return err

    if not os.path.isabs(body.trashFolder):
        return jsonify({'error': 'Trash folder must be an absolute path'}), 400

    def _repair_and_serialize():
        result = scan_and_repair(
            body.directory,
            trash_folder=body.trashFolder,
            attempt_repair=body.attemptRepair,
            quarantine_unfixable=body.quarantineUnfixable,
            dry_run=body.dryRun,
            max_workers=body.workers,
        )
        # Serialize RepairResult objects and separate problem files from stats
        problems = [
            r.to_dict()
            for r in result.get('results', [])
            if r.status != RepairStatus.CLEAN
        ]
        return {
            'checked': result['checked'],
            'clean': result['clean'],
            'repaired': result['repaired'],
            'quarantined': result['quarantined'],
            'permission_errors': result['permission_errors'],
            'skipped': result['skipped'],
            'errors': result['errors'],
            'problems': problems,
        }

    if not _run_operation('repair', _repair_and_serialize):
        return _busy_response()
    return jsonify({'status': 'started', 'operation': 'repair'})
