// =============================================================
// PixSieve — Frontend Application
// =============================================================

// =============================================================
// Global Error Handlers
// =============================================================
window.addEventListener('error', e => {
    console.error('[UNCAUGHT]', e.message, e.filename + ':' + e.lineno + ':' + e.colno, e.error);
});
window.addEventListener('unhandledrejection', e => {
    console.error('[UNHANDLED PROMISE]', e.reason);
});

// =============================================================
// Logging
// =============================================================
const LOG_LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
let _logLevel = LOG_LEVELS.debug;

const log = {
    _fmt(level, area, msg, data) {
        const ts = new Date().toISOString().slice(11, 23);
        const prefix = `[${ts}] [${level.toUpperCase()}] [${area}]`;
        if (data !== undefined) {
            console[level === 'error' ? 'error' : level === 'warn' ? 'warn' : 'log'](prefix, msg, data);
        } else {
            console[level === 'error' ? 'error' : level === 'warn' ? 'warn' : 'log'](prefix, msg);
        }
    },
    debug(area, msg, data) { if (_logLevel <= LOG_LEVELS.debug) this._fmt('debug', area, msg, data); },
    info(area, msg, data)  { if (_logLevel <= LOG_LEVELS.info)  this._fmt('info',  area, msg, data); },
    warn(area, msg, data)  { if (_logLevel <= LOG_LEVELS.warn)  this._fmt('warn',  area, msg, data); },
    error(area, msg, data) { if (_logLevel <= LOG_LEVELS.error) this._fmt('error', area, msg, data); },
};

// =============================================================
// Web Worker — Filter/Sort
// =============================================================
let _filterWorker = null;
let _filterWorkerBusy = false;
let _filterGeneration = 0;

function _initFilterWorker() {
    if (!window.Worker) return;
    try {
        _filterWorker = new Worker('/static/js/filter-worker.js');
        _filterWorker.onmessage = function (e) {
            _filterWorkerBusy = false;
            if (e.data.error) {
                log.error('worker', 'Filter worker error: ' + e.data.error);
                return;
            }
            // A newer applyFilters() call (worker or sync fallback) may have
            // already run and rendered while this response was in flight —
            // applying a stale result now would clobber those newer results.
            if (e.data.generation !== _filterGeneration) return;
            filteredGroups = e.data.filteredGroups;
            currentPage = 1;
            renderGroups();
            updatePagination();
        };
        _filterWorker.onerror = function () {
            _filterWorker = null; // fall back to synchronous path
            _filterWorkerBusy = false;
        };
    } catch (_) {
        _filterWorker = null;
    }
}

// =============================================================
// Cleanup on page unload — close SSE streams, terminate worker
// =============================================================
window.addEventListener('beforeunload', () => {
    if (state.scanStream) { state.scanStream.close(); state.scanStream = null; }
    if (typeof opsStream !== 'undefined' && opsStream) { opsStream.close(); opsStream = null; }
    if (_filterWorker) { _filterWorker.terminate(); _filterWorker = null; }
});

// =============================================================
// State
// =============================================================
const state = {
    scanStream: null,
    groups: [],
    filteredGroups: [],
    selections: {},
    undoStack: [],
    directoryHistory: [],
    errorImages: [],
    currentPage: 1,
    pageSize: 25,
    lightboxImages: [],
    lightboxIndex: 0,
    connectionLost: false,
    scanFolders: [],
    isScanning: false,
    isPaused: false,
    stageStartTimes: {},
    currentView: 'grid',
    showAllGroups: false,
};
Object.defineProperties(window, {
    groups:          { get: () => state.groups,          set: v => { state.groups = v; },          configurable: true },
    filteredGroups:  { get: () => state.filteredGroups,  set: v => { state.filteredGroups = v; },  configurable: true },
    selections:      { get: () => state.selections,      set: v => { state.selections = v; },      configurable: true },
    undoStack:       { get: () => state.undoStack,       set: v => { state.undoStack = v; },       configurable: true },
    directoryHistory:{ get: () => state.directoryHistory,set: v => { state.directoryHistory = v; },configurable: true },
    errorImages:     { get: () => state.errorImages,     set: v => { state.errorImages = v; },     configurable: true },
    currentPage:     { get: () => state.currentPage,     set: v => { state.currentPage = v; },     configurable: true },
    lightboxImages:  { get: () => state.lightboxImages,  set: v => { state.lightboxImages = v; },  configurable: true },
    lightboxIndex:   { get: () => state.lightboxIndex,   set: v => { state.lightboxIndex = v; },   configurable: true },
    connectionLost:  { get: () => state.connectionLost,  set: v => { state.connectionLost = v; },  configurable: true },
    scanFolders:     { get: () => state.scanFolders,     set: v => { state.scanFolders = v; },     configurable: true },
    isScanning:      { get: () => state.isScanning,      set: v => { state.isScanning = v; },      configurable: true },
    isPaused:        { get: () => state.isPaused,        set: v => { state.isPaused = v; },        configurable: true },
    stageStartTimes: { get: () => state.stageStartTimes, set: v => { state.stageStartTimes = v; }, configurable: true },
    currentView:     { get: () => state.currentView,     set: v => { state.currentView = v; },     configurable: true },
    showAllGroups:   { get: () => state.showAllGroups,   set: v => { state.showAllGroups = v; },   configurable: true },
});

// =============================================================
// Theme
// =============================================================
function initTheme() {
    const saved = localStorage.getItem('pixsieve-theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = saved || (prefersDark ? 'dark' : 'light');
    applyTheme(theme);
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('pixsieve-theme', theme);
    const btn = document.querySelector('.theme-toggle');
    if (btn) btn.textContent = theme === 'dark' ? '\u{1F319}' : '\u{2600}\u{FE0F}';
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(current === 'dark' ? 'light' : 'dark');
}

// =============================================================
// Focus Trap (Accessibility)
// =============================================================
let _focusTrapCleanup = null;

function trapFocus(modal) {
    const focusable = modal.querySelectorAll('button, input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    first.focus();

    function handler(e) {
        if (e.key !== 'Tab') return;
        if (e.shiftKey) {
            if (document.activeElement === first) { e.preventDefault(); last.focus(); }
        } else {
            if (document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    }
    modal.addEventListener('keydown', handler);
    _focusTrapCleanup = () => modal.removeEventListener('keydown', handler);
}

function releaseFocusTrap() {
    if (_focusTrapCleanup) { _focusTrapCleanup(); _focusTrapCleanup = null; }
}

/**
 * Wire click-to-dismiss on each modal/overlay's own backdrop element. Only
 * fires when the click target IS the overlay element itself (the dark
 * padding area), never when it's a descendant (the modal card, a button,
 * the lightbox image) -- event bubbling doesn't affect e.target, so this
 * can't misfire from clicks inside the modal content. Previously none of
 * these had any backdrop-dismiss behavior at all; Cancel/Escape were the
 * only way out, unlike the near-universal "click outside to close" pattern.
 * Reuses each modal's own hide function, so the existing in-flight guards
 * (_deleteInFlight / _renameInFlight) are respected automatically.
 */
function _wireModalBackdropClose() {
    const overlays = [
        ['deleteModal', () => hideDeleteModal()],
        ['renameModal', () => hideRenameModal()],
        ['cacheModal', () => hideCacheModal()],
        ['autoFillModal', () => _hideAutoFillModal()],
        ['compareSliderOverlay', () => closeCompareSlider()],
        ['lightbox', () => closeLightbox()],
    ];
    overlays.forEach(([id, hide]) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('click', e => {
            if (e.target === el) hide();
        });
    });
}

/**
 * Pure decision function for what Escape should dismiss, given which
 * modals are currently active. Kept side-effect-free (no DOM access) and
 * separate from the keydown handler so it's directly unit-testable without
 * needing to load the whole app.js DOM-wiring around it. Priority order
 * matches the modal stacking order in the markup: cache modal first (it's
 * never opened on top of the lightbox), then delete/rename (which stack on
 * top of the lightbox), then the auto-fill modal, then finally the
 * lightbox/compare-slider underneath everything else.
 */
function _resolveEscapeAction(states) {
    if (states.cacheModalActive) return 'cache';
    if (states.deleteModalActive || states.renameModalActive) return 'delete-rename';
    if (states.autoFillModalActive) return 'autofill';
    return 'lightbox';
}

// =============================================================
// Scan Folders List (multi-folder + reference folder support)
// =============================================================
function _initScanFolderList() {
    const list = document.getElementById('scanFolderList');
    if (!list) return;
    list.innerHTML = '';
    _addScanFolderRow();
}

function _addScanFolderRow(path) {
    const list = document.getElementById('scanFolderList');
    if (!list) return;

    const row = document.createElement('div');
    row.className = 'per-folder-row scan-folder-row';
    row.innerHTML = `
        <div class="pf-path-wrapper drop-zone">
            <input type="text" class="pf-path" placeholder="Enter full path or drag a folder here"
                   autocomplete="off" value="${escapeHtml(path || '')}"
                   role="combobox" aria-autocomplete="list" aria-expanded="false">
            <div class="autocomplete-list"></div>
        </div>
        <label class="pf-reference-toggle" title="Mark as reference folder — images here can never be deleted">
            <input type="radio" name="referenceFolder" class="pf-reference-radio">
            <span>Reference</span>
        </label>
        <button type="button" class="btn-icon-remove" data-action="remove-scan-folder" title="Remove folder" aria-label="Remove folder">✕</button>
    `;
    list.appendChild(row);

    const pathInput = row.querySelector('.pf-path');
    pathInput.addEventListener('input', () => filterHistory(pathInput));
    pathInput.addEventListener('focus', () => showHistory(pathInput));

    const refRadio = row.querySelector('.pf-reference-radio');
    refRadio.addEventListener('change', () => {
        document.querySelectorAll('.scan-folder-row .pf-reference-toggle').forEach(label => {
            label.classList.remove('reference-active');
        });
        if (refRadio.checked) {
            row.querySelector('.pf-reference-toggle').classList.add('reference-active');
        }
    });

    _wireFolderDropZone(row);
}

function _removeScanFolderRow(btn) {
    const list = document.getElementById('scanFolderList');
    const row = btn.closest('.scan-folder-row');
    if (!list || !row) return;

    if (list.querySelectorAll('.scan-folder-row').length <= 1) {
        showToast('At least one folder is required.', 'info');
        return;
    }
    row.remove();
}

function _wireFolderDropZone(rowEl) {
    const zone = rowEl.querySelector('.pf-path-wrapper');
    const input = rowEl.querySelector('.pf-path');
    if (!zone || !input) return;

    let dragCounter = 0;

    zone.addEventListener('dragenter', e => {
        e.preventDefault();
        dragCounter++;
        zone.classList.add('drag-over');
    });

    zone.addEventListener('dragleave', () => {
        dragCounter--;
        if (dragCounter <= 0) {
            dragCounter = 0;
            zone.classList.remove('drag-over');
        }
    });

    zone.addEventListener('dragover', e => e.preventDefault());

    zone.addEventListener('drop', e => {
        e.preventDefault();
        dragCounter = 0;
        zone.classList.remove('drag-over');

        // Try to get folder path from dropped items
        const items = e.dataTransfer.items;
        if (items && items.length > 0) {
            const entry = items[0].webkitGetAsEntry && items[0].webkitGetAsEntry();
            if (entry && entry.isDirectory) {
                // fullPath is available on directory entries (prefixed with /)
                input.value = entry.fullPath.replace(/^\//, '');
                showToast('Folder dropped — note: browsers may only provide the folder name, not the full system path.', 'info');
                return;
            }
            // Fallback: use the file path if available
            const file = e.dataTransfer.files[0];
            if (file && file.path) {
                // Electron/NW.js environments expose file.path
                const dirPath = file.path.replace(/[\\/][^\\/]+$/, '');
                input.value = dirPath;
                return;
            }
        }
        showToast('Could not read folder path. Please type the path manually.', 'info');
    });
}

function _collectScanFolders() {
    const rows = document.querySelectorAll('#scanFolderList .scan-folder-row');
    const folders = [];
    rows.forEach(row => {
        const path = row.querySelector('.pf-path').value.trim();
        if (!path) return;
        const isReference = row.querySelector('.pf-reference-radio').checked;
        folders.push({ path, isReference });
    });
    return folders;
}

function _validateScanFolders(folders) {
    if (folders.length === 0) {
        return { valid: false, error: 'Please enter at least one folder to scan.' };
    }

    const seen = new Set();
    for (const f of folders) {
        const normalized = f.path.replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '');
        if (seen.has(normalized)) {
            return { valid: false, error: `Duplicate folder path: ${f.path}. Each folder can only be added once.` };
        }
        seen.add(normalized);
    }

    if (folders.filter(f => f.isReference).length > 1) {
        return { valid: false, error: 'Only one folder can be marked as the reference folder.' };
    }

    return { valid: true, error: '' };
}

function _formatScanFoldersSummary(scanFolders) {
    if (!scanFolders || scanFolders.length === 0) return '';
    if (scanFolders.length === 1) return scanFolders[0].path;
    const refCount = scanFolders.filter(f => f.isReference).length;
    return `${scanFolders.length} folders${refCount > 0 ? ' (1 reference)' : ''}`;
}

// =============================================================
// Initialization
// =============================================================
document.addEventListener('DOMContentLoaded', () => {
    log.info('init', 'DOMContentLoaded fired');
    try {
        initTheme();
        _initScanFolderList();
        _initFilterWorker();
        _wireModalBackdropClose();
        checkForRecovery();
        loadDirectoryHistory();
        startConnectionMonitor();
        updateThresholdDisplay();
        updateWorkersDisplay();
        log.info('init', 'Initialization complete');
    } catch (err) {
        log.error('init', 'Initialization failed', err);
    }
});

function checkForRecovery() {
    loadCacheStats();

    fetch('/api/status')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'complete' && data.has_results) {
                const dirSummary = Array.isArray(data.directories) && data.directories.length
                    ? _formatScanFoldersSummary(data.directories.map(d => ({ path: d.path, isReference: d.is_reference })))
                    : data.directory;
                let info = `Found ${data.group_count} duplicate groups from: ${dirSummary}`;
                if (data.error_count > 0) {
                    info += ` (${data.error_count} files had errors)`;
                }
                document.getElementById('recoveryBanner').classList.add('active');
                document.getElementById('recoveryInfo').textContent = info;

                if (data.auto_disabled_perceptual) {
                    showPerceptualWarning(dirSummary);
                }
            } else if (['scanning', 'analyzing', 'comparing'].includes(data.status)) {
                isScanning = true;
                showProgressSection();
                startProgressPolling();
            }
        })
        .catch(err => log.warn('recovery', 'Failed to check status', err));
}

function showPerceptualWarning(directory) {
    document.getElementById('warningBanner').classList.add('active');
}

function hidePerceptualWarning() {
    document.getElementById('warningBanner').classList.remove('active');
}

function restoreSession() {
    document.getElementById('recoveryBanner').classList.remove('active');
    loadResults();
}

function dismissRecovery() {
    document.getElementById('recoveryBanner').classList.remove('active');
    hidePerceptualWarning();
    fetch('/api/clear', { method: 'POST' });
}

function loadDirectoryHistory() {
    fetch('/api/history')
        .then(r => r.json())
        .then(data => {
            directoryHistory = data.directories || [];
        })
        .catch(err => log.warn('history', 'Failed to load history', err));
}

// =============================================================
// UI Helpers
// =============================================================
function updateThresholdDisplay() {
    const val = document.getElementById('threshold').value;
    document.getElementById('thresholdValue').textContent = val;
}

function updateWorkersDisplay() {
    const val = document.getElementById('workers').value;
    document.getElementById('workersValue').textContent = val;
}

function toggleAdvancedOptions() {
    const toggle = document.querySelector('.advanced-toggle');
    const content = document.getElementById('advancedContent');
    toggle.classList.toggle('expanded');
    content.classList.toggle('active');
    toggle.setAttribute('aria-expanded', toggle.classList.contains('expanded') ? 'true' : 'false');
}

function toggleDetectionMode(mode) {
    if (mode === 'exact' && document.getElementById('exactOnly').checked) {
        document.getElementById('perceptualOnly').checked = false;
    } else if (mode === 'perceptual' && document.getElementById('perceptualOnly').checked) {
        document.getElementById('exactOnly').checked = false;
    }
}

// =============================================================
// Connection Monitoring
// Loss detected via SSE close/fetch rejection - no ping poll needed.
// =============================================================
function startConnectionMonitor() {
    // no-op: connection loss is signalled by SSE stream close or fetch errors
}

function _handleFetchError() {
    log.warn('net', 'Fetch error — marking connection lost');
    state.connectionLost = true;
    updateConnectionStatus(false);
}

function _handleFetchRecovery() {
    if (state.connectionLost) {
        state.connectionLost = false;
        updateConnectionStatus(true);
    }
}

function updateConnectionStatus(connected) {
    const el = document.getElementById('connectionStatus');
    const text = document.getElementById('connectionText');
    if (connected) {
        el.className = 'connection-status connected';
        text.textContent = 'Connected';
    } else {
        el.className = 'connection-status disconnected';
        text.textContent = 'Disconnected - Retrying...';
    }
}

// =============================================================
// Directory History/Autocomplete
// =============================================================
function showHistory(inputEl) {
    renderHistory(directoryHistory, inputEl);
}

function filterHistory(inputEl) {
    const value = inputEl.value.toLowerCase();
    const filtered = directoryHistory.filter(d => d.toLowerCase().includes(value));
    renderHistory(filtered, inputEl);
}

function renderHistory(dirs, inputEl) {
    const wrapper = inputEl.closest('.pf-path-wrapper');
    const list = wrapper && wrapper.querySelector('.autocomplete-list');
    if (!list) return;
    if (dirs.length === 0) {
        list.classList.remove('active');
        return;
    }
    list.innerHTML = dirs.map(d =>
        `<div class="autocomplete-item" data-dir="${escapeHtml(d)}">${escapeHtml(d)}</div>`
    ).join('');
    list.querySelectorAll('.autocomplete-item').forEach(item => {
        item.addEventListener('click', () => selectDirectory(item.dataset.dir, inputEl));
    });
    list.classList.add('active');
}

function selectDirectory(dir, inputEl) {
    inputEl.value = dir;
    const wrapper = inputEl.closest('.pf-path-wrapper');
    const list = wrapper && wrapper.querySelector('.autocomplete-list');
    if (list) list.classList.remove('active');
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('.scan-folder-row')) {
        document.querySelectorAll('.autocomplete-list.active').forEach(list => list.classList.remove('active'));
    }
});

// =============================================================
// Scanning
// =============================================================
function startScan() {
    log.info('scan', 'startScan() called');
    const scanFolders = _collectScanFolders();
    const validation = _validateScanFolders(scanFolders);
    if (!validation.valid) {
        showToast(validation.error, 'error');
        return;
    }

    const threshold = parseInt(document.getElementById('threshold').value) || 10;
    const exactOnly = document.getElementById('exactOnly').checked;
    const perceptualOnly = document.getElementById('perceptualOnly').checked;
    const recursive = document.getElementById('recursive').checked;
    const useCache = document.getElementById('useCache').checked;
    const workers = parseInt(document.getElementById('workers').value) || 4;
    const resolveSymlinks = document.getElementById('resolveSymlinks').checked;
    const autoSelectStrategy = document.getElementById('autoSelectStrategy').value;
    const includeVideos = document.getElementById('includeVideos').checked;

    let useLsh = null;
    const lshMode = document.querySelector('input[name="lshMode"]:checked').value;
    if (lshMode === 'on') useLsh = true;
    else if (lshMode === 'off') useLsh = false;

    state.scanFolders = scanFolders;
    hidePerceptualWarning();
    setScanBtnLoading(true);

    fetch('/api/scan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            directories: scanFolders, threshold, exactOnly, perceptualOnly,
            recursive, useCache, useLsh, workers, resolveSymlinks, autoSelectStrategy,
            includeVideos,
        })
    })
    .then(r => { if (!r.ok) throw new Error(r.statusText || 'Request failed'); return r.json(); })
    .then(data => {
        if (data.error) {
            showToast('Error: ' + data.error, 'error');
            setScanBtnLoading(false);
            return;
        }
        state.isScanning = true;
        state.isPaused = false;
        state.stageStartTimes = {};
        showProgressSection();
        startProgressPolling();
    })
    .catch(err => {
        showToast('Failed to start scan: ' + err, 'error');
        setScanBtnLoading(false);
    });
}

function showProgressSection() {
    document.getElementById('scanBtn').disabled = true;
    document.getElementById('progressSection').classList.add('active');
    document.getElementById('groupsContainer').classList.remove('active');
    document.getElementById('statsBar').classList.remove('active');
    document.getElementById('actionBar').classList.remove('active');
    document.getElementById('filterBar').classList.remove('active');
    document.getElementById('errorSection').classList.remove('active');

    // Reset top progress bar
    const topFill = document.getElementById('progressTopFill');
    if (topFill) topFill.style.width = '0%';

    // Reset stage nodes
    document.querySelectorAll('.stage-node').forEach(el => {
        el.classList.remove('active', 'completed', 'error');
        const elapsed = el.querySelector('.stage-node-elapsed');
        if (elapsed) elapsed.textContent = '';
    });

    document.getElementById('cancelBtn').style.display = 'inline-block';
    document.getElementById('pauseBtn').style.display = 'inline-block';
    document.getElementById('resumeBtn').style.display = 'none';
}

function hideProgressSection() {
    setScanBtnLoading(false);
    document.getElementById('progressSection').classList.remove('active');
}

function setScanBtnLoading(loading) {
    const btn = document.getElementById('scanBtn');
    btn.disabled = loading;
    btn.textContent = loading ? 'Scanning...' : 'Start Scan';
}

// Non-blocking toast - replaces bare alert() calls
function showToast(message, type) {
    log.info('toast', `[${type || 'default'}] ${message}`);
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container';
        // Toasts are the app's primary async feedback channel for nearly
        // every action; without a live region, screen reader users get no
        // announcement at all when one appears.
        container.setAttribute('role', 'status');
        container.setAttribute('aria-live', 'polite');
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = 'toast' + (type ? ' toast-' + type : '');
    toast.textContent = message;
    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

function startProgressPolling() {
    log.info('sse', 'Opening EventSource /api/scan/stream');
    _stopScanStream();
    state.scanStream = new EventSource('/api/scan/stream');

    state.scanStream.onmessage = (e) => {
        let data;
        try { data = JSON.parse(e.data); } catch { log.warn('sse', 'Failed to parse SSE data', e.data); return; }

        _handleFetchRecovery();
        updateProgressUI(data);

        state.isPaused = data.paused;
        if (state.isPaused) {
            document.getElementById('pauseBtn').style.display = 'none';
            document.getElementById('resumeBtn').style.display = 'inline-block';
        } else {
            document.getElementById('pauseBtn').style.display = 'inline-block';
            document.getElementById('resumeBtn').style.display = 'none';
        }

        if (data.status === 'complete' || data.status === 'error' || data.status === 'cancelled') {
            _stopScanStream();
            state.isScanning = false;

            if (data.status === 'complete') {
                if (data.auto_disabled_perceptual) {
                    showPerceptualWarning(_formatScanFoldersSummary(state.scanFolders));
                }
                loadResults();
            } else if (data.status === 'cancelled') {
                hideProgressSection();
                showToast('Scan cancelled.');
            } else {
                hideProgressSection();
                showToast('Scan error: ' + data.message, 'error');
            }
        }
    };

    state.scanStream.onerror = (err) => {
        log.error('sse', 'EventSource error, closing stream', err);
        _stopScanStream();
        state.isScanning = false;
        _handleFetchError();
    };
}

function _stopScanStream() {
    if (state.scanStream) {
        state.scanStream.close();
        state.scanStream = null;
    }
}

// checkProgress kept for compatibility but no longer called
function checkProgress() {}

function updateProgressUI(data) {
    // Thin top progress bar
    const pct = data.progress || 0;
    const topFill = document.getElementById('progressTopFill');
    if (topFill) topFill.style.width = pct + '%';

    document.getElementById('progressPercentage').textContent = pct + '%';
    document.getElementById('progressText').textContent = data.message;

    const stageNames = {
        'idle': 'Starting...',
        'scanning': 'Scanning Files',
        'analyzing': 'Analyzing Images',
        'exact_matching': 'Finding Exact Duplicates',
        'perceptual_matching': 'Finding Similar Images',
        'complete': 'Complete'
    };
    document.getElementById('progressTitle').textContent = stageNames[data.stage] || data.stage;

    updateStageNodes(data.stage);

    const details = data.progress_details || {};
    document.getElementById('liveTotalFiles').textContent = formatNumber(data.total_files || 0);
    document.getElementById('liveAnalyzed').textContent = formatNumber(data.analyzed || 0);
    document.getElementById('liveRate').textContent = (details.rate || 0).toFixed(1) + '/s';
    document.getElementById('liveEta').textContent = formatEta(details.eta_seconds);
    document.getElementById('liveCacheHits').textContent = formatNumber(details.cache_hits || 0);
    document.getElementById('liveElapsed').textContent = formatElapsed(details.elapsed_seconds || 0);
}

function updateStageNodes(stage) {
    const stages = ['scanning', 'analyzing', 'exact_matching', 'perceptual_matching', 'complete'];
    const nodeIds = {
        'scanning':           'stageScanning',
        'analyzing':          'stageAnalyzing',
        'exact_matching':     'stageExact',
        'perceptual_matching':'stagePerceptual',
        'complete':           'stageComplete',
    };
    const currentIndex = stages.indexOf(stage);
    const now = Date.now();

    for (let i = 0; i < stages.length; i++) {
        const s = stages[i];
        const el = document.getElementById(nodeIds[s]);
        if (!el) continue;

        el.classList.remove('active', 'completed', 'error');

        if (i < currentIndex) {
            el.classList.add('completed');
            // Show elapsed time for completed stages
            const elapsedEl = el.querySelector('.stage-node-elapsed');
            if (elapsedEl && stageStartTimes[s]) {
                const endTime = stageStartTimes[stages[i + 1]] || now;
                elapsedEl.textContent = formatElapsed((endTime - stageStartTimes[s]) / 1000);
            }
        } else if (i === currentIndex) {
            el.classList.add('active');
            if (!stageStartTimes[s]) {
                stageStartTimes[s] = now;
            }
            // Show running time for active stage
            const elapsedEl = el.querySelector('.stage-node-elapsed');
            if (elapsedEl && stageStartTimes[s]) {
                elapsedEl.textContent = formatElapsed((now - stageStartTimes[s]) / 1000);
            }
        }
    }
}

function cancelScan() {
    if (confirm('Are you sure you want to cancel the scan?')) {
        fetch('/api/cancel', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'cancel_requested') {
                    document.getElementById('progressText').textContent = 'Cancelling...';
                }
            });
    }
}

function pauseScan() {
    fetch('/api/pause', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'paused') {
                document.getElementById('pauseBtn').style.display = 'none';
                document.getElementById('resumeBtn').style.display = 'inline-block';
            }
        });
}

function resumeScan() {
    fetch('/api/resume', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'resumed') {
                document.getElementById('pauseBtn').style.display = 'inline-block';
                document.getElementById('resumeBtn').style.display = 'none';
            }
        });
}

function loadResults() {
    log.info('results', 'loadResults() — fetching /api/groups');
    fetch('/api/groups')
        .then(r => r.json())
        .then(data => {
            groups = data.groups;
            selections = data.selections || {};
            errorImages = data.error_images || [];

            if (Object.keys(selections).length === 0) {
                groups.forEach(g => _applyDefaultGroupSelections(g, selections));
            }

            const scannedPaths = Array.isArray(data.directories)
                ? data.directories.map(d => d.path)
                : state.scanFolders.map(f => f.path);
            scannedPaths.forEach(p => {
                if (p && !directoryHistory.includes(p)) directoryHistory.unshift(p);
            });
            directoryHistory = directoryHistory.slice(0, 10);

            if (data.settings && data.settings.auto_select_strategy) {
                document.getElementById('strategySelect').value = data.settings.auto_select_strategy;
            }

            currentPage = 1;
            applyFilters();
            updateStats();
            renderErrorImages();

            hideProgressSection();
            document.getElementById('groupsContainer').classList.add('active');
            document.getElementById('statsBar').classList.add('active');
            document.getElementById('filterBar').classList.add('active');

            if (groups.length > 0) {
                document.getElementById('actionBar').classList.add('active');
            }

            loadCacheStats();
        })
        .catch(err => {
            log.error('results', 'Failed to load groups', err);
            showToast('Failed to load results.', 'error');
        });
}

function newScan() {
    if (confirm('Start a new scan? Current results will be cleared.')) {
        fetch('/api/clear', { method: 'POST' }).then(() => {
            groups = [];
            selections = {};
            errorImages = [];
            showAllGroups = false;
            hidePerceptualWarning();
            document.getElementById('groupsContainer').classList.remove('active');
            document.getElementById('statsBar').classList.remove('active');
            document.getElementById('actionBar').classList.remove('active');
            document.getElementById('filterBar').classList.remove('active');
            document.getElementById('cacheBanner').classList.remove('active');
            document.getElementById('errorSection').classList.remove('active');
            const pb = document.getElementById('paginationBottom');
            if (pb) pb.classList.remove('active');
        });
    }
}

// =============================================================
// Error Images Display
// =============================================================
function renderErrorImages() {
    const section = document.getElementById('errorSection');
    const grid = document.getElementById('errorImagesGrid');
    const countEl = document.getElementById('errorSectionCount');

    if (errorImages.length === 0) {
        section.classList.remove('active');
        return;
    }

    section.classList.add('active');
    countEl.textContent = errorImages.length;

    grid.innerHTML = errorImages.map(img => `
        <div class="error-image-item">
            <div class="error-item-header">
                <div class="error-image-path">${escapeHtml(img.path)}</div>
                <button class="error-copy-btn" title="Copy path to clipboard"
                    data-action="copy-error-path" data-path="${escapeHtml(img.path)}">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                    </svg>
                </button>
            </div>
            <div class="error-image-error">${escapeHtml(img.error || 'Unknown error')}</div>
        </div>
    `).join('');
}

function copyErrorPath(btn, path) {
    navigator.clipboard.writeText(path).then(() => {
        btn.classList.add('copied');
        setTimeout(() => btn.classList.remove('copied'), 1500);
    });
}

function toggleErrorSection() {
    const section = document.getElementById('errorSection');
    section.classList.toggle('expanded');
    const header = section.querySelector('.error-section-header');
    if (header) header.setAttribute('aria-expanded', section.classList.contains('expanded') ? 'true' : 'false');
}

// =============================================================
// Filtering & Pagination
function clearFilters() {
    document.getElementById('filterType').value = 'all';
    document.getElementById('searchFilter').value = '';
    applyFilters();
}

// =============================================================
function applyFilters() {
    const typeFilter = document.getElementById('filterType').value;
    const sortBy = document.getElementById('sortBy').value;
    const search = document.getElementById('searchFilter').value.toLowerCase();

    const generation = ++_filterGeneration;

    // Use Web Worker when available and not already processing
    if (_filterWorker && !_filterWorkerBusy) {
        _filterWorkerBusy = true;
        _filterWorker.postMessage({ groups, typeFilter, sortBy, search, generation });
        return;
    }

    // Synchronous fallback (no Worker support or worker is busy)
    filteredGroups = groups.filter(g => {
        if (typeFilter !== 'all' && g.match_type !== typeFilter) return false;
        if (search) {
            const hasMatch = g.images.some(img =>
                img.filename.toLowerCase().includes(search) ||
                img.directory.toLowerCase().includes(search)
            );
            if (!hasMatch) return false;
        }
        return true;
    });

    filteredGroups.sort((a, b) => {
        if (sortBy === 'savings') return b.potential_savings - a.potential_savings;
        if (sortBy === 'count') return b.image_count - a.image_count;
        return a.id - b.id;
    });

    currentPage = 1;
    renderGroups();
    updatePagination();
}

let _applyStrategyInFlight = false;

function applyStrategy() {
    if (_applyStrategyInFlight) return;
    const select = document.getElementById('strategySelect');
    const strategy = select.value;

    _applyStrategyInFlight = true;
    select.disabled = true;

    fetch('/api/apply_strategy', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ strategy })
    })
    .then(r => r.json())
    .then(data => {
        if (data.selections) {
            selections = data.selections;
            renderGroups();
            updateStats();
            showUndoToast('Selection strategy applied: ' + strategy);
        }
    })
    .catch(err => {
        log.error('strategy', 'Failed to apply strategy', err);
        showToast('Failed to apply strategy.', 'error');
    })
    .finally(() => {
        _applyStrategyInFlight = false;
        select.disabled = false;
    });
}

function updatePagination() {
    const paginationBottom = document.getElementById('paginationBottom');
    const showAllBtnTop = document.getElementById('showAllBtnTop');
    const showAllBtn = document.getElementById('showAllBtn');

    if (showAllGroups) {
        const suffix = _virtualRendered < filteredGroups.length
            ? ` (${_virtualRendered} loaded)`
            : '';
        const label = `Showing all ${filteredGroups.length} groups${suffix}`;
        document.getElementById('pageInfo').textContent = label;
        document.getElementById('pageInfoBottom').textContent = label;
        document.getElementById('prevBtn').disabled = true;
        document.getElementById('nextBtn').disabled = true;
        document.getElementById('prevBtnBottom').disabled = true;
        document.getElementById('nextBtnBottom').disabled = true;
        if (showAllBtnTop) { showAllBtnTop.textContent = 'Show Pages'; showAllBtnTop.classList.add('active'); }
        if (showAllBtn) { showAllBtn.textContent = 'Show Pages'; showAllBtn.classList.add('active'); }
    } else {
        const totalPages = Math.ceil(filteredGroups.length / state.pageSize) || 1;
        const label = `Page ${currentPage} of ${totalPages}`;
        document.getElementById('pageInfo').textContent = label;
        document.getElementById('pageInfoBottom').textContent = label;
        document.getElementById('prevBtn').disabled = currentPage <= 1;
        document.getElementById('nextBtn').disabled = currentPage >= totalPages;
        document.getElementById('prevBtnBottom').disabled = currentPage <= 1;
        document.getElementById('nextBtnBottom').disabled = currentPage >= totalPages;
        if (showAllBtnTop) { showAllBtnTop.textContent = 'Show All'; showAllBtnTop.classList.remove('active'); }
        if (showAllBtn) { showAllBtn.textContent = 'Show All'; showAllBtn.classList.remove('active'); }
    }

    if (paginationBottom) {
        paginationBottom.classList.toggle('active', filteredGroups.length > 0);
    }
}

function prevPage() {
    if (!showAllGroups && currentPage > 1) {
        currentPage--;
        renderGroups();
        window.scrollTo(0, 0);
    }
}

function nextPage() {
    const totalPages = Math.ceil(filteredGroups.length / state.pageSize);
    if (!showAllGroups && currentPage < totalPages) {
        currentPage++;
        renderGroups();
        window.scrollTo(0, 0);
    }
}

function setView(view) {
    currentView = view;
    document.querySelectorAll('.view-btn').forEach(btn => {
        const active = btn.dataset.view === view;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    renderGroups();
}

function toggleShowAll() {
    showAllGroups = !showAllGroups;
    currentPage = 1;
    renderGroups();
}

// =============================================================
// Intersection Observer — lazy image loading
// =============================================================
const _groupImageObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
        if (entry.isIntersecting) {
            _loadGroupImages(entry.target);
            _groupImageObserver.unobserve(entry.target);
        }
    }
}, { rootMargin: '200px' });

function _loadGroupImages(groupEl) {
    groupEl.querySelectorAll('img[data-src], video[data-src]').forEach(el => {
        el.src = el.dataset.src;
        el.removeAttribute('data-src');
    });
}

// =============================================================
// Virtual Scrolling — infinite scroll for "Show All" mode
// =============================================================
let _virtualRendered = 0;
const _VIRTUAL_CHUNK = 15;
let _scrollSentinelObserver = null;

function _initScrollSentinel() {
    if (_scrollSentinelObserver) _scrollSentinelObserver.disconnect();

    const sentinel = document.getElementById('scrollSentinel');
    if (!sentinel) return;

    _scrollSentinelObserver = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && showAllGroups) {
            _appendNextChunk();
        }
    }, { rootMargin: '600px' });

    _scrollSentinelObserver.observe(sentinel);
}

function _appendNextChunk() {
    if (_virtualRendered >= filteredGroups.length) return;

    const container = document.getElementById('groupsContainer');
    const sentinel = document.getElementById('scrollSentinel');
    const end = Math.min(_virtualRendered + _VIRTUAL_CHUNK, filteredGroups.length);
    const chunk = filteredGroups.slice(_virtualRendered, end);

    const fragment = document.createDocumentFragment();
    const temp = document.createElement('div');
    temp.innerHTML = chunk.map(g => _buildGroupCardHtml(g)).join('');

    while (temp.firstChild) {
        const card = temp.firstChild;
        fragment.appendChild(card);
    }

    // Insert before sentinel
    container.insertBefore(fragment, sentinel);

    // Observe new cards for lazy images
    container.querySelectorAll('.group-card:not([data-observed])').forEach(card => {
        card.setAttribute('data-observed', '1');
        _groupImageObserver.observe(card);
    });

    _virtualRendered = end;
    updatePagination();
}

// =============================================================
// Rendering
// =============================================================
function renderGroups() {
    const container = document.getElementById('groupsContainer');

    if (filteredGroups.length === 0) {
        if (groups.length === 0) {
            container.innerHTML = `
                <div class="no-results">
                    <h2>✨ No Duplicates Found</h2>
                    <p>Your image collection is clean — no exact or perceptual duplicates detected.</p>
                </div>
            `;
        } else {
            container.innerHTML = `
                <div class="no-results no-results--filtered">
                    <h2>No matches for current filters</h2>
                    <p>${groups.length} group${groups.length !== 1 ? 's' : ''} hidden by active filters.
                    <a href="#" onclick="clearFilters(); return false;">Clear filters</a></p>
                </div>
            `;
        }
        updatePagination();
        return;
    }

    if (showAllGroups) {
        // Virtual scroll mode: render first chunk + sentinel, append more on scroll
        _virtualRendered = 0;
        const initial = filteredGroups.slice(0, _VIRTUAL_CHUNK);
        _virtualRendered = initial.length;
        container.innerHTML = initial.map(g => _buildGroupCardHtml(g)).join('')
            + '<div id="scrollSentinel" style="height:1px"></div>';
        container.querySelectorAll('.group-card').forEach(card => {
            card.setAttribute('data-observed', '1');
            _groupImageObserver.observe(card);
        });
        _initScrollSentinel();
    } else {
        // Paginated mode
        const pageGroups = filteredGroups.slice((currentPage - 1) * state.pageSize, currentPage * state.pageSize);
        container.innerHTML = pageGroups.map(group => _buildGroupCardHtml(group)).join('');
        container.querySelectorAll('.group-card').forEach(card => {
            _groupImageObserver.observe(card);
        });
    }

    updatePagination();
}

function _buildGroupCardHtml(group) {
    let imagesHtml;
    if (currentView === 'list') {
        imagesHtml = _buildGroupImagesListHtml(group);
    } else if (currentView === 'compare') {
        imagesHtml = _buildGroupImagesCompareHtml(group);
    } else {
        imagesHtml = _buildGroupImagesGridHtml(group);
    }

    const sliderBtn = group.image_count === 2
        ? `<button class="compare-slider-btn" onclick="event.stopPropagation(); openCompareSlider(${group.id})" title="Open side-by-side comparison slider">&#8596; Slider</button>`
        : '';

    return `
        <div class="group-card" data-group-id="${group.id}">
            <div class="group-header">
                <div class="group-title">
                    Group #${group.id}
                    <span style="color: var(--text-muted); font-weight: normal;">(${group.image_count} images)</span>
                </div>
                <div style="display:flex;align-items:center;gap:10px;">
                    ${sliderBtn}
                    <span class="group-badge ${group.match_type}">${group.match_type}</span>
                    <span style="color: var(--text-muted);">Save ${group.potential_savings_formatted}</span>
                </div>
            </div>
            ${imagesHtml}
        </div>
    `;
}

function _buildGroupImagesGridHtml(group) {
    return `
        <div class="group-images">
            ${group.images.map((img, idx) => `
                <div class="image-card ${selections[img.path] === 'keep' ? 'selected' : 'to-delete'} ${img.is_reference ? 'reference-locked' : ''}"
                     data-path="${escapeHtml(img.path)}"
                     tabindex="0" role="button"
                     ${img.is_reference ? 'title="Reference folder image — cannot be deleted"' : ''}
                     onclick="toggleSelection('${escapeJs(img.path)}', ${group.id})">
                    <div class="image-wrapper">
                        <img class="image-preview"
                             data-src="/api/thumbnail?path=${encodeURIComponent(img.path)}"
                             alt="${escapeHtml(img.filename)}"
                             loading="lazy"
                             onerror="_onImageError(this)"
                             ondblclick="event.stopPropagation(); openLightbox(${group.id}, ${idx})">
                        ${img.is_reference ? '<div class="reference-badge">🔒 REFERENCE</div>' : ''}
                        ${selections[img.path] === 'keep'
                            ? '<div class="keep-badge">✔ KEEP</div>'
                            : '<div class="delete-badge">✖ DELETE</div>'}
                    </div>
                    <div class="image-info">
                        <div class="image-filename">${escapeHtml(img.filename)}</div>
                        <div class="image-path">${escapeHtml(img.directory)}</div>
                        <div class="image-meta">
                            <div class="meta-item">
                                <span class="meta-label">Size</span>
                                <span class="meta-value">${img.file_size_formatted}</span>
                            </div>
                            <div class="meta-item">
                                <span class="meta-label">Resolution</span>
                                <span class="meta-value">${img.resolution}</span>
                            </div>
                            <div class="meta-item">
                                <span class="meta-label">Megapixels</span>
                                <span class="meta-value">${img.megapixels} MP</span>
                            </div>
                            <div class="meta-item">
                                <span class="meta-label">Format</span>
                                <span class="meta-value">${img.format || 'Unknown'}</span>
                            </div>
                            ${img.media_type === 'video' && img.duration_formatted ? `
                            <div class="meta-item">
                                <span class="meta-label">Duration</span>
                                <span class="meta-value">${img.duration_formatted}</span>
                            </div>` : ''}
                        </div>
                        <div class="quality-score">
                            <div class="meta-item">
                                <span class="meta-label">Quality Score</span>
                                <span class="meta-value">${img.quality_score}</span>
                            </div>
                            <div class="quality-bar">
                                <div class="quality-fill" style="width: ${Math.min(100, img.quality_score)}%"></div>
                            </div>
                        </div>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

function _buildGroupImagesListHtml(group) {
    return `
        <div class="group-images-list">
            ${group.images.map((img, idx) => {
                const keep = selections[img.path] === 'keep';
                return `
                    <div class="image-row ${keep ? 'selected' : 'to-delete'} ${img.is_reference ? 'reference-locked' : ''}"
                         tabindex="0" role="button"
                         ${img.is_reference ? 'title="Reference folder image — cannot be deleted"' : ''}
                         onclick="toggleSelection('${escapeJs(img.path)}', ${group.id})">
                        <img class="image-row-thumb"
                             data-src="/api/thumbnail?path=${encodeURIComponent(img.path)}"
                             alt="${escapeHtml(img.filename)}"
                             onerror="_onImageError(this)">
                        <div class="image-row-name">${escapeHtml(img.filename)}</div>
                        <div class="image-row-path">${escapeHtml(img.directory)}</div>
                        <div class="image-row-meta">
                            <span>${img.file_size_formatted}</span>
                            <span>${img.resolution}</span>
                            <span>${img.format || 'Unknown'}</span>
                        </div>
                        <div class="image-row-quality">
                            <span style="font-size:0.72rem;color:var(--text-muted)">Q: ${img.quality_score}</span>
                            <div class="image-row-quality-bar">
                                <div class="quality-fill" style="width:${Math.min(100, img.quality_score)}%"></div>
                            </div>
                        </div>
                        ${img.is_reference ? '<span class="reference-badge">🔒 REF</span>' : ''}
                        <div class="image-row-badge ${keep ? 'keep' : 'delete'}">${keep ? '✔ KEEP' : '✖ DELETE'}</div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function _buildGroupImagesCompareHtml(group) {
    return `
        <div class="group-images-compare">
            ${group.images.map((img, idx) => {
                const keep = selections[img.path] === 'keep';
                return `
                    <div class="compare-card ${keep ? 'selected' : 'to-delete'} ${img.is_reference ? 'reference-locked' : ''}"
                         tabindex="0" role="button"
                         ${img.is_reference ? 'title="Reference folder image — cannot be deleted"' : ''}
                         onclick="toggleSelection('${escapeJs(img.path)}', ${group.id})"
                         ondblclick="event.stopPropagation(); openLightbox(${group.id}, ${idx})">
                        <img class="compare-preview"
                             data-src="/api/thumbnail?path=${encodeURIComponent(img.path)}"
                             alt="${escapeHtml(img.filename)}"
                             onerror="_onImageError(this)">
                        <div class="compare-info">
                            <div class="compare-filename">
                                ${img.is_reference ? '🔒 REFERENCE' : (keep ? '✔ KEEP' : '✖ DELETE')} — ${escapeHtml(img.filename)}
                            </div>
                            <div class="compare-meta">
                                ${img.file_size_formatted} &bull; ${img.resolution} &bull; ${img.megapixels} MP &bull; ${img.format || 'Unknown'}<br>
                                Quality: ${img.quality_score}
                            </div>
                            <div class="quality-bar" style="margin-top:8px">
                                <div class="quality-fill" style="width:${Math.min(100, img.quality_score)}%"></div>
                            </div>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

// =============================================================
// Selection Management
// =============================================================
function _applyDefaultGroupSelections(group, selections) {
    const refImg = group.images.find(img => img.is_reference);
    if (refImg) {
        group.images.forEach(img => {
            selections[img.path] = img.is_reference ? 'keep' : 'delete';
        });
    } else {
        group.images.forEach((img, idx) => {
            selections[img.path] = idx === 0 ? 'keep' : 'delete';
        });
    }
}

function toggleSelection(path, groupId) {
    const group = groups.find(g => g.id === groupId);
    if (!group) return;

    const clickedImg = group.images.find(img => img.path === path);
    if (!clickedImg) return;

    if (clickedImg.is_reference) {
        showToast('This image is in the reference folder and is protected from deletion.', 'info');
        return;
    }

    const prevSelections = {...selections};
    undoStack.push({ type: 'selection', data: prevSelections });
    if (undoStack.length > 50) undoStack.shift();

    const hasReference = group.images.some(img => img.is_reference);
    if (hasReference) {
        // The reference copy already anchors the group as the survivor —
        // remaining non-reference duplicates are independent "also keep
        // this copy too" toggles rather than an exclusive single-keep pick.
        selections[path] = selections[path] === 'delete' ? 'keep' : 'delete';
    } else if (selections[path] === 'delete') {
        group.images.forEach(img => {
            selections[img.path] = img.path === path ? 'keep' : 'delete';
        });
    }

    saveSelections();
    renderGroups();
    updateStats();
    showUndoToast('Selection updated');
}

function saveSelections() {
    fetch('/api/selections', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ selections })
    });
}

function resetSelections() {
    const prevSelections = {...selections};
    undoStack.push({ type: 'selection', data: prevSelections });

    groups.forEach(g => _applyDefaultGroupSelections(g, selections));

    saveSelections();
    renderGroups();
    updateStats();
}

function updateStats() {
    const totalGroups = groups.length;
    const totalDupes = groups.reduce((sum, g) => sum + g.images.length - 1, 0);

    let selectedCount = 0;
    let selectedSize = 0;

    // Build path→file_size lookup for O(1) access instead of O(n²)
    const sizeByPath = {};
    for (const g of groups) {
        for (const img of g.images) {
            sizeByPath[img.path] = img.file_size;
        }
    }

    Object.entries(selections).forEach(([path, status]) => {
        if (status === 'delete') {
            selectedCount++;
            selectedSize += sizeByPath[path] || 0;
        }
    });

    document.getElementById('statGroups').textContent = totalGroups;
    document.getElementById('statDupes').textContent = totalDupes;
    document.getElementById('statSavings').textContent = formatSize(selectedSize);
    document.getElementById('statSelected').textContent = selectedCount;

    if (errorImages.length > 0) {
        document.getElementById('statErrorsContainer').style.display = 'block';
        document.getElementById('statErrors').textContent = errorImages.length;
    } else {
        document.getElementById('statErrorsContainer').style.display = 'none';
    }

    document.getElementById('actionSummary').textContent = `${selectedCount} files selected for removal`;
    document.getElementById('actionSavings').textContent = `(${formatSize(selectedSize)})`;
}

// =============================================================
// Undo
// =============================================================
function showUndoToast(message) {
    const toast = document.getElementById('undoToast');
    document.getElementById('undoMessage').textContent = message;
    toast.classList.add('active');
    setTimeout(() => toast.classList.remove('active'), 5000);
}

function undoAction() {
    if (undoStack.length === 0) return;

    const action = undoStack.pop();
    if (action.type === 'selection') {
        selections = action.data;
        saveSelections();
        renderGroups();
        updateStats();
    }

    document.getElementById('undoToast').classList.remove('active');
}

// =============================================================
// Export
// =============================================================
function _buildExportData() {
    return groups.map(g => ({
        id: g.id,
        match_type: g.match_type,
        images: g.images.map(img => ({
            path: img.path,
            filename: img.filename,
            directory: img.directory,
            file_size: img.file_size,
            file_size_formatted: img.file_size_formatted,
            resolution: img.resolution,
            format: img.format || 'Unknown',
            quality_score: img.quality_score,
            status: selections[img.path] === 'keep' ? 'keep' : 'delete'
        }))
    }));
}

function _downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

function exportTxt() {
    const lines = ['# Duplicate Image Report', ''];
    groups.forEach(g => {
        lines.push(`## Group ${g.id} (${g.match_type})`);
        g.images.forEach(img => {
            const status = selections[img.path] === 'keep' ? 'KEEP' : 'DELETE';
            lines.push(`[${status}] ${img.path}`);
        });
        lines.push('');
    });
    if (errorImages.length > 0) {
        lines.push('## Failed to Analyze');
        errorImages.forEach(img => {
            lines.push(`[ERROR] ${img.path}`);
            lines.push(`        Reason: ${img.error || 'Unknown error'}`);
        });
        lines.push('');
    }
    _downloadBlob(new Blob([lines.join('\n')], { type: 'text/plain' }), 'duplicate_report.txt');
}

function exportCsv() {
    const rows = ['group_id,match_type,status,path,file_size,resolution,format,quality_score'];
    groups.forEach(g => {
        g.images.forEach(img => {
            const status = selections[img.path] === 'keep' ? 'keep' : 'delete';
            rows.push(`${g.id},${g.match_type},${status},"${img.path}",${img.file_size},"${img.resolution}",${img.format || 'Unknown'},${img.quality_score}`);
        });
    });
    _downloadBlob(new Blob([rows.join('\n')], { type: 'text/csv' }), 'duplicate_report.csv');
}

function exportJson() {
    const data = { generated: new Date().toISOString(), groups: _buildExportData() };
    _downloadBlob(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), 'duplicate_report.json');
}

function exportHtml() {
    const data = _buildExportData();
    const rows = data.flatMap(g => g.images.map(img =>
        `<tr class="${escapeHtml(img.status)}"><td>${g.id}</td><td>${escapeHtml(g.match_type)}</td>` +
        `<td>${escapeHtml(img.status.toUpperCase())}</td><td>${escapeHtml(img.path)}</td>` +
        `<td>${escapeHtml(img.file_size_formatted)}</td><td>${escapeHtml(img.resolution)}</td>` +
        `<td>${escapeHtml(img.format)}</td><td>${img.quality_score}</td></tr>`
    ));
    const html = `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>PixSieve Report</title>
<style>body{font-family:system-ui;margin:2rem;background:#111;color:#e8e8e8}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #333;padding:6px 10px;text-align:left}
th{background:#222}tr.keep{background:rgba(34,197,94,.1)}tr.delete{background:rgba(239,68,68,.1)}</style>
</head><body><h1>PixSieve Duplicate Report</h1>
<p>Generated: ${new Date().toLocaleString()}</p>
<table><thead><tr><th>Group</th><th>Type</th><th>Status</th><th>Path</th><th>Size</th><th>Resolution</th><th>Format</th><th>Quality</th></tr></thead>
<tbody>${rows.join('')}</tbody></table></body></html>`;
    _downloadBlob(new Blob([html], { type: 'text/html' }), 'duplicate_report.html');
}

/** Toggle a dropdown menu's visibility and keep its trigger button's
 * aria-expanded in sync, so assistive tech is told the menu's real state
 * rather than just seeing a class name change. */
function _toggleDropdownMenu(menuId, buttonSelector) {
    const menu = document.getElementById(menuId);
    const isOpen = menu.classList.toggle('open');
    const btn = document.querySelector(buttonSelector);
    if (btn) btn.setAttribute('aria-expanded', String(isOpen));
}

function _closeDropdownMenu(menuId, buttonSelector) {
    const menu = document.getElementById(menuId);
    menu.classList.remove('open');
    const btn = document.querySelector(buttonSelector);
    if (btn) btn.setAttribute('aria-expanded', 'false');
}

function toggleExportMenu() {
    _toggleDropdownMenu('exportMenu', '[data-action="toggle-export-menu"]');
}

// =============================================================
// Quick Operations (batch ops from results view)
// =============================================================
function toggleQuickOpsMenu() {
    _toggleDropdownMenu('quickOpsMenu', '[data-action="toggle-quickops-menu"]');
}

function _getDeleteFiles() {
    return Object.entries(selections)
        .filter(([, v]) => v === 'delete')
        .map(([path]) => path);
}

// Shared by both quick ops -- they act on the same "files marked for
// deletion" set, so running two at once doesn't make sense. Same rationale
// as _cacheOpInFlight above: no server-side concurrency guard exists for
// /api/batch-operation, so this is purely a client-side double-submit guard.
let _quickOpInFlight = false;

function quickOpConvertJpg() {
    if (_quickOpInFlight) return;
    _closeDropdownMenu('quickOpsMenu', '[data-action="toggle-quickops-menu"]');
    const files = _getDeleteFiles();
    if (!files.length) { showToast('No files marked for deletion.', 'info'); return; }
    if (!confirm(`Convert ${files.length} file(s) to JPG? Original files will remain.`)) return;

    const btn = document.querySelector('[data-action="quickop-convert-jpg"]');
    _quickOpInFlight = true;
    if (btn) btn.disabled = true;

    fetch('/api/batch-operation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation: 'convert_jpg', files }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) { showToast('Error: ' + data.error, 'error'); return; }
        showToast(`Converted ${data.converted} file(s). Skipped: ${data.skipped}. Errors: ${data.errors}.`, 'success');
    })
    .catch(() => showToast('Request failed.', 'error'))
    .finally(() => {
        _quickOpInFlight = false;
        if (btn) btn.disabled = false;
    });
}

function quickOpMove() {
    if (_quickOpInFlight) return;
    _closeDropdownMenu('quickOpsMenu', '[data-action="toggle-quickops-menu"]');
    const files = _getDeleteFiles();
    if (!files.length) { showToast('No files marked for deletion.', 'info'); return; }
    const destination = prompt(`Move ${files.length} file(s) to directory:\nEnter full path:`);
    if (!destination) return;

    const btn = document.querySelector('[data-action="quickop-move"]');
    _quickOpInFlight = true;
    if (btn) btn.disabled = true;

    fetch('/api/batch-operation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation: 'move', files, destination }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) { showToast('Error: ' + data.error, 'error'); return; }
        showToast(`Moved ${data.moved} file(s). Errors: ${data.errors}.`, 'success');
    })
    .catch(() => showToast('Request failed.', 'error'))
    .finally(() => {
        _quickOpInFlight = false;
        if (btn) btn.disabled = false;
    });
}

// Close export/quick-ops menus on outside click. Scoped to each menu's own
// wrapper (menu.parentElement), not a shared ".export-dropdown" class match
// -- both dropdowns use that same class name, so a closest()-based check
// would incorrectly treat a click inside either one as "inside both".
document.addEventListener('click', e => {
    [
        ['exportMenu', '[data-action="toggle-export-menu"]'],
        ['quickOpsMenu', '[data-action="toggle-quickops-menu"]'],
    ].forEach(([menuId, buttonSelector]) => {
        const menu = document.getElementById(menuId);
        if (menu && menu.classList.contains('open') && !menu.parentElement.contains(e.target)) {
            _closeDropdownMenu(menuId, buttonSelector);
        }
    });
}, true);

// =============================================================
// Delete Modal
// =============================================================
function showDeleteModal(singlePath) {
    pendingSingleDeletePath = singlePath || null;

    let count, text;
    if (pendingSingleDeletePath) {
        count = 1;
        text = `Move "${pendingSingleDeletePath.split(/[\\/]/).pop()}" to the trash folder?`;
    } else {
        count = Object.values(selections).filter(s => s === 'delete').length;
        if (count === 0) {
            showToast('No files selected for removal.', 'error')
            return;
        }
        text = `Are you sure you want to move ${count} files to the trash folder?`;
    }
    document.getElementById('deleteModalText').textContent = text;

    const preset = document.getElementById('trashDirPreset').value.trim();
    const scanDir = state.scanFolders.length > 0 ? state.scanFolders[0].path : '';

    if (preset) {
        document.getElementById('trashDir').value = preset;
    } else if (scanDir && !document.getElementById('trashDir').value) {
        document.getElementById('trashDir').value = scanDir + '_duplicates_trash';
    }

    const modal = document.getElementById('deleteModal');
    modal.classList.add('active');
    trapFocus(modal);
}

let _deleteInFlight = false;

function hideDeleteModal() {
    // Block dismissal while a request is pending — otherwise Cancel (or
    // Escape) lets the user navigate the lightbox to a different image
    // before the response arrives, and the eventual callback would mutate
    // whatever image is showing THEN, not the one actually deleted.
    if (_deleteInFlight) return;
    document.getElementById('deleteModal').classList.remove('active');
    pendingSingleDeletePath = null;
    releaseFocusTrap();
}

function _setDeleteModalBusy(busy) {
    document.querySelectorAll('#deleteModal .btn').forEach(b => { b.disabled = busy; });
}

let _deleteStream = null;
let _deleteElapsedTimer = null;
let _deleteStartTime = null;

function _startDeleteElapsed() {
    _deleteStartTime = Date.now();
    const el = document.getElementById('deleteElapsed');
    if (el) el.textContent = '0s';
    _deleteElapsedTimer = setInterval(() => {
        if (!_deleteStartTime) return;
        const secs = Math.floor((Date.now() - _deleteStartTime) / 1000);
        if (el) el.textContent = secs < 60 ? secs + 's' : Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
    }, 1000);
}

function _stopDeleteElapsed() {
    if (_deleteElapsedTimer) {
        clearInterval(_deleteElapsedTimer);
        _deleteElapsedTimer = null;
    }
    _deleteStartTime = null;
}

function executeDelete() {
    if (_deleteInFlight) return;
    const trashDir = document.getElementById('trashDir').value.trim();
    if (!trashDir) {
        showToast('Please enter a trash directory.', 'error')
        return;
    }

    const singlePath = pendingSingleDeletePath; // capture before hideDeleteModal() clears it
    const filesToDelete = singlePath
        ? [singlePath]
        : Object.entries(selections).filter(([, status]) => status === 'delete').map(([path]) => path);

    _deleteInFlight = true;
    _setDeleteModalBusy(true);
    document.getElementById('deleteProgressLabel').textContent = 'Moving files...';
    document.getElementById('deleteProgressFill').style.width = '0%';
    document.getElementById('deleteProgressText').textContent = filesToDelete.length > 1 ? `0 / ${filesToDelete.length}` : '';
    document.getElementById('deleteProgress').classList.add('active');
    _startDeleteElapsed();

    fetch('/api/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({files: filesToDelete, trashDir})
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            _finishDelete(null, filesToDelete, singlePath, data.error);
            return;
        }
        _startDeleteStream(filesToDelete, singlePath);
    })
    .catch(() => _finishDelete(null, filesToDelete, singlePath, 'Request failed.'));
}

function _startDeleteStream(filesToDelete, singlePath) {
    if (_deleteStream) {
        _deleteStream.close();
        _deleteStream = null;
    }

    _deleteStream = new EventSource('/api/delete/stream');

    _deleteStream.onmessage = (e) => {
        let data;
        try { data = JSON.parse(e.data); } catch { return; }

        if (data.status === 'running') {
            const done = data.moved + data.errors;
            const pct = data.total > 0 ? Math.round((done / data.total) * 100) : 0;
            document.getElementById('deleteProgressFill').style.width = pct + '%';
            const filename = data.current_file ? data.current_file.split(/[\\/]/).pop() : '';
            document.getElementById('deleteProgressText').textContent =
                `${done} / ${data.total}` + (filename ? ` — ${filename}` : '');
        }

        if (data.status === 'complete' || data.status === 'error') {
            _deleteStream.close();
            _deleteStream = null;
            _finishDelete(data, filesToDelete, singlePath);
        }
    };

    _deleteStream.onerror = () => {
        if (_deleteStream) {
            _deleteStream.close();
            _deleteStream = null;
        }
        // The stream can drop after the operation already reached a terminal
        // state (server closed it); fall back to a final status check rather
        // than assuming failure.
        fetch('/api/delete/status')
            .then(r => r.json())
            .then(data => _finishDelete(data, filesToDelete, singlePath))
            .catch(() => _finishDelete(null, filesToDelete, singlePath, 'Lost connection while moving files.'));
    };
}

function _finishDelete(data, filesToDelete, singlePath, errorMessage) {
    _deleteInFlight = false;
    _setDeleteModalBusy(false);
    _stopDeleteElapsed();
    document.getElementById('deleteProgress').classList.remove('active');
    hideDeleteModal();

    if (!data) {
        showToast(errorMessage || 'Request failed.', 'error');
        return;
    }

    if (singlePath) {
        _handleSingleImageDeleteResult(singlePath, data);
        return;
    }

    showToast(data.errors > 0 ? (data.moved + " file(s) moved (" + data.errors + " errors)") : (data.moved + " file(s) moved to trash."), data.errors > 0 ? "error" : undefined);

    groups.forEach(g => {
        g.images = g.images.filter(img => !filesToDelete.includes(img.path));
    });
    groups = groups.filter(g => g.images.length > 1);

    filesToDelete.forEach(path => delete selections[path]);

    applyFilters();
    updateStats();

    if (groups.length === 0) {
        document.getElementById('actionBar').classList.remove('active');
    }
}

// =============================================================
// Lightbox
// =============================================================
function openLightbox(groupId, imgIdx) {
    const group = groups.find(g => g.id === groupId);
    if (!group) return;

    lightboxImages = group.images.map(img => img.path);
    lightboxIndex = imgIdx;

    updateLightboxImage();
    const lightbox = document.getElementById('lightbox');
    lightbox.classList.add('active');
    trapFocus(lightbox);
}

function updateLightboxImage() {
    const path = lightboxImages[lightboxIndex];
    const meta = (typeof _editorFindImageMeta === 'function') ? _editorFindImageMeta(path) : null;
    const isVideo = meta && meta.media_type === 'video';

    const imgEl = document.getElementById('lightboxImg');
    const videoEl = document.getElementById('lightboxVideo');
    const filename = (path || '').split(/[\\/]/).pop();

    if (isVideo) {
        imgEl.hidden = true;
        imgEl.src = '';
        videoEl.hidden = false;
        videoEl.poster = '/api/thumbnail?path=' + encodeURIComponent(path);
        videoEl.src = '/api/image?path=' + encodeURIComponent(path);
        videoEl.setAttribute('aria-label', filename);
    } else {
        videoEl.pause();
        videoEl.hidden = true;
        videoEl.removeAttribute('src');
        videoEl.load();
        imgEl.hidden = false;
        imgEl.src = '/api/image?path=' + encodeURIComponent(path);
        imgEl.alt = filename;
    }

    if (typeof editorOnImageChanged === 'function') editorOnImageChanged(path);
}

function lightboxPrev(e) {
    e.stopPropagation();
    lightboxIndex = (lightboxIndex - 1 + lightboxImages.length) % lightboxImages.length;
    updateLightboxImage();
}

function lightboxNext(e) {
    e.stopPropagation();
    lightboxIndex = (lightboxIndex + 1) % lightboxImages.length;
    updateLightboxImage();
}

function closeLightbox() {
    document.getElementById('lightbox').classList.remove('active');
    releaseFocusTrap();
    const videoEl = document.getElementById('lightboxVideo');
    if (videoEl) videoEl.pause();
    if (typeof editorReset === 'function') editorReset();
}

// =============================================================
// Lightbox: rename (F2) and single-image delete
// =============================================================
let _renameOriginalName = null;
let pendingSingleDeletePath = null;

function openRenameModal() {
    if (typeof editorHasPendingEdit !== 'undefined' && editorHasPendingEdit) {
        showToast('Save or discard your edit before renaming.', 'error');
        return;
    }
    const path = lightboxImages[lightboxIndex];
    if (!path) return;

    const filename = path.split(/[\\/]/).pop();
    _renameOriginalName = filename;

    const input = document.getElementById('renameInput');
    input.value = filename;

    const modal = document.getElementById('renameModal');
    modal.classList.add('active');
    trapFocus(modal);

    input.focus();
    const dot = filename.lastIndexOf('.');
    if (dot > 0) input.setSelectionRange(0, dot); else input.select();
}

let _renameInFlight = false;

function hideRenameModal() {
    // Block dismissal while a request is pending — same rationale as
    // hideDeleteModal: otherwise the user can navigate the lightbox to a
    // different image before the response arrives, and _applyRenameToState's
    // trailing updateLightboxImage() would then reset whatever image is
    // showing THEN, discarding any edit the user started on it in the meantime.
    if (_renameInFlight) return;
    document.getElementById('renameModal').classList.remove('active');
    releaseFocusTrap();
}

function _setRenameModalBusy(busy) {
    document.getElementById('renameInput').disabled = busy;
    document.querySelectorAll('#renameModal .btn').forEach(b => { b.disabled = busy; });
}

function executeRename() {
    if (_renameInFlight) return;
    const path = lightboxImages[lightboxIndex];
    const newName = document.getElementById('renameInput').value.trim();

    if (!path) { hideRenameModal(); return; }
    if (!newName) { showToast('File name cannot be empty.', 'error'); return; }
    if (newName === _renameOriginalName) { hideRenameModal(); return; }

    _renameInFlight = true;
    _setRenameModalBusy(true);

    fetch('/api/image/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, newName }),
    })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        _renameInFlight = false;
        _setRenameModalBusy(false);
        if (!ok || data.error) {
            showToast('Rename failed: ' + (data.error || 'unknown error'), 'error');
            return;
        }
        hideRenameModal();
        _applyRenameToState(path, data.path, data.filename);
        showToast(`Renamed to ${data.filename}`, 'success');
    })
    .catch(() => {
        _renameInFlight = false;
        _setRenameModalBusy(false);
        showToast('Request failed.', 'error');
    });
}

function _applyRenameToState(oldPath, newPath, newFilename) {
    // 1. Lightbox's own array of paths.
    const idx = lightboxImages.indexOf(oldPath);
    if (idx !== -1) lightboxImages[idx] = newPath;

    // 2. The group entry — filename/path are plain strings baked in at scan
    //    time, not derived getters, so they must be updated by hand.
    for (const g of groups) {
        const img = g.images.find(i => i.path === oldPath);
        if (img) {
            img.path = newPath;
            img.filename = newFilename;
            break;
        }
    }

    // 3. Carry over any keep/delete mark so it isn't silently lost just
    //    because the file's name changed.
    if (Object.prototype.hasOwnProperty.call(selections, oldPath)) {
        selections[newPath] = selections[oldPath];
        delete selections[oldPath];
    }

    updateLightboxImage(); // refreshes <img src> and resets editor state for the new path
    applyFilters();        // full re-render — rename is infrequent, simplest safe approach
}

document.addEventListener('keydown', e => {
    if (e.target.id !== 'renameInput') return;
    if (e.key === 'Enter') { e.preventDefault(); executeRename(); }
    if (e.key === 'Escape') { e.preventDefault(); hideRenameModal(); }
});

function _handleSingleImageDeleteResult(path, data) {
    if (data.error) {
        // Request-level validation failure (e.g. bad trash directory) — no
        // moved/errors/error_details fields exist on this response shape.
        showToast(`Delete failed: ${data.error}`, 'error');
        return;
    }
    if (data.errors > 0 || data.moved !== 1) {
        const detail = (data.error_details && data.error_details[0] && data.error_details[0].error) || 'Unknown error';
        showToast(`Delete failed: ${detail}`, 'error');
        return; // lightbox, groups, selections all untouched — file is still on disk
    }

    delete selections[path];

    // Find the group containing this image BEFORE mutating anything.
    const group = groups.find(g => g.images.some(img => img.path === path));

    // Capture the sibling count now — the `groups = groups.filter(...)` line
    // below may drop this exact group object out of the array, so this is
    // the one source of truth for deciding the lightbox's fate afterward.
    let remainingInGroup = 0;
    if (group) {
        group.images = group.images.filter(img => img.path !== path);
        remainingInGroup = group.images.length;
    }

    const idx = lightboxImages.indexOf(path);
    if (idx !== -1) lightboxImages.splice(idx, 1);

    groups = groups.filter(g => g.images.length > 1); // same rule the batch-delete flow already applies

    applyFilters();
    updateStats();
    if (groups.length === 0) {
        document.getElementById('actionBar').classList.remove('active');
    }

    if (remainingInGroup > 1 && lightboxImages.length > 0) {
        lightboxIndex = Math.min(lightboxIndex, lightboxImages.length - 1);
        updateLightboxImage();
    } else {
        closeLightbox();
    }

    showToast(`Moved to trash: ${path.split(/[\\/]/).pop()}`, 'success');
}

// =============================================================
// Compare Slider
// =============================================================
let _compareSliderPos = 50; // percentage 0–100
let _compareSliderDragging = false;
let _compareSliderCleanup = null;

function openCompareSlider(groupId) {
    const group = groups.find(g => g.id === groupId);
    if (!group || group.images.length < 2) return;

    const imgA = group.images[0];
    const imgB = group.images[1];

    // Videos can't be rendered by an <img> tag - fall back to the server-
    // generated frame thumbnail for the slider (also a more useful
    // comparison for "did resolution/quality differ" than a moving image).
    const compareSrc = (img) =>
        (img.media_type === 'video' ? '/api/thumbnail' : '/api/image') + '?path=' + encodeURIComponent(img.path);
    const imgAEl = document.getElementById('compareImgA');
    const imgBEl = document.getElementById('compareImgB');
    imgAEl.src = compareSrc(imgA);
    imgBEl.src = compareSrc(imgB);
    imgAEl.alt = imgA.filename;
    imgBEl.alt = imgB.filename;
    document.getElementById('compareSliderLabelA').textContent = imgA.filename;
    document.getElementById('compareSliderLabelB').textContent = imgB.filename;

    _setCompareSliderPos(50);
    const overlay = document.getElementById('compareSliderOverlay');
    overlay.classList.add('active');
    // trapFocus() focuses the first focusable element in the overlay as a
    // side effect; the explicit focus() below overrides that back to the
    // drag handle specifically, since arrow-key nudging (the overlay's own
    // documented keyboard interaction) needs focus there, not on whichever
    // element happens to come first in the markup.
    trapFocus(overlay);
    document.getElementById('compareSliderHandle').focus();
    // Clean up previous listeners before attaching new ones
    if (_compareSliderCleanup) { _compareSliderCleanup(); _compareSliderCleanup = null; }
    _attachCompareSliderEvents();
}

function closeCompareSlider() {
    document.getElementById('compareSliderOverlay').classList.remove('active');
    releaseFocusTrap();
    if (_compareSliderCleanup) { _compareSliderCleanup(); _compareSliderCleanup = null; }
}

function _setCompareSliderPos(pos) {
    _compareSliderPos = Math.max(1, Math.min(99, pos));
    document.getElementById('compareImgAWrapper').style.width = _compareSliderPos + '%';
    document.getElementById('compareSliderHandle').style.left = _compareSliderPos + '%';
    document.getElementById('compareSliderHandle').setAttribute('aria-valuenow', Math.round(_compareSliderPos));
}

function _attachCompareSliderEvents() {
    const container = document.getElementById('compareSliderContainer');
    const handle    = document.getElementById('compareSliderHandle');

    function posFromClientX(clientX) {
        const rect = container.getBoundingClientRect();
        return ((clientX - rect.left) / rect.width) * 100;
    }

    function onMouseMove(e) {
        if (!_compareSliderDragging) return;
        _setCompareSliderPos(posFromClientX(e.clientX));
    }
    function onMouseUp() { _compareSliderDragging = false; }
    function onTouchMove(e) {
        if (!_compareSliderDragging) return;
        _setCompareSliderPos(posFromClientX(e.touches[0].clientX));
    }
    function onKeyDown(e) {
        if (e.key === 'ArrowLeft')  { _setCompareSliderPos(_compareSliderPos - 2); e.preventDefault(); }
        if (e.key === 'ArrowRight') { _setCompareSliderPos(_compareSliderPos + 2); e.preventDefault(); }
    }

    container.addEventListener('mousedown',  () => { _compareSliderDragging = true; });
    container.addEventListener('touchstart', () => { _compareSliderDragging = true; }, { passive: true });
    document.addEventListener('mousemove',  onMouseMove);
    document.addEventListener('mouseup',    onMouseUp);
    document.addEventListener('touchmove',  onTouchMove, { passive: true });
    document.addEventListener('touchend',   onMouseUp);
    handle.addEventListener('keydown', onKeyDown);

    _compareSliderCleanup = () => {
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup',   onMouseUp);
        document.removeEventListener('touchmove', onTouchMove);
        document.removeEventListener('touchend',  onMouseUp);
        handle.removeEventListener('keydown', onKeyDown);
    };
}

// =============================================================
// Keyboard Navigation
// =============================================================
document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT') return;

    const lightboxActive = document.getElementById('lightbox').classList.contains('active');
    if (lightboxActive && typeof editorHandleLightboxKeydown === 'function' && editorHandleLightboxKeydown(e)) {
        return;
    }

    const deleteModalActive = document.getElementById('deleteModal').classList.contains('active');
    const renameModalActive = document.getElementById('renameModal').classList.contains('active');

    if (e.key === 'Escape') {
        // Dropdown menus are lightweight, non-modal popups -- always close
        // them on Escape regardless of what else is open, independent of
        // the modal-priority dispatch below.
        _closeDropdownMenu('exportMenu', '[data-action="toggle-export-menu"]');
        _closeDropdownMenu('quickOpsMenu', '[data-action="toggle-quickops-menu"]');

        const action = _resolveEscapeAction({
            cacheModalActive: document.getElementById('cacheModal').classList.contains('active'),
            deleteModalActive,
            renameModalActive,
            autoFillModalActive: document.getElementById('autoFillModal').classList.contains('active'),
        });
        // Dismiss only whichever modal is actually open. Previously this
        // unconditionally called hideCacheModal() first regardless of which
        // modal was open, which tore down ANY modal's focus trap (a single
        // shared _focusTrapCleanup slot, released unconditionally inside
        // hideCacheModal()) even when the cache modal itself wasn't showing.
        // Concretely: pressing Escape while the delete modal was open
        // mid-request (which correctly no-ops its own dismissal via
        // _deleteInFlight) still ran hideCacheModal()'s releaseFocusTrap()
        // first, silently removing the Tab-cycling constraint from the
        // still-open, still-busy delete modal.
        if (action === 'cache') { hideCacheModal(); return; }
        if (action === 'delete-rename') {
            // A modal is stacked on top of the lightbox — dismiss just that,
            // don't cascade into closing the lightbox underneath it. This
            // matters even when focus has moved off the modal's <input>
            // (e.g. Tab to its Cancel button), since the top-of-handler
            // INPUT guard no longer short-circuits in that case.
            hideDeleteModal();
            hideRenameModal();
            return;
        }
        if (action === 'autofill') { _hideAutoFillModal(); return; }
        closeLightbox();
        closeCompareSlider();
        return;
    }

    if (lightboxActive) {
        // Don't let lightbox shortcuts fire while a modal is open on top of it.
        if (deleteModalActive || renameModalActive) {
            return;
        }
        if (e.key === 'ArrowLeft') lightboxPrev(e);
        if (e.key === 'ArrowRight') lightboxNext(e);
        if (e.key === 'F2') { e.preventDefault(); openRenameModal(); }
        if (e.key === 'Delete') { e.preventDefault(); showDeleteModal(lightboxImages[lightboxIndex]); }
        return;
    }

    if (e.key === 'z' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        undoAction();
        return;
    }

    if (e.key === 'z') {
        undoAction();
        return;
    }

    // Page navigation: left/right arrows when results are shown
    if (e.key === 'ArrowLeft' && document.getElementById('groupsContainer').classList.contains('active')) {
        e.preventDefault();
        prevPage();
        return;
    }
    if (e.key === 'ArrowRight' && document.getElementById('groupsContainer').classList.contains('active')) {
        e.preventDefault();
        nextPage();
        return;
    }

    // Focus search with /
    if (e.key === '/') {
        const search = document.getElementById('searchFilter');
        if (search) {
            e.preventDefault();
            search.focus();
            search.select();
        }
        return;
    }

    // Delete: open trash modal when results are visible
    if (e.key === 'Delete' && document.getElementById('actionBar').classList.contains('active')) {
        e.preventDefault();
        showDeleteModal();
        return;
    }

    // N: start new scan (when results shown, not focused on input)
    if (e.key === 'n' && document.getElementById('groupsContainer').classList.contains('active')) {
        e.preventDefault();
        newScan();
        return;
    }
});

// =============================================================
// Cache Management
// =============================================================
function loadCacheStats() {
    fetch('/api/cache/stats')
        .then(r => r.json())
        .then(data => {
            const entries = data.total_entries || 0;
            const sizeMb = data.db_size_mb || 0;

            document.getElementById('cacheInfo').textContent =
                `⚡ Cache: ${entries.toLocaleString()} images cached (${sizeMb} MB)`;
            document.getElementById('cacheModalEntries').textContent = entries.toLocaleString();
            document.getElementById('cacheModalSize').textContent = `${sizeMb} MB`;
            document.getElementById('cacheModalPath').textContent = data.db_path || 'Unknown';

            if (entries > 0) {
                document.getElementById('cacheBanner').classList.add('active');
            }
        })
        .catch(() => {
            document.getElementById('cacheInfo').textContent = '⚡ Cache: Unable to load stats';
        });
}

function showCacheModal() {
    loadCacheStats();
    const modal = document.getElementById('cacheModal');
    modal.classList.add('active');
    trapFocus(modal);
}

function hideCacheModal() {
    document.getElementById('cacheModal').classList.remove('active');
    releaseFocusTrap();
}

// Shared by both cache actions below -- they operate on the same cache and
// shouldn't run concurrently with each other any more than with themselves.
// Guards re-entrancy client-side (a user double-clicking Clear/Cleanup);
// unlike the /api/operations/* routes, /api/cache/clear and /api/cache/cleanup
// have no server-side concurrency guard of their own to fall back on.
let _cacheOpInFlight = false;

function clearCache() {
    if (_cacheOpInFlight) return;
    if (!confirm('Clear all cached analysis data? Next scan will re-analyze all images.')) {
        return;
    }

    const btn = document.querySelector('[data-action="clear-cache"]');
    _cacheOpInFlight = true;
    if (btn) btn.disabled = true;

    fetch('/api/cache/clear', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            showToast('Cache cleared successfully.');
            loadCacheStats();
            document.getElementById('cacheBanner').classList.remove('active');
        })
        .catch(() => showToast('Failed to clear cache.', 'error'))
        .finally(() => {
            _cacheOpInFlight = false;
            if (btn) btn.disabled = false;
        });
}

function cleanupCache() {
    if (_cacheOpInFlight) return;

    const btn = document.querySelector('[data-action="cleanup-cache"]');
    _cacheOpInFlight = true;
    if (btn) btn.disabled = true;

    fetch('/api/cache/cleanup', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ max_age_days: 30 })
    })
        .then(r => r.json())
        .then(data => {
            showToast("Cleanup: " + data.missing_removed + " missing + " + data.stale_removed + " stale entries removed.");
            loadCacheStats();
        })
        .catch(() => showToast('Failed to cleanup cache.', 'error'))
        .finally(() => {
            _cacheOpInFlight = false;
            if (btn) btn.disabled = false;
        });
}

// =============================================================
// Utilities
// =============================================================

function _onImageError(img) {
    img.removeAttribute('src');
    img.removeAttribute('data-src');
    img.style.display = 'none';
    const wrapper = img.closest('.image-wrapper, .image-row, .compare-card');
    if (wrapper && !wrapper.querySelector('.img-error-badge')) {
        const badge = document.createElement('div');
        badge.className = 'img-error-badge';
        badge.textContent = 'Preview unavailable';
        wrapper.appendChild(badge);
    }
}

function formatSize(bytes) {
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return bytes.toFixed(1) + ' ' + units[i];
}

function formatNumber(n) {
    return n.toLocaleString();
}

function formatEta(seconds) {
    if (!seconds || seconds <= 0) return '--';
    if (seconds < 60) return Math.round(seconds) + 's';
    if (seconds < 3600) {
        const m = Math.floor(seconds / 60);
        const s = Math.round(seconds % 60);
        return `${m}m ${s}s`;
    }
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

function formatElapsed(seconds) {
    if (!seconds || seconds <= 0) return '0s';
    if (seconds < 60) return Math.round(seconds) + 's';
    if (seconds < 3600) {
        const m = Math.floor(seconds / 60);
        const s = Math.round(seconds % 60);
        return `${m}m ${s}s`;
    }
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function escapeJs(str) {
    if (!str) return '';
    // Used to embed values inside a single-quoted JS string literal that
    // itself sits inside a double-quoted onclick="..." HTML attribute, so
    // both the JS-string delimiter (') and the HTML-attribute delimiter (")
    // must be escaped, or a path containing a literal " breaks out of the
    // attribute.
    return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

// Debounce utility — delays fn by wait ms, restarting on each call
let _debounceTimers = {};
function debounce(key, fn, wait) {
    clearTimeout(_debounceTimers[key]);
    _debounceTimers[key] = setTimeout(fn, wait);
}

// =============================================================
// Tab Navigation
// =============================================================
function switchTab(tabName) {
    log.info('tabs', `switchTab("${tabName}")`);
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
        btn.setAttribute('aria-selected', 'false');
    });
    document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
    const panel = document.getElementById('tab-' + tabName);
    if (!panel) {
        log.error('tabs', `Tab panel not found: #tab-${tabName}`);
        return;
    }
    panel.classList.add('active');
    log.debug('tabs', `Panel #tab-${tabName} activated, display=${getComputedStyle(panel).display}`);
    const activeBtn = document.querySelector(`[data-tab="${tabName}"]`);
    if (activeBtn) { activeBtn.classList.add('active'); activeBtn.setAttribute('aria-selected', 'true'); }
}

// =============================================================
// Operations Module — SSE replaces setInterval polling
// =============================================================
let opsStream = null;
let currentOperation = 'move-to-parent';
let opsElapsedTimer = null;
let opsStartTime = null;

function _startOpsElapsed() {
    opsStartTime = Date.now();
    const el = document.getElementById('opsElapsed');
    if (el) el.textContent = '0s';
    opsElapsedTimer = setInterval(() => {
        if (!opsStartTime) return;
        const secs = Math.floor((Date.now() - opsStartTime) / 1000);
        if (el) el.textContent = secs < 60 ? secs + 's' : Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
    }, 1000);
}

function _stopOpsElapsed() {
    if (opsElapsedTimer) {
        clearInterval(opsElapsedTimer);
        opsElapsedTimer = null;
    }
    opsStartTime = null;
}

function _resetOpsProgress() {
    const fill = document.getElementById('opsProgressFill');
    const text = document.getElementById('opsProgressText');
    const elapsed = document.getElementById('opsElapsed');
    if (fill) { fill.classList.add('indeterminate'); fill.style.width = ''; }
    if (text) text.textContent = '';
    if (elapsed) elapsed.textContent = '0s';
}

function _setOpsProgress(pct, progressText) {
    const fill = document.getElementById('opsProgressFill');
    const text = document.getElementById('opsProgressText');
    if (fill) {
        if (pct !== null && pct !== undefined) {
            fill.classList.remove('indeterminate');
            fill.style.width = pct + '%';
        } else {
            fill.classList.add('indeterminate');
            fill.style.width = '';
        }
    }
    if (text && progressText) text.textContent = progressText;
}

function selectOperation(opName, el) {
    log.info('ops', `selectOperation("${opName}")`);
    currentOperation = opName;
    document.querySelectorAll('.ops-sidebar-item').forEach(item => item.classList.remove('active'));
    const target = el || document.querySelector(`[data-op="${opName}"]`);
    if (target) target.classList.add('active');

    document.querySelectorAll('.ops-form').forEach(f => f.classList.remove('active'));
    const form = document.getElementById('ops-' + opName);
    if (form) form.classList.add('active');

    document.getElementById('opsResult').classList.remove('active');
    document.getElementById('opsRunning').classList.remove('active');
}

// ---------------------------------------------------------------------------
// Per-folder date range helpers
// ---------------------------------------------------------------------------

function _togglePerFolderMode(prefix) {
    const checked = document.getElementById(prefix + 'PerFolder').checked;
    document.getElementById(prefix + 'SingleMode').style.display = checked ? 'none' : '';
    document.getElementById(prefix + 'PerFolderMode').style.display = checked ? '' : 'none';
}

function _loadChildFolders(prefix) {
    const directory = document.getElementById('opsDirectory').value.trim();
    if (!directory) {
        showToast('Enter a target directory first.', 'error');
        return;
    }
    const listEl = document.getElementById(prefix + 'FolderList');
    listEl.innerHTML = '<p class="muted">Loading folders\u2026</p>';

    const defaultStart = document.getElementById(prefix + 'DefaultStart').value;
    const defaultEnd = document.getElementById(prefix + 'DefaultEnd').value;

    fetch('/api/list-subfolders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ directory }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            listEl.innerHTML = '<p class="text-danger">' + data.error + '</p>';
            return;
        }
        if (!data.folders || data.folders.length === 0) {
            listEl.innerHTML = '<p class="muted">No child folders found.</p>';
            return;
        }
        listEl.innerHTML = '';
        data.folders.forEach(name => {
            const row = document.createElement('div');
            row.className = 'per-folder-row';
            row.dataset.folder = data.parent.replace(/\\/g, '/') + '/' + name;
            const safeName = escapeHtml(name);
            row.innerHTML =
                '<span class="per-folder-name" title="' + safeName + '">' + safeName + '</span>' +
                '<input type="date" class="pf-start" value="' + defaultStart + '">' +
                '<span class="pf-sep">to</span>' +
                '<input type="date" class="pf-end" value="' + defaultEnd + '">' +
                '<label class="pf-toggle"><input type="checkbox" class="pf-enabled" checked> Include</label>';
            listEl.appendChild(row);
        });
        showToast(data.folders.length + ' folder(s) loaded.', 'info');
    })
    .catch(err => {
        listEl.innerHTML = '<p class="text-danger">Failed to load folders: ' + err + '</p>';
    });
}

let _pendingAutoFill = null;

function _autoFillMonthly(prefix) {
    const startVal = document.getElementById(prefix + 'AutoFillStart').value;
    if (!startVal) { showToast('Pick an auto-fill start date first.', 'error'); return; }
    const rows = document.querySelectorAll('#' + prefix + 'FolderList .per-folder-row');
    if (!rows.length) { showToast('Load child folders first.', 'error'); return; }
    const base = new Date(startVal + 'T00:00:00');
    const planned = [];
    rows.forEach((row, i) => {
        const start = new Date(base.getFullYear(), base.getMonth() + i, 1);
        const end = new Date(base.getFullYear(), base.getMonth() + i + 1, 0);
        planned.push({
            name: row.querySelector('.per-folder-name').textContent,
            startDate: start.toISOString().slice(0, 10),
            endDate: end.toISOString().slice(0, 10),
        });
    });
    _pendingAutoFill = { prefix, planned };
    const preview = document.getElementById('autoFillPreview');
    preview.innerHTML = planned.map(p =>
        '<div class="autofill-preview-row">' +
            '<span class="autofill-folder-name">' + p.name + '</span>' +
            '<span class="autofill-range">' + p.startDate + '  \u2192  ' + p.endDate + '</span>' +
        '</div>'
    ).join('');
    const modal = document.getElementById('autoFillModal');
    modal.classList.add('active');
    trapFocus(modal);
}

function _applyAutoFill() {
    if (!_pendingAutoFill) return;
    const { prefix, planned } = _pendingAutoFill;
    const rows = document.querySelectorAll('#' + prefix + 'FolderList .per-folder-row');
    rows.forEach((row, i) => {
        if (!planned[i]) return;
        row.querySelector('.pf-start').value = planned[i].startDate;
        row.querySelector('.pf-end').value = planned[i].endDate;
    });
    document.getElementById('autoFillModal').classList.remove('active');
    releaseFocusTrap();
    _pendingAutoFill = null;
    showToast(planned.length + ' folder(s) filled with monthly ranges.', 'info');
}

function _hideAutoFillModal() {
    document.getElementById('autoFillModal').classList.remove('active');
    releaseFocusTrap();
    _pendingAutoFill = null;
}

function _collectFolderRanges(listId) {
    const rows = document.querySelectorAll('#' + listId + ' .per-folder-row');
    const ranges = [];
    rows.forEach(row => {
        const enabled = row.querySelector('.pf-enabled');
        if (enabled && !enabled.checked) return;
        ranges.push({
            folder: row.dataset.folder,
            name: row.querySelector('.per-folder-name').textContent,
            startDate: row.querySelector('.pf-start').value,
            endDate: row.querySelector('.pf-end').value,
        });
    });
    return ranges;
}

function buildOperationPayload(opName) {
    const directory = document.getElementById('opsDirectory').value.trim();
    const dryRun = document.getElementById('opsDryRun').checked;

    const payload = { directory, dryRun };

    switch (opName) {
        case 'move-to-parent': {
            const extInput = document.getElementById('mtpExtensions').value.trim();
            if (extInput) {
                payload.extensions = extInput.split(/\s+/);
            }
            payload.includeVideos = document.getElementById('mtpIncludeVideos').checked;
            break;
        }
        case 'move': {
            payload.destination = document.getElementById('moveDestination').value.trim();
            payload.overwrite = document.getElementById('moveOverwrite').checked;
            break;
        }
        case 'rename-random': {
            payload.nameLength = parseInt(document.getElementById('renameLength').value) || 12;
            payload.workers = parseInt(document.getElementById('renameWorkers').value) || 4;
            payload.recursive = document.getElementById('renameRecursive').checked;
            payload.includeVideos = document.getElementById('renameIncludeVideos').checked;
            break;
        }
        case 'rename-parent':
            break;
        case 'sort-alpha':
            break;
        case 'sort-color': {
            payload.method = document.getElementById('colorMethod').value;
            payload.nColors = parseInt(document.getElementById('colorNColors').value) || 3;
            payload.copyFiles = document.getElementById('colorCopy').checked;
            payload.includeVideos = document.getElementById('colorIncludeVideos').checked;
            break;
        }
        case 'sort-resolution': {
            payload.copyFiles = document.getElementById('resCopy').checked;
            payload.includeVideos = document.getElementById('resIncludeVideos').checked;
            break;
        }
        case 'fix-extensions': {
            payload.recursive = document.getElementById('fixExtRecursive').checked;
            break;
        }
        case 'convert': {
            payload.quality = parseInt(document.getElementById('convertQuality').value) || 95;
            payload.deleteOriginals = document.getElementById('convertDeleteOrig').checked;
            payload.recursive = document.getElementById('convertRecursive').checked;
            break;
        }
        case 'randomize-dates': {
            if (document.getElementById('datesPerFolder').checked) {
                payload.perFolder = true;
                payload.folderRanges = _collectFolderRanges('datesFolderList');
            } else {
                payload.startDate = document.getElementById('datesStartDate').value;
                payload.endDate = document.getElementById('datesEndDate').value;
                payload.recursive = document.getElementById('datesRecursive').checked;
            }
            payload.syncExif = document.getElementById('datesSyncExif').checked;
            payload.includeVideos = document.getElementById('datesIncludeVideos').checked;
            break;
        }
        case 'cleanup':
            break;
        case 'strip-ratings': {
            payload.recursive = document.getElementById('ratingsRecursive').checked;
            payload.includeVideos = document.getElementById('ratingsIncludeVideos').checked;
            break;
        }
        case 'repair': {
            payload.trashFolder = document.getElementById('repairTrashDir').value.trim();
            payload.attemptRepair = document.getElementById('repairAttemptRepair').checked;
            payload.quarantineUnfixable = document.getElementById('repairQuarantine').checked;
            payload.workers = parseInt(document.getElementById('repairWorkers').value) || 4;
            break;
        }
        case 'pipeline': {
            const steps = [];
            document.querySelectorAll('.pipeline-steps input[type="checkbox"]:checked').forEach(cb => {
                steps.push(cb.value);
            });
            payload.steps = steps;
            payload.startDate = document.getElementById('pipeStartDate').value;
            payload.endDate = document.getElementById('pipeEndDate').value;
            payload.nameLength = parseInt(document.getElementById('pipeNameLength').value) || 12;
            payload.jpgQuality = parseInt(document.getElementById('pipeQuality').value) || 95;
            payload.trashDir = document.getElementById('pipeTrashDir').value.trim();
            payload.includeVideos = document.getElementById('pipeIncludeVideos').checked;
            break;
        }
    }
    return payload;
}

function getEndpointForOperation(opName) {
    const endpoints = {
        'move-to-parent':  '/api/operations/move-to-parent',
        'move':            '/api/operations/move',
        'rename-random':   '/api/operations/rename/random',
        'rename-parent':   '/api/operations/rename/parent',
        'sort-alpha':      '/api/operations/sort/alpha',
        'sort-color':      '/api/operations/sort/color',
        'sort-resolution': '/api/operations/sort/resolution',
        'fix-extensions':  '/api/operations/fix-extensions',
        'convert':         '/api/operations/convert',
        'randomize-dates': '/api/operations/metadata/randomize-dates',
        'strip-ratings':   '/api/operations/metadata/strip-ratings',
        'cleanup':         '/api/operations/cleanup',
        'repair':          '/api/operations/repair',
        'pipeline':        '/api/operations/pipeline',
    };
    return endpoints[opName];
}

function togglePipeTrashDir() {
    const checked = document.getElementById('pipeStep_repair_corrupt').checked;
    document.getElementById('pipeTrashDirGroup').style.display = checked ? '' : 'none';
}

function runOperation(opName) {
    log.info('ops', `runOperation("${opName}")`);
    const payload = buildOperationPayload(opName);

    if (!payload.directory) {
        showToast('Please enter a target directory.', 'error')
        return;
    }
    if (opName === 'move' && !payload.destination) {
        showToast('Please enter a destination directory.', 'error')
        return;
    }
    if (opName === 'repair' && !payload.trashFolder) {
        showToast('Please enter a trash folder path.', 'error')
        return;
    }
    if (opName === 'pipeline' && (!payload.steps || payload.steps.length === 0)) {
        showToast('Please select at least one pipeline step.', 'error')
        return;
    }
    if (!payload.dryRun) {
        if (!confirm('Dry Run is OFF. Files WILL be modified. Continue?')) {
            return;
        }
    }

    let endpoint = getEndpointForOperation(opName);
    // Use per-folder endpoint when per-folder mode is active
    if (payload.perFolder) {
        if (opName === 'randomize-dates') endpoint = '/api/operations/metadata/randomize-dates-per-folder';
        if (!payload.folderRanges || payload.folderRanges.length === 0) {
            showToast('No folders loaded. Click "Load Child Folders" first.', 'error');
            return;
        }
    }

    document.getElementById('opsResult').classList.remove('active');
    document.getElementById('opsRunning').classList.add('active');
    document.getElementById('opsRunningText').textContent = 'Starting ' + opName + '...';
    document.querySelectorAll('.ops-actions .btn').forEach(btn => btn.disabled = true);
    _resetOpsProgress();
    _startOpsElapsed();

    fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            _stopOpsElapsed();
            showOpsError(data.error);
            return;
        }
        document.getElementById('opsRunningText').textContent = 'Running ' + opName + '...';
        // Replace polling with SSE stream
        startOpsStream();
    })
    .catch(err => {
        showOpsError('Failed to start operation: ' + err);
    });
}

function startOpsStream() {
    if (opsStream) {
        opsStream.close();
        opsStream = null;
    }

    opsStream = new EventSource('/api/operations/stream');

    opsStream.onmessage = (e) => {
        let data;
        try { data = JSON.parse(e.data); } catch { return; }

        // Relay progress updates from backend
        if (data.status === 'running') {
            _setOpsProgress(data.progress, data.progress_text);
            if (data.progress_text) {
                document.getElementById('opsRunningText').textContent = data.progress_text;
            }
        }

        if (data.status === 'complete') {
            opsStream.close();
            opsStream = null;
            _stopOpsElapsed();
            _setOpsProgress(100, '');
            document.getElementById('opsRunning').classList.remove('active');
            document.querySelectorAll('.ops-actions .btn').forEach(btn => btn.disabled = false);
            showOpsResult(data.result, data.operation);
        } else if (data.status === 'error') {
            opsStream.close();
            opsStream = null;
            _stopOpsElapsed();
            showOpsError(data.error || 'Operation failed');
        }
    };

    opsStream.onerror = () => {
        if (opsStream) {
            opsStream.close();
            opsStream = null;
        }
        _stopOpsElapsed();
        showOpsError('Connection lost during operation. Check server logs.');
    };
}

function showOpsResult(result, operationName) {
    const resultDiv = document.getElementById('opsResult');
    const titleEl = document.getElementById('opsResultTitle');
    const gridEl = document.getElementById('opsResultGrid');

    resultDiv.classList.remove('error');
    resultDiv.classList.add('active');

    const dryTag = document.getElementById('opsDryRun').checked ? ' (Dry Run)' : '';
    titleEl.textContent = 'Operation Complete: ' + (operationName || '') + dryTag;

    gridEl.innerHTML = '';

    if (!result) {
        gridEl.innerHTML = '<div class="ops-stat"><div class="ops-stat-value">--</div><div class="ops-stat-label">No data</div></div>';
        return;
    }

    if (typeof result === 'object' && !Array.isArray(result)) {
        const firstVal = Object.values(result)[0];
        if (firstVal && typeof firstVal === 'object' && !Array.isArray(firstVal)) {
            for (const [stepName, stepResult] of Object.entries(result)) {
                const stepHeader = document.createElement('div');
                stepHeader.style.cssText = 'grid-column: 1 / -1; color: var(--accent); font-weight: 600; margin-top: 12px; padding-bottom: 4px; border-bottom: 1px solid var(--border);';
                stepHeader.textContent = stepName;
                gridEl.appendChild(stepHeader);

                for (const [key, value] of Object.entries(stepResult)) {
                    gridEl.appendChild(createStatCard(key, value));
                }
            }
            return;
        }

        for (const [key, value] of Object.entries(result)) {
            if (key === 'problems') continue;
            if (typeof value === 'object' && !Array.isArray(value)) {
                const subHeader = document.createElement('div');
                subHeader.style.cssText = 'grid-column: 1 / -1; color: var(--text-muted); font-weight: 600; margin-top: 8px;';
                subHeader.textContent = key;
                gridEl.appendChild(subHeader);
                for (const [k, v] of Object.entries(value)) {
                    gridEl.appendChild(createStatCard(k, v));
                }
            } else if (Array.isArray(value)) {
                gridEl.appendChild(createStatCard(key, value.length + ' items'));
            } else {
                gridEl.appendChild(createStatCard(key, value));
            }
        }

        if (result.problems && result.problems.length > 0) {
            const section = document.createElement('div');
            section.style.cssText = 'grid-column: 1 / -1; margin-top: 16px;';
            // Design-system semantic colors, not arbitrary hex -- these
            // previously used unrelated hardcoded hex values (e.g. '#4caf50'
            // for a "success" state that doesn't match var(--success)
            // elsewhere in the app) and never adapted for light theme.
            const statusColors = {
                repaired: 'var(--success)', quarantined: 'var(--warning)',
                permission_error: 'var(--danger)', skipped: 'var(--text-muted)',
                error: 'var(--danger)'
            };
            let rows = result.problems.slice(0, 100).map(p => {
                const color = statusColors[p.status] || 'var(--text-muted)';
                return `<tr style="border-bottom:1px solid var(--border-subtle);">
                    <td style="padding:4px 8px;"><span style="color:${color};font-weight:600;">${escapeHtml(p.status)}</span></td>
                    <td style="padding:4px 8px;color:var(--text-muted);">${escapeHtml(p.corruption_type)}</td>
                    <td style="padding:4px 8px;word-break:break-all;">${escapeHtml(p.path)}</td>
                    <td style="padding:4px 8px;color:var(--text-muted);font-size:0.8em;">${escapeHtml(p.error || '')}</td>
                </tr>`;
            }).join('');
            if (result.problems.length > 100) {
                rows += `<tr><td colspan="4" style="padding:4px 8px;color:var(--text-muted);">... and ${result.problems.length - 100} more</td></tr>`;
            }
            section.innerHTML = `
                <div style="color:var(--accent);font-weight:600;margin-bottom:8px;">Problem Files (${result.problems.length})</div>
                <table style="width:100%;border-collapse:collapse;font-size:0.85em;">
                    <thead><tr style="border-bottom:1px solid var(--border);">
                        <th style="text-align:left;padding:4px 8px;color:var(--text-muted);">Status</th>
                        <th style="text-align:left;padding:4px 8px;color:var(--text-muted);">Corruption</th>
                        <th style="text-align:left;padding:4px 8px;color:var(--text-muted);">File</th>
                        <th style="text-align:left;padding:4px 8px;color:var(--text-muted);">Detail</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table>`;
            gridEl.appendChild(section);
        }
    }
}

function createStatCard(label, value) {
    const card = document.createElement('div');
    card.className = 'ops-stat';
    card.innerHTML = `<div class="ops-stat-value">${escapeHtml(String(value))}</div><div class="ops-stat-label">${escapeHtml(label)}</div>`;
    return card;
}

function showOpsError(message) {
    _stopOpsElapsed();
    document.getElementById('opsRunning').classList.remove('active');
    document.querySelectorAll('.ops-actions .btn').forEach(btn => btn.disabled = false);

    const resultDiv = document.getElementById('opsResult');
    const titleEl = document.getElementById('opsResultTitle');
    const gridEl = document.getElementById('opsResultGrid');

    resultDiv.classList.add('active', 'error');
    titleEl.textContent = 'Error';
    gridEl.innerHTML = '<div style="grid-column: 1/-1; color: var(--danger); padding: 10px;">' + escapeHtml(message) + '</div>';
}


// =============================================================
// Event Delegation
// =============================================================
document.addEventListener('click', e => {
    const btn = e.target.closest('[data-action],[data-tab],[data-op],[data-run-op]');
    if (!btn) return;

    const action = btn.dataset.action;
    const tab    = btn.dataset.tab;
    const op     = btn.dataset.op;
    const runOp  = btn.dataset.runOp;

    log.debug('event', `click delegation hit`, { action, tab, op, runOp, tag: btn.tagName, id: btn.id });

    if (tab)   { switchTab(tab); return; }
    if (op)    { selectOperation(op, btn); return; }
    if (runOp) { runOperation(runOp); return; }

    switch (action) {
        case 'start-scan':       startScan(); break;
        case 'new-scan':         newScan(); break;
        case 'restore-session':  restoreSession(); break;
        case 'dismiss-recovery': dismissRecovery(); break;
        case 'toggle-advanced':  toggleAdvancedOptions(); break;
        case 'pause-scan':       pauseScan(); break;
        case 'resume-scan':      resumeScan(); break;
        case 'cancel-scan':      cancelScan(); break;
        case 'show-cache-modal': showCacheModal(); break;
        case 'set-view':         setView(btn.dataset.view); break;
        case 'prev-page':        prevPage(); break;
        case 'next-page':        nextPage(); break;
        case 'toggle-show-all':  toggleShowAll(); break;
        case 'toggle-theme':     toggleTheme(); break;
        case 'toggle-errors':    toggleErrorSection(); break;
        case 'reset-selections': resetSelections(); break;
        case 'toggle-export-menu':  toggleExportMenu(); break;
        case 'export-txt':          exportTxt(); break;
        case 'export-csv':          exportCsv(); break;
        case 'export-json':         exportJson(); break;
        case 'export-html':         exportHtml(); break;
        case 'toggle-quickops-menu':toggleQuickOpsMenu(); break;
        case 'quickop-convert-jpg': quickOpConvertJpg(); break;
        case 'quickop-move':        quickOpMove(); break;
        case 'show-delete-modal':showDeleteModal(); break;
        case 'hide-delete-modal':hideDeleteModal(); break;
        case 'execute-delete':   executeDelete(); break;
        case 'open-rename-modal':       openRenameModal(); break;
        case 'hide-rename-modal':       hideRenameModal(); break;
        case 'execute-rename':          executeRename(); break;
        case 'lightbox-delete-current': showDeleteModal(lightboxImages[lightboxIndex]); break;
        case 'cleanup-cache':    cleanupCache(); break;
        case 'clear-cache':      clearCache(); break;
        case 'hide-cache-modal': hideCacheModal(); break;
        case 'close-lightbox':       closeLightbox(); break;
        case 'lightbox-prev':        lightboxPrev(e); break;
        case 'lightbox-next':        lightboxNext(e); break;
        case 'edit-rotate-left':     editorRotateLeft(); break;
        case 'edit-rotate-right':    editorRotateRight(); break;
        case 'edit-flip-h':          editorFlipH(); break;
        case 'edit-flip-v':          editorFlipV(); break;
        case 'edit-save':            editorSave(); break;
        case 'edit-discard':         editorDiscard(); break;
        case 'edit-crop':           editorEnterCropMode(); break;
        case 'edit-crop-apply':     editorApplyCrop(); break;
        case 'edit-crop-cancel':    editorExitCropMode(); break;
        case 'edit-undo':           editorUndo(); break;
        case 'edit-redo':           editorRedo(); break;
        case 'close-compare-slider': closeCompareSlider(); break;
        case 'undo':             undoAction(); break;
        case 'loadDatesFolders': _loadChildFolders('dates'); break;
        case 'autoFillDates':    _autoFillMonthly('dates'); break;
        case 'confirm-autofill': _applyAutoFill(); break;
        case 'hide-autofill-modal': _hideAutoFillModal(); break;
        case 'add-scan-folder':    _addScanFolderRow(); break;
        case 'remove-scan-folder': _removeScanFolderRow(btn); break;
        case 'copy-error-path': {
            const path = btn.dataset.path;
            if (path) copyErrorPath(btn, path);
            break;
        }
    }
});

// Keyboard activation for role="button" elements
document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const el = e.target;
    if (el.getAttribute('role') === 'button' && el.tagName !== 'BUTTON') {
        e.preventDefault();
        el.click();
    }
});

// Input event delegation
document.addEventListener('input', e => {
    const t = e.target;
    if (t.id === 'threshold')    { updateThresholdDisplay(); return; }
    if (t.id === 'workers')      { updateWorkersDisplay(); return; }
    if (t.id === 'searchFilter') { debounce('search', applyFilters, 250); return; }
    if (t.dataset.output) {
        const out = document.getElementById(t.dataset.output);
        if (out) out.textContent = t.value;
    }
});

// Change event delegation
document.addEventListener('change', e => {
    const t = e.target;
    if (t.dataset.detection) { toggleDetectionMode(t.dataset.detection); return; }
    if (t.id === 'filterType' || t.id === 'sortBy') { applyFilters(); return; }
    if (t.id === 'strategySelect')  { applyStrategy(); return; }
    if (t.dataset.change === 'toggle-pipe-trash') { togglePipeTrashDir(); return; }
    if (t.dataset.change === 'datesPerFolder') { _togglePerFolderMode('dates'); return; }
});

log.info('init', 'app.js fully parsed — all event listeners registered');
