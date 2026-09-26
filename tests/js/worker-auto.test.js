import { describe, it, expect, vi } from 'vitest';
import { loadApp } from './load-app.js';

// Drive-aware worker controls: the scan form's Auto toggle, empty-means-auto
// number fields for operations, and the "USB SSD → 4 workers" badge.

describe('worker Auto toggle', () => {
  it('starts in auto mode with the slider disabled', async () => {
    const window = await loadApp();
    const { document } = window;
    expect(document.getElementById('workersAuto').checked).toBe(true);
    expect(document.getElementById('workers').disabled).toBe(true);
    expect(document.getElementById('workersValue').textContent).toBe('Auto');
  });

  it('unchecking Auto enables the slider and shows its value', async () => {
    const window = await loadApp();
    const { document } = window;
    const auto = document.getElementById('workersAuto');
    auto.checked = false;
    auto.dispatchEvent(new window.Event('input', { bubbles: true }));

    expect(document.getElementById('workers').disabled).toBe(false);
    expect(document.getElementById('workersValue').textContent).toBe('4');
  });

  it('empty operation worker fields mean auto (null)', async () => {
    const window = await loadApp();
    const { document } = window;
    expect(window._optionalWorkers('renameWorkers')).toBeNull();
    document.getElementById('repairWorkers').value = '6';
    expect(window._optionalWorkers('repairWorkers')).toBe(6);
  });

  function _scanBody(window) {
    window.fetch = vi.fn(() => new Promise(() => {}));
    window.document.querySelector('#scanFolderList .scan-folder-row .pf-path').value = '/photos';
    window.startScan();
    const call = window.fetch.mock.calls.find(([url]) => url === '/api/scan');
    expect(call).toBeTruthy();
    return JSON.parse(call[1].body);
  }

  it('startScan sends workers: null in auto mode', async () => {
    const window = await loadApp();
    expect(_scanBody(window).workers).toBeNull();
  });

  it('startScan sends the slider value when Auto is off', async () => {
    const window = await loadApp();
    const { document } = window;
    document.getElementById('workersAuto').checked = false;
    document.getElementById('workers').value = '12';
    expect(_scanBody(window).workers).toBe(12);
  });
});

describe('storage badge', () => {
  it('shows the drive and worker count for automatic decisions', async () => {
    const window = await loadApp();
    const el = window.document.getElementById('opsStorageBadge');
    window.renderStorageBadge('opsStorageBadge', {
      workers: 2, source: 'auto', label: 'USB SSD', reason: 'USB SSD -> 2 workers (copy)',
    });
    expect(el.hidden).toBe(false);
    expect(el.textContent).toBe('USB SSD → 2 workers');
    expect(el.title).toBe('USB SSD -> 2 workers (copy)');
  });

  it('uses singular for one worker', async () => {
    const window = await loadApp();
    window.renderStorageBadge('scanStorageBadge', { workers: 1, source: 'probe', label: 'external HDD' });
    expect(window.document.getElementById('scanStorageBadge').textContent).toBe('external HDD → 1 worker');
  });

  it('labels manual and default counts', async () => {
    const window = await loadApp();
    const el = window.document.getElementById('opsStorageBadge');
    window.renderStorageBadge('opsStorageBadge', { workers: 8, source: 'user', label: 'NVMe SSD' });
    expect(el.textContent).toBe('8 workers (set manually)');
    window.renderStorageBadge('opsStorageBadge', { workers: 4, source: 'fallback', label: 'unknown drive' });
    expect(el.textContent).toBe('4 workers (default)');
  });

  it('hides when there is no decision', async () => {
    const window = await loadApp();
    const el = window.document.getElementById('opsStorageBadge');
    window.renderStorageBadge('opsStorageBadge', { workers: 2, source: 'auto', label: 'SD card' });
    window.renderStorageBadge('opsStorageBadge', null);
    expect(el.hidden).toBe(true);
    expect(el.textContent).toBe('');
  });

  it('never interprets the label as HTML', async () => {
    const window = await loadApp();
    const el = window.document.getElementById('opsStorageBadge');
    window.renderStorageBadge('opsStorageBadge', { workers: 2, source: 'auto', label: '<img src=x>' });
    expect(el.querySelector('img')).toBeNull();
  });
});
