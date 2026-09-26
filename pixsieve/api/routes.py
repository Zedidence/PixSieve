"""
Flask routes for PixSieve GUI.

Contains all API endpoints for the web interface.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request, send_file, render_template, Response

from ..state import scan_state, HistoryManager
from ..models import DuplicateGroup
from ..database import get_cache
from ..config import VIDEO_EXTENSIONS, is_media_file
from ..scanner.dependencies import HAS_VIDEO_SUPPORT, cv2
from ..utils import formatters, validators, selection, get_unique_path
from .orchestrator import ScanOrchestrator
from .schemas import (
    parse_request,
    ScanRequest,
    SelectionsRequest,
    ApplyStrategyRequest,
    DeleteRequest,
    BatchOperationRequest,
    SaveImageRequest,
    RenameImageRequest,
)

# Extensions the in-browser lightbox editor is allowed to save over.
# Server-side allowlist — never trust the client's hidden/disabled toolbar alone.
_EDITABLE_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

# Background state for the move-to-trash operation, polled/streamed by the
# delete confirmation modal so large batches show real progress instead of
# a request that appears to hang until every file has been moved.
_delete_state: dict[str, Any] = {
    'status': 'idle',  # idle | running | complete | error
    'total': 0,
    'moved': 0,
    'errors': 0,
    'error_details': [],
    'current_file': '',
}
_delete_lock = threading.Lock()

# Create blueprint for routes
api = Blueprint('api', __name__)

# Module logger
_logger = logging.getLogger(__name__)

# Lock for thread-safe state persistence
_state_lock = threading.Lock()

# Thumbnail cache directory (persistent across sessions)
_THUMB_DIR = Path(tempfile.gettempdir()) / 'pixsieve_thumbs'
_THUMB_DIR.mkdir(exist_ok=True)
_THUMB_MAX_SIZE = 300  # px max dimension
_THUMB_MAX_CACHE_MB = 500  # Evict oldest thumbs when cache exceeds this size


# Placeholder poster served when a video thumbnail can't be generated
# (opencv not installed, or the frame grab failed - corrupt/unsupported
# codec). Inline SVG rather than a bundled binary asset - <video poster=""&gt;
# accepts any image mimetype.
_VIDEO_PLACEHOLDER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200" viewBox="0 0 300 200">
<rect width="300" height="200" fill="#2a2a2a"/>
<circle cx="150" cy="100" r="34" fill="#555"/>
<path d="M138 82 L170 100 L138 118 Z" fill="#ccc"/>
</svg>"""


def _grab_video_frame(path: str):
    """
    Grab a representative frame (~10% into the video) as a PIL Image.

    Returns None if opencv isn't installed or the frame can't be decoded.
    """
    if not HAS_VIDEO_SUPPORT:
        return None

    from PIL import Image

    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return None
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        target = max(0, int(frame_count * 0.1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        return Image.fromarray(frame[:, :, ::-1])  # BGR -> RGB
    finally:
        cap.release()


def _evict_thumbs_if_needed():
    """Remove oldest thumbnails when the cache exceeds _THUMB_MAX_CACHE_MB."""
    try:
        thumbs = sorted(_THUMB_DIR.glob('*.jpg'), key=lambda p: p.stat().st_atime)
        total = sum(p.stat().st_size for p in thumbs)
        limit = _THUMB_MAX_CACHE_MB * 1024 * 1024
        while total > limit and thumbs:
            oldest = thumbs.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
    except OSError:
        pass  # Non-critical — best effort cleanup


def _safe_save_state():
    """Thread-safe state save to prevent JSON corruption."""
    with _state_lock:
        scan_state.save()


# =============================================================================
# Route Handlers
# =============================================================================

@api.route('/')
def index():
    """Serve the main HTML page."""
    from ..operations.capabilities import OPERATION_VIDEO_SUPPORT
    return render_template('index.html', video_support=OPERATION_VIDEO_SUPPORT)


@api.route('/api/list-subfolders', methods=['POST'])
def api_list_subfolders():
    """Return immediate child folders of a directory."""
    data = request.json or {}
    directory = data.get('directory', '').strip()
    if not directory:
        return jsonify({'error': 'Directory path is required'}), 400
    if not os.path.isabs(directory):
        return jsonify({'error': 'Directory must be an absolute path'}), 400
    if not os.path.isdir(directory):
        return jsonify({'error': f'Directory not found: {directory}'}), 400

    try:
        folders = sorted(
            entry.name
            for entry in os.scandir(directory)
            if entry.is_dir() and not entry.name.startswith('.')
        )
    except PermissionError:
        return jsonify({'error': 'Permission denied reading directory'}), 403

    return jsonify({'folders': folders, 'parent': directory})


@api.route('/api/scan', methods=['POST'])
def api_scan():
    """Start a new duplicate-image scan.
    ---
    tags: [Scan]
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [directories]
            properties:
              directories:
                type: array
                description: List of folders to scan; at most one may set isReference true
                items:
                  type: object
                  properties:
                    path:
                      type: string
                    isReference:
                      type: boolean
                      default: false
              threshold:
                type: integer
                default: 10
                minimum: 0
                maximum: 64
              exactOnly:
                type: boolean
                default: false
              perceptualOnly:
                type: boolean
                default: false
              recursive:
                type: boolean
                default: true
              useCache:
                type: boolean
                default: true
              workers:
                type: integer
                nullable: true
                default: null
                minimum: 1
                maximum: 32
                description: null = pick from the drive type (HDD/SSD/NVMe, SATA/USB/network)
              resolveSymlinks:
                type: boolean
                default: true
              autoSelectStrategy:
                type: string
                default: quality
              includeVideos:
                type: boolean
                default: false
                description: Also scan/deduplicate video files (requires opencv-python-headless)
    responses:
      200:
        description: Scan started
      400:
        description: Validation error
      409:
        description: Scan already running
    """
    # Reject if a scan is already running
    if scan_state.status in ('scanning', 'analyzing', 'comparing'):
        return jsonify({'error': 'A scan is already in progress. Cancel it first or wait for it to finish.'}), 409

    body, err = parse_request(ScanRequest, request.json)
    if err:
        return err

    directories = [
        {'path': d.path.strip(), 'is_reference': d.isReference}
        for d in body.directories
    ]
    threshold = body.threshold
    exact_only = body.exactOnly
    perceptual_only = body.perceptualOnly
    recursive = body.recursive
    use_cache = body.useCache
    use_lsh = body.useLsh
    workers = body.workers
    resolve_symlinks = body.resolveSymlinks
    auto_select_strategy = body.autoSelectStrategy
    include_videos = body.includeVideos

    # Validate directories (existence, permissions, at-most-one-reference)
    is_valid, error = validators.validate_directories(directories)
    if not is_valid:
        return jsonify({'error': error}), 400

    # Validate remaining scan parameters (threshold/exclusivity/workers) —
    # directory itself is already known-valid from the check above.
    is_valid, error = validators.validate_scan_params(
        directory=directories[0]['path'],
        threshold=threshold,
        exact_only=exact_only,
        perceptual_only=perceptual_only,
        workers=workers,
    )
    if not is_valid:
        return jsonify({'error': error}), 400

    # Create scan orchestrator
    orchestrator = ScanOrchestrator(
        scan_state=scan_state,
        directories=directories,
        threshold=threshold,
        exact_only=exact_only,
        perceptual_only=perceptual_only,
        recursive=recursive,
        use_cache=use_cache,
        use_lsh=use_lsh,
        workers=workers,
        resolve_symlinks=resolve_symlinks,
        auto_select_strategy=auto_select_strategy,
        include_videos=include_videos,
        save_callback=_safe_save_state,
    )

    # Start scan in background thread
    thread = threading.Thread(target=orchestrator.run)
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started'})


@api.route('/api/cancel', methods=['POST'])
def api_cancel():
    """Cancel the current scan."""
    if scan_state.status in ('scanning', 'analyzing', 'comparing'):
        scan_state.request_cancel()
        return jsonify({'status': 'cancel_requested'})
    return jsonify({'status': 'no_scan_running'})


@api.route('/api/pause', methods=['POST'])
def api_pause():
    """Pause the current scan."""
    if scan_state.status in ('scanning', 'analyzing', 'comparing'):
        scan_state.pause()
        return jsonify({'status': 'paused'})
    return jsonify({'status': 'no_scan_running'})


@api.route('/api/resume', methods=['POST'])
def api_resume():
    """Resume a paused scan."""
    if scan_state.paused:
        scan_state.resume()
        return jsonify({'status': 'resumed'})
    return jsonify({'status': 'not_paused'})


@api.route('/api/ping')
def api_ping():
    """Simple endpoint for connection monitoring."""
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})


@api.route('/api/status')
def api_status():
    """Return current scan status and progress.
    ---
    tags: [Scan]
    responses:
      200:
        description: Current scan state
    """
    status_dict = scan_state.to_status_dict()
    # Add auto-disabled flag if relevant
    if scan_state.settings.get('auto_disabled_perceptual'):
        status_dict['auto_disabled_perceptual'] = True
    return jsonify(status_dict)


@api.route('/api/scan/stream')
def api_scan_stream():
    """
    Server-Sent Events endpoint for real-time scan progress.

    Replaces the 500 ms polling loop in the frontend with a push-based
    stream. The connection closes automatically when the scan reaches a
    terminal state (complete, error, cancelled, or idle).
    """
    def generate():
        while True:
            status_dict = scan_state.to_status_dict()
            if scan_state.settings.get('auto_disabled_perceptual'):
                status_dict['auto_disabled_perceptual'] = True
            yield f"data: {json.dumps(status_dict)}\n\n"
            if status_dict['status'] in ('complete', 'error', 'cancelled', 'idle'):
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


@api.route('/api/history')
def api_history():
    """Return directory scan history."""
    history = HistoryManager.load()
    return jsonify(history)


@api.route('/api/groups')
def api_groups():
    """Return all duplicate groups found by the last scan.
    ---
    tags: [Results]
    responses:
      200:
        description: Array of duplicate groups
    """
    return jsonify(scan_state.to_groups_dict())


@api.route('/api/selections', methods=['POST'])
def api_selections():
    """Save user selections."""
    body, err = parse_request(SelectionsRequest, request.json or {})
    if err:
        return err
    scan_state.selections = body.selections
    _safe_save_state()
    return jsonify({'status': 'saved'})


@api.route('/api/apply_strategy', methods=['POST'])
def api_apply_strategy():
    """Apply an auto-selection strategy to current results."""
    body, err = parse_request(ApplyStrategyRequest, request.json or {})
    if err:
        return err

    if not scan_state.groups:
        return jsonify({'error': 'No groups to apply strategy to'}), 400

    scan_state.selections = selection.resolve_group_selections(scan_state.groups, body.strategy)
    scan_state.settings['auto_select_strategy'] = body.strategy
    _safe_save_state()

    return jsonify({
        'status': 'applied',
        'selections': scan_state.selections,
    })


@api.route('/api/clear', methods=['POST'])
def api_clear():
    """Clear current session state."""
    scan_state.reset()
    scan_state.clear_file()
    return jsonify({'status': 'cleared'})


@api.route('/api/image')
def api_image():
    """Serve an image file for preview.

    Security: Only serves images within the scanned directory
    to prevent path traversal attacks.
    """
    path = request.args.get('path', '').strip()

    if not path:
        return jsonify({'error': 'No path specified'}), 400

    # Security check: Validate path is within one of the scanned directories
    if scan_state.directories:
        if not validators.validate_path_in_any_directory(path, scan_state.directories):
            _logger.warning(f"Blocked access to file outside scan directories: {path}")
            return jsonify({'error': 'Access denied: file outside scan directory'}), 403
    else:
        # No scan results available - don't serve any files
        return jsonify({'error': 'No active scan results'}), 403

    # Verify file exists
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404

    if not os.path.isfile(path):
        return jsonify({'error': 'Path is not a file'}), 400

    # Serve the file
    try:
        return send_file(path)
    except (OSError, PermissionError) as e:
        _logger.error(f"Error serving file {path}: {e}")
        return jsonify({'error': f'Error serving file: {str(e)}'}), 500


@api.route('/api/image/save', methods=['POST'])
def api_save_image():
    """Save an edited image (from the lightbox editor) back to disk.

    Accepts a canvas `toDataURL()` payload, re-validates it as a real image
    via Pillow (never trusts raw client bytes), and writes it atomically via
    a temp file + os.replace. Only jpg/jpeg/png/webp may be edited — this
    mirrors the client-side toolbar restriction but is enforced independently
    here, since the client-side check is UX only, not a security boundary.
    """
    body, err = parse_request(SaveImageRequest, request.json)
    if err:
        return err
    path = body.path

    ext = os.path.splitext(path)[1].lower()
    if ext not in _EDITABLE_IMAGE_EXTENSIONS:
        return jsonify({'error': 'Editing is not supported for this file type'}), 400

    # Security check: same as /api/image
    if scan_state.directories:
        if not validators.validate_path_in_any_directory(path, scan_state.directories):
            _logger.warning(f"Blocked save to file outside scan directory: {path}")
            return jsonify({'error': 'Access denied: file outside scan directory'}), 403
        if validators.is_path_reference_protected(path, scan_state.directories):
            _logger.warning(f"Blocked save to reference-folder file: {path}")
            return jsonify({'error': 'This file is in the protected reference folder and cannot be modified'}), 403
    else:
        return jsonify({'error': 'No active scan results'}), 403

    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    if not os.path.isfile(path):
        return jsonify({'error': 'Path is not a file'}), 400

    try:
        _, encoded = body.dataUrl.split(',', 1)
        decoded = base64.b64decode(encoded)
    except (ValueError, binascii.Error):
        return jsonify({'error': 'Invalid image data'}), 400

    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(decoded))
        img.load()
    except (UnidentifiedImageError, OSError):
        return jsonify({'error': 'Uploaded data is not a valid image'}), 400

    if ext in ('.jpg', '.jpeg'):
        save_format, save_kwargs = 'JPEG', {'quality': 95}
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
    elif ext == '.webp':
        save_format, save_kwargs = 'WEBP', {'quality': 95}
    else:  # .png
        save_format, save_kwargs = 'PNG', {}

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=ext)
        with os.fdopen(fd, 'wb') as f:
            img.save(f, save_format, **save_kwargs)
        os.replace(tmp_path, path)
    except OSError as e:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        _logger.error(f"Error saving edited image {path}: {e}")
        return jsonify({'error': f'Error saving image: {str(e)}'}), 500

    return jsonify({
        'status': 'saved',
        'path': path,
        'mtime': os.path.getmtime(path),
        'size': os.path.getsize(path),
        'width': img.width,
        'height': img.height,
    })


@api.route('/api/image/rename', methods=['POST'])
def api_rename_image():
    """Rename a single image file in place, within its current directory only."""
    body, err = parse_request(RenameImageRequest, request.json)
    if err:
        return err

    path = body.path
    new_name = body.newName  # already trimmed/validated by the schema

    # Security check: same as /api/image
    if scan_state.directories:
        if not validators.validate_path_in_any_directory(path, scan_state.directories):
            _logger.warning(f"Blocked rename of file outside scan directory: {path}")
            return jsonify({'error': 'Access denied: file outside scan directory'}), 403
        if validators.is_path_reference_protected(path, scan_state.directories):
            _logger.warning(f"Blocked rename of reference-folder file: {path}")
            return jsonify({'error': 'This file is in the protected reference folder and cannot be renamed'}), 403
    else:
        return jsonify({'error': 'No active scan results'}), 403

    if not is_media_file(path):
        return jsonify({'error': 'Only image and video files can be renamed'}), 400
    if not is_media_file(new_name):
        return jsonify({'error': 'The new name must keep an image or video file extension'}), 400
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    if not os.path.isfile(path):
        return jsonify({'error': 'Path is not a file'}), 400

    directory = os.path.dirname(path)
    new_path = os.path.join(directory, new_name)

    # Belt-and-suspenders: even though the schema rejects path separators in
    # newName, confirm the resolved destination is still the same directory
    # before touching the filesystem.
    if os.path.dirname(os.path.abspath(new_path)) != os.path.dirname(os.path.abspath(path)):
        return jsonify({'error': 'Invalid file name'}), 400

    abs_path = os.path.abspath(path)
    abs_new_path = os.path.abspath(new_path)
    if abs_new_path == abs_path:
        return jsonify({'error': 'New name is the same as the current name'}), 400

    # A case-only rename on a case-insensitive filesystem (e.g. Windows NTFS,
    # macOS APFS) would make os.path.exists(new_path) report True even though
    # it's the SAME file — let os.rename handle that natively rather than
    # wrongly rejecting a legitimate capitalization fix as a collision.
    # samefile() compares the underlying file, so it covers both platforms;
    # normcase() only folds case on Windows.
    if os.path.exists(new_path) and not os.path.samefile(path, new_path):
        return jsonify({'error': f'A file named "{new_name}" already exists'}), 409

    try:
        os.rename(path, new_path)
    except OSError as e:
        _logger.error(f"Error renaming {path} -> {new_path}: {e}")
        return jsonify({'error': f'Error renaming file: {str(e)}'}), 500

    return jsonify({'status': 'renamed', 'path': new_path, 'filename': os.path.basename(new_path)})


@api.route('/api/thumbnail')
def api_thumbnail():
    """Serve a cached thumbnail for an image.

    Generates a 300px-max-dimension JPEG thumbnail on first request
    and caches it to disk. Subsequent requests serve from cache.
    Falls back to the full image on error.
    """
    path = request.args.get('path', '').strip()

    if not path:
        return jsonify({'error': 'No path specified'}), 400

    # Security check: same as /api/image
    if scan_state.directories:
        if not validators.validate_path_in_any_directory(path, scan_state.directories):
            return jsonify({'error': 'Access denied: file outside scan directory'}), 403
    else:
        return jsonify({'error': 'No active scan results'}), 403

    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404

    # Build cache key from path + mtime
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return jsonify({'error': 'Cannot read file'}), 500

    cache_key = hashlib.sha256(f"{path}:{mtime}".encode()).hexdigest()
    thumb_path = _THUMB_DIR / f"{cache_key}.jpg"

    # Serve from cache if available
    if thumb_path.exists():
        return send_file(str(thumb_path), mimetype='image/jpeg')

    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        # Video thumbnail = a single grabbed frame, resized/saved as JPEG
        # via the same PIL pipeline used for images below. Falls back to a
        # placeholder poster (not the raw video bytes - unlike /api/image's
        # fallback, an <img>/poster can't render those) if opencv isn't
        # installed or the frame grab fails.
        frame = _grab_video_frame(path)
        if frame is None:
            return Response(_VIDEO_PLACEHOLDER_SVG, mimetype='image/svg+xml')
        try:
            frame.thumbnail((_THUMB_MAX_SIZE, _THUMB_MAX_SIZE))
            if frame.mode not in ('RGB', 'L'):
                frame = frame.convert('RGB')
            frame.save(str(thumb_path), 'JPEG', quality=80, optimize=True)
            _evict_thumbs_if_needed()
            return send_file(str(thumb_path), mimetype='image/jpeg')
        except (OSError, ValueError) as e:
            _logger.warning(f"Video thumbnail generation failed for {path}: {e}")
            return Response(_VIDEO_PLACEHOLDER_SVG, mimetype='image/svg+xml')

    # Generate thumbnail
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.thumbnail((_THUMB_MAX_SIZE, _THUMB_MAX_SIZE), Image.LANCZOS)
            # Convert to RGB for JPEG (handles RGBA, palette, etc.)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            img.save(str(thumb_path), 'JPEG', quality=80, optimize=True)

        _evict_thumbs_if_needed()
        return send_file(str(thumb_path), mimetype='image/jpeg')
    except (OSError, ValueError, ImportError) as e:
        _logger.warning(f"Thumbnail generation failed for {path}: {e}")
        # Fall back to full image
        try:
            return send_file(path)
        except (OSError, PermissionError):
            return jsonify({'error': 'Cannot serve image'}), 500


@api.route('/api/delete', methods=['POST'])
def api_delete():
    """Move selected files to a trash directory.
    ---
    tags: [Results]
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [files, trashDir]
            properties:
              files:
                type: array
                items:
                  type: string
              trashDir:
                type: string
                description: Absolute path to trash destination
    responses:
      200:
        description: Move results with counts and any errors
      400:
        description: Validation error
      403:
        description: Path outside scan directory
    """
    data = request.json
    if not data:
        return jsonify({'error': 'Request body required'}), 400

    files = data.get('files', [])
    trash_dir = data.get('trashDir', '').strip()

    # Validate inputs
    if not trash_dir:
        return jsonify({'error': 'No trash directory specified'}), 400
    if not os.path.isabs(trash_dir):
        return jsonify({'error': 'Trash directory must be an absolute path'}), 400
    if not isinstance(files, list):
        return jsonify({'error': 'Files must be a list'}), 400
    if len(files) == 0:
        return jsonify({'error': 'No files specified'}), 400

    non_media = [f for f in files if not is_media_file(f)]
    if non_media:
        return jsonify({
            'error': 'Only image and video files can be moved to trash',
            'invalid_paths': non_media,
        }), 400

    # Validate all file paths are within a scanned directory
    if scan_state.directories:
        invalid_paths = []
        for filepath in files:
            if not validators.validate_path_in_any_directory(filepath, scan_state.directories):
                invalid_paths.append(filepath)

        if invalid_paths:
            _logger.warning(f"Blocked deletion of files outside scan directory: {invalid_paths}")
            return jsonify({
                'error': 'Security error: some files are outside the scanned directory',
                'invalid_paths': invalid_paths
            }), 403

        # Hard server-side guard: never trust client-submitted selections —
        # unconditionally reject any file under the reference folder.
        protected_paths = [
            f for f in files if validators.is_path_reference_protected(f, scan_state.directories)
        ]
        if protected_paths:
            _logger.warning(f"Blocked deletion of reference-folder files: {protected_paths}")
            return jsonify({
                'error': 'Security error: some files are in the protected reference folder and cannot be deleted',
                'protected_paths': protected_paths,
            }), 403

    # Create trash directory
    try:
        os.makedirs(trash_dir, exist_ok=True)
    except PermissionError:
        return jsonify({'error': f'Cannot create trash directory (permission denied): {trash_dir}'}), 400
    except OSError as e:
        return jsonify({'error': f'Cannot create trash directory: {e}'}), 400

    with _delete_lock:
        if _delete_state['status'] == 'running':
            return jsonify({'error': 'A move-to-trash operation is already in progress'}), 409
        _delete_state.update({
            'status': 'running',
            'total': len(files),
            'moved': 0,
            'errors': 0,
            'error_details': [],
            'current_file': '',
        })

    thread = threading.Thread(target=_delete_worker, args=(list(files), trash_dir), daemon=True)
    thread.start()

    return jsonify({'status': 'started', 'total': len(files)})


def _delete_worker(files: list[str], trash_dir: str) -> None:
    """Move files to the trash directory one at a time, reporting progress."""
    moved = 0
    errors = 0
    error_details = []

    for filepath in files:
        with _delete_lock:
            _delete_state['current_file'] = filepath

        try:
            is_valid, error_msg = validators.validate_file_accessible(filepath)
            if not is_valid:
                errors += 1
                error_details.append({'path': filepath, 'error': error_msg})
                _logger.warning(f"Cannot move {filepath}: {error_msg}")
                continue

            filename = os.path.basename(filepath)
            dest = os.path.join(trash_dir, filename)

            # Handle name conflicts
            counter = 1
            base, ext = os.path.splitext(filename)
            while os.path.exists(dest):
                dest = os.path.join(trash_dir, f"{base}_{counter}{ext}")
                counter += 1

            shutil.move(filepath, dest)
            moved += 1

        except PermissionError as e:
            errors += 1
            error_details.append({'path': filepath, 'error': 'Permission denied'})
            _logger.warning(f"Permission denied moving {filepath}: {e}")
        except FileNotFoundError:
            errors += 1
            error_details.append({'path': filepath, 'error': 'File not found (may have been deleted)'})
        except OSError as e:
            errors += 1
            error_details.append({'path': filepath, 'error': str(e)})
            _logger.warning(f"OS error moving {filepath}: {e}")
        except Exception as e:
            errors += 1
            error_details.append({'path': filepath, 'error': str(e)})
            _logger.exception(f"Unexpected error moving {filepath}: {e}")
        finally:
            with _delete_lock:
                _delete_state['moved'] = moved
                _delete_state['errors'] = errors
                _delete_state['error_details'] = error_details

    with _delete_lock:
        _delete_state['status'] = 'complete'
        _delete_state['current_file'] = ''


@api.route('/api/delete/status')
def api_delete_status():
    """Return current move-to-trash progress."""
    with _delete_lock:
        return jsonify(dict(_delete_state))


@api.route('/api/delete/stream')
def api_delete_stream():
    """Server-Sent Events endpoint for real-time move-to-trash progress."""
    def generate():
        while True:
            with _delete_lock:
                state = dict(_delete_state)
            yield f"data: {json.dumps(state)}\n\n"
            if state['status'] in ('complete', 'error', 'idle'):
                break
            time.sleep(0.3)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


@api.route('/api/cache/stats')
def api_cache_stats():
    """Return cache statistics."""
    cache = get_cache()
    stats = cache.get_stats()
    return jsonify(stats)


@api.route('/api/cache/clear', methods=['POST'])
def api_cache_clear():
    """Clear the image analysis cache."""
    cache = get_cache()
    cache.clear()
    return jsonify({'status': 'cleared'})


@api.route('/api/batch-operation', methods=['POST'])
def api_batch_operation():
    """Run a batch operation on specific files from the results view.
    ---
    tags: [Results]
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [operation, files]
            properties:
              operation:
                type: string
                enum: [convert_jpg, move]
              files:
                type: array
                items:
                  type: string
              destination:
                type: string
                description: Required for move operation
              quality:
                type: integer
                default: 90
                description: JPEG quality for convert_jpg (1-100)
    responses:
      200:
        description: Operation results
      400:
        description: Validation error
      403:
        description: File outside scan directory

    Supported operations: ``convert_jpg``, ``move``.
    Files must all be within the currently scanned directory.
    """
    body, err = parse_request(BatchOperationRequest, request.json)
    if err:
        return err

    operation = body.operation
    files = body.files

    non_media = [f for f in files if not is_media_file(f)]
    if non_media:
        return jsonify({
            'error': 'Only image and video files can be processed',
            'invalid_paths': non_media,
        }), 400

    # Validate all paths are within a scanned directory
    if scan_state.directories:
        bad = [f for f in files if not validators.validate_path_in_any_directory(f, scan_state.directories)]
        if bad:
            return jsonify({'error': 'Some files are outside the scanned directory', 'invalid_paths': bad}), 403

        # Hard server-side guard: reference-folder files may never be moved
        # or converted, regardless of what the client submits — the feature's
        # intent is that the reference folder is untouched canonical truth.
        protected = [f for f in files if validators.is_path_reference_protected(f, scan_state.directories)]
        if protected:
            _logger.warning(f"Blocked batch-operation on reference-folder files: {protected}")
            return jsonify({
                'error': 'Security error: some files are in the protected reference folder and cannot be modified',
                'protected_paths': protected,
            }), 403
    else:
        return jsonify({'error': 'No active scan results'}), 403

    if operation == 'convert_jpg':
        quality = body.quality
        converted, skipped, errors_list = 0, 0, []

        try:
            from PIL import Image as _PILImage
        except ImportError:
            return jsonify({'error': 'Pillow is not installed'}), 500

        for src in files:
            if not os.path.isfile(src):
                errors_list.append({'path': src, 'error': 'File not found'})
                continue
            if src.lower().endswith('.jpg') or src.lower().endswith('.jpeg'):
                skipped += 1
                continue
            try:
                with _PILImage.open(src) as img:
                    if img.mode not in ('RGB', 'L'):
                        img = img.convert('RGB')
                    base, _ = os.path.splitext(src)
                    dest_folder = os.path.dirname(src) or '.'
                    dest = str(get_unique_path(Path(dest_folder), os.path.basename(base) + '.jpg'))
                    img.save(dest, 'JPEG', quality=quality, optimize=True)
                converted += 1
            except (OSError, ValueError) as exc:
                errors_list.append({'path': src, 'error': str(exc)})

        return jsonify({'converted': converted, 'skipped': skipped, 'errors': len(errors_list), 'error_details': errors_list})

    if operation == 'move':
        destination = (body.destination or '').strip()
        if not os.path.isabs(destination):
            return jsonify({'error': 'destination must be an absolute path'}), 400
        try:
            os.makedirs(destination, exist_ok=True)
        except OSError as exc:
            return jsonify({'error': f'Cannot create destination: {exc}'}), 400

        moved, errors_list = 0, []
        for src in files:
            try:
                filename = os.path.basename(src)
                dest = str(get_unique_path(Path(destination), filename))
                shutil.move(src, dest)
                moved += 1
            except (OSError, shutil.Error, ValueError) as exc:
                errors_list.append({'path': src, 'error': str(exc)})

        return jsonify({'moved': moved, 'errors': len(errors_list), 'error_details': errors_list})

    return jsonify({'error': f'Unknown operation: {operation}'}), 400


@api.route('/api/cache/cleanup', methods=['POST'])
def api_cache_cleanup():
    """Clean up stale and missing entries from cache."""
    cache = get_cache()
    
    missing_removed = cache.cleanup_missing()
    
    data = request.json or {}
    max_age_days = data.get('max_age_days', 30)
    stale_removed = cache.cleanup_stale(max_age_days=max_age_days)
    
    cache.vacuum()

    # Also clean thumbnail cache
    thumbs_removed = 0
    try:
        for thumb in _THUMB_DIR.glob('*.jpg'):
            thumb.unlink(missing_ok=True)
            thumbs_removed += 1
    except OSError:
        pass

    return jsonify({
        'missing_removed': missing_removed,
        'stale_removed': stale_removed,
        'thumbs_removed': thumbs_removed,
    })