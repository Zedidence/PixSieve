// Shared helper for loading the real pixsieve/static/js/app.js into a jsdom
// window so its functions can be unit-tested directly, without needing to
// duplicate any of its logic into a separate testable copy (which would risk
// silently drifting from the real file).
//
// app.js is a classic (non-module) browser script full of top-level
// `document.addEventListener(...)` registrations and a couple of
// unconditional top-level constructs (e.g. `new IntersectionObserver(...)`)
// that don't exist in jsdom by default -- those are polyfilled below as
// minimal no-ops purely so the file evaluates without throwing. Nothing here
// simulates real user interaction; each test calls the specific function it
// needs directly via `window.<name>`.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_JS_PATH = path.resolve(__dirname, '../../pixsieve/static/js/app.js');

const INDEX_HTML_PATH = path.resolve(__dirname, '../../pixsieve/templates/index.html');

// Real production markup, not a hand-picked fixture: Jinja2 {{ }}/{% %} tags
// only appear as text content in this template (never inside a structural
// tag/attribute), so the HTML parser treats them as inert text and every
// real element id/class app.js expects to find is present. Using the actual
// template means the harness doesn't silently drift from what's shipped, and
// doesn't need updating every time a new element gets referenced.
// Returns a Promise resolving to the window once app.js's own
// DOMContentLoaded handler (which does real init: theme, backdrop-close
// wiring, etc.) has actually run. That handler doesn't execute synchronously
// during window.eval() -- jsdom always dispatches DOMContentLoaded as a
// separate task, per spec, even though eval() registers the listener
// synchronously -- so anything depending on init-time wiring (e.g.
// _wireModalBackdropClose()) must await this instead of using the window
// immediately after a synchronous load.
export async function loadApp(html) {
  if (html === undefined) {
    html = readFileSync(INDEX_HTML_PATH, 'utf-8');
  }
  // 'dangerously' (not 'outside-only') is required for inline onclick=/
  // ondblclick= HTML attribute handlers -- which the dynamically-generated
  // duplicate-card markup relies on -- to actually be wired up; without it
  // they're inert and el.click() silently does nothing. This does NOT
  // execute the real <script src="..."> tags in index.html (jsdom only
  // fetches external script resources with an explicit `resources` loader,
  // which isn't configured here) -- only truly inline <script> content
  // (index.html has one, registering the service worker, which is a no-op
  // since jsdom has no navigator.serviceWorker).
  const dom = new JSDOM(html, { url: 'http://localhost/', runScripts: 'dangerously' });
  const { window } = dom;

  // Minimal no-op polyfills for browser APIs jsdom doesn't implement, that
  // app.js touches unconditionally at parse time (not inside a function).
  window.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  // initTheme() (run on DOMContentLoaded, which jsdom fires automatically
  // once the document is ready) calls matchMedia to detect the OS color
  // scheme preference; jsdom doesn't implement it.
  window.matchMedia = window.matchMedia || (() => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  // Several DOMContentLoaded-time init calls (cache stats, recovery check)
  // fetch from the server; jsdom has no network stack. Reject so app.js's
  // existing .catch() handlers run instead of leaving unhandled rejections.
  window.fetch = window.fetch || (() => Promise.reject(new Error('fetch not available in tests')));
  // showToast() (and others) use requestAnimationFrame purely to defer a
  // class toggle by one frame for a CSS transition; jsdom doesn't implement
  // it. A macrotask is a close enough stand-in for test purposes.
  window.requestAnimationFrame = window.requestAnimationFrame || (cb => setTimeout(cb, 0));

  // Attached before eval() runs, so it's already listening by the time
  // app.js's own DOMContentLoaded handler (registered during eval) fires;
  // listeners run in registration order, so this always resolves after
  // app.js's init has finished.
  const ready = new Promise(resolve => {
    window.addEventListener('DOMContentLoaded', resolve, { once: true });
  });

  const source = readFileSync(APP_JS_PATH, 'utf-8');
  window.eval(source);

  await ready;
  return window;
}
