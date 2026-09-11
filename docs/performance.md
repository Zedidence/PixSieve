# Performance & Configuration Guide

Covers LSH acceleration, caching, format support, and tuning options for large image collections.

---

## Table of Contents

1. [HEIC/HEIF Support](#heicheif-support)
2. [Video Duplicate Detection](#video-duplicate-detection)
3. [LSH Acceleration](#lsh-acceleration)
4. [Performance Optimizations](#performance-optimizations)
5. [Caching System](#caching-system)
6. [Troubleshooting](#troubleshooting)

---

## HEIC/HEIF Support

PixSieve supports Apple's HEIC/HEIF formats through the `pillow-heif` library:

```bash
pip install pillow-heif
```

If `pillow-heif` is not installed, HEIC/HEIF files are skipped with a warning. All other formats continue to work normally.

**Supported modern formats:** HEIC/HEIF (requires pillow-heif), WebP, AVIF, JPEG XL

To verify HEIC support is active:

```python
from pixsieve import has_heif_support
print(has_heif_support())  # True if available
```

---

## Video Duplicate Detection

Video support (`--include-videos` / `includeVideos`) is opt-in and off by default — video decoding via `opencv-python-headless` is much slower than image analysis. When enabled:

- Each video is sampled at 5 evenly-spaced frames (skipping the first/last 5% to avoid title cards and fade artifacts) and hashed with the same pHash routine used for images.
- Duplicate matching for videos is **brute-force only** (no LSH) — video libraries are expected to be orders of magnitude smaller than image libraries, so O(n²) is fine in practice.
- Video analysis results are cached the same way image analysis is, keyed by path + mtime + size.

Install with `pip install opencv-python-headless` or `pip install pixsieve[video]`.

---

## LSH Acceleration

For large collections, perceptual duplicate detection uses Locality-Sensitive Hashing (LSH) to achieve near-linear performance instead of the quadratic brute-force approach.

### How It Works

Traditional perceptual matching compares every pair of images — O(n²) comparisons. For 650K images, that's **211 billion comparisons**, completely impractical.

LSH uses a clever indexing technique:
1. **Build index**: Sample random bits from each perceptual hash to create bucket keys
2. **Query candidates**: Only images that share at least one bucket are compared
3. **Verify matches**: Check actual Hamming distance only for candidate pairs

This reduces comparisons to approximately O(n), with typical speedups of **20–1000x**.

### Performance Impact

| Collection Size | Brute Force | LSH | Speedup |
|----------------|-------------|-----|---------|
| 1,000 images | 500K | ~50K | ~10x |
| 10,000 images | 50M | ~500K | ~100x |
| 100,000 images | 5B | ~10M | ~500x |
| 650,000 images | 211B | ~200M | ~1000x |

### Auto-Selection

LSH is automatically enabled when:
- Collection has **≥1,000 images** (configurable via `LSH_AUTO_THRESHOLD`)
- Perceptual matching is enabled (not `--exact-only`)

Override with `--lsh` or `--no-lsh` from the CLI.

### Auto-Tuned Parameters

| Collection Size | Tables | Bits/Table | Measured Recall (threshold ≤10) | Measured Recall (threshold 15, at the boundary) |
|----------------|--------|------------|----------------------------------|--------------------------------------------------|
| < 10K | 15 | 20 | >99.5% | **~99.1%** |
| 10K–50K | 18 | 18 | >99.5% | >99.5% |
| 50K–200K | 20 | 16 | >99.5% | >99.5% |
| 200K–500K | 25 | 14 | >99.5% | >99.5% |
| ≥ 500K | 30 | 12 | not separately measured | not separately measured |

These figures are empirically measured (`tests/test_lsh_recall.py`, synthetic bit-injection at
controlled Hamming distances - not just asserted) rather than theoretical, with one exception noted
below. For `threshold` values up to 10, every measured tier clears >99.5% recall, including exactly
at the threshold boundary. The one real exception among the measured tiers: the smallest tier's
parameters (<10K images, only 15 tables), at `threshold=15` (the top of the recommended 5-15 range)
and exactly at the boundary distance, measure **around 99.1%** recall - materially below a ">99.9%"
claim. This matches the caveat below, just with a number behind it now. The ≥500K tier's parameters
exist in `lsh.py::calculate_optimal_params()` and are exercised by the scanner at that collection
size, but `tests/test_lsh_recall.py` does not currently include a synthetic recall run at that scale
- more tables and fewer bits/table than the 200K-500K tier is the same direction of tradeoff that
tier already makes, so it's expected to behave similarly, but that's an expectation, not a
measurement.

**Note:** LSH is probabilistic and may occasionally miss edge-case duplicates at exactly the threshold boundary - this is most pronounced for small collections (<10K images) using `threshold` values toward the higher end (15) of the recommended range. For critical applications, use `--no-lsh` to force brute-force comparison, or prefer a lower `threshold` if your collection is small.

---

## Performance Optimizations

### Memory-Efficient LSH (v2.2.0)

The LSH implementation uses a **streaming generator** for candidate pair iteration, avoiding materializing all pairs in memory:

| Collection Size | Old Memory | New Memory |
|-----------------|------------|------------|
| 50K images | 100–500 MB | ~0 MB |
| 200K images | 1–5 GB | ~0 MB |
| 650K images | 2.4–24 GB | ~0 MB |

Also uses **Union-Find with rank optimization** to skip already-grouped pairs in O(α(n)) amortized time.

### Single-Pass File Discovery (v2.2.0)

File discovery uses a single directory traversal with case-insensitive extension matching instead of separate traversals per extension variant. Provides a **20–30% speedup** in the file discovery phase.

### Batched Progress Callbacks (v2.2.0)

Progress callbacks are batched (every 1000 files or 1 second) to reduce threading overhead, providing a **10–15% speedup** during image analysis.

### Optional File Hashing (v2.2.0)

When doing perceptual-only matching, skip the SHA-256 calculation for a **10–20% speedup**:

```python
analyzed, stats = analyze_images_parallel(images, calculate_hash=False)
```

### Combined Impact

| Optimization | Phase | Improvement |
|--------------|-------|-------------|
| Memory-efficient LSH | Perceptual matching | 90%+ memory reduction |
| Single-pass discovery | File discovery | 20–30% faster |
| Batched callbacks | Image analysis | 10–15% faster |
| Optional hashing | Image analysis | 10–20% faster (when applicable) |

For a typical 50K image scan, these optimizations can save 2–5 minutes of processing time.

---

## Caching System

PixSieve uses SQLite to cache image analysis results, dramatically speeding up subsequent scans.

### How It Works

- **Cache location:** `~/.duplicate_finder_cache.db`
- **Cache key:** File path + modification time + file size
- **Invalidation:** Automatic when a file's modification time or size changes. A *moved* file
  is a fresh cache miss under its new path - the old path's row isn't deleted immediately, it
  becomes orphaned until the next cleanup (`POST /api/cache/cleanup`, or `cleanup_missing()`).
  **Known edge case:** if a file's content changes but its modification time *and* size both
  happen to stay identical, the cache has no way to detect this and will keep serving stale
  results - there is no content-hash fallback in the cache key.

### Performance Impact

| Scenario | Typical Time (650K images) |
|----------|---------------------------|
| First scan (empty cache) | 15–30 minutes |
| Re-scan (100% cache hits) | 30–60 seconds |
| Re-scan after adding 1K photos | 1–2 minutes |

### Cache Management (GUI)

After a scan completes, a cache info banner appears with options to:
- **View stats**: Number of cached images and database size
- **Cleanup**: Remove entries for deleted files and stale data
- **Clear**: Wipe the entire cache to force fresh analysis

### Cache Management (CLI/API)

```bash
# Get cache statistics
curl http://localhost:5000/api/cache/stats

# Remove stale entries (files deleted, entries older than 30 days)
curl -X POST http://localhost:5000/api/cache/cleanup \
  -H "Content-Type: application/json" \
  -d '{"max_age_days": 30}'

# Wipe all cached data
curl -X POST http://localhost:5000/api/cache/clear
```

---

## Troubleshooting

**Scan is slow on first run**
Normal — the first scan must analyze every image. Subsequent scans use caching and are much faster.

**Perceptual matching is slow**
For collections under 1,000 images, brute-force is used by default. For larger collections, LSH activates automatically. Check with `--verbose` to see which mode is active. Force LSH with `--lsh`.

**Cache is using too much disk space**
Use the "Manage Cache" button in the GUI or run `POST /api/cache/cleanup`.

**Images aren't detected as duplicates**
Try lowering the threshold (e.g., `--threshold 5`) for stricter matching or raising it (e.g., `--threshold 15`) for looser matching.

**LSH is missing some duplicates**
LSH is probabilistic and may occasionally miss edge cases at exactly the threshold boundary. Use `--no-lsh` for brute-force comparison, or lower the threshold slightly.

**HEIC files not being processed**
Install HEIC support with `pip install pillow-heif`, then verify with `python -c "from pixsieve import has_heif_support; print(has_heif_support())"`.
