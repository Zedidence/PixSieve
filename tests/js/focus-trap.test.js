import { describe, it, expect } from 'vitest';
import { loadApp } from './load-app.js';

function dispatchTab(window, target, { shift = false } = {}) {
  const event = new window.KeyboardEvent('keydown', {
    key: 'Tab',
    shiftKey: shift,
    bubbles: true,
    cancelable: true,
  });
  target.dispatchEvent(event);
  return event;
}

describe('trapFocus / releaseFocusTrap', () => {
  it('focuses the first focusable element and wraps Tab from last back to first', async () => {
    const window = await loadApp();
    const { document } = window;

    const container = document.createElement('div');
    container.innerHTML = '<button id="a">a</button><button id="b">b</button><button id="c">c</button>';
    document.body.appendChild(container);

    window.trapFocus(container);
    expect(document.activeElement.id).toBe('a');

    document.getElementById('c').focus();
    const event = dispatchTab(window, container);
    expect(document.activeElement.id).toBe('a');
    expect(event.defaultPrevented).toBe(true);
  });

  it('wraps Shift+Tab from first back to last', async () => {
    const window = await loadApp();
    const { document } = window;

    const container = document.createElement('div');
    container.innerHTML = '<button id="a">a</button><button id="b">b</button><button id="c">c</button>';
    document.body.appendChild(container);

    window.trapFocus(container);
    document.getElementById('a').focus();
    const event = dispatchTab(window, container, { shift: true });
    expect(document.activeElement.id).toBe('c');
    expect(event.defaultPrevented).toBe(true);
  });

  it('stops trapping once released', async () => {
    const window = await loadApp();
    const { document } = window;

    const container = document.createElement('div');
    container.innerHTML = '<button id="a">a</button><button id="b">b</button>';
    document.body.appendChild(container);

    window.trapFocus(container);
    window.releaseFocusTrap();

    document.getElementById('b').focus();
    const event = dispatchTab(window, container);
    // No handler left listening -- nothing should call preventDefault, and
    // focus should stay wherever it was (jsdom doesn't simulate the
    // browser's native Tab traversal, so "no wrap" is the only observable
    // signal that the trap's own keydown listener was actually removed).
    expect(event.defaultPrevented).toBe(false);
    expect(document.activeElement.id).toBe('b');
  });
});

describe('lightbox focus trap wiring', () => {
  it('openLightbox traps focus inside #lightbox; closeLightbox releases it', async () => {
    const window = await loadApp();
    const { document } = window;

    window.groups = [
      {
        id: 1,
        images: [
          { path: '/a.jpg', filename: 'a.jpg', media_type: 'image' },
          { path: '/b.jpg', filename: 'b.jpg', media_type: 'image' },
        ],
      },
    ];

    window.openLightbox(1, 0);

    const lightbox = document.getElementById('lightbox');
    expect(lightbox.classList.contains('active')).toBe(true);
    expect(lightbox.contains(document.activeElement)).toBe(true);

    // Focus the last focusable element in the lightbox and confirm Tab wraps
    // back to the first -- proves trapFocus() was actually installed, not
    // just that some element happened to be focused.
    const focusable = lightbox.querySelectorAll(
      'button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    expect(focusable.length).toBeGreaterThan(0);
    focusable[focusable.length - 1].focus();
    const event = dispatchTab(window, lightbox);
    expect(document.activeElement).toBe(focusable[0]);
    expect(event.defaultPrevented).toBe(true);

    window.closeLightbox();
    expect(lightbox.classList.contains('active')).toBe(false);

    // After release, the same Tab dispatch on the (now inactive) lightbox
    // must no longer be intercepted.
    focusable[focusable.length - 1].focus();
    const afterClose = dispatchTab(window, lightbox);
    expect(afterClose.defaultPrevented).toBe(false);
  });
});
