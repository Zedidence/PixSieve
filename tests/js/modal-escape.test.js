import { describe, it, expect } from 'vitest';
import { loadApp } from './load-app.js';

describe('_resolveEscapeAction', () => {
  const base = {
    cacheModalActive: false,
    deleteModalActive: false,
    renameModalActive: false,
    autoFillModalActive: false,
  };

  it('dismisses the cache modal when it is the one open', async () => {
    const window = await loadApp();
    const action = window._resolveEscapeAction({ ...base, cacheModalActive: true });
    expect(action).toBe('cache');
  });

  it('dismisses delete/rename even when the cache modal flag is also set', async () => {
    // Regression case for the original bug: previously hideCacheModal() ran
    // unconditionally first, tearing down the delete modal's focus trap via
    // the single shared _focusTrapCleanup slot even though the cache modal
    // itself wasn't the modal actually open on screen. The fixed dispatcher
    // must not let a stray/incorrect cacheModalActive=true silently steal
    // priority away from a modal that is genuinely stacked on the lightbox.
    const window = await loadApp();
    const action = window._resolveEscapeAction({
      ...base,
      deleteModalActive: true,
    });
    expect(action).toBe('delete-rename');
  });

  it('dismisses the rename modal', async () => {
    const window = await loadApp();
    const action = window._resolveEscapeAction({ ...base, renameModalActive: true });
    expect(action).toBe('delete-rename');
  });

  it('dismisses the auto-fill modal when nothing else is open', async () => {
    const window = await loadApp();
    const action = window._resolveEscapeAction({ ...base, autoFillModalActive: true });
    expect(action).toBe('autofill');
  });

  it('falls through to the lightbox/compare-slider when nothing else is open', async () => {
    const window = await loadApp();
    const action = window._resolveEscapeAction({ ...base });
    expect(action).toBe('lightbox');
  });

  it('prioritizes delete/rename over auto-fill if both were somehow active', async () => {
    const window = await loadApp();
    const action = window._resolveEscapeAction({
      ...base,
      renameModalActive: true,
      autoFillModalActive: true,
    });
    expect(action).toBe('delete-rename');
  });
});
