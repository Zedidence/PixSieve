import { describe, it, expect, vi } from 'vitest';
import { loadApp } from './load-app.js';

// A fetch stub that never resolves, so the calling function's .finally()
// never runs during the test -- lets us assert the in-flight state and
// disabled button while a "request" is still pending.
function pendingFetch() {
  return new Promise(() => {});
}

describe('client-side double-submit guards', () => {
  it('clearCache disables its button and ignores a second click while pending', async () => {
    const window = await loadApp();
    const { document } = window;
    window.fetch = vi.fn(pendingFetch);
    window.confirm = () => true;

    window.clearCache();
    const btn = document.querySelector('[data-action="clear-cache"]');
    expect(btn.disabled).toBe(true);

    window.clearCache();
    expect(window.fetch).toHaveBeenCalledTimes(1);
  });

  it('cleanupCache and clearCache share one in-flight guard', async () => {
    const window = await loadApp();
    const { document } = window;
    window.fetch = vi.fn(pendingFetch);
    window.confirm = () => true;

    window.clearCache();
    window.cleanupCache();

    expect(window.fetch).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-action="cleanup-cache"]').disabled).toBe(false);
  });

  it('quickOpConvertJpg disables its button and ignores a second click while pending', async () => {
    const window = await loadApp();
    const { document } = window;
    window.fetch = vi.fn(pendingFetch);
    window.confirm = () => true;
    window.selections = { '/a.jpg': 'delete' };

    window.quickOpConvertJpg();
    const btn = document.querySelector('[data-action="quickop-convert-jpg"]');
    expect(btn.disabled).toBe(true);

    window.quickOpConvertJpg();
    expect(window.fetch).toHaveBeenCalledTimes(1);
  });

  it('quickOpMove disables its button and ignores a second click while pending', async () => {
    const window = await loadApp();
    const { document } = window;
    window.fetch = vi.fn(pendingFetch);
    window.prompt = () => '/dest';
    window.selections = { '/a.jpg': 'delete' };

    window.quickOpMove();
    const btn = document.querySelector('[data-action="quickop-move"]');
    expect(btn.disabled).toBe(true);

    window.quickOpMove();
    expect(window.fetch).toHaveBeenCalledTimes(1);
  });

  it('applyStrategy disables the select and ignores a second call while pending', async () => {
    const window = await loadApp();
    const { document } = window;
    window.fetch = vi.fn(pendingFetch);

    window.applyStrategy();
    const select = document.getElementById('strategySelect');
    expect(select.disabled).toBe(true);

    window.applyStrategy();
    expect(window.fetch).toHaveBeenCalledTimes(1);
  });
});
