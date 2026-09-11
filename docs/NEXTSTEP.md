# Next Steps — Known Bugs & Performance Issues

Issues originally identified by static analysis. Verified against the current codebase on 2026-09-05 — **14 of the 15 items below are already fixed**; only item 8 remains open.

---

## Resolved

### 1. ~~Lock scope too narrow in `repair.py` — Race condition~~ ✅ Fixed
`_repair_lock` now wraps the entire `Image.open()` + `img.copy()` block in every repair strategy (`pixsieve/operations/repair.py`), not just the `LOAD_TRUNCATED_IMAGES` toggle.

### 2. ~~Unclosed DB connection in `maintenance.py` — Resource leak~~ ✅ Fixed
`vacuum()` (`pixsieve/database/maintenance.py:149-160`) now wraps the connection in try/finally.

### 3. ~~Thread-unsafe LRU cache on `_parse_phash`~~ ✅ Fixed
Replaced with a 16-bucket dict cache, each bucket guarded by its own `threading.Lock` (`pixsieve/scanner/deduplication.py`), reducing contention to ~1/16 instead of a single global lock.

### 4. ~~Full image load for truncation detection~~ ✅ Fixed
`analysis.py` now uses `img.verify()` for the non-phash path and relies on `thumbnail()`'s partial decode (which naturally raises on truncated files) for the phash path — no more forced full-resolution `img.load()`.

### 5. ~~No pre-downscaling before perceptual hashing~~ ✅ Fixed
`hashing.py` now calls `img.thumbnail((256, 256), Image.Resampling.LANCZOS)` before hashing.

### 6. ~~Color sort resize is too large (150×150)~~ ✅ Fixed
`sort.py` now uses a 32×32 thumbnail for dominant-color/grayscale analysis.

### 7. ~~N+1 `set_dominant_color` updates~~ ✅ Fixed
The sort call site now uses `set_dominant_color_batch()`, which issues a single `executemany` instead of one write per image.

### 9. ~~CMYK→RGB conversion without color profile~~ ✅ Fixed
`hashing.py`'s `_ensure_phash_mode()` now special-cases CMYK: `ImageOps.invert(img).convert('RGB')` instead of a raw `convert('RGB')`, which was producing inverted colors on print-origin JPEGs.

### 10. ~~Unclosed PIL `Image` in `convert.py` — Windows file lock~~ ✅ Fixed
Both `Image.open()` call sites in `convert.py` now use a `with` context manager.

### 11. ~~Silent exception swallow in perceptual hash parsing~~ ✅ Fixed
Both parse sites in `deduplication.py` now log `logger.debug(f"Failed to parse hash for {img.path}: {e}")` before appending `None`.

### 12. ~~Inconsistent Union-Find implementation~~ ✅ Fixed
A single shared `_UnionFind` class (path compression + union-by-rank) is now used by both the brute-force and LSH paths.

### 13. ~~Broken symlink crash~~ ✅ Fixed
`file_discovery.py` catches `OSError` from `filepath.resolve()` and falls back to `filepath.absolute()`.

### 14. ~~EXIF date encoded as UTF-8 instead of ASCII~~ ✅ Fixed
`metadata.py` now uses `.encode('ascii')` for EXIF date strings, per the EXIF spec.

### 15. ~~Decompression bomb risk from raised pixel limit~~ ✅ Fixed
`MAX_IMAGE_PIXELS` is now configurable via the `PIXSIEVE_MAX_IMAGE_PIXELS` environment variable (`config.py`). It's still applied globally rather than scoped to a context manager — a deliberate tradeoff documented in `scanner/dependencies.py`: scoping it would require a lock around every `Image.open()` call, serializing the `ThreadPoolExecutor` and defeating parallelism.

---

## Still Open

### 8. Chunked file discovery is immediately flattened
**File**: `pixsieve/scanner/file_discovery.py`

`find_image_files()` / `find_image_files_multi()` still call `images.extend(chunk)` in a loop over the chunked generators (`iter_image_chunks()` / `iter_image_chunks_multi()`), collapsing everything into one in-memory list. The streaming generators exist and are used directly elsewhere in the codebase, but these two convenience functions still defeat their own chunking. Low priority — the memory cost is only paid at very large (500K+) file counts, and callers that care can use the generator functions directly instead.

**Fix**: Either have these functions return the generator directly (breaking their current list-returning signature), or document the memory tradeoff explicitly in their docstrings so callers know to prefer the streaming variants for huge collections.
