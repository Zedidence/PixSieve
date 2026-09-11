import { describe, it, expect } from 'vitest';
import { loadApp } from './load-app.js';

function clickOn(window, el) {
  const event = new window.MouseEvent('click', { bubbles: true, cancelable: true });
  el.dispatchEvent(event);
}

describe('modal backdrop click-to-close', () => {
  it('clicking the deleteModal overlay itself closes it', async () => {
    const window = await loadApp();
    const { document } = window;
    const modal = document.getElementById('deleteModal');
    modal.classList.add('active');

    clickOn(window, modal);

    expect(modal.classList.contains('active')).toBe(false);
  });

  it('clicking inside the modal card does NOT close it', async () => {
    const window = await loadApp();
    const { document } = window;
    const modal = document.getElementById('deleteModal');
    modal.classList.add('active');

    const inner = modal.querySelector('.modal');
    expect(inner).not.toBeNull();
    clickOn(window, inner);

    expect(modal.classList.contains('active')).toBe(true);
  });

  it('respects the delete-in-flight guard (does not close mid-request)', async () => {
    const window = await loadApp();
    const { document } = window;
    // Never resolves, so executeDelete()'s fetch stays pending and
    // _deleteInFlight (set synchronously before the fetch call) stays true
    // for the duration of this test.
    window.fetch = () => new Promise(() => {});
    document.getElementById('trashDir').value = '/trash';

    window.showDeleteModal('/some/file.jpg');
    window.executeDelete();
    const modal = document.getElementById('deleteModal');
    expect(modal.classList.contains('active')).toBe(true);

    clickOn(window, modal);

    expect(modal.classList.contains('active')).toBe(true);
  });

  it('clicking the lightbox backdrop closes it', async () => {
    const window = await loadApp();
    const { document } = window;
    window.groups = [{ id: 1, images: [{ path: '/a.jpg', filename: 'a.jpg', media_type: 'image' }] }];
    window.openLightbox(1, 0);

    const lightbox = document.getElementById('lightbox');
    expect(lightbox.classList.contains('active')).toBe(true);

    clickOn(window, lightbox);

    expect(lightbox.classList.contains('active')).toBe(false);
  });

  it('clicking the cacheModal overlay itself closes it', async () => {
    const window = await loadApp();
    const { document } = window;
    const modal = document.getElementById('cacheModal');
    modal.classList.add('active');

    clickOn(window, modal);

    expect(modal.classList.contains('active')).toBe(false);
  });
});
