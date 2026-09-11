# PixSieve - Image Deduplication & Media Manager

A comprehensive tool for finding duplicate and visually similar images (and, optionally, videos), plus a full suite of media file operations and a built-in lightbox editor. Features both a web GUI and command-line interface, intelligent caching for fast re-scans, LSH-accelerated perceptual matching for large collections, and optimized handling of 650K+ image libraries.

## Features

### Duplicate Detection
- **Multi-stage detection**: Exact hash matching + perceptual hash for visually similar images
- **LSH acceleration**: O(n) perceptual matching instead of O(n²) for large collections
- **45+ image formats**: Including RAW formats (CR2, NEF, ARW, DNG) and modern formats (HEIC/HEIF)
- **Video duplicate detection** *(opt-in)*: Multi-frame perceptual hashing catches re-encoded, resized, or recompressed duplicate videos (requires `opencv-python-headless`)
- **Quality-based selection**: Automatically identifies the highest quality version to keep
- **SQLite caching**: Re-scans are 10–100x faster

### Media File Operations
- **Move**: Flatten directory hierarchy or move with structure preservation
- **Rename**: Random alphanumeric names or parent-folder-based naming
- **Sort**: Alphabetical grouping, resolution, or color-based sorting with K-means clustering
- **Convert**: Fix wrong extensions, batch convert PNG/BMP/WEBP to JPG
- **Metadata**: Randomize EXIF dates and file system timestamps
- **Ratings**: Strip 5-star/favorite rating tags from images via exiftool
- **Repair**: Scan for corrupt/unreadable images, attempt repair (re-encode → strip EXIF → convert to PNG), quarantine what can't be fixed (web GUI only)
- **Cleanup**: Delete empty folders recursively
- **Pipeline**: Chain multiple operations in a single command

### Interface
- **Web GUI**: Browser interface with tabbed navigation for duplicates and operations
- **CLI**: Subcommand architecture for automation and scripting
- **Built-in image editor**: Rotate, flip, and crop directly in the lightbox, with undo/redo, rename, and delete — nothing touches disk until you hit Save
- **Safe by default**: Dry-run mode on all operations, confirmation required for destructive actions
- **Session recovery**: GUI remembers your progress if you close the browser
- **Compare slider**: Drag-to-reveal before/after comparison for any two-image duplicate pair
- **Quick Operations**: Convert or move marked files directly from the results view without tab-switching
- **Dark/light theme**, drag-and-drop directory selection, and offline-capable via a service worker
- **OpenAPI/Swagger UI**: Interactive API docs served at `/docs/`
- **Docker support**: One-command deployment via `docker compose up`

---

## Installation

### Standard (pip)

```bash
# Clone or download, then install dependencies
pip install -r requirements.txt

# Or install as a package (includes all optional deps)
pip install -e .
```

**Required:** Python 3.9+, Pillow, imagehash, Flask, numpy, tqdm, scikit-learn, piexif, pillow-heif, pydantic, flasgger

**Optional:** pywin32 (Windows file creation time support), opencv-python-headless (video duplicate detection), playwright (E2E tests only)

### Docker

```bash
# Build and start (photos mounted read-only from $PHOTO_DIR, default: current directory)
PHOTO_DIR=/path/to/your/photos docker compose up --build

# Or edit docker-compose.yml to hardcode the volume path
docker compose up --build
```

Access the UI at `http://localhost:5000`.

---

## Quick Start

### Web GUI

```bash
python -m pixsieve
```

Opens a browser at `http://localhost:5000`. Use the **Duplicate Finder** tab to scan and review duplicates, and the **File Operations** tab for all 14 file management operations.

### Command Line

```bash
# Scan for duplicates — report only
python -m pixsieve cli /path/to/photos

# Move duplicates to a trash folder
python -m pixsieve cli duplicates /path/to/photos --action move --trash-dir ./trash --no-dry-run

# Include videos in the scan (requires opencv-python-headless)
python -m pixsieve cli duplicates /path/to/photos --include-videos

# File operations (all default to dry-run — add --no-dry-run to execute)
python -m pixsieve cli move-to-parent /path/to/photos --no-dry-run
python -m pixsieve cli rename random /path/to/photos --length 16 --no-dry-run
python -m pixsieve cli sort alpha /path/to/photos --no-dry-run
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,convert_jpg,cleanup_empty" --no-dry-run
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/cli.md](docs/cli.md) | Full CLI reference — all subcommands and options |
| [docs/api.md](docs/api.md) | REST API and Python library reference |
| [docs/operations.md](docs/operations.md) | Detailed guide for all file operations |
| [docs/performance.md](docs/performance.md) | LSH, caching, HEIC support, and performance tuning |
| [docs/modernization.md](docs/modernization.md) | Roadmap and status of all modernization work |
| [docs/changelog.md](docs/changelog.md) | Version history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and contribution guidelines |
| [tests/README.md](tests/README.md) | Testing guide (unit, integration, E2E) |

---

## Package Structure

```
PixSieve/
├── pixsieve/
│   ├── config.py          # Configuration constants
│   ├── models.py          # ImageInfo and DuplicateGroup data classes
│   ├── lsh.py             # Locality-Sensitive Hashing implementation
│   ├── database/          # SQLite caching backend
│   ├── scanner/           # Core scanning and duplicate detection
│   │   ├── video_analysis.py       # Video frame sampling + perceptual hashing
│   │   └── video_deduplication.py  # Video duplicate grouping
│   ├── operations/        # 14 media file operations (incl. repair, ratings)
│   ├── cli/               # Command-line interface
│   ├── api/               # Flask REST API
│   │   ├── routes.py      # Duplicate-finder endpoints
│   │   ├── operations_routes.py  # File-operation endpoints
│   │   ├── orchestrator.py
│   │   └── schemas.py     # Pydantic v2 request validation models
│   ├── static/
│   │   ├── css/app.css
│   │   ├── js/app.js
│   │   ├── js/editor.js         # Lightbox image editor (rotate/flip/crop/rename/delete)
│   │   ├── js/filter-worker.js  # Web Worker for filtering/sorting
│   │   └── sw.js          # Service Worker (offline cache)
│   └── utils/             # Shared utilities
├── docs/                  # Reference documentation
├── tests/
│   ├── *.py               # 288 unit/integration tests
│   └── e2e/               # Playwright end-to-end tests (19 tests)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── setup.py
```

---

## How It Works

**Detection:** Exact duplicates are found via SHA-256 hash. Perceptual duplicates are found via pHash, comparing visual similarity regardless of resolution, compression, or minor edits. For collections of 5,000+ images, LSH is automatically enabled, reducing comparisons from O(n²) to near-linear.

**Quality scoring:** When duplicates are found, images are scored by resolution (50pts), file size (30pts), bit depth (10pts), and format quality (20pts — RAW > lossless > lossy). The highest-scoring image is recommended to keep.

**Threshold guide:**

| Threshold | Meaning |
|-----------|---------|
| 0 | Identical perceptual hashes only |
| 5 | Very similar — same image, minor differences |
| 10 | Similar — default, good balance |
| 15 | Somewhat similar — catches resizes/crops |
| 20+ | Loose — may have false positives |

---

## License

MIT License — feel free to use and modify!

**Author:** Zach
**Repository:** [Zedidence/PixSieve](https://github.com/Zedidence/PixSieve)
