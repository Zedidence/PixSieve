# Next Steps — Known Bugs & Performance Issues

Issues identified by static analysis. Ordered by priority.

---

## Critical

### 1. Lock scope too narrow in `repair.py` — Race condition
**File**: `pixsieve/operations/repair.py:192-200`

`_repair_lock` is acquired only to toggle `ImageFile.LOAD_TRUNCATED_IMAGES`, but the actual `Image.open()` call happens outside the lock. Another thread can flip the flag between the toggle and the open.

**Fix**: Extend the lock to cover the entire `Image.open()` + `img.copy()` block.

```python
with _repair_lock:
    old_setting = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        with Image.open(path) as img:
            img_copy = img.copy()
            fmt = img.format or "JPEG"
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = old_setting
```

---

### 2. Unclosed DB connection in `maintenance.py` — Resource leak
**File**: `pixsieve/database/maintenance.py:153-155`

`vacuum()` opens a raw `sqlite3.connect()` with no try/finally. If `VACUUM` raises, the connection leaks.

**Fix**: Wrap in try/finally or a context manager.

---

### 3. Thread-unsafe LRU cache on `_parse_phash`
**File**: `pixsieve/scanner/deduplication.py:24-27`

`@lru_cache(maxsize=None)` on `_parse_phash()` is not thread-safe under heavy parallel load. Multiple threads can race on identical hash strings.

**Fix**: Wrap cache reads/writes with a `threading.Lock`, or replace with a thread-safe dict.

---

## Major Performance

### 4. Full image load for truncation detection
**File**: `pixsieve/scanner/analysis.py:63-75`

`img.load()` forces the entire pixel buffer into RAM just to detect truncation. A 100MP image = 400MB+ unnecessarily allocated.

**Fix**: Use `img.verify()` instead — it validates image structure without materializing pixels.

---

### 5. No pre-downscaling before perceptual hashing
**File**: `pixsieve/scanner/hashing.py:68-84`

pHash is computed on the full-resolution image. The hash is effectively identical whether computed on the original or a 256×256 thumbnail, but the memory and CPU cost differs by orders of magnitude.

**Fix**: Add `img.thumbnail((256, 256), Image.Resampling.LANCZOS)` before calling the hash function.

---

### 6. Color sort resize is too large (150×150)
**File**: `pixsieve/operations/sort.py:193-196`

`get_dominant_color()` resizes every image to 150×150 before K-means. A 32×32 thumbnail is statistically sufficient for dominant color extraction and is ~22× less data.

**Fix**: Replace `img.resize((150, 150))` with `img.thumbnail((32, 32), Image.Resampling.LANCZOS)`.

---

### 7. N+1 `set_dominant_color` updates
**File**: `pixsieve/database/operations.py:231-255`

Called per-image in a loop from the sort operation. ~~Each call triggers an individual `UPDATE` transaction with a separate write lock.~~

**Partial fix (F1)**: `set_dominant_color()` is now async — calls are enqueued to the background writer and batched into a single transaction per drain cycle, so no write locks are held by the caller. The N+1 issue at the call site still exists but no longer causes lock contention.

**Remaining fix**: Collect `(color, path)` pairs and flush with a single `executemany` to reduce round-trips to the background writer queue.

---

### 8. Chunked file discovery is immediately flattened
**File**: `pixsieve/scanner/file_discovery.py:86-112`

`iter_image_chunks()` lazily yields chunks, but `find_image_files()` immediately calls `images.extend(chunk)` in a loop, collapsing everything into one list and defeating the chunking mechanism.

**Fix**: Either keep the result as a generator throughout, or document the memory cost explicitly.

---

### 9. CMYK→RGB conversion without color profile
**File**: `pixsieve/scanner/hashing.py:20-29`

`_ensure_phash_mode()` does a raw `img.convert('RGB')` on CMYK images. Without an ICC profile, CMYK→RGB produces incorrect (often inverted) colors. This generates wrong perceptual hashes for CMYK images, which are common in print-origin JPEGs.

**Fix**: Use a profile-aware conversion or explicitly handle CMYK as a special case before hashing.

---

### 10. Unclosed PIL `Image` in `convert.py` — Windows file lock
**File**: `pixsieve/operations/convert.py:137-171`

`Image.open()` is never explicitly closed. On Windows, this holds a file lock and prevents the original from being moved or deleted afterward.

**Fix**: Wrap with `with Image.open(path) as img:` or call `img.close()` in a finally block.

---

## Bugs

### 11. Silent exception swallow in perceptual hash parsing
**File**: `pixsieve/scanner/deduplication.py:147-148`

`except Exception: parsed_hashes.append(None)` produces no log output. Corrupted hash strings fail invisibly, silently skipping comparisons with no indication of how many were affected.

**Fix**: Add `logger.debug(f"Failed to parse hash for {img.path}: {e}")` inside the except block.

---

### 12. Inconsistent Union-Find implementation
**File**: `pixsieve/scanner/deduplication.py:400-410`

`_collect_duplicate_groups` defines a local `find()` that does path compression but not union-by-rank, while the brute-force and LSH paths use a full union-by-rank implementation. Inconsistent and redundant.

**Fix**: Extract Union-Find into a shared helper and use it everywhere.

---

### 13. Broken symlink crash
**File**: `pixsieve/scanner/file_discovery.py:64-72`

`filepath.resolve()` raises `OSError` on broken symlinks in Python 3.10+, crashing the scan silently or noisily depending on where the exception is caught.

**Fix**: Catch `OSError` and fall back to `filepath.absolute()`.

---

### 14. EXIF date encoded as UTF-8 instead of ASCII
**File**: `pixsieve/operations/metadata.py:90`

EXIF spec requires 7-bit ASCII for date strings. `.encode('utf-8')` is technically incorrect even though the date format only produces ASCII characters. Strict EXIF readers may reject it.

**Fix**: Change to `.encode('ascii')`.

---

### 15. Decompression bomb risk from raised pixel limit
**File**: `pixsieve/config.py:64-67`

PIL's default `MAX_IMAGE_PIXELS` (~89MP) is intentionally conservative. It's raised globally to 500MP, meaning a malicious or malformed JPEG could consume gigabytes during decompression in any context.

**Fix**: Apply the higher limit only within a context manager scoped to trusted analysis paths, not as a global module-level override.
