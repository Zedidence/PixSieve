import { describe, it, expect } from 'vitest';
import { loadApp } from './load-app.js';

function clickOn(window, el) {
  el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

function dispatchKey(window, target, key) {
  target.dispatchEvent(new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

describe('lightbox / compare-slider alt text', () => {
  it('updateLightboxImage sets alt text to the filename', async () => {
    const window = await loadApp();
    const { document } = window;
    window.lightboxImages = ['/photos/vacation.jpg'];
    window.lightboxIndex = 0;

    window.updateLightboxImage();

    expect(document.getElementById('lightboxImg').alt).toBe('vacation.jpg');
  });

  it('openCompareSlider sets alt text on both images', async () => {
    const window = await loadApp();
    const { document } = window;
    window.groups = [{
      id: 1,
      images: [
        { path: '/a.jpg', filename: 'a.jpg', media_type: 'image' },
        { path: '/b.jpg', filename: 'b.jpg', media_type: 'image' },
      ],
    }];

    window.openCompareSlider(1);

    expect(document.getElementById('compareImgA').alt).toBe('a.jpg');
    expect(document.getElementById('compareImgB').alt).toBe('b.jpg');
  });
});

describe('toast live region', () => {
  it('showToast creates a container announced via role=status/aria-live', async () => {
    const window = await loadApp();
    const { document } = window;

    window.showToast('Something happened', 'info');

    const container = document.getElementById('toastContainer');
    expect(container.getAttribute('role')).toBe('status');
    expect(container.getAttribute('aria-live')).toBe('polite');
    expect(container.textContent).toContain('Something happened');
  });
});

describe('dropdown menu aria-expanded and dismissal', () => {
  it('toggleExportMenu syncs aria-expanded on its button', async () => {
    const window = await loadApp();
    const { document } = window;
    const btn = document.querySelector('[data-action="toggle-export-menu"]');
    expect(btn.getAttribute('aria-expanded')).toBe('false');

    window.toggleExportMenu();
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    expect(document.getElementById('exportMenu').classList.contains('open')).toBe(true);

    window.toggleExportMenu();
    expect(btn.getAttribute('aria-expanded')).toBe('false');
  });

  it('Escape closes an open dropdown menu', async () => {
    const window = await loadApp();
    const { document } = window;
    window.toggleQuickOpsMenu();
    expect(document.getElementById('quickOpsMenu').classList.contains('open')).toBe(true);

    dispatchKey(window, document, 'Escape');

    expect(document.getElementById('quickOpsMenu').classList.contains('open')).toBe(false);
    expect(
      document.querySelector('[data-action="toggle-quickops-menu"]').getAttribute('aria-expanded')
    ).toBe('false');
  });

  it('outside click closes only the clicked-outside-of menu, not the other one', async () => {
    const window = await loadApp();
    const { document } = window;
    window.toggleExportMenu();
    window.toggleQuickOpsMenu();
    expect(document.getElementById('exportMenu').classList.contains('open')).toBe(true);
    expect(document.getElementById('quickOpsMenu').classList.contains('open')).toBe(true);

    // Click inside the quick-ops dropdown's own wrapper -- must not close it,
    // and must not incorrectly close the unrelated export menu either.
    const quickOpsWrapper = document.getElementById('quickOpsMenu').parentElement;
    clickOn(window, quickOpsWrapper);

    expect(document.getElementById('quickOpsMenu').classList.contains('open')).toBe(true);
    expect(document.getElementById('exportMenu').classList.contains('open')).toBe(false);
  });

  it('clicking fully outside both dropdowns closes both', async () => {
    const window = await loadApp();
    const { document } = window;
    window.toggleExportMenu();
    window.toggleQuickOpsMenu();

    clickOn(window, document.body);

    expect(document.getElementById('exportMenu').classList.contains('open')).toBe(false);
    expect(document.getElementById('quickOpsMenu').classList.contains('open')).toBe(false);
  });
});
