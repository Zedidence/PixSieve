# Changelog

## Unreleased

- **Duplicate-finding correctness fixes**: hardlinked files (and, as a side effect, symlinked files even with `--no-resolve-symlinks`) are no longer double-counted as false "exact duplicate" pairs (`pixsieve.scanner.file_discovery`, identity-based dedup via `st_dev`/`st_ino`). Fixed a confirmed bug where CLI `--export` on a reference-anchored scan could show `[KEEP]`/`[DUPE]` markers that disagreed with what the delete action actually did (`utils.exporters`, `utils.selection.stamp_group_selections`). Fixed a crash (undefined-name/`AttributeError`) in perceptual-duplicate matching's malformed-hash error handler (`pixsieve.scanner.deduplication`).
- **Chain-drift mitigation for perceptual grouping**: Union-Find's transitive merging could previously fold a burst-photo sequence into one over-broad group even when the first and last images individually exceeded the similarity threshold. Groups are now diameter-checked and re-split via complete-linkage clustering when needed (`pixsieve.scanner.deduplication._split_high_diameter_groups`).
- **EXIF-orientation-aware perceptual hashing**: a photo and its EXIF-rotated (not pixel-rotated) twin are now correctly caught as duplicates (`pixsieve.scanner.hashing._ensure_phash_mode`, `ImageOps.exif_transpose()`). Bumps the analysis cache's schema version, so already-cached images are re-analyzed once on next scan to pick up corrected hashes.
- **Content-aware quality scoring**: `calculate_quality_score()` now includes a small (≤10 point) sharpness tiebreaker computed from the same thumbnail already produced for perceptual hashing - a cheap PIL-only edge-variance proxy, not a full blur/artifact detector (`pixsieve.scanner.hashing.calculate_sharpness_score`).
- **EXIF-capture-date-aware NEWEST/OLDEST selection**: the `newest`/`oldest` auto-select strategies now prefer EXIF `DateTimeOriginal` over filesystem modification time when available, falling back to mtime otherwise (`pixsieve.utils.selection`, new `ImageInfo.capture_date` field).
- **LSH recall empirically measured**: `tests/test_lsh_recall.py` measures real LSH recall (synthetic bit-injection at controlled Hamming distances) against `docs/performance.md`'s previously-unverified "Expected Recall >99.9%" claim. The claim holds for `threshold` values up to 10 across every collection-size tier; the smallest tier (<10K images) at `threshold=15` and exactly at the boundary distance measures closer to 99.1% - `docs/performance.md` updated accordingly.
- **Doc/config cleanup**: corrected `docs/performance.md`'s documented LSH auto-enable threshold (was stated as 5,000, actual `LSH_AUTO_THRESHOLD` value is 1,000) and removed the unused `LSH_DEFAULT_TABLES`/`LSH_DEFAULT_BITS` config constants (dead code - `scanner.lsh.calculate_optimal_params()` hardcodes its own tiered values). Documented the cache key's known mtime+size-coincidence staleness edge case and the API-only (not CLI) large-library perceptual-auto-disable threshold.
- **Video duplicate detection** (opt-in): `--include-videos` (CLI) / `includeVideos` (API) samples evenly-spaced frames from each video and multi-frame-pHash-matches them (`pixsieve.scanner.video_analysis`, `video_deduplication`). Brute-force only (no LSH) — video collections are expected to be much smaller than image collections. Requires `opencv-python-headless` (`pip install pixsieve[video]`).
- **Built-in lightbox image editor**: rotate/flip/crop with a shared undo/redo stack (capped at 10), rename, and delete, directly from the duplicate-group lightbox (`static/js/editor.js`, `POST /api/image/save`, `POST /api/image/rename`). Preview-then-explicit-Save model — nothing touches disk until Save is clicked. Edits are blocked on files inside a protected reference folder.
- **Repair corrupt images** operation: scans for corrupt/unreadable images, attempts repair in order (re-encode → strip EXIF → convert to PNG), and quarantines files it can't fix (`pixsieve.operations.repair`, `POST /api/operations/repair`, and the `repair_corrupt` pipeline step). Web GUI / API only — no dedicated CLI subcommand.
- **Strip favorite ratings** operation: removes 5-star/favorite rating tags via `exiftool` (`pixsieve.operations.ratings`, CLI `strip-ratings`, `POST /api/operations/metadata/strip-ratings`). Ported from the standalone `faveRemover` script.
- **Sort by resolution**: sorts images into `sorted_by_resolution/<category>/<orientation>/` folders by resolution tier (tiny → 8k+) and orientation (`pixsieve.operations.sort.sort_by_resolution`, `POST /api/operations/sort/resolution`). Web GUI / API only.
- **Merged EXIF/filesystem date randomization**: `randomize_exif_dates()` and `randomize_file_dates()` are replaced by a single `randomize_dates(sync_exif=...)`; the CLI's separate `metadata randomize-exif` subcommand is gone in favor of `metadata randomize-dates --no-exif`. Added `randomize_dates_per_folder()` (API/GUI only) for a distinct date range per folder.
- **Multi-directory scans with a reference folder**: `POST /api/scan` now takes a `directories` array (each with `isReference`) instead of a single `directory` string; a reference directory's images are never modified and are auto-kept in any group they appear in. Mirrors the CLI's existing `--reference-dir`/`--auto-select-strategy` flags.
- **New endpoints**: `/api/list-subfolders`, `/api/cancel`, `/api/pause`, `/api/resume`, `/api/scan/stream` (SSE), `/api/apply_strategy`, `/api/thumbnail` (cached, video-aware), `/api/delete/status` + `/api/delete/stream` (move-to-trash is now backgrounded), `/api/operations/stream` (SSE), `/api/operations/metadata/randomize-dates-per-folder`.
- **New dependency**: `opencv-python-headless` (optional, `video` extra) for video frame sampling.

## v3.1.0

- **Compare slider**: Drag-to-reveal before/after overlay for any 2-image duplicate group (⇔ Slider button in group header). Keyboard nudging, touch support, Esc to close.
- **Quick Operations**: "⚡ Quick Ops" dropdown in the action bar — "Convert to JPG" and "Move to Folder…" run on deletion-marked files via the new `/api/batch-operation` endpoint.
- **Pydantic v2 request validation**: `pixsieve/api/schemas.py` with typed models; key routes now validate through `parse_request()` instead of ad-hoc `data.get()`.
- **Swagger UI** (`flasgger`): Interactive API docs at `/docs/`, spec at `/apispec.json`. Key routes annotated with OpenAPI 3.0 YAML docstrings.
- **Web Worker filtering**: `filter-worker.js` offloads `applyFilters()` off the main thread with a synchronous fallback.
- **Playwright E2E tests**: `tests/e2e/test_critical_flows.py` — app shell, scan flows, results view, API health checks.
- **Docker support**: `Dockerfile` + `docker-compose.yml` with `$PHOTO_DIR` volume mount.
- **New dependencies**: `pydantic>=2.0.0`, `flasgger>=0.9.7`; `e2e` extras group in `pyproject.toml` / `setup.py`.
- **Package data**: `static/css/` and `static/js/` are now included in the installed package.

## v3.0.0

- **Media file operations**: 12 operations across 7 modules (move, rename, sort, convert, metadata, cleanup, pipeline)
- **CLI subcommand architecture**: All operations available as CLI subcommands with full backward compatibility
- **Web GUI operations tab**: Tabbed interface with sidebar navigation for all 12 operations
- **14 new API endpoints**: Full REST API for all operations with background execution and polling
- **Color-based image sorting**: K-means clustering for dominant color, B&W classification, and palette sorting
- **Pipeline workflows**: Chain operations (rename, convert, date randomization, cleanup) in a single command
- **EXIF manipulation**: Randomize EXIF dates and file system timestamps with piexif
- **229 tests**: 157 new operation tests + 72 existing tests, all passing
- **New dependencies**: scikit-learn (color sorting), piexif (EXIF manipulation), tqdm (now required)
- **Bug fix**: Windows file locking in `fix_extensions()` — files now closed before rename

## v2.2.0

- **Memory-efficient LSH**: Generator-based candidate pair iteration eliminates memory explosion for large collections (90%+ memory reduction for 650K+ images)
- **Union-Find with rank optimization**: Faster grouping with O(α(n)) amortized complexity
- **Single-pass file discovery**: 20–30% faster directory scanning
- **Batched progress callbacks**: 10–15% faster analysis by reducing threading overhead
- **Optional file hashing**: New `calculate_hash` parameter to skip SHA-256 when not needed (10–20% faster for perceptual-only scans)
- **Fixed SQLite variable limit error**: Cache queries now chunked to handle 50K+ file collections
- Added `iter_candidate_pairs()` generator method to `HammingLSH` class
- Added `estimate_candidate_pairs()` method for progress reporting without memory overhead

## v2.1.0

- **HEIC/HEIF format support** via pillow-heif with graceful fallback
- **LSH acceleration** for perceptual matching — O(n) instead of O(n²), auto-enables at ≥5,000 images
- **SQLite caching** for 10–100x faster re-scans
- **Cache management UI** in web interface with new cache API endpoints
- Support for modern image formats: WebP, AVIF, JPEG XL
- Improved progress messages with ETA and processing rate
- New `--lsh` and `--no-lsh` CLI options

## v2.0.0

- Fixed version inconsistency across files (`__init__.py` is now the single source of truth)
- Added input validation to all API endpoints (path validation, threshold range, mutual exclusivity)
- Fixed path traversal vulnerability in `/api/image` endpoint
- Added missing `--lsh`, `--no-lsh`, and `--no-cache` CLI flags
- Added comprehensive test suite (100+ tests: models, scanner, LSH, database)
- Added logging to all exception handlers
- Batched progress callbacks (every 0.5 seconds) to reduce overhead
- Added configuration file support (`~/.pixsieve/config.json`)
- Prepared package for PyPI distribution (pyproject.toml, MANIFEST.in, LICENSE)
- Created full REST API documentation

## v1.0.0

- Initial release with web GUI and CLI
- Multi-stage duplicate detection (exact + perceptual)
- Quality-based image ranking (resolution, file size, bit depth, format)
- Session recovery in GUI
