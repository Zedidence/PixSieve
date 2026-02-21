# CLI Reference

The CLI uses a subcommand architecture. The `duplicates` subcommand is the default when no subcommand is given, keeping existing usage patterns fully backward compatible.

```
python -m pixsieve [gui | cli [subcommand] | config]
```

---

## Table of Contents

1. [Duplicate Detection](#duplicate-detection)
2. [File Operations](#file-operations)
   - [move-to-parent](#move-to-parent)
   - [move](#move)
   - [rename](#rename)
   - [sort](#sort)
   - [fix-extensions](#fix-extensions)
   - [convert](#convert)
   - [metadata](#metadata)
   - [cleanup](#cleanup)
   - [pipeline](#pipeline)
3. [Configuration](#configuration)

---

## Duplicate Detection

```bash
# Basic scan — report only (backward compatible)
python -m pixsieve cli /path/to/photos

# Explicit subcommand
python -m pixsieve cli duplicates /path/to/photos --threshold 5

# Move duplicates to a folder
python -m pixsieve cli duplicates /path/to/photos --action move --trash-dir ./trash --no-dry-run

# Actually delete duplicates (BE CAREFUL)
python -m pixsieve cli duplicates /path/to/photos --action delete --no-dry-run

# Force LSH on or off
python -m pixsieve cli /path/to/photos --lsh
python -m pixsieve cli /path/to/photos --no-lsh

# Export results
python -m pixsieve cli /path/to/photos --export results.csv --export-format csv
```

### Options

| Option | Description |
|--------|-------------|
| `-r, --no-recursive` | Don't scan subdirectories |
| `-t, --threshold N` | Perceptual hash threshold (0–64, lower = stricter). Default: 10 |
| `--exact-only` | Only find exact duplicates |
| `--perceptual-only` | Only find perceptual duplicates |
| `--lsh` | Force LSH acceleration on |
| `--no-lsh` | Force brute-force comparison (disable LSH) |
| `-a, --action ACTION` | Action: `report`, `delete`, `move`, `hardlink`, `symlink` |
| `--trash-dir PATH` | Directory for moved duplicates |
| `--no-dry-run` | Actually perform the action |
| `-w, --workers N` | Number of parallel workers. Default: 4 |
| `-e, --export PATH` | Export results to file |
| `--export-format FMT` | Export format: `txt` or `csv` |
| `--no-cache` | Disable SQLite caching |
| `-v, --verbose` | Verbose output |

---

## File Operations

All operation subcommands default to **dry-run mode**. Use `--no-dry-run` to execute. All accept `-v, --verbose` for detailed output.

### move-to-parent

Move all images from subdirectories into the parent folder, flattening the hierarchy.

```bash
python -m pixsieve cli move-to-parent /path/to/photos
python -m pixsieve cli move-to-parent /path/to/photos --extensions .jpg .png --no-dry-run
```

| Option | Description |
|--------|-------------|
| `--extensions` | Only move files with these extensions (e.g., `.jpg .png`) |
| `--dry-run / --no-dry-run` | Simulate or execute |

---

### move

Move files from source to destination while preserving directory structure.

```bash
python -m pixsieve cli move /path/to/source /path/to/dest
python -m pixsieve cli move /path/to/source /path/to/dest --overwrite --no-dry-run
```

| Option | Description |
|--------|-------------|
| `destination` | Destination directory (required) |
| `--overwrite` | Overwrite existing files at destination |
| `--dry-run / --no-dry-run` | Simulate or execute |

---

### rename

Two rename strategies: `random` (alphanumeric names) and `parent` (folder-based names).

```bash
# Random alphanumeric names
python -m pixsieve cli rename random /path/to/photos --length 16 --no-dry-run
python -m pixsieve cli rename random /path/to/photos --extensions .jpg .png --no-recursive

# Parent-folder-based names (e.g., ArtistA_AlbumX_1.jpg)
python -m pixsieve cli rename parent /path/to/photos --no-dry-run
```

**Options for `rename random`:**

| Option | Description |
|--------|-------------|
| `--length` | Length of random name (default: 12) |
| `-w, --workers` | Number of parallel workers (default: 4) |
| `--extensions` | Only rename files with these extensions |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run / --no-dry-run` | Simulate or execute |

---

### sort

Two sort strategies: `alpha` (alphabetical grouping) and `color` (color-based sorting).

```bash
# Sort into A-G, H-N, O-T, U-Z, 0-9 folders
python -m pixsieve cli sort alpha /path/to/photos --no-dry-run

# Sort by dominant color
python -m pixsieve cli sort color /path/to/photos --method dominant --no-dry-run

# Classify as color vs. black & white
python -m pixsieve cli sort color /path/to/photos --method bw --no-dry-run

# Sort by color palette (3 colors)
python -m pixsieve cli sort color /path/to/photos --method palette --n-colors 3

# Analyze color distribution without moving files
python -m pixsieve cli sort color /path/to/photos --method analyze

# Copy instead of move
python -m pixsieve cli sort color /path/to/photos --method dominant --copy --no-dry-run
```

**Options for `sort color`:**

| Option | Description |
|--------|-------------|
| `--method` | `dominant`, `bw`, `palette`, or `analyze` (default: `dominant`) |
| `--copy` | Copy files instead of moving |
| `--n-colors` | Number of palette colors for `palette` method (default: 3) |
| `--dry-run / --no-dry-run` | Simulate or execute |

---

### fix-extensions

Rename files whose extensions don't match their actual image format.

```bash
python -m pixsieve cli fix-extensions /path/to/photos
python -m pixsieve cli fix-extensions /path/to/photos --no-recursive --no-dry-run
```

| Option | Description |
|--------|-------------|
| `--no-recursive` | Do not process subdirectories |
| `--dry-run / --no-dry-run` | Simulate or execute |

---

### convert

Convert PNG, BMP, and WEBP images to JPG.

```bash
python -m pixsieve cli convert /path/to/photos --quality 90 --no-dry-run
python -m pixsieve cli convert /path/to/photos --delete-originals --no-dry-run
```

| Option | Description |
|--------|-------------|
| `--quality` | JPG quality 1–100 (default: 95) |
| `--delete-originals` | Delete original files after conversion |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run / --no-dry-run` | Simulate or execute |

---

### metadata

Two metadata operations: `randomize-exif` (EXIF dates) and `randomize-dates` (file system timestamps).

```bash
# Randomize EXIF date fields
python -m pixsieve cli metadata randomize-exif /path/to/photos \
  --start 2020-01-01 --end 2023-12-31 --no-dry-run

# Randomize file system timestamps
python -m pixsieve cli metadata randomize-dates /path/to/photos \
  --start 2020-01-01 --end 2023-12-31 --no-dry-run
```

| Option | Description |
|--------|-------------|
| `--start` | Start date `YYYY-MM-DD` (required) |
| `--end` | End date `YYYY-MM-DD` (required) |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run / --no-dry-run` | Simulate or execute |

---

### cleanup

Recursively delete all empty directories.

```bash
python -m pixsieve cli cleanup /path/to/photos --no-dry-run
```

---

### pipeline

Chain multiple operations in a single command. Steps run sequentially.

```bash
# Basic pipeline
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,convert_jpg,cleanup_empty" --no-dry-run

# Pipeline including date operations
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,randomize_exif,randomize_dates,cleanup_empty" \
  --start 2020-01-01 --end 2023-12-31 --no-dry-run

# Customize individual step settings
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,convert_jpg" \
  --length 16 --quality 90 --delete-originals --no-dry-run
```

**Available steps:** `random_rename`, `convert_jpg`, `randomize_exif`, `randomize_dates`, `cleanup_empty`

| Option | Description |
|--------|-------------|
| `--steps` | Comma-separated list of steps (required) |
| `--start` | Start date for date steps (`YYYY-MM-DD`) |
| `--end` | End date for date steps (`YYYY-MM-DD`) |
| `--length` | Random name length for `random_rename` (default: 12) |
| `--quality` | JPG quality for `convert_jpg` (default: 95) |
| `--delete-originals` | Delete originals after `convert_jpg` |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run / --no-dry-run` | Simulate or execute |

---

## Configuration

```bash
# View current configuration
python -m pixsieve config

# Create a config file at ~/.pixsieve/config.json
python -m pixsieve config --init
```

Configuration supports environment variables > config file > defaults. Configurable settings include thresholds, workers, LSH settings, cache settings, and file paths.
