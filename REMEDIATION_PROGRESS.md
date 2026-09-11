# PixSieve Remediation Progress

Tracks execution of the comprehensive codebase remediation plan (bugs, security/robustness,
validation consistency, accessibility, theme, docs) produced from a full function-by-function
audit of the backend, API/CLI, and frontend. Full plan detail:
`C:\Users\zdaly\.claude\plans\do-a-granular-and-abstract-cray.md`.

Last worked on: 2026-09-10. **Nothing described as "done" below has been committed as of writing
this file** — that happens right after, in the same session.

Test suite as of this commit: **430 Python tests passed, 4 skipped** (`pytest tests/ -q
--ignore=tests/e2e`) and **33 JS tests passed** (`npx vitest run`).

---

## Done

### Phase 0 — Critical data-safety fix
`scan_and_repair(dry_run=True)` was not a real dry run — `_attempt_repair()` unconditionally wrote
to disk regardless of the flag. Fixed by threading `dry_run` through all three repair strategies via
scratch-file verification instead of overwriting the original. New `tests/test_operations_repair.py`
(6 tests, using a genuinely-truncated JPEG, not mocks).

### Phase 1 — Concurrency, security-adjacent, data-integrity robustness
- `_run_operation()` in `pixsieve/api/operations_routes.py` now returns 409 on a concurrent request,
  matching `/api/scan`/`/api/delete` (all 14 routes covered by one fix).
- CLI `parse_date()` crash-on-invalid-date fixed in `_handle_metadata`/`_handle_pipeline`.
- `_loadChildFolders()`'s unescaped `innerHTML`, `escapeJs()`'s missing `"` escaping,
  `app.py`'s hardcoded Flask `secret_key` → `os.urandom(24)`, added `MAX_CONTENT_LENGTH`.
- `invalidate_directory()`'s SQL `LIKE` prefix-collision/wildcard-injection bug fixed (escaping +
  trailing separator).
- Narrowed 3 bare `except Exception` swallows (`schema.py`, `maintenance.py`, `validators.py`) to
  log unexpected failures instead of hiding them identically to the expected no-op case.
- `randomize_dates_per_folder()` no longer over-reports EXIF-write failures as success.
- CSV export now uses `csv.writer` instead of hand-rolled string interpolation (was corrupting rows
  containing commas/quotes).

### Phase 2 — Validation & config consolidation
- Centralized `PERCEPTUAL_AUTO_DISABLE_THRESHOLD`, a new `LSH_DEDUPE_SEEN_SET_MAX`, and
  `DEFAULT_API_WORKERS` into `config.py` (previously scattered/duplicated across `api/orchestrator.py`
  and `api/schemas.py`).
- **Deleted `pixsieve/user_config.py` and the `pixsieve config` CLI subcommand** — dead code, never
  actually consulted by any real code path. `docs/cli.md`'s Configuration section rewritten to
  describe what's actually configurable.
- **Migrated all 14 `/api/operations/*` routes to Pydantic schema validation** (`pixsieve/api/schemas.py`)
  — the single largest item in the plan. Added `MoveToParentRequest`, `RandomizeDatesPerFolderRequest`,
  `RecursiveVideoRequest`, `SortColorRequest`, `SortResolutionRequest`, `PipelineRequest`; extended
  existing schemas with `includeVideos`/`syncExif` fields to match real route behavior. Fixed
  `parse_request()`'s error format (was dumping pydantic's multi-line internal representation).
  Deduplicated `_validate_directory` against the shared `utils.validators.validate_directory`.
  Added a `Literal['keep','delete']` constraint to `SelectionsRequest`. `_parse_extensions()` now
  guards against a non-list value.
- CLI parity: unified the Windows-invalid-filename-character set (`config.WINDOWS_INVALID_FILENAME_CHARS`,
  previously two independently-drifted copies), added bounds to `--workers`/`--length`/`--quality`/
  `--threshold` via a new `_bounded_int()` helper, fixed the `duplicates` subcommand's confusing
  `-r` (meant "off") to use the shared `_add_recursive_arg()` like every other subcommand.
- Removed the dead `--dry-run` CLI flag (only `--no-dry-run` was ever read); updated `docs/cli.md`.
- Removed orphaned `prompt_for_directory()` (singular, superseded by `prompt_for_directories()`) and
  `_format_group_header()`'s unused `match_type` param.
- Doc fixes: `docs/api.md`'s stale `/api/scan` example, `docs/performance.md`'s LSH table missing the
  ≥500K tier, `deduplication.py` docstrings referencing the old 5000 threshold (real value: 1000, now
  referenced via `config.LSH_AUTO_THRESHOLD` so it can't drift again).

### Phase 3 — Frontend: JS test infra + modal/accessibility hardening
- **New: `tests/js/` + `package.json`/`vitest.config.js`** — vitest + jsdom loading the *actual*
  `pixsieve/static/js/app.js` and `pixsieve/templates/index.html` (not a mocked copy), via
  `tests/js/load-app.js`. `runScripts: 'dangerously'` is required for the dynamically-generated
  duplicate-card markup's inline `onclick=`/`ondblclick=` handlers to actually wire up in jsdom — this
  does not execute the page's own `<script src>` tags (no resource loader configured), only truly
  inline script content. Run with `npx vitest run` (from repo root, needs `npm install` first).
- Fixed the Escape-key routing bug: a stray unconditional `hideCacheModal()` call was releasing a
  *different* modal's focus trap via a single shared cleanup slot. New pure `_resolveEscapeAction()`
  function decides what to dismiss based on which modal is actually active.
- Added focus trapping (`trapFocus`/`releaseFocusTrap`) to `#autoFillModal`, `#compareSliderOverlay`,
  and the lightbox itself (which also gained `role="dialog"`/`aria-modal`).
- Added click-outside-to-close on all 4 modals + lightbox + compare-slider, via `_wireModalBackdropClose()`.
- Added client-side double-submit guards to `clearCache`/`cleanupCache` (shared flag),
  `quickOpConvertJpg`/`quickOpMove` (shared flag), and `applyStrategy` — none of these routes have a
  server-side concurrency guard (they live in `routes.py`, not the `operations_routes.py` blueprint
  Phase 1 covered), so this is a client-side-only fix.
- **Made the keep/delete duplicate cards keyboard-accessible** (`tabindex="0" role="button"` on
  `.image-card`/`.image-row`/`.compare-card`) — the single highest-impact accessibility fix in the
  plan; the existing generic `role="button"` Enter/Space keydown handler already covers activation,
  no new JS logic needed.
- Fixed missing `alt` text on lightbox/compare-slider images, added a `role="status"`/`aria-live`
  toast container (and the same on `#undoToast`), added `aria-haspopup`/`aria-expanded` + Escape-close
  + correctly-scoped outside-click-close to the Export/Quick-Ops dropdown menus (the old outside-click
  handler only covered `exportMenu` and, being class-based rather than per-menu-scoped, would have
  misbehaved once quick-ops got the same treatment).

### Phase 4.1–4.2 — Theme/contrast fixes (started, not finished)
- Cache Management modal: replaced hardcoded `#0f0f23`/`#888`/`#fff`/`#666` inline styles with
  `.cache-info-box`/`.cache-info-row`/`.cache-info-label`/`.cache-info-value` classes using design
  tokens (`var(--bg-overlay)`, `var(--text-muted)`, `var(--text)`) — `#0f0f23` was the exact literal
  navy the original design-system redesign was supposed to eliminate everywhere.
- Fixed 3 `color: #ccc` occurrences (`.form-group label`, `.error-image-path`,
  `.pipeline-step-item label`) → `var(--text)` — real WCAG failures in light theme (~1.6:1 contrast).
- `.cache-manage-btn` now sets an explicit `color: #fff` instead of inheriting the theme's text color
  against its own fixed dark background (was ~1.7:1 in light theme).
- `showOpsResult()`'s "Problem Files" status colors now use `var(--success)`/`var(--warning)`/
  `var(--danger)`/`var(--text-muted)` instead of unrelated hardcoded hex that never matched the design
  system and never adapted for light theme.
- Fixed the dead `.images-grid` mobile media-query rule → `.group-images` (the real class name) — the
  single-column collapse on narrow viewports was never actually applying.
- Normalized 10 ad hoc `3px`/`4px`/`8px` border-radius values onto the `--r-sm`/`--r-md` scale.

---

## Not done — remaining plan items

### Phase 4.3 — Replace native `confirm()`/`prompt()` with styled modals
5 call sites (cancel-scan, new-scan, Quick-Op convert, clear-cache, Quick-Op move destination) still
use blocking native dialogs, inconsistent with the app's own styled/focus-trapped modal pattern used
for delete/rename. **Not started** — this needs a new reusable confirm-modal component design (and,
for the move-destination case, a proper directory-input modal with the same autocomplete/history
treatment every other directory field in the app has). Bigger design/build task than anything else
in Phase 4, which is why it was deferred rather than rushed.

### Phase 4.4 — Remaining polish (small, independent, low risk)
- `editor.js`'s crop tool has no touch event support (mouse-only), unlike the compare-slider in the
  same lightbox.
- The single-key `z` undo shortcut's guard only excludes `<input>`, not `<select>`/`<textarea>`/
  `contenteditable`.
- Dead code: `checkProgress(){}` no-op stub, and `startProgressPolling()` is a stale name (it opens an
  `EventSource`/SSE connection now, not a poll loop).
- FOUC: theme is applied on `DOMContentLoaded`, so a saved light-theme preference briefly flashes dark
  on load. Fix is a small inline theme-detection `<script>` in `<head>`, before first paint.

### Phase 6 — Deferred, high-risk, isolated work (do last, one at a time, own worktree)
- **DB write-path synchronization** (`pixsieve/database/connection.py`): the `_write_lock` used by
  `put()`/`invalidate()`/etc. doesn't synchronize against the separate `_BackgroundWriter` thread's own
  connection. Needs a design decision (route all writes through one connection vs. share one lock)
  before any code changes — rushing this risks a deadlock or throughput regression in the exact
  large-library scan hot path it's meant to protect.
- **`api/orchestrator.py` / `cli/orchestrator.py` dedup extraction**: ~150 lines of near-identical
  duplicate-finding sequencing logic exists independently in both files (already a source of drift —
  the large-library auto-scaling logic only exists in the API path today). Needs characterization
  tests written *first* (snapshot scan results from both paths against a shared fixture set) before
  any extraction.
- Lower-priority, no urgency: `platform.py`'s Windows symlink-support detection doesn't account for
  Developer Mode; `sort.py`'s three sort functions have no `recursive` parameter unlike every other
  operation module; `convert.py`'s unreachable self-overwrite edge case (add a defensive
  assertion/comment only).

---

## Notes for whoever picks this up next

- The JS test harness (`tests/js/load-app.js`) loads the real production files, not copies — if you
  add new app.js functions that touch DOM elements not in the real `index.html` template, or new
  browser APIs jsdom doesn't implement, you may need to add another minimal polyfill there (see the
  existing `IntersectionObserver`/`matchMedia`/`fetch`/`requestAnimationFrame` stubs for the pattern).
- Run `npm install` once before `npx vitest run` works (installs `vitest`+`jsdom` as dev dependencies;
  `node_modules/` is gitignored).
- Phase 2's route migration changed some error-response bodies (single clean sentence instead of
  pydantic's multi-line dump) and tightened a few previously-silently-clamped numeric fields
  (`workers`/`quality`/etc.) into hard rejections — both deliberate, not regressions, but worth knowing
  if something downstream ever depended on the old shapes.
- `PICKUPHERE.md` (repo root) is a separate, already-resolved handoff note from an earlier, unrelated
  session (the faveRemover/ImageGalleryEditor port) — not related to this remediation effort.
