# PixSieve — Modernization Roadmap

Progress: **18 / 20 complete**

---

## High Priority

### 1. Split the monolithic frontend files
`app.js` (~2,100 LOC) and `app.css` (~2,000 LOC) are monoliths. Consider:

- **JS:** Split into ES modules (`scan.js`, `operations.js`, `lightbox.js`, `state.js`, `ui.js`) and use a bundler (esbuild/Vite) or native `<script type="module">`
- **CSS:** Split into partials (`_variables.css`, `_layout.css`, `_progress.css`, `_cards.css`) and use CSS `@import` or a build step

> **Deferred** — requires adopting a build tool (esbuild/Vite). Splitting without a bundler creates load-order coupling that's hard to test. Recommended as a dedicated follow-up once a build step is in place.

### 2. Add WebSocket support (replace SSE)
SSE (`EventSource`) is one-directional and can't send client messages. WebSockets via Flask-SocketIO would enable:

- Bidirectional communication (pause/cancel without separate POST requests)
- Better reconnection handling
- Multiplexed channels (scan progress + operation progress on one connection)

> **Deferred** — Flask-SocketIO is a significant new dependency and requires replacing all SSE generators. Recommended alongside item 10 (Quart migration) since both affect the async I/O model.

### ~~3. Virtual scrolling for large result sets~~ ✅
> "Show All" mode now uses infinite scroll via IntersectionObserver — renders groups in chunks of 15 as the user scrolls, with a sentinel element triggering the next batch. Paginated mode unchanged. Prevents DOM bloat for large result sets.

### ~~4. Image thumbnail caching~~ ✅
> `/api/thumbnail` endpoint generates 300px JPEG thumbnails via Pillow, cached to disk keyed by path + mtime. Frontend previews use thumbnails; lightbox serves full-res.

---

## Medium Priority

### ~~5. Add result export formats~~ ✅
> Export dropdown with four formats: **TXT**, **CSV**, **JSON**, and **HTML** (standalone styled report with color-coded keep/delete rows).

### ~~6. Incremental/differential scanning~~ ✅
> The cache already tracks file mtime + size per entry and automatically skips unchanged files (cache key = `path:mtime:size`). Enhancement: completion summary now shows cache hit rate (e.g., "1,200/1,500 reused from cache (80%)") so users see the speedup on re-scans.

### ~~7. Web Workers for heavy frontend computation~~ ✅
> Filtering and sorting are now offloaded to `filter-worker.js` via the Web Worker API. `applyFilters()` posts `{ groups, typeFilter, sortBy, search }` to the worker and receives back `filteredGroups`, keeping the UI thread free during large result sets. Falls back to synchronous filtering in environments that don't support `Worker`.

### ~~8. Add dark/light theme toggle~~ ✅
> Light theme via `[data-theme="light"]` CSS custom property overrides. Toggle button in header, persisted to `localStorage`, respects `prefers-color-scheme` on first visit.

### ~~9. Rate-limit and queue API endpoints~~ ✅
> `/api/scan` rejects concurrent scans with HTTP 409 and a clear error message. Rate-limiting on `/api/image` not yet implemented.

### 10. Async Flask (Quart migration)
Flask runs synchronously — scan threads block the WSGI worker. Migrating to Quart (async Flask drop-in) or adding `flask[async]` would allow:

- Native `async def` route handlers
- `asyncio`-based file I/O
- Better SSE/WebSocket support without threading hacks

> **Deferred** — Quart is a near-drop-in but the SSE generators and background threads need careful async rewrites. Recommended together with item 2 (WebSocket).

---

## Low Priority (Polish)

### ~~11. Service Worker for offline capability~~ ✅
> Network-first service worker caches the app shell (`/`, CSS, JS). UI loads instantly on repeat visits even if the server is restarting. API calls are never cached.

### ~~12. Drag-and-drop directory selection~~ ✅
> Drop zone with visual overlay on the directory input. Reads folder name from `webkitGetAsEntry()` or Electron's `file.path`.

### ~~13. Image comparison slider~~ ✅
> Before/after comparison slider added to all duplicate groups with exactly 2 images. Clicking the **⇔ Slider** button in the group header opens a fullscreen overlay (`#compareSliderOverlay`) with a draggable handle that clips image A to reveal image B underneath. Keyboard nudging (←/→), touch support, and Escape to close. CSS handles the clip-path reveal; no third-party library required.

### ~~14. Batch operations from results view~~ ✅
> **Quick Ops** dropdown added to the action bar. "Convert to JPG" and "Move to Folder…" run directly on files marked for deletion without switching to the Operations tab. Backed by the new `/api/batch-operation` endpoint which accepts `{ operation, files, ... }` and validates all paths against the scanned directory before executing.

### ~~15. Accessibility audit~~ ✅
> Focus trap in modals (`role="dialog"`, `aria-modal`), `aria-live="polite"` on progress section, contrast fix (`--text-muted` from `#666` to `#888` for WCAG AA).

### ~~16. Typed Python API with Pydantic~~ ✅
> `pixsieve/api/schemas.py` defines Pydantic v2 request models (`ScanRequest`, `DeleteRequest`, `BatchOperationRequest`, `DateRangeRequest`, etc.) and a `parse_request()` helper that returns a typed model or a Flask 400 response. Key routes (`/api/scan`, `/api/selections`, `/api/apply_strategy`, `/api/batch-operation`) now validate through these models instead of ad-hoc `data.get()` calls.

### ~~17. OpenAPI/Swagger documentation~~ ✅
> `flasgger` registered in `create_app()`. Interactive Swagger UI available at **`/docs/`**. Key routes annotated with YAML OpenAPI 3.0 docstrings (request body schemas, response codes, tags). `apispec.json` served at `/apispec.json`.

### 18. Replace lsh.py (16,479 LOC) with a library
The LSH implementation is massive. Consider using `datasketch`, `faiss`, or `annoy` for approximate nearest neighbor search — battle-tested, optimized in C/C++, and significantly less code to maintain.

> **Deferred** — Replacing `lsh.py` requires matching the existing similarity thresholds and hash distance semantics exactly, plus regression testing across the full scanner pipeline. High risk of subtle behaviour changes.

### ~~19. Add E2E tests~~ ✅
> Playwright test suite added at `tests/e2e/test_critical_flows.py`. Covers: app shell loads (title, header, tab navigation, theme toggle, Swagger UI), scan form validation (empty/invalid directory, real scan), results view (stats bar, filter bar, view toggles, compare slider, export dropdown), and API health checks (`/api/ping`, `/api/status`, `/api/cache/stats`). Run with `pytest tests/e2e/ --base-url http://localhost:5000` after `playwright install chromium`.

### ~~20. Container support~~ ✅
> `Dockerfile` (Python 3.11-slim, system libs for Pillow) and `docker-compose.yml` (port 5000, volume mount for photo library via `$PHOTO_DIR`) added. Start with `docker compose up --build`. Access at `http://localhost:5000`.
