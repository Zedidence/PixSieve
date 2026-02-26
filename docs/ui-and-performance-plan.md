# PixSieve: UI Modernization & Large-Library Performance Plan

## Context

PixSieve's web GUI was scaffolded quickly and shows the hallmarks of AI-generated design: a generic `#1a1a2e` navy dark theme, blue-to-purple gradient buttons, 3,592 lines of inline CSS with repeated `linear-gradient` patterns, and no real design system. The backend has excellent algorithmic choices (LSH, Union-Find, WAL SQLite) but has specific bottlenecks that will surface at 500k–750k files: a single write-lock serializing parallel cache writes, un-streamed file discovery, polling-based progress, and LSH params that stop tuning at 200k images.

This plan addresses both issues in two independent tracks.

---

## Track 1: UI Modernization

### What Makes the Current UI Feel AI-Generated

1. **Generic navy palette**: `#1a1a2e`, `#16213e`, `#0f0f23` are the exact colors that every AI-scaffolded dark dashboard uses. No personality.
2. **Blue-to-purple gradient buttons**: `linear-gradient(135deg, #4a9eff, #6c5ce7)` — this exact gradient appears in thousands of AI-generated UIs.
3. **Border-radius: 12px everywhere**: Over-rounded corners on every card, banner, and input field creates a "soft" look that has no visual tension.
4. **Repeated `linear-gradient` backgrounds on banners**: Recovery, warning, and cache banners all use the same `linear-gradient(135deg, ...)` pattern.
5. **No typographic hierarchy**: Everything uses `-apple-system` at default weight. No deliberate scale.
6. **3,592 lines of inline CSS**: Copy-paste style with no design system — values are repeated literally instead of referenced via variables.

### Goal

Replace with a purposeful, data-dense design. The principle: **opinionated typography + restrained color + useful density**. This is a tool, not a dashboard — it should look like one.

---

### Change 1: CSS Design System

**File:** Extract from `pixsieve/templates/index.html` → new `pixsieve/static/css/app.css`

Introduce CSS custom properties as the foundation:

```css
:root {
  /* Typography */
  --font-sans: 'Inter', ui-sans-serif, system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;

  /* Palette — replace generic navy with near-black neutral */
  --bg-base:       #0e0e0e;
  --bg-raised:     #161616;
  --bg-overlay:    #1e1e1e;
  --border:        #2a2a2a;
  --border-subtle: #1f1f1f;
  --text:          #e8e8e8;
  --text-muted:    #666;
  --text-faint:    #3a3a3a;

  /* Single accent: amber replaces the blue+purple gradient duo */
  --accent:        #f5a623;
  --accent-dim:    rgba(245, 166, 35, 0.12);

  /* Semantic colors */
  --success: #22c55e;
  --warning: #f59e0b;
  --danger:  #ef4444;
  --info:    #38bdf8;

  /* Spacing scale */
  --sp-1: 4px;   --sp-2: 8px;   --sp-3: 12px;
  --sp-4: 16px;  --sp-5: 20px;  --sp-6: 24px;
  --sp-8: 32px;  --sp-12: 48px;

  /* Border radii — tighter than before */
  --r-sm: 3px;
  --r-md: 6px;
  --r-lg: 10px;
}
```

**Specific style replacements:**

| Old | New |
|-----|-----|
| `background: #1a1a2e` | `background: var(--bg-base)` (`#0e0e0e`) |
| `linear-gradient(135deg, #4a9eff, #6c5ce7)` on buttons | `background: var(--accent)` flat with `box-shadow` on hover |
| `border-radius: 12px` on cards | `border-radius: var(--r-md)` (6px) |
| `font-family: -apple-system, ...` | `font-family: var(--font-sans)` + Inter loaded via `<link>` |
| Repeated `linear-gradient` banners | Solid `var(--bg-raised)` with `border-left: 3px solid var(--warning)` accent |

---

### Change 2: Operations Sidebar — Grouped Navigation

**File:** `pixsieve/templates/index.html` (sidebar HTML)

Currently 13 items listed identically. Reorganize with section headers:

```
ORGANIZE
  Move to Parent
  Move with Structure
  Sort Alphabetically
  Sort by Color

TRANSFORM
  Fix Extensions
  Convert to JPG
  Rename (Random)
  Rename by Folder

METADATA
  Randomize EXIF Dates
  Randomize File Dates

MAINTENANCE
  Repair Corrupt Files
  Delete Empty Folders
  Pipeline (Multi-step)
```

Section headers: `font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-faint)`. This is how professional tools (VS Code, Linear, Figma) separate sidebar sections.

---

### Change 3: Progress — Stage Timeline

**File:** `pixsieve/templates/index.html` (progress section HTML + CSS)

Replace the 5-dot stage indicator and animated stripe bar with:

- A thin `2px` progress line at the very top of the progress panel (like YouTube's red bar), using `width: N%` CSS transition
- A horizontal timeline below it showing stage names with elapsed time for completed stages:
  ```
  [●]──────[●]──────[○]──────[○]──────[○]
  Scan     Analyze   Hash     Match    Done
  2m 34s   1m 12s    ...      ...      ...
  ```
- Active stage: single pulsing `opacity` animation on the dot (not the full stripe animation)
- Remove `@keyframes progressStripe` entirely

---

### Change 4: Duplicate Cards — Tighter + Virtual

**File:** `pixsieve/templates/index.html` (card CSS + JavaScript)

- Reduce default `min-width: 280px` → `min-width: 200px`
- Add compact toggle: `min-width: 140px` for large result sets
- Replace `<p>` metadata lines with a 3-column micro-table: `Resolution | Size | Quality`
- Add Intersection Observer for lazy loading: group cards only render their `<img src>` when the group scrolls into view
- Add `loading="lazy"` to all image tags

---

### Change 5: Extract JavaScript → SSE Client

**File:** New `pixsieve/static/js/app.js`

Split ~800 lines of inline JS. Replace the `setInterval` polling loop with an `EventSource` connection:

```js
// Before: polling every 1 second
const poller = setInterval(async () => {
  const r = await fetch('/api/operations/status');
  ...
}, 1000);

// After: SSE push
const stream = new EventSource('/api/operations/stream');
stream.onmessage = (e) => {
  const state = JSON.parse(e.data);
  updateUI(state);
  if (['complete', 'error', 'idle'].includes(state.status)) {
    stream.close();
  }
};
```

---

## Track 2: Performance for 500k–750k Files

### 2.1 Stream File Discovery

**File:** `pixsieve/scanner/file_discovery.py`

**Problem:** `Path.rglob('*')` materializes the entire directory tree before returning. At 750k files this is 100–150MB held in memory, and the frontend shows nothing during discovery.

**Fix:** Convert to a chunked generator:

```python
def discover_files(root, extensions, chunk_size=1000, ...) -> Generator[list[Path], None, None]:
    chunk = []
    seen = set()
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        if path.suffix.lower() not in extensions:
            continue
        resolved = path.resolve() if resolve_symlinks else path
        if resolved in seen:
            continue
        seen.add(resolved)
        chunk.append(path)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
```

Update `pixsieve/scanner/parallel.py` to consume the generator, feeding chunks to the `ThreadPoolExecutor` as they arrive. Frontend gets discovered-count SSE events before analysis completes.

---

### 2.2 Fix the Write-Lock Bottleneck ✅ DONE (F1)

**Files:** `pixsieve/database/connection.py`, `pixsieve/database/operations.py`

**Implemented approach (supersedes the original chunked-lock plan):**

A `_BackgroundWriter` daemon thread owns a dedicated write connection and drains a `queue.Queue` of write callables in batches. Each drain cycle executes inside a single `BEGIN/COMMIT`. Worker threads call `enqueue_write(fn)` and return immediately — no blocking on `_write_lock`.

Key behaviours:
- `put_batch()` pre-computes row tuples in the calling thread (safe `os.stat`), then enqueues one callable per `WRITE_BATCH_SIZE` chunk — no lock held by the caller
- `put_async()` (new) enqueues a single-row write; used by I1 streaming mode
- `set_dominant_color()` is now async — benefits G2 parallel sort workers
- `get_batch()` calls `flush_writes()` first to guarantee read-after-write consistency
- `last_accessed` updates in `get()` and `get_batch()` are enqueued asynchronously instead of being bundled into the read transaction
- Background writer connection is opened **lazily** on first write to avoid racing with schema initialisation (`PRAGMA journal_mode=WAL` needs exclusive access on a new DB file)
- `mmap_size = 2147483648` and `journal_size_limit = 67108864` PRAGMAs are set on both the background writer's connection and the regular `connection()` context manager

---

### 2.3 LSH Parameter Tuning for 500k+

**Files:** `pixsieve/scanner/deduplication.py`, `pixsieve/lsh.py`, `pixsieve/config.py`

**Problem:** `calculate_optimal_params()` plateaus at the `> 200k` tier: `(25, 14)`. At 750k images this is under-indexed — more tables and fewer bits per table gives better recall.

**Fix:**

```python
def calculate_optimal_params(num_images, threshold=10):
    if num_images < 10_000:    return (15, 20)
    elif num_images < 50_000:  return (18, 18)
    elif num_images < 200_000: return (20, 16)
    elif num_images < 500_000: return (25, 14)
    else:                       return (30, 12)  # new: 500k–750k+ tier
```

In `config.py`, lower `LSH_AUTO_THRESHOLD` from 5000 → 1000. For libraries in the 500k range, even 1000-image subsets benefit from LSH.

---

### 2.4 Server-Sent Events Progress Endpoint

**File:** `pixsieve/api/operations_routes.py`

**Problem:** Frontend polls `/api/operations/status` every second. A 2-hour scan of 750k files generates 7,200 unnecessary HTTP round trips.

**Fix:** Add SSE endpoint:

```python
@bp.route('/api/operations/stream')
def stream_status():
    def generate():
        while True:
            state = _get_state_snapshot()
            yield f"data: {json.dumps(state)}\n\n"
            if state['status'] in ('complete', 'error', 'idle'):
                break
            time.sleep(0.5)
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )
```

Keep the existing `/api/operations/status` endpoint for compatibility (initial page load state check).

---

### 2.5 Virtual Scrolling for Large Result Sets

**File:** `pixsieve/templates/index.html` (JavaScript)

**Problem:** 10% duplicate rate in a 750k library = 75,000 groups. Rendering all cards at once crashes the browser tab.

**Fix (Intersection Observer):**

```js
const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (entry.isIntersecting) {
      loadGroupImages(entry.target);  // inject <img> tags
      observer.unobserve(entry.target);
    }
  }
}, { rootMargin: '200px' });

// For each group: render header only, observe it
groups.forEach(group => {
  const el = renderGroupHeader(group);  // no image cards yet
  observer.observe(el);
  container.appendChild(el);
});
```

Also add pagination controls: render groups in pages of 100, with "Load 100 more" at the bottom.

---

### 2.6 Large-Library Config Constants

**File:** `pixsieve/config.py`

```python
# Large library thresholds and tuning
LARGE_LIBRARY_THRESHOLD = 100_000    # files — triggers large-library mode
LARGE_LIBRARY_WORKERS   = min(os.cpu_count() * 4, 32)  # more aggressive parallelism
WRITE_BATCH_SIZE        = 5_000      # cache insert batch size before lock release
DISCOVERY_CHUNK_SIZE    = 1_000      # files per discovery chunk
```

Expose `LARGE_LIBRARY_THRESHOLD` and `LARGE_LIBRARY_WORKERS` in the Advanced Options UI section.

---

## Files to Modify

| File | Change |
|------|--------|
| `pixsieve/templates/index.html` | Remove inline CSS/JS; add `<link>`/`<script>` refs; update markup |
| `pixsieve/static/css/app.css` | **New**: extracted + redesigned CSS with custom properties |
| `pixsieve/static/js/app.js` | **New**: extracted JS + SSE client |
| `pixsieve/api/operations_routes.py` | Add `/api/operations/stream` SSE endpoint |
| `pixsieve/scanner/file_discovery.py` | Convert to chunked generator |
| `pixsieve/scanner/parallel.py` | Consume generator chunks |
| `pixsieve/database/operations.py` | Sub-batch `put_batch()` with periodic lock release |
| `pixsieve/database/connection.py` | Add `mmap_size`, `journal_size_limit` pragmas |
| `pixsieve/scanner/deduplication.py` | Add 500k+ LSH param tier |
| `pixsieve/config.py` | Add large-library constants; tune `LSH_AUTO_THRESHOLD` |

---

## Verification Checklist

- [ ] Browser loads Inter font; no blue-to-purple gradient buttons visible
- [ ] Operations sidebar shows 4 grouped sections with faint category headers
- [ ] Progress panel shows thin top-bar + stage timeline (no stripe animation)
- [ ] DevTools → Network: `EventSource` to `/api/operations/stream` active during scan; no repeated XHR polling
- [ ] DOM inspection during large result view: groups below viewport have no `<img>` tags until scrolled into view
- [ ] `calculate_optimal_params(600_000)` returns `(30, 12)`
- [ ] Log output during scan shows chunked discovery (batches of 1000 files logged progressively)
- [x] F1 background writer active: `put_batch()` enqueues writes non-blocking; `pixsieve-db-writer` daemon thread visible in thread list during scan
