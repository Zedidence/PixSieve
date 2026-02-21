# Changelog

## v3.0.0 (Current)

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
