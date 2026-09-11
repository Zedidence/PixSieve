import { describe, it, expect, beforeEach } from 'vitest';
import { loadApp } from './load-app.js';

function makeGroup() {
  return {
    id: 1,
    image_count: 2,
    match_type: 'exact',
    potential_savings_formatted: '1 MB',
    images: [
      {
        path: '/a.jpg', filename: 'a.jpg', directory: '/', file_size_formatted: '1 KB',
        resolution: '10x10', megapixels: '0.0', format: 'JPEG', quality_score: 5,
        is_reference: false, media_type: 'image',
      },
      {
        path: '/b.jpg', filename: 'b.jpg', directory: '/', file_size_formatted: '1 KB',
        resolution: '10x10', megapixels: '0.0', format: 'JPEG', quality_score: 5,
        is_reference: false, media_type: 'image',
      },
    ],
  };
}

function dispatchKey(window, el, key) {
  const event = new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  el.dispatchEvent(event);
  return event;
}

// toggleSelection() only actually flips anything when the clicked image is
// currently marked 'delete' (the non-reference branch is a no-op otherwise
// -- see app.js), and it calls saveSelections() -> fetch() with no .catch(),
// so every test needs a realistic starting selection and a resolving fetch
// stub to avoid an unhandled rejection.
async function setUp() {
  const window = await loadApp();
  window.fetch = () => Promise.resolve({ json: async () => ({}) });
  const group = makeGroup();
  window.groups = [group];
  window.selections = { '/a.jpg': 'keep', '/b.jpg': 'delete' };
  return { window, document: window.document, group };
}

describe('keyboard accessibility for keep/delete cards', () => {
  it('grid image-card is a tabindex-0 role=button element activated by Enter', async () => {
    const { window, document, group } = await setUp();
    document.body.insertAdjacentHTML('beforeend', window._buildGroupImagesGridHtml(group));
    const card = document.querySelector('.image-card[data-path="/b.jpg"]');
    expect(card.getAttribute('tabindex')).toBe('0');
    expect(card.getAttribute('role')).toBe('button');

    card.focus();
    dispatchKey(window, card, 'Enter');

    expect(window.selections['/b.jpg']).toBe('keep');
    expect(window.selections['/a.jpg']).toBe('delete');
  });

  it('grid image-card is also activated by Space', async () => {
    const { window, document, group } = await setUp();
    document.body.insertAdjacentHTML('beforeend', window._buildGroupImagesGridHtml(group));
    const card = document.querySelector('.image-card[data-path="/b.jpg"]');

    card.focus();
    dispatchKey(window, card, ' ');

    expect(window.selections['/b.jpg']).toBe('keep');
  });

  it('list-view image-row is keyboard-activatable', async () => {
    const { window, document, group } = await setUp();
    document.body.insertAdjacentHTML('beforeend', window._buildGroupImagesListHtml(group));
    const rows = document.querySelectorAll('.image-row');
    expect(rows.length).toBe(2);
    rows.forEach(row => {
      expect(row.getAttribute('tabindex')).toBe('0');
      expect(row.getAttribute('role')).toBe('button');
    });

    // Rows render in image order: index 0 is a.jpg (currently 'keep', a
    // no-op click), index 1 is b.jpg (currently 'delete', flips to 'keep').
    rows[1].focus();
    dispatchKey(window, rows[1], 'Enter');
    expect(window.selections['/b.jpg']).toBe('keep');
  });

  it('compare-view compare-card is keyboard-activatable', async () => {
    const { window, document, group } = await setUp();
    document.body.insertAdjacentHTML('beforeend', window._buildGroupImagesCompareHtml(group));
    const cards = document.querySelectorAll('.compare-card');
    expect(cards.length).toBe(2);
    cards.forEach(c => {
      expect(c.getAttribute('tabindex')).toBe('0');
      expect(c.getAttribute('role')).toBe('button');
    });

    cards[1].focus();
    dispatchKey(window, cards[1], 'Enter');
    expect(window.selections['/b.jpg']).toBe('keep');
  });

  it('does not activate on unrelated keys', async () => {
    const { window, document, group } = await setUp();
    document.body.insertAdjacentHTML('beforeend', window._buildGroupImagesGridHtml(group));
    const card = document.querySelector('.image-card[data-path="/b.jpg"]');

    card.focus();
    dispatchKey(window, card, 'a');

    expect(window.selections['/b.jpg']).toBe('delete');
  });
});
