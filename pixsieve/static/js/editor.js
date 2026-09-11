// =============================================================
// Lightbox image editor
//
// Model: preview-then-explicit-Save. Rotate/flip/crop only transform an
// offscreen canvas and update the lightbox <img>; nothing touches disk
// until Save is clicked. Navigating away or closing the lightbox silently
// discards any pending (unsaved) edit. Rotate/flip/crop share one linear
// undo/redo history (full-snapshot stack, capped at 10, matching the
// source ImageGalleryEditor app this was ported from).
// =============================================================

const EDITABLE_EXTS = new Set(['jpg', 'jpeg', 'png', 'webp']);
const EDITOR_UNDO_CAP = 10;

let editorPath = null;            // path of the image currently open in the lightbox
let editorCleanUrl = null;        // '/api/image?path=...' — last known to match disk
let editorPreviewDataUrl = null;  // pending canvas result, or null if no pending edit
let editorHasPendingEdit = false;

let editorUndoStack = [];         // stack of prior editorPreviewDataUrl values (may include null)
let editorRedoStack = [];

let editorIsCropping = false;
let editorIsDragging = false;
let editorCropStart = null;       // {x, y} in crop-canvas pixel space
let editorCropEnd = null;         // {x, y} in crop-canvas pixel space
let editorCropScale = 1;          // display px per natural px, captured on entering crop mode

let _editorInfoFilename = '';
let _editorInfoSize = '';

function _editorExt(path) {
    const m = /\.([a-z0-9]+)$/i.exec(path || '');
    return m ? m[1].toLowerCase() : '';
}

function editorOnImageChanged(path) {
    if (editorIsCropping) editorExitCropMode();

    editorPath = path;
    editorCleanUrl = '/api/image?path=' + encodeURIComponent(path);
    editorPreviewDataUrl = null;
    editorHasPendingEdit = false;
    editorUndoStack = [];
    editorRedoStack = [];

    const toolbar = document.getElementById('lightboxEditToolbar');
    const editable = EDITABLE_EXTS.has(_editorExt(path));
    if (toolbar) toolbar.style.display = editable ? 'flex' : 'none';

    const meta = _editorFindImageMeta(path);
    _editorInfoFilename = meta ? meta.filename : (path.split(/[\\/]/).pop() || '');
    _editorInfoSize = meta ? meta.file_size_formatted : '';
    _editorRenderInfo();

    _editorUpdateButtons();
}

function editorReset() {
    if (editorIsCropping) editorExitCropMode();

    editorPath = null;
    editorCleanUrl = null;
    editorPreviewDataUrl = null;
    editorHasPendingEdit = false;
    editorUndoStack = [];
    editorRedoStack = [];
    _editorUpdateButtons();
}

function _editorUpdateButtons() {
    const saveBtn = document.getElementById('lightboxSaveBtn');
    const discardBtn = document.getElementById('lightboxDiscardBtn');
    const undoBtn = document.getElementById('lightboxUndoBtn');
    const redoBtn = document.getElementById('lightboxRedoBtn');
    const renameBtn = document.getElementById('lightboxRenameBtn');
    if (saveBtn) saveBtn.disabled = !editorHasPendingEdit;
    if (discardBtn) discardBtn.disabled = !editorHasPendingEdit;
    if (undoBtn) undoBtn.disabled = editorUndoStack.length === 0;
    if (redoBtn) redoBtn.disabled = editorRedoStack.length === 0;
    if (renameBtn) renameBtn.disabled = editorHasPendingEdit;
}

function editorPushUndo() {
    editorUndoStack.push(editorPreviewDataUrl);
    if (editorUndoStack.length > EDITOR_UNDO_CAP) editorUndoStack.shift();
    editorRedoStack = [];
}

function editorUndo() {
    if (editorIsCropping || editorUndoStack.length === 0) return;
    editorRedoStack.push(editorPreviewDataUrl);
    if (editorRedoStack.length > EDITOR_UNDO_CAP) editorRedoStack.shift();
    editorPreviewDataUrl = editorUndoStack.pop();
    editorHasPendingEdit = editorPreviewDataUrl !== null;
    document.getElementById('lightboxImg').src = editorPreviewDataUrl || editorCleanUrl;
    _editorUpdateButtons();
}

function editorRedo() {
    if (editorIsCropping || editorRedoStack.length === 0) return;
    editorUndoStack.push(editorPreviewDataUrl);
    if (editorUndoStack.length > EDITOR_UNDO_CAP) editorUndoStack.shift();
    editorPreviewDataUrl = editorRedoStack.pop();
    editorHasPendingEdit = editorPreviewDataUrl !== null;
    document.getElementById('lightboxImg').src = editorPreviewDataUrl || editorCleanUrl;
    _editorUpdateButtons();
}

function _editorLoadCurrentImage() {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('Image load failed'));
        img.src = editorPreviewDataUrl || editorCleanUrl;
    });
}

function _editorEncode(canvas) {
    const ext = _editorExt(editorPath);
    if (ext === 'jpg' || ext === 'jpeg') return canvas.toDataURL('image/jpeg', 0.95);
    if (ext === 'webp') return canvas.toDataURL('image/webp', 0.95);
    return canvas.toDataURL('image/png');
}

let _editorTransformInFlight = false;

async function _editorApplyTransform(transformFn) {
    // Re-entrancy guard: rotate/flip/crop all funnel through here, and it's
    // async (awaits an Image load) — a double-click or key-repeat firing the
    // same or a different transform button twice in quick succession would
    // otherwise start a second transform before the first finishes writing
    // back editorPreviewDataUrl, with the second silently clobbering the
    // first and both pushing an undo entry for what was one user action.
    if (!editorPath || _editorTransformInFlight) return;
    _editorTransformInFlight = true;
    try {
        const img = await _editorLoadCurrentImage();
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        transformFn(canvas, ctx, img);

        editorPushUndo();
        editorPreviewDataUrl = _editorEncode(canvas);
        editorHasPendingEdit = true;
        document.getElementById('lightboxImg').src = editorPreviewDataUrl;
        _editorUpdateButtons();
    } catch (err) {
        showToast('Edit failed: ' + err.message, 'error');
    } finally {
        _editorTransformInFlight = false;
    }
}

function editorRotateLeft() {
    _editorApplyTransform((canvas, ctx, img) => {
        canvas.width = img.height;
        canvas.height = img.width;
        ctx.translate(0, canvas.height);
        ctx.rotate(-Math.PI / 2);
        ctx.drawImage(img, 0, 0);
    });
}

function editorRotateRight() {
    _editorApplyTransform((canvas, ctx, img) => {
        canvas.width = img.height;
        canvas.height = img.width;
        ctx.translate(canvas.width, 0);
        ctx.rotate(Math.PI / 2);
        ctx.drawImage(img, 0, 0);
    });
}

function editorFlipH() {
    _editorApplyTransform((canvas, ctx, img) => {
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.translate(canvas.width, 0);
        ctx.scale(-1, 1);
        ctx.drawImage(img, 0, 0);
    });
}

function editorFlipV() {
    _editorApplyTransform((canvas, ctx, img) => {
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.translate(0, canvas.height);
        ctx.scale(1, -1);
        ctx.drawImage(img, 0, 0);
    });
}

// ---------------------------------------------------------------------------
// Crop
// ---------------------------------------------------------------------------

function _editorImageDisplayRect() {
    // #lightboxImg has no wrapping "editor area" — .lightbox centers it
    // directly via flexbox with object-fit:contain, so the image's own
    // bounding rect already IS the letterboxed display rect.
    const img = document.getElementById('lightboxImg');
    const box = document.getElementById('lightbox').getBoundingClientRect();
    const r = img.getBoundingClientRect();
    return { left: r.left - box.left, top: r.top - box.top, width: r.width, height: r.height };
}

function editorEnterCropMode() {
    if (!editorPath || editorIsCropping) return;
    const img = document.getElementById('lightboxImg');
    if (!img.naturalWidth) return; // defensive: image not loaded yet

    const rect = _editorImageDisplayRect();
    editorIsCropping = true;
    editorIsDragging = false;
    editorCropStart = null;
    editorCropEnd = null;
    editorCropScale = rect.width / img.naturalWidth;

    const canvas = document.getElementById('lightboxCropCanvas');
    canvas.style.left = rect.left + 'px';
    canvas.style.top = rect.top + 'px';
    canvas.style.width = rect.width + 'px';
    canvas.style.height = rect.height + 'px';
    canvas.width = Math.round(rect.width);
    canvas.height = Math.round(rect.height);
    canvas.style.display = 'block';

    const editToolbar = document.getElementById('lightboxEditToolbar');
    if (editToolbar) editToolbar.style.display = 'none';
    document.getElementById('lightboxCropToolbar').style.display = 'flex';
    _editorDrawCropOverlay();
}

function editorExitCropMode() {
    if (!editorIsCropping) return;
    editorIsCropping = false;
    editorIsDragging = false;
    editorCropStart = null;
    editorCropEnd = null;

    document.getElementById('lightboxCropCanvas').style.display = 'none';
    document.getElementById('lightboxCropToolbar').style.display = 'none';
    const toolbar = document.getElementById('lightboxEditToolbar');
    if (toolbar) toolbar.style.display = EDITABLE_EXTS.has(_editorExt(editorPath)) ? 'flex' : 'none';
}

function _editorNormalizedCropRect() {
    const x1 = Math.min(editorCropStart.x, editorCropEnd.x);
    const y1 = Math.min(editorCropStart.y, editorCropEnd.y);
    const x2 = Math.max(editorCropStart.x, editorCropEnd.x);
    const y2 = Math.max(editorCropStart.y, editorCropEnd.y);
    return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

function _editorDrawCropOverlay() {
    const canvas = document.getElementById('lightboxCropCanvas');
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!editorCropStart || !editorCropEnd) return;

    const r = _editorNormalizedCropRect();
    if (r.w <= 0 || r.h <= 0) return;

    // Dark tint over the whole overlay, then punch a transparent hole for
    // the selection (clearRect reveals the image beneath — the canvas has
    // no background of its own, so this is a hole, not a black box).
    ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.clearRect(r.x, r.y, r.w, r.h);

    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 2;
    ctx.strokeRect(r.x + 1, r.y + 1, r.w - 2, r.h - 2);

    ctx.strokeStyle = 'rgba(255,255,255,0.5)';
    ctx.lineWidth = 1;
    for (let i = 1; i <= 2; i++) {
        const gx = r.x + r.w * i / 3, gy = r.y + r.h * i / 3;
        ctx.beginPath(); ctx.moveTo(gx, r.y); ctx.lineTo(gx, r.y + r.h); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(r.x, gy); ctx.lineTo(r.x + r.w, gy); ctx.stroke();
    }

    const label = Math.round(r.w / editorCropScale) + ' × ' + Math.round(r.h / editorCropScale);
    ctx.font = '12px sans-serif';
    const boxW = ctx.measureText(label).width + 12, boxH = 20;
    const lx = Math.max(0, Math.min(r.x + r.w - boxW, canvas.width - boxW));
    const ly = Math.min(r.y + r.h + 6, canvas.height - boxH);
    ctx.fillStyle = 'rgba(0,0,0,0.75)';
    ctx.fillRect(lx, ly, boxW, boxH);
    ctx.fillStyle = '#fff';
    ctx.fillText(label, lx + 6, ly + 14);
}

function _editorCropMouseDown(e) {
    if (!editorIsCropping) return;
    e.preventDefault();
    editorIsDragging = true;
    editorCropStart = { x: e.offsetX, y: e.offsetY };
    editorCropEnd = { x: e.offsetX, y: e.offsetY };
    _editorDrawCropOverlay();
}

function _editorCropMouseMove(e) {
    if (!editorIsCropping || !editorIsDragging) return;
    editorCropEnd = { x: e.offsetX, y: e.offsetY };
    _editorDrawCropOverlay();
}

function _editorCropMouseUp() {
    editorIsDragging = false;
}

async function editorApplyCrop() {
    // _editorApplyTransform's own _editorTransformInFlight guard covers
    // double-apply re-entrancy; nothing extra needed here.
    if (!editorIsCropping || !editorCropStart || !editorCropEnd) { editorExitCropMode(); return; }
    const r = _editorNormalizedCropRect();
    if (r.w < 10 || r.h < 10) { showToast('Crop area too small.', 'error'); return; }

    const scale = editorCropScale;
    await _editorApplyTransform((canvas, ctx, img) => {
        let ix = Math.round(r.x / scale), iy = Math.round(r.y / scale);
        let iw = Math.round(r.w / scale), ih = Math.round(r.h / scale);
        ix = Math.max(0, Math.min(ix, img.naturalWidth - 1));
        iy = Math.max(0, Math.min(iy, img.naturalHeight - 1));
        iw = Math.max(1, Math.min(iw, img.naturalWidth - ix));
        ih = Math.max(1, Math.min(ih, img.naturalHeight - iy));
        canvas.width = iw;
        canvas.height = ih;
        ctx.drawImage(img, ix, iy, iw, ih, 0, 0, iw, ih);
    });
    editorExitCropMode();
}

(function _editorInitCrop() {
    const canvas = document.getElementById('lightboxCropCanvas');
    if (!canvas) return;
    canvas.addEventListener('mousedown', _editorCropMouseDown);
    canvas.addEventListener('mousemove', _editorCropMouseMove);
    canvas.addEventListener('mouseup', _editorCropMouseUp);
    canvas.addEventListener('mouseleave', _editorCropMouseUp);
    window.addEventListener('resize', () => { if (editorIsCropping) editorExitCropMode(); });
})();

// ---------------------------------------------------------------------------
// Keyboard (called from app.js's lightbox-scoped keydown handler)
// ---------------------------------------------------------------------------

function editorHandleLightboxKeydown(e) {
    if (editorIsCropping) {
        if (e.key === 'Escape') { e.preventDefault(); editorExitCropMode(); return true; }
        if (e.key === 'Enter') { e.preventDefault(); editorApplyCrop(); return true; }
        return true; // swallow everything else (incl. arrows) while cropping
    }
    const key = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && key === 'z') { e.preventDefault(); editorUndo(); return true; }
    if ((e.ctrlKey || e.metaKey) && (key === 'y' || (e.shiftKey && key === 'z'))) { e.preventDefault(); editorRedo(); return true; }
    return false;
}

// ---------------------------------------------------------------------------
// Metadata info bar (filename / dimensions / file size)
// ---------------------------------------------------------------------------

function _editorFindImageMeta(path) {
    if (typeof groups === 'undefined') return null;
    for (const g of groups) {
        const found = g.images.find(i => i.path === path);
        if (found) return found;
    }
    return null;
}

function _editorRenderInfo() {
    const bar = document.getElementById('lightboxInfo');
    if (!bar) return;
    const img = document.getElementById('lightboxImg');
    const video = document.getElementById('lightboxVideo');
    const showingVideo = video && !video.hidden;

    let dims = '';
    let duration = '';
    if (showingVideo) {
        dims = video.videoWidth ? `${video.videoWidth} × ${video.videoHeight}` : '';
        const meta = _editorFindImageMeta(editorPath);
        duration = (meta && meta.duration_formatted) || '';
    } else {
        dims = (img && img.naturalWidth) ? `${img.naturalWidth} × ${img.naturalHeight}` : '';
    }

    bar.textContent = [_editorInfoFilename, dims, duration, _editorInfoSize].filter(Boolean).join('  ·  ');
}

(function _editorInitInfo() {
    const img = document.getElementById('lightboxImg');
    if (img) img.addEventListener('load', _editorRenderInfo);
    const video = document.getElementById('lightboxVideo');
    if (video) video.addEventListener('loadedmetadata', _editorRenderInfo);
})();

function editorDiscard() {
    if (!editorHasPendingEdit) return;
    editorPreviewDataUrl = null;
    editorHasPendingEdit = false;
    editorUndoStack = [];
    editorRedoStack = [];
    document.getElementById('lightboxImg').src = editorCleanUrl;
    _editorUpdateButtons();
}

let editorSaveInFlight = false;

function editorSave() {
    if (!editorHasPendingEdit || !editorPath || editorSaveInFlight) return;

    // Capture path/data now — the user is free to navigate the lightbox
    // while this request is in flight (there's no modal to lock here), so
    // by the time the response arrives editorPath/editorPreviewDataUrl may
    // already refer to a different image. Every use below is guarded by
    // comparing against this captured path, not the live globals.
    const path = editorPath;
    const dataUrl = editorPreviewDataUrl;

    editorSaveInFlight = true;
    const saveBtn = document.getElementById('lightboxSaveBtn');
    if (saveBtn) saveBtn.disabled = true;

    fetch('/api/image/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, dataUrl }),
    })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        editorSaveInFlight = false;
        if (editorPath !== path) return; // user navigated away; this response no longer applies to what's on screen

        if (!ok || data.error) {
            showToast('Error: ' + (data.error || 'Save failed'), 'error');
            _editorUpdateButtons(); // leave pending edit intact so the user can retry
            return;
        }
        showToast('Image saved.', 'success');
        editorCleanUrl = '/api/image?path=' + encodeURIComponent(path) + '&t=' + data.mtime;
        document.getElementById('lightboxImg').src = editorCleanUrl;
        editorPreviewDataUrl = null;
        editorHasPendingEdit = false;
        editorUndoStack = [];
        editorRedoStack = [];
        _editorUpdateButtons();
        _editorApplySavedMeta(path, data);
    })
    .catch(() => {
        editorSaveInFlight = false;
        if (editorPath !== path) return;
        showToast('Request failed.', 'error');
        _editorUpdateButtons();
    });
}

function _editorApplySavedMeta(path, data) {
    // Keep the thumbnail grid / duplicate-group view in sync with an edit —
    // otherwise they keep showing pre-edit size/dimensions until a full rescan.
    const meta = _editorFindImageMeta(path);
    if (meta && typeof data.size === 'number' && typeof data.width === 'number' && typeof data.height === 'number') {
        meta.file_size = data.size;
        meta.file_size_formatted = (typeof formatSize === 'function') ? formatSize(data.size) : meta.file_size_formatted;
        meta.width = data.width;
        meta.height = data.height;
        meta.pixel_count = data.width * data.height;
        meta.resolution = `${data.width}x${data.height}`;
        meta.megapixels = Math.round((meta.pixel_count / 1e6) * 100) / 100;
        _editorInfoSize = meta.file_size_formatted;
    }
    _editorRenderInfo();
    if (typeof applyFilters === 'function') applyFilters();
}
