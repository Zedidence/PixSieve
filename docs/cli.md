# CLI Reference

The CLI uses a subcommand architecture. The `duplicates` subcommand is the default when no subcommand is given, keeping existing usage patterns fully backward compatible.

```
python -m pixsieve [gui | cli [subcommand]]
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
   - [strip-ratings](#strip-ratings)
   - [pipeline](#pipeline)
3. [Web-GUI-only operations](#web-gui-only-operations)
4. [Configuration](#configuration)

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

# Include video files (requires opencv-python-headless)
python -m pixsieve cli /path/to/photos --include-videos

# Mark one scanned directory as the canonical reference (never modified)
python -m pixsieve cli duplicates /library /incoming --reference-dir /library --no-dry-run
```

### Options

| Option | Description |
|--------|-------------|
| `directory` | One or more directories to scan (space-separated for multiple) |
| `--no-recursive` | Don't scan subdirectories |
| `-t, --threshold N` | Perceptual hash threshold (0–64, lower = stricter). Default: 10 |
| `--exact-only` | Only find exact duplicates |
| `--perceptual-only` | Only find perceptual duplicates |
| `--include-videos` | Also scan/deduplicate video files (mp4, mov, avi, mkv, etc). Requires `opencv-python-headless` (`pip install pixsieve[video]`). Off by default |
| `--reference-dir PATH` | Mark one of the scanned directories as the canonical/reference folder — its images are never deleted/moved, and any group containing one auto-keeps all reference copies. Must exactly match one of the given `directory` arguments |
| `--auto-select-strategy` | Which file to auto-keep in groups with no reference image: `quality`, `largest`, `smallest`, `newest`, `oldest`. Default: `quality` |
| `--no-resolve-symlinks` | Skip symlink canonicalization during discovery (5–15% faster on drives with no symlinks; trades off deduping files reachable via multiple symlinks) |
| `--lsh` | Force LSH acceleration on (useful for 1K–5K images) |
| `--no-lsh` | Force brute-force comparison (disable LSH auto-selection) |
| `-a, --action ACTION` | Action: `report`, `delete`, `move`, `hardlink`, `symlink`. Default: `report` |
| `--trash-dir PATH` | Directory for moved duplicates (for `--action move`) |
| `--no-dry-run` | Actually perform the action |
| `-w, --workers N` | Number of parallel workers |
| `-e, --export PATH` | Export results to file |
| `--export-format FMT` | Export format: `txt` or `csv` |
| `--no-cache` | Disable SQLite caching |
| `-v, --verbose` | Verbose output |
| `--no-progress` | Disable progress bars (useful for piping output) |
| `--log-file PATH` | Also write logs to this file (recommended for long, unattended scans) |

**Platform notes:** `--action hardlink` requires the same filesystem; on Windows it needs admin privileges. `--action symlink` on Windows needs admin privileges or Developer Mode.

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
| `--no-dry-run` | Actually perform the operation (default is dry-run/simulate only) |

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
| `--no-dry-run` | Actually perform the operation (default is dry-run/simulate only) |

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
| `--no-dry-run` | Actually perform the operation (default is dry-run/simulate only) |

`rename parent` takes no operation-specific options beyond the common `--no-dry-run`/`-v`.

---

### sort

Two CLI sort strategies: `alpha` (alphabetical grouping) and `color` (color-based sorting). Resolution-based sorting is available in the web GUI only (see [Web-GUI-only operations](#web-gui-only-operations)).

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
| `--no-dry-run` | Actually perform the operation (default is dry-run/simulate only) |

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
| `--no-dry-run` | Actually perform the operation (default is dry-run/simulate only) |

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
| `--no-dry-run` | Actually perform the operation (default is dry-run/simulate only) |

---

### metadata

A single `randomize-dates` sub-subcommand covers both filesystem timestamps and EXIF date tags — there is no longer a separate `randomize-exif` command. By default it randomizes filesystem timestamps **and** EXIF `DateTimeOriginal`/`DateTimeDigitized`/`DateTime` for JPG/TIFF files; pass `--no-exif` to touch filesystem timestamps only.

```bash
# Randomize filesystem timestamps + EXIF dates (default)
python -m pixsieve cli metadata randomize-dates /path/to/photos \
  --start 2020-01-01 --end 2023-12-31 --no-dry-run

# Filesystem timestamps only, skip EXIF
python -m pixsieve cli metadata randomize-dates /path/to/photos \
  --start 2020-01-01 --end 2023-12-31 --no-exif --no-dry-run
```

| Option | Description |
|--------|-------------|
| `--start` | Start date `YYYY-MM-DD` (required) |
| `--end` | End date `YYYY-MM-DD` (required) |
| `--no-exif` | Skip EXIF date update; only update filesystem timestamps |
| `--no-recursive` | Do not process subdirectories |
| `--no-dry-run` | Actually perform the operation (default is dry-run/simulate only) |

> Note: the REST API's equivalent endpoint (`POST /api/operations/metadata/randomize-dates`) defaults the other way — its `syncExif` field defaults to `false` (filesystem-only), for backward compatibility with the route's old filesystem-only behavior. Pass `"syncExif": true` explicitly to also write EXIF dates via the API. See [api.md](api.md).

---

### cleanup

Recursively delete all empty directories.

```bash
python -m pixsieve cli cleanup /path/to/photos --no-dry-run
```

---

### strip-ratings

Remove 5-star/favorite rating tags (`Rating`, `RatingPercent`, `XMP:Rating`, `EXIF:Rating`) from images. Requires the `exiftool` binary on `PATH`.

```bash
python -m pixsieve cli strip-ratings /path/to/photos
python -m pixsieve cli strip-ratings /path/to/photos --no-recursive --no-dry-run
```

| Option | Description |
|--------|-------------|
| `--no-recursive` | Do not process subdirectories |
| `--no-dry-run` | Actually perform the operation (default is dry-run/simulate only) |

**Note:** destructive — files are modified in place with no backup, using `exiftool -overwrite_original`.

---

### pipeline

Chain multiple operations in a single command. Steps run sequentially.

```bash
# Basic pipeline
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,convert_jpg,cleanup_empty" --no-dry-run

# Pipeline including a date step
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,randomize_dates,cleanup_empty" \
  --start 2020-01-01 --end 2023-12-31 --no-dry-run

# Customize individual step settings
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,convert_jpg" \
  --length 16 --quality 90 --delete-originals --no-dry-run
```

**Available steps:** `random_rename`, `convert_jpg`, `randomize_dates`, `cleanup_empty`, `repair_corrupt`

| Option | Description |
|--------|-------------|
| `--steps` | Comma-separated list of steps (required) |
| `--start` | Start date for the `randomize_dates` step (`YYYY-MM-DD`) |
| `--end` | End date for the `randomize_dates` step (`YYYY-MM-DD`) |
| `--length` | Random name length for `random_rename` (default: 12) |
| `--quality` | JPG quality for `convert_jpg` (default: 95) |
| `--delete-originals` | Delete originals after `convert_jpg` |
| `--no-recursive` | Do not process subdirectories |
| `--no-dry-run` | Actually perform the operation (default is dry-run/simulate only) |

**Note:** the CLI has no `--trash-dir` flag for pipeline, so a `repair_corrupt` step run from the CLI always quarantines to the built-in `DEFAULT_TRASH_DIR` rather than a custom folder. To choose a custom trash folder for repair, use the web GUI or REST API instead.

---

## Web-GUI-only operations

Two operations exist in `pixsieve.operations` and the REST API but have no CLI subcommand:

- **Sort by resolution** (`sort_by_resolution()` / `POST /api/operations/sort/resolution`) — sorts images into `sorted_by_resolution/<category>/<orientation>/` folders by resolution tier (tiny → 8k+) and orientation (landscape/portrait/square).
- **Repair corrupt images** (`scan_and_repair()` / `POST /api/operations/repair`) — scans for corrupt/unreadable images, attempts repair (re-encode → strip EXIF → convert to PNG), and quarantines files it can't fix. Available from the CLI only indirectly, as the `repair_corrupt` pipeline step (see [pipeline](#pipeline)).

See [operations.md](operations.md) for full details on both.

---

## Configuration

There is no runtime config file or general-purpose environment variable
layer — thresholds, worker counts, LSH tuning, and cache settings are plain
module constants in `pixsieve/config.py`. Change them there if you need
different defaults.

The one exception is the maximum decompressed image size PIL will accept
(guards against decompression-bomb images), which can be overridden without
editing source:

```bash
PIXSIEVE_MAX_IMAGE_PIXELS=1000000000 python -m pixsieve cli /path/to/photos
```
