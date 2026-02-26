// =============================================================
// PixSieve — Frontend Application
// =============================================================

// =============================================================
// State
// =============================================================
let scanInterval = null;
let groups = [];
let filteredGroups = [];
let selections = {};
let undoStack = [];
let directoryHistory = [];
let errorImages = [];
let currentPage = 1;
const PAGE_SIZE = 10;
let lightboxImages = [];
let lightboxIndex = 0;
let lastPollTime = Date.now();
let connectionLost = false;
let scanDirectory = '';
let isScanning = false;
let isPaused = false;
let stageStartTimes = {};

// =============================================================
// Initialization
// =============================================================
document.addEventListener('DOMContentLoaded', () => {
    checkForRecovery();
    loadDirectoryHistory();
    startConnectionMonitor();
    updateThresholdDisplay();
    updateWorkersDisplay();
});

function checkForRecovery() {
    loadCacheStats();

    fetch('/api/status')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'complete' && data.has_results) {
                let info = `Found ${data.group_count} duplicate groups from: ${data.directory}`;
                if (data.error_count > 0) {
                    info += ` (${data.error_count} files had errors)`;
                }
                document.getElementById('recoveryBanner').classList.add('active');
                document.getElementById('recoveryInfo').textContent = info;

                if (data.auto_disabled_perceptual) {
                    showPerceptualWarning(data.directory);
                }
            } else if (['scanning', 'analyzing', 'comparing'].includes(data.status)) {
                isScanning = true;
                showProgressSection();
                startProgressPolling();
            }
        });
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
        });
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
// =============================================================
function startConnectionMonitor() {
    setInterval(() => {
        fetch('/api/ping')
            .then(r => {
                if (r.ok) {
                    if (connectionLost) {
                        connectionLost = false;
                        updateConnectionStatus(true);
                    }
                    lastPollTime = Date.now();
                }
            })
            .catch(() => {
                connectionLost = true;
                updateConnectionStatus(false);
            });
    }, 5000);
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
function showHistory() {
    renderHistory(directoryHistory);
}

function filterHistory() {
    const input = document.getElementById('directory').value.toLowerCase();
    const filtered = directoryHistory.filter(d => d.toLowerCase().includes(input));
    renderHistory(filtered);
}

function renderHistory(dirs) {
    const list = document.getElementById('historyList');
    if (dirs.length === 0) {
        list.classList.remove('active');
        return;
    }
    list.innerHTML = dirs.map(d =>
        `<div class="autocomplete-item" onclick="selectDirectory('${escapeJs(d)}')">${escapeHtml(d)}</div>`
    ).join('');
    list.classList.add('active');
}

function selectDirectory(dir) {
    document.getElementById('directory').value = dir;
    document.getElementById('historyList').classList.remove('active');
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('.form-group')) {
        document.getElementById('historyList').classList.remove('active');
    }
});

// =============================================================
// Scanning
// =============================================================
function startScan() {
    const directory = document.getElementById('directory').value.trim();
    if (!directory) {
        alert('Please enter a directory path');
        return;
    }

    const threshold = parseInt(document.getElementById('threshold').value) || 10;
    const exactOnly = document.getElementById('exactOnly').checked;
    const perceptualOnly = document.getElementById('perceptualOnly').checked;
    const recursive = document.getElementById('recursive').checked;
    const useCache = document.getElementById('useCache').checked;
    const workers = parseInt(document.getElementById('workers').value) || 4;
    const autoSelectStrategy = document.getElementById('autoSelectStrategy').value;

    let useLsh = null;
    const lshMode = document.querySelector('input[name="lshMode"]:checked').value;
    if (lshMode === 'on') useLsh = true;
    else if (lshMode === 'off') useLsh = false;

    scanDirectory = directory;
    hidePerceptualWarning();

    fetch('/api/scan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            directory, threshold, exactOnly, perceptualOnly,
            recursive, useCache, useLsh, workers, autoSelectStrategy,
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }
        isScanning = true;
        isPaused = false;
        stageStartTimes = {};
        showProgressSection();
        startProgressPolling();
    })
    .catch(err => {
        alert('Failed to start scan: ' + err);
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
    document.getElementById('scanBtn').disabled = false;
    document.getElementById('progressSection').classList.remove('active');
}

function startProgressPolling() {
    if (scanInterval) clearInterval(scanInterval);
    scanInterval = setInterval(checkProgress, 500);
}

function checkProgress() {
    fetch('/api/status')
        .then(r => r.json())
        .then(data => {
            updateProgressUI(data);

            if (data.status === 'complete' || data.status === 'error' || data.status === 'cancelled') {
                clearInterval(scanInterval);
                scanInterval = null;
                isScanning = false;

                if (data.status === 'complete') {
                    if (data.auto_disabled_perceptual) {
                        showPerceptualWarning(scanDirectory);
                    }
                    loadResults();
                } else if (data.status === 'cancelled') {
                    hideProgressSection();
                    alert('Scan cancelled');
                } else {
                    hideProgressSection();
                    alert('Scan error: ' + data.message);
                }
            }

            isPaused = data.paused;
            if (isPaused) {
                document.getElementById('pauseBtn').style.display = 'none';
                document.getElementById('resumeBtn').style.display = 'inline-block';
            } else {
                document.getElementById('pauseBtn').style.display = 'inline-block';
                document.getElementById('resumeBtn').style.display = 'none';
            }
        })
        .catch(() => {});
}

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
    fetch('/api/groups')
        .then(r => r.json())
        .then(data => {
            groups = data.groups;
            selections = data.selections || {};
            errorImages = data.error_images || [];

            if (Object.keys(selections).length === 0) {
                groups.forEach(g => {
                    g.images.forEach((img, idx) => {
                        selections[img.path] = idx === 0 ? 'keep' : 'delete';
                    });
                });
            }

            if (data.directory && !directoryHistory.includes(data.directory)) {
                directoryHistory.unshift(data.directory);
                directoryHistory = directoryHistory.slice(0, 10);
            }

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
        });
}

function newScan() {
    if (confirm('Start a new scan? Current results will be cleared.')) {
        fetch('/api/clear', { method: 'POST' }).then(() => {
            groups = [];
            selections = {};
            errorImages = [];
            hidePerceptualWarning();
            document.getElementById('groupsContainer').classList.remove('active');
            document.getElementById('statsBar').classList.remove('active');
            document.getElementById('actionBar').classList.remove('active');
            document.getElementById('filterBar').classList.remove('active');
            document.getElementById('cacheBanner').classList.remove('active');
            document.getElementById('errorSection').classList.remove('active');
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
            <div class="error-image-path">${escapeHtml(img.path)}</div>
            <div class="error-image-error">${escapeHtml(img.error || 'Unknown error')}</div>
        </div>
    `).join('');
}

function toggleErrorSection() {
    const section = document.getElementById('errorSection');
    section.classList.toggle('expanded');
}

// =============================================================
// Filtering & Pagination
// =============================================================
function applyFilters() {
    const typeFilter = document.getElementById('filterType').value;
    const sortBy = document.getElementById('sortBy').value;
    const search = document.getElementById('searchFilter').value.toLowerCase();

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

function applyStrategy() {
    const strategy = document.getElementById('strategySelect').value;

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
    });
}

function updatePagination() {
    const totalPages = Math.ceil(filteredGroups.length / PAGE_SIZE) || 1;
    document.getElementById('pageInfo').textContent = `Page ${currentPage} of ${totalPages}`;
    document.getElementById('prevBtn').disabled = currentPage <= 1;
    document.getElementById('nextBtn').disabled = currentPage >= totalPages;
}

function prevPage() {
    if (currentPage > 1) {
        currentPage--;
        renderGroups();
        updatePagination();
        window.scrollTo(0, 0);
    }
}

function nextPage() {
    const totalPages = Math.ceil(filteredGroups.length / PAGE_SIZE);
    if (currentPage < totalPages) {
        currentPage++;
        renderGroups();
        updatePagination();
        window.scrollTo(0, 0);
    }
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
    groupEl.querySelectorAll('img[data-src]').forEach(img => {
        img.src = img.dataset.src;
        img.removeAttribute('data-src');
    });
}

// =============================================================
// Rendering
// =============================================================
function renderGroups() {
    const container = document.getElementById('groupsContainer');

    if (filteredGroups.length === 0) {
        container.innerHTML = `
            <div class="no-results">
                <h2>✨ No Duplicates Found!</h2>
                <p>Your image collection is clean, or no matches for current filters.</p>
            </div>
        `;
        return;
    }

    const start = (currentPage - 1) * PAGE_SIZE;
    const pageGroups = filteredGroups.slice(start, start + PAGE_SIZE);

    // Build HTML with data-src for lazy loading (no src= on images)
    container.innerHTML = pageGroups.map((group) => `
        <div class="group-card" data-group-id="${group.id}">
            <div class="group-header">
                <div class="group-title">
                    Group #${group.id}
                    <span style="color: var(--text-muted); font-weight: normal;">(${group.image_count} images)</span>
                </div>
                <div>
                    <span class="group-badge ${group.match_type}">${group.match_type}</span>
                    <span style="margin-left: 10px; color: var(--text-muted);">Save ${group.potential_savings_formatted}</span>
                </div>
            </div>
            <div class="group-images">
                ${group.images.map((img, idx) => `
                    <div class="image-card ${selections[img.path] === 'keep' ? 'selected' : 'to-delete'}"
                         data-path="${escapeHtml(img.path)}"
                         onclick="toggleSelection('${escapeJs(img.path)}', ${group.id})">
                        <div class="image-wrapper">
                            <img class="image-preview"
                                 data-src="/api/image?path=${encodeURIComponent(img.path)}"
                                 alt="${escapeHtml(img.filename)}"
                                 loading="lazy"
                                 onerror="this.alt='Failed to load'"
                                 ondblclick="event.stopPropagation(); openLightbox(${group.id}, ${idx})">
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
        </div>
    `).join('');

    // Observe each group card for lazy image injection
    container.querySelectorAll('.group-card').forEach(card => {
        _groupImageObserver.observe(card);
    });
}

// =============================================================
// Selection Management
// =============================================================
function toggleSelection(path, groupId) {
    const group = groups.find(g => g.id === groupId);
    if (!group) return;

    const prevSelections = {...selections};
    undoStack.push({ type: 'selection', data: prevSelections });
    if (undoStack.length > 50) undoStack.shift();

    if (selections[path] === 'delete') {
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

    groups.forEach(g => {
        g.images.forEach((img, idx) => {
            selections[img.path] = idx === 0 ? 'keep' : 'delete';
        });
    });

    saveSelections();
    renderGroups();
    updateStats();
}

function updateStats() {
    const totalGroups = groups.length;
    const totalDupes = groups.reduce((sum, g) => sum + g.images.length - 1, 0);

    let selectedCount = 0;
    let selectedSize = 0;

    Object.entries(selections).forEach(([path, status]) => {
        if (status === 'delete') {
            selectedCount++;
            for (const g of groups) {
                const img = g.images.find(i => i.path === path);
                if (img) {
                    selectedSize += img.file_size;
                    break;
                }
            }
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
function exportSelections() {
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

    const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'duplicate_report.txt';
    a.click();
    URL.revokeObjectURL(url);
}

// =============================================================
// Delete Modal
// =============================================================
function showDeleteModal() {
    const count = Object.values(selections).filter(s => s === 'delete').length;
    if (count === 0) {
        alert('No files selected for removal');
        return;
    }
    document.getElementById('deleteModalText').textContent =
        `Are you sure you want to move ${count} files to the trash folder?`;

    const preset = document.getElementById('trashDirPreset').value.trim();
    const scanDir = document.getElementById('directory').value.trim();

    if (preset) {
        document.getElementById('trashDir').value = preset;
    } else if (scanDir && !document.getElementById('trashDir').value) {
        document.getElementById('trashDir').value = scanDir + '_duplicates_trash';
    }

    document.getElementById('deleteModal').classList.add('active');
}

function hideDeleteModal() {
    document.getElementById('deleteModal').classList.remove('active');
}

function executeDelete() {
    const trashDir = document.getElementById('trashDir').value.trim();
    if (!trashDir) {
        alert('Please enter a trash directory');
        return;
    }

    const filesToDelete = Object.entries(selections)
        .filter(([path, status]) => status === 'delete')
        .map(([path]) => path);

    fetch('/api/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({files: filesToDelete, trashDir})
    })
    .then(r => r.json())
    .then(data => {
        hideDeleteModal();
        alert(`Moved ${data.moved} files to trash.\n${data.errors} errors.`);

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
    });
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
    document.getElementById('lightbox').classList.add('active');
}

function updateLightboxImage() {
    const path = lightboxImages[lightboxIndex];
    document.getElementById('lightboxImg').src = '/api/image?path=' + encodeURIComponent(path);
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
}

// =============================================================
// Keyboard Navigation
// =============================================================
document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT') return;

    if (e.key === 'Escape') {
        closeLightbox();
        hideDeleteModal();
        hideCacheModal();
        return;
    }

    if (document.getElementById('lightbox').classList.contains('active')) {
        if (e.key === 'ArrowLeft') lightboxPrev(e);
        if (e.key === 'ArrowRight') lightboxNext(e);
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
    document.getElementById('cacheModal').classList.add('active');
}

function hideCacheModal() {
    document.getElementById('cacheModal').classList.remove('active');
}

function clearCache() {
    if (!confirm('Clear all cached analysis data? Next scan will re-analyze all images.')) {
        return;
    }

    fetch('/api/cache/clear', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            alert('Cache cleared successfully.');
            loadCacheStats();
            document.getElementById('cacheBanner').classList.remove('active');
        })
        .catch(() => alert('Failed to clear cache.'));
}

function cleanupCache() {
    fetch('/api/cache/cleanup', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ max_age_days: 30 })
    })
        .then(r => r.json())
        .then(data => {
            alert(`Cleanup complete:\n- Removed ${data.missing_removed} entries for deleted files\n- Removed ${data.stale_removed} stale entries`);
            loadCacheStats();
        })
        .catch(() => alert('Failed to cleanup cache.'));
}

// =============================================================
// Utilities
// =============================================================
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
    return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

// =============================================================
// Tab Navigation
// =============================================================
function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));

    document.getElementById('tab-' + tabName).classList.add('active');
    const buttons = document.querySelectorAll('.tab-btn');
    if (tabName === 'duplicates') buttons[0].classList.add('active');
    else buttons[1].classList.add('active');
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

function selectOperation(opName) {
    currentOperation = opName;

    document.querySelectorAll('.ops-sidebar-item').forEach(item => item.classList.remove('active'));
    event.currentTarget.classList.add('active');

    document.querySelectorAll('.ops-form').forEach(f => f.classList.remove('active'));
    const form = document.getElementById('ops-' + opName);
    if (form) form.classList.add('active');

    document.getElementById('opsResult').classList.remove('active');
    document.getElementById('opsRunning').classList.remove('active');
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
            break;
        }
        case 'sort-resolution': {
            payload.copyFiles = document.getElementById('resCopy').checked;
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
        case 'randomize-exif': {
            payload.startDate = document.getElementById('exifStartDate').value;
            payload.endDate = document.getElementById('exifEndDate').value;
            payload.recursive = document.getElementById('exifRecursive').checked;
            break;
        }
        case 'randomize-dates': {
            payload.startDate = document.getElementById('datesStartDate').value;
            payload.endDate = document.getElementById('datesEndDate').value;
            payload.recursive = document.getElementById('datesRecursive').checked;
            break;
        }
        case 'cleanup':
            break;
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
        'randomize-exif':  '/api/operations/metadata/randomize-exif',
        'randomize-dates': '/api/operations/metadata/randomize-dates',
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
    const payload = buildOperationPayload(opName);

    if (!payload.directory) {
        alert('Please enter a target directory.');
        return;
    }
    if (opName === 'move' && !payload.destination) {
        alert('Please enter a destination directory.');
        return;
    }
    if (opName === 'repair' && !payload.trashFolder) {
        alert('Please enter a trash folder path.');
        return;
    }
    if (opName === 'pipeline' && (!payload.steps || payload.steps.length === 0)) {
        alert('Please select at least one pipeline step.');
        return;
    }
    if (!payload.dryRun) {
        if (!confirm('Dry Run is OFF. Files WILL be modified. Continue?')) {
            return;
        }
    }

    const endpoint = getEndpointForOperation(opName);

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
            const statusColors = {
                repaired: '#4caf50', quarantined: '#ff9800',
                permission_error: '#f44336', skipped: '#888', error: '#f44336'
            };
            let rows = result.problems.slice(0, 100).map(p => {
                const color = statusColors[p.status] || '#aaa';
                return `<tr style="border-bottom:1px solid var(--border-subtle);">
                    <td style="padding:4px 8px;"><span style="color:${color};font-weight:600;">${escapeHtml(p.status)}</span></td>
                    <td style="padding:4px 8px;color:#aaa;">${escapeHtml(p.corruption_type)}</td>
                    <td style="padding:4px 8px;word-break:break-all;">${escapeHtml(p.path)}</td>
                    <td style="padding:4px 8px;color:#888;font-size:0.8em;">${escapeHtml(p.error || '')}</td>
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
