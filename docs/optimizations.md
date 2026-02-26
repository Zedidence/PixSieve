# Optimization Backlog

Opportunities identified 2026-02-20 via codebase analysis. Checked boxes = completed.

---

## Priority Legend
- **Critical** — 50%+ potential gain, low effort
- **High** — significant gain or important for scalability
- **Medium** — meaningful improvement, moderate effort
- **Low** — minor gain or niche scenario

---

## Critical / Quick Wins

- [x] **J1: Worker Count Auto-Detection** — `DEFAULT_WORKERS = 4` is hardcoded. Auto-detect based on `cpu_count * 2`, capped by available memory.
  - Files: [`pixsieve/config.py:58`](../pixsieve/config.py), [`pixsieve/scanner/parallel.py:23`](../pixsieve/scanner/parallel.py)
  - Effort: ~1.5h | Benefit: 50–100% on modern multi-core systems

- [x] **F5: SQLite PRAGMA Tuning** — Only WAL mode is set. Add `synchronous = NORMAL`, `cache_size = -64000`, `temp_store = MEMORY`.
  - File: [`pixsieve/database/connection.py:73-74`](../pixsieve/database/connection.py)
  - Effort: ~0.5h | Benefit: 5–15%

- [x] **A2: LSH Bit Extraction Caching** — `_hash_to_bits()` calls `.flatten().tolist()` on every hash lookup. Pre-compute and cache during `add()`.
  - File: [`pixsieve/lsh.py:119-144`](../pixsieve/lsh.py)
  - Effort: ~1h | Benefit: 20–40% on LSH indexing/querying

---

## High Priority

- [x] **A1: LSH Pair Deduplication Overhead** — Duplicate pairs from multiple table collisions are yielded and deduplicated downstream. Track already-yielded pairs within LSH to reduce redundant comparisons.
  - Files: [`pixsieve/lsh.py:220-243`](../pixsieve/lsh.py), [`pixsieve/scanner/deduplication.py:280-301`](../pixsieve/scanner/deduplication.py)
  - Effort: ~2h | Benefit: 15–25% on large collections

- [x] **D2/J1: Worker Scaling (parallel)** — Companion to J1; ensure `ThreadPoolExecutor` in parallel.py actually uses the scaled worker count.
  - File: [`pixsieve/scanner/parallel.py:93-97`](../pixsieve/scanner/parallel.py)
  - Effort: ~1h | Benefit: 30–50% on 8+ core systems (depends on J1)

- [x] **G1: Dominant Color Caching for Sort** — Sort's K-means clustering re-opens and re-analyzes every image. Extract dominant color during initial `analyze_image()` and store in `ImageInfo`; reuse in sort.
  - Files: [`pixsieve/scanner/analysis.py`](../pixsieve/scanner/analysis.py), [`pixsieve/operations/sort.py`](../pixsieve/operations/sort.py)
  - Effort: ~4h | Benefit: 60–90% speedup on sort operations

---

## Medium Priority

- [x] **B1: Union-Find Rank Optimization (brute-force path)** — The LSH path uses union-by-rank; the brute-force path (< 5000 files) does not. Unify them.
  - File: [`pixsieve/scanner/deduplication.py:140-151`](../pixsieve/scanner/deduplication.py)
  - Effort: ~1h | Benefit: 5–15% on small/medium collections

- [x] **B2: Perceptual Hash Parsing Cache** — Both brute-force and LSH paths independently re-parse hex hashes to `imagehash` objects on every run. Cache parsed hashes (LRU or on `ImageInfo`).
  - File: [`pixsieve/scanner/deduplication.py:133-138`](../pixsieve/scanner/deduplication.py)
  - Effort: ~2h | Benefit: 20–30% on re-runs with warm cache

- [x] **B3: Progress Bar Update Throttling** — Progress updates every 1000 comparisons; for 50M comparisons this is still 50K updates. Throttle to 10K–100K or timer-based (0.5s).
  - File: [`pixsieve/scanner/deduplication.py:156-174`](../pixsieve/scanner/deduplication.py), [`:272-308`](../pixsieve/scanner/deduplication.py)
  - Effort: ~1h | Benefit: 2–5%

- [x] **C1: Redundant PIL Image Mode Conversion** — Image mode is checked and converted to RGB in two separate places during a single image's analysis pass.
  - Files: [`pixsieve/scanner/analysis.py:80-90`](../pixsieve/scanner/analysis.py), [`pixsieve/scanner/hashing.py:61-67`](../pixsieve/scanner/hashing.py)
  - Effort: ~1h | Benefit: 5–10%

- [x] **F1: Database Write Lock Contention** — Single `threading.Lock()` serializes all DB writes. Fine at 4 workers, bottleneck at 8–16. Implement queue-based batch writer.
  - File: [`pixsieve/database/connection.py:34`](../pixsieve/database/connection.py)
  - Effort: ~3h | Benefit: 20–40% (critical if J1 is implemented)

- [x] **F3: Cache Cleanup Memory Usage** — `cleanup_missing()` loads ALL cached paths into memory before checking existence. Batch in chunks instead.
  - File: [`pixsieve/database/maintenance.py:59-84`](../pixsieve/database/maintenance.py)
  - Effort: ~1h | Benefit: 100–500MB RAM savings for large caches

- [x] **F4: Database Index Coverage** — `idx_images_phash_prefix` only indexes the first 8 hex chars (32 bits). Consider extending prefix length to 12–16 chars or adding a full index.
  - File: [`pixsieve/database/schema.py:94-96`](../pixsieve/database/schema.py)
  - Effort: ~1h | Benefit: 10–20% on phash queries

- [x] **G2: Parallel Move Operations** — `shutil.move()` calls are sequential. Parallelize with a bounded `ThreadPoolExecutor` (4–8 workers).
  - File: [`pixsieve/operations/move.py`](../pixsieve/operations/move.py)
  - Effort: ~3h | Benefit: 30–50% on large file moves

- [x] **I1: ImageInfo Streaming** — All `ImageInfo` objects accumulate in RAM before deduplication begins. Stream results to the DB immediately after analysis and read back for dedup.
  - Files: [`pixsieve/scanner/parallel.py:48`](../pixsieve/scanner/parallel.py), [`pixsieve/scanner/deduplication.py:88`](../pixsieve/scanner/deduplication.py)
  - Effort: ~4h | Benefit: 100–200MB RAM savings for very large collections

---

## Low Priority

- [x] **A3: LSH Bucket Distribution Analysis** — Warn when bucket size distribution is heavily skewed (CV > 1.0), suggesting parameter tuning. Informational/UX.
  - File: [`pixsieve/lsh.py:258-263`](../pixsieve/lsh.py)
  - Effort: ~1h | Benefit: Better parameter tuning guidance

- [x] **D3: Bounded Queue Future Submission** — All futures are submitted immediately before any results are consumed. For 650K+ files, bound the in-flight queue size.
  - File: [`pixsieve/scanner/parallel.py:94-97`](../pixsieve/scanner/parallel.py)
  - Effort: ~2h | Benefit: < 5% (memory edge case)

- [x] **E2: Symlink Resolution Toggle** — `filepath.resolve()` is called for every discovered file. A `--no-resolve-symlinks` flag would skip this on local drives.
  - File: [`pixsieve/scanner/file_discovery.py:50`](../pixsieve/scanner/file_discovery.py)
  - Effort: ~1h | Benefit: 5–15% if disabled

- [x] **G3: Parallel EXIF Operations** — EXIF metadata stripping processes one file at a time via piexif. Parallelize like the analysis phase.
  - File: [`pixsieve/operations/metadata.py`](../pixsieve/operations/metadata.py)
  - Effort: ~2h | Benefit: 20–30%

- [x] **C3: Configurable Decompression Bomb Limit** — `Image.MAX_IMAGE_PIXELS = 500_000_000` is hardcoded. Make it a config parameter.
  - File: [`pixsieve/scanner/dependencies.py:44`](../pixsieve/scanner/dependencies.py)
  - Effort: ~0.5h | Benefit: Flexibility for scientific/aerial imagery users

---

## Already Well-Implemented (For Reference)

| Feature | File | Notes |
|---------|------|-------|
| LSH streaming via generator | `lsh.py` | 90%+ memory reduction vs materializing all pairs |
| Batched progress callbacks | `parallel.py:88-123` | 10–15% speedup, timer-based |
| DB write chunking (500-item) | `database/operations.py` | Respects SQLite 999 variable limit |
| Single-pass file discovery | `file_discovery.py` | 20–30% vs multi-pass |
| `calculate_hash` opt-out | `analysis.py:51-52` | 10–20% speedup for perceptual-only scans |
| Union-Find with rank (LSH path) | `deduplication.py:237-256` | Correct, only brute-force path lags |
