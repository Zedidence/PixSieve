# Duplicate Image Finder & Media Manager

A comprehensive tool for finding duplicate and visually similar images, plus a full suite of media file operations. Features both a web GUI and command-line interface, intelligent caching for fast re-scans, LSH-accelerated perceptual matching for large collections, and optimized handling of 650K+ image libraries.

## Features

### Duplicate Detection
- **Multi-stage detection**: Exact hash matching + perceptual hash for visually similar images
- **LSH acceleration**: O(n) perceptual matching instead of O(n²) for large collections
- **45+ image formats**: Including RAW formats (CR2, NEF, ARW, DNG) and modern formats (HEIC/HEIF)
- **Quality-based selection**: Automatically identifies the highest quality version to keep
- **SQLite caching**: Re-scans are 10–100x faster

### Media File Operations
- **Move**: Flatten directory hierarchy or move with structure preservation
- **Rename**: Random alphanumeric names or parent-folder-based naming
- **Sort**: Alphabetical grouping or color-based sorting with K-means clustering
- **Convert**: Fix wrong extensions, batch convert PNG/BMP/WEBP to JPG
- **Metadata**: Randomize EXIF dates and file system timestamps
- **Cleanup**: Delete empty folders recursively
- **Pipeline**: Chain multiple operations in a single command

### Interface
- **Web GUI**: Browser interface with tabbed navigation for duplicates and operations
- **CLI**: Subcommand architecture for automation and scripting
- **Safe by default**: Dry-run mode on all operations, confirmation required for destructive actions
- **Session recovery**: GUI remembers your progress if you close the browser

---

## Installation

```bash
# Clone or download, then install dependencies
pip install -r requirements.txt

# Or install as a package
pip install -e .
```

**Dependencies:** Python 3.9+, Pillow, imagehash, Flask, numpy, tqdm, scikit-learn, piexif, pillow-heif (optional, for HEIC/HEIF). Optional: pywin32 (Windows file creation time).

---

## Quick Start

### Web GUI

```bash
python -m dupefinder
```

Opens a browser at `http://localhost:5000`. Use the **Duplicate Finder** tab to scan and review duplicates, and the **File Operations** tab for all 12 file management operations.

### Command Line

```bash
# Scan for duplicates — report only
python -m dupefinder cli /path/to/photos

# Move duplicates to a trash folder
python -m dupefinder cli duplicates /path/to/photos --action move --trash-dir ./trash --no-dry-run

# File operations (all default to dry-run — add --no-dry-run to execute)
python -m dupefinder cli move-to-parent /path/to/photos --no-dry-run
python -m dupefinder cli rename random /path/to/photos --length 16 --no-dry-run
python -m dupefinder cli sort alpha /path/to/photos --no-dry-run
python -m dupefinder cli pipeline /path/to/photos \
  --steps "random_rename,convert_jpg,cleanup_empty" --no-dry-run
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/cli.md](docs/cli.md) | Full CLI reference — all subcommands and options |
| [docs/api.md](docs/api.md) | REST API and Python library reference |
| [docs/operations.md](docs/operations.md) | Detailed guide for all 12 file operations |
| [docs/performance.md](docs/performance.md) | LSH, caching, HEIC support, and performance tuning |
| [docs/changelog.md](docs/changelog.md) | Version history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and contribution guidelines |
| [tests/README.md](tests/README.md) | Testing guide |

---

## Package Structure

```
DupeFinderGUI/
├── dupefinder/
│   ├── config.py        # Configuration constants
│   ├── models.py        # ImageInfo and DuplicateGroup data classes
│   ├── lsh.py           # Locality-Sensitive Hashing implementation
│   ├── database/        # SQLite caching backend
│   ├── scanner/         # Core scanning and duplicate detection
│   ├── operations/      # 12 media file operations
│   ├── cli/             # Command-line interface
│   ├── api/             # Flask REST API
│   └── utils/           # Shared utilities
├── docs/                # Reference documentation
├── tests/               # Test suite (229 tests)
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
**Repository:** [Zedidence/DupeFinderGUI](https://github.com/Zedidence/DupeFinderGUI)
