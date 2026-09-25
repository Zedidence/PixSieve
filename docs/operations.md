# File Operations Reference

Complete reference for all media file operations available in PixSieve.

Most operations are accessible via **CLI subcommands**, **Web GUI**, and **REST API**. Two ([Sort by Resolution](#sort-by-resolution) and [Repair Corrupt Images](#repair-corrupt-images)) are Web GUI / API only, with no dedicated CLI subcommand — repair is reachable from the CLI indirectly via the `repair_corrupt` [pipeline](#pipeline) step. Every operation defaults to **dry-run mode** for safety.

---

## Table of Contents

1. [Move to Parent](#move-to-parent)
2. [Move with Structure](#move-with-structure)
3. [Rename Random](#rename-random)
4. [Rename by Parent](#rename-by-parent)
5. [Sort Alphabetical](#sort-alphabetical)
6. [Sort by Color](#sort-by-color)
7. [Sort by Resolution](#sort-by-resolution)
8. [Fix Extensions](#fix-extensions)
9. [Convert to JPG](#convert-to-jpg)
10. [Randomize Dates](#randomize-dates)
11. [Strip Favorite Ratings](#strip-favorite-ratings)
12. [Repair Corrupt Images](#repair-corrupt-images)
13. [Cleanup Empty Folders](#cleanup-empty-folders)
14. [Pipeline](#pipeline)

---

## Move to Parent

Move all images from subdirectories into the parent folder, flattening the directory hierarchy.

**Module:** `pixsieve.operations.move`
**Function:** `move_to_parent()`

### CLI

```bash
python -m pixsieve cli move-to-parent /path/to/photos
python -m pixsieve cli move-to-parent /path/to/photos --extensions .jpg .png --no-dry-run
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--extensions` | Only move files with these extensions (e.g., `.jpg .png`) |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/move-to-parent
```

```json
{
  "directory": "/path/to/photos",
  "extensions": [".jpg", ".png"],
  "dryRun": true
}
```

### Result Stats

| Key | Description |
|-----|-------------|
| `moved` | Number of files moved |
| `skipped` | Files already in parent directory |
| `errors` | Number of errors |

### Notes

- Files already in the parent directory are skipped
- Duplicate filenames get `_1`, `_2`, etc. appended
- Preserves file extensions
- Defaults to all image extensions if `--extensions` not specified

---

## Move with Structure

Move files from source to destination while preserving the directory structure.

**Module:** `pixsieve.operations.move`
**Function:** `move_with_structure()`

### CLI

```bash
python -m pixsieve cli move /path/to/source /path/to/dest
python -m pixsieve cli move /path/to/source /path/to/dest --overwrite --no-dry-run
```

| Option | Description |
|--------|-------------|
| `directory` | Source directory (required) |
| `destination` | Destination directory (required) |
| `--overwrite` | Overwrite existing files at destination |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/move
```

```json
{
  "directory": "/path/to/source",
  "destination": "/path/to/dest",
  "overwrite": false,
  "dryRun": true
}
```

### Result Stats

| Key | Description |
|-----|-------------|
| `moved` | Number of files moved |
| `skipped` | Files skipped (already exist, overwrite=false) |
| `errors` | Number of errors |

### Notes

- Automatically creates necessary directories in destination
- Cleans up empty source directories after moving (unless dry-run)
- If `overwrite=false`, existing destination files are skipped

---

## Rename Random

Rename all matching files to random alphanumeric names using parallel processing.

**Module:** `pixsieve.operations.rename`
**Function:** `rename_random()`

### CLI

```bash
python -m pixsieve cli rename random /path/to/photos
python -m pixsieve cli rename random /path/to/photos --length 16 --workers 8 --no-dry-run
python -m pixsieve cli rename random /path/to/photos --extensions .jpg .png --no-recursive
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--length` | Length of random name (default: 12) |
| `-w, --workers` | Number of parallel workers (default: 4) |
| `--extensions` | Only rename files with these extensions |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/rename/random
```

```json
{
  "directory": "/path/to/photos",
  "nameLength": 16,
  "workers": 4,
  "extensions": [".jpg", ".png"],
  "recursive": true,
  "dryRun": true
}
```

### Result Stats

| Key | Description |
|-----|-------------|
| `success` | Number of files successfully renamed |
| `failed` | Number of files that failed |
| `errors` | List of error messages |

### Notes

- Preserves file extensions
- Generates unique names automatically (retries up to 100 times)
- Uses `ThreadPoolExecutor` for parallel processing
- Characters used: `a-z`, `A-Z`, `0-9`

---

## Rename by Parent

Rename files based on parent and grandparent folder names, producing structured names like `ArtistA_AlbumX_1.jpg`.

**Module:** `pixsieve.operations.rename`
**Function:** `rename_by_parent()`

### CLI

```bash
python -m pixsieve cli rename parent /path/to/photos
python -m pixsieve cli rename parent /path/to/photos --no-dry-run
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/rename/parent
```

```json
{
  "directory": "/path/to/photos",
  "dryRun": true
}
```

### Expected Directory Structure

```
root_dir/
  ArtistA/
    AlbumX/
      img1.jpg  ->  ArtistA_AlbumX_1.jpg
      img2.jpg  ->  ArtistA_AlbumX_2.jpg
    AlbumY/
      img1.jpg  ->  ArtistA_AlbumY_1.jpg
  ArtistB/
    img1.jpg    ->  ArtistB_1.jpg
```

### Result Stats

| Key | Description |
|-----|-------------|
| `renamed` | Number of files renamed |
| `skipped` | Files already correctly named |
| `errors` | Number of errors |

### Notes

- Sanitizes filenames for Windows compatibility (removes `< > : " / \ | ? *`)
- Handles Windows path length limits (max 250 characters)
- Resolves naming conflicts by appending counter suffixes
- If no subfolders exist, uses parent folder name only

---

## Sort Alphabetical

Sort files into subfolders based on the first character of their filename.

**Module:** `pixsieve.operations.sort`
**Function:** `sort_alphabetical()`

### CLI

```bash
python -m pixsieve cli sort alpha /path/to/photos
python -m pixsieve cli sort alpha /path/to/photos --no-dry-run
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/sort/alpha
```

```json
{
  "directory": "/path/to/photos",
  "dryRun": true
}
```

### Group Folders Created

| Folder | Characters |
|--------|------------|
| `A-G` | A, B, C, D, E, F, G |
| `H-N` | H, I, J, K, L, M, N |
| `O-T` | O, P, Q, R, S, T |
| `U-Z` | U, V, W, X, Y, Z |
| `0-9` | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |

### Result Stats

| Key | Description |
|-----|-------------|
| `moved` | Number of files moved |
| `skipped` | Files with no matching group |
| `errors` | Number of errors |

### Notes

- Only sorts files in the top-level directory (not recursive)
- Case-insensitive grouping
- Files starting with special characters are skipped

---

## Sort by Color

Sort images by color properties using K-means clustering. Requires `scikit-learn`.

**Module:** `pixsieve.operations.sort`
**Class:** `ColorImageSorter`

### Methods

| Method | Description |
|--------|-------------|
| `dominant` | Sort by the single most prevalent color |
| `bw` | Classify as color or black & white |
| `palette` | Sort by multi-color palette signature |
| `analyze` | Return color distribution stats (no file moves) |

### CLI

```bash
# Sort by dominant color
python -m pixsieve cli sort color /path/to/photos --method dominant

# Classify as color vs black & white
python -m pixsieve cli sort color /path/to/photos --method bw --no-dry-run

# Sort by color palette (3 colors)
python -m pixsieve cli sort color /path/to/photos --method palette --n-colors 3

# Analyze color distribution (no moves)
python -m pixsieve cli sort color /path/to/photos --method analyze

# Copy instead of move
python -m pixsieve cli sort color /path/to/photos --method dominant --copy
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--method` | Sort method: `dominant`, `bw`, `palette`, `analyze` (default: `dominant`) |
| `--copy` | Copy files instead of moving |
| `--n-colors` | Number of palette colors (for `palette` method, default: 3) |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/sort/color
```

```json
{
  "directory": "/path/to/photos",
  "method": "dominant",
  "copyFiles": false,
  "nColors": 3,
  "dryRun": true
}
```

### Output Folders

**Dominant color** creates `sorted_by_dominant_color/` with subfolders: `red`, `blue`, `green`, `yellow`, `orange`, `purple`, `pink`, `cyan`, `brown`, `black`, `white`, `gray`

**B&W** creates `sorted_by_color_type/` with subfolders: `color`, `black_and_white`

**Palette** creates `sorted_by_color_palette/` with subfolders named by sorted unique color names (e.g., `blue_green_white`)

### Result Stats

| Key | Description |
|-----|-------------|
| `processed` | Number of images processed (dominant/palette) |
| `color` | Number of color images (bw/analyze) |
| `bw` | Number of B&W images (bw/analyze) |
| `skipped` | Number of images skipped |
| `distribution` | Dict mapping color names to counts (analyze only) |

### Notes

- Resizes images to 150x150 for color analysis (performance)
- Grayscale detection uses channel variation threshold of 10
- K-means uses `random_state=42` for reproducibility
- Supported formats: `.jpg`, `.jpeg`, `.png`, `.bmp`, `.gif`, `.tiff`, `.webp`

---

## Sort by Resolution

Sort images into subfolders by resolution category and orientation. **Web GUI / API only — no CLI subcommand.**

**Module:** `pixsieve.operations.sort`
**Function:** `sort_by_resolution()`

### API

```
POST /api/operations/sort/resolution
```

```json
{
  "directory": "/path/to/photos",
  "copyFiles": false,
  "dryRun": true
}
```

### Output Folders

Creates `<category>/<orientation>/` folders directly inside the target directory, where:

**Category** (based on the longer edge):

| Category | Longer edge |
|----------|-------------|
| `tiny` | < 100 px |
| `thumbnail` | 100–299 px |
| `small` | 300–639 px |
| `medium` | 640–1279 px |
| `large` | 1280–1919 px |
| `hd` | 1920–2559 px (1080p+) |
| `2k` | 2560–3839 px |
| `4k` | 3840–7679 px |
| `8k_plus` | 7680 px+ |

**Orientation:** `landscape` (width/height ratio > 1.05), `portrait` (< 0.95), or `square` (roughly 1:1)

### Result Stats

| Key | Description |
|-----|-------------|
| `processed` | Number of files successfully moved/copied |
| `skipped` | Files that could not be read |
| `errors` | Files that failed during move/copy |
| `by_category` | Dict mapping `"<category>/<orientation>"` to file count |

### Notes

- Only sorts files in the top-level directory (not recursive)
- Reports progress via an `on_progress(percent, message)` callback, surfaced through `/api/operations/status`

---

## Fix Extensions

Scan images and fix file extensions that don't match the actual image format.

**Module:** `pixsieve.operations.convert`
**Function:** `fix_extensions()`

### CLI

```bash
python -m pixsieve cli fix-extensions /path/to/photos
python -m pixsieve cli fix-extensions /path/to/photos --no-recursive --no-dry-run
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/fix-extensions
```

```json
{
  "directory": "/path/to/photos",
  "recursive": true,
  "dryRun": true
}
```

### Result Stats

| Key | Description |
|-----|-------------|
| `total` | Total image files scanned |
| `valid` | Files with correct extensions |
| `fixed` | Files with corrected extensions |
| `unknown` | Files with unknown/unsupported formats |

### Notes

- Uses PIL to detect actual image format
- Handles naming conflicts automatically (appends `_1`, `_2`, etc.)
- Skips non-image files silently
- Closes files before renaming to avoid Windows file locking

---

## Convert to JPG

Convert PNG, BMP, and WEBP images to JPG format.

**Module:** `pixsieve.operations.convert`
**Function:** `batch_convert_to_jpg()`

### CLI

```bash
python -m pixsieve cli convert /path/to/photos
python -m pixsieve cli convert /path/to/photos --quality 90 --delete-originals --no-dry-run
python -m pixsieve cli convert /path/to/photos --no-recursive
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--quality` | JPG quality 1–100 (default: 95) |
| `--delete-originals` | Delete original files after conversion |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/convert
```

```json
{
  "directory": "/path/to/photos",
  "quality": 95,
  "deleteOriginals": false,
  "recursive": true,
  "dryRun": true
}
```

### Result Stats

| Key | Description |
|-----|-------------|
| `converted` | Number of files converted |
| `deleted` | Number of originals deleted |
| `failed` | Number of conversion failures |

### Notes

- Only converts `.png`, `.bmp`, and `.webp` formats
- Handles transparency by compositing onto white background
- Handles RGBA, LA, and palette (P) modes
- Saves with `optimize=True` for smaller file sizes
- Generates unique filenames to avoid conflicts

---

## Randomize Dates

Randomize filesystem timestamps (mtime/atime, and ctime on Windows) for every image, and optionally also write EXIF date metadata (`DateTimeOriginal`, `DateTimeDigitized`, `DateTime`) for EXIF-compatible formats (JPG/TIFF). This single function replaced the previous separate `randomize_exif_dates()` / `randomize_file_dates()` functions — there is no longer a distinct "EXIF-only" operation.

**Module:** `pixsieve.operations.metadata`
**Function:** `randomize_dates()` (plus `randomize_dates_per_folder()` for a per-folder date range, API/GUI only)

### CLI

```bash
# Filesystem timestamps + EXIF dates (CLI default)
python -m pixsieve cli metadata randomize-dates /path/to/photos \
  --start 2020-01-01 --end 2023-12-31 --no-dry-run

# Filesystem timestamps only
python -m pixsieve cli metadata randomize-dates /path/to/photos \
  --start 2020-01-01 --end 2023-12-31 --no-exif --no-dry-run
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--start` | Start date YYYY-MM-DD (required) |
| `--end` | End date YYYY-MM-DD (required) |
| `--no-exif` | Skip EXIF date write; filesystem timestamps only |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/metadata/randomize-dates
```

```json
{
  "directory": "/path/to/photos",
  "startDate": "2020-01-01",
  "endDate": "2023-12-31",
  "recursive": true,
  "syncExif": false,
  "dryRun": true
}
```

There is also `POST /api/operations/metadata/randomize-dates-per-folder`, which takes a `folderRanges` array of `{folder, startDate, endDate}` entries instead of a single directory/date range, so different photo sets can be randomized into different ranges in one call.

**Important default difference:** the CLI defaults to writing EXIF dates too (`--no-exif` opts out), but the REST API defaults `syncExif` to `false` (filesystem-only) for backward compatibility with the route's old name/behavior. Pass `"syncExif": true` explicitly via the API to also write EXIF dates.

### Result Stats

| Key | Description |
|-----|-------------|
| `success` | Number of files updated |
| `failed` | Number of files that failed |

### Notes

- Filesystem timestamps are set for all image formats; EXIF writing is limited to `.jpg`, `.jpeg`, `.tiff`, `.tif` and requires `piexif`
- On Windows, also sets `ctime` (creation time) if `pywin32` is installed; gracefully degrades if not
- Each file gets a unique random date within the range, processed in parallel (`max_workers`, default 4)

---

## Strip Favorite Ratings

Find and remove 5-star/favorite rating tags (`Rating`, `RatingPercent`, `XMP:Rating`, `EXIF:Rating`, `XMP-xmp:Rating`) from images via the external `exiftool` binary. A file counts as favorited if its rating tag is ≥5 (or `RatingPercent` ≥99).

**Module:** `pixsieve.operations.ratings`
**Function:** `strip_favorite_ratings()`

### CLI

```bash
python -m pixsieve cli strip-ratings /path/to/photos
python -m pixsieve cli strip-ratings /path/to/photos --no-recursive --no-dry-run
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/metadata/strip-ratings
```

```json
{
  "directory": "/path/to/photos",
  "recursive": true,
  "dryRun": true
}
```

### Result Stats

| Key | Description |
|-----|-------------|
| `scanned` | Number of image files scanned |
| `favorited` | Number of files with a favorite rating found |
| `success` | Number of ratings successfully stripped (or would be, in dry-run) |
| `failed` | Number of files that failed removal |
| `files` | List of affected file paths |

### Notes

- Requires the `exiftool` binary on `PATH`; returns zeroed stats and an error if it's not available
- Scanning and removal are batched (200 files/batch for scanning, 100/batch for removal) for speed on large libraries
- **Destructive**: uses `exiftool -overwrite_original`, so no backup copy is created — dry-run is the only safety net

---

## Repair Corrupt Images

Scan for corrupt or unreadable images, attempt automated repair, and quarantine files that can't be fixed. **Web GUI / API (and pipeline `repair_corrupt` step) only — no standalone CLI subcommand.**

**Module:** `pixsieve.operations.repair`
**Function:** `scan_and_repair()`

Repair strategies are attempted in order until one succeeds:

1. **Re-encode** — open with `LOAD_TRUNCATED_IMAGES=True`, copy pixel data, save back. Fixes truncated files and minor pixel corruption.
2. **Strip EXIF + re-save** — fixes bad/malformed metadata blocks.
3. **Convert to PNG** — last resort: recover whatever pixel data PIL can read and save as a clean PNG, replacing the original.

Files PIL can't identify at all (`INVALID_FORMAT`) skip straight to quarantine, since no repair strategy applies.

### API

```
POST /api/operations/repair
```

```json
{
  "directory": "/path/to/photos",
  "trashFolder": "/path/to/photos/.trash",
  "attemptRepair": true,
  "quarantineUnfixable": true,
  "workers": 4,
  "dryRun": true
}
```

`trashFolder` is required (absolute path); it's created on demand.

### Result Stats

| Key | Description |
|-----|-------------|
| `checked` | Total files examined |
| `clean` | Files with no corruption |
| `repaired` | Files successfully repaired in place |
| `quarantined` | Files moved to `trashFolder` |
| `permission_errors` | Files that could not be read at all |
| `skipped` | Files skipped (read-only, `INVALID_FORMAT` with quarantine disabled, etc.) |
| `errors` | Unexpected errors during processing |
| `problems` | Per-file detail (path, corruption type, status, repair attempts, trash path) for every non-clean file |

### Notes

- Files already inside `trashFolder` are excluded from scanning
- Repair modifies files in place — always dry-run first
- Permission checks (readable/writable/parent-writable) are reported per file and never crash the scan
- Also available as the `repair_corrupt` step in [pipeline](#pipeline); the CLI's `pipeline --steps` accepts it but has no way to set a custom trash folder, so it falls back to the built-in default trash directory

---

## Cleanup Empty Folders

Recursively delete all empty directories under the target directory.

**Module:** `pixsieve.operations.cleanup`
**Function:** `delete_empty_folders()`

### CLI

```bash
python -m pixsieve cli cleanup /path/to/photos
python -m pixsieve cli cleanup /path/to/photos --no-dry-run
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/cleanup
```

```json
{
  "directory": "/path/to/photos",
  "dryRun": true
}
```

### Result Stats

| Key | Description |
|-----|-------------|
| `deleted` | Number of empty folders deleted |
| `errors` | Number of errors |

### Notes

- Walks the tree bottom-up so nested empty trees are fully removed
- The root directory itself is never deleted
- Requires appropriate permissions to delete folders

---

## Pipeline

Chain multiple operations together in a single workflow. Steps execute in order.

**Module:** `pixsieve.operations.pipeline`
**Function:** `run_pipeline()`

### Available Steps

| Step Key | Description |
|----------|-------------|
| `random_rename` | Rename files to random alphanumeric names |
| `convert_jpg` | Convert PNG/BMP/WEBP to JPG |
| `randomize_dates` | Randomize filesystem + EXIF dates (requires start/end dates) |
| `cleanup_empty` | Delete empty folders |
| `repair_corrupt` | Scan and repair corrupt images (requires a trash folder — via API's `trashDir`, or defaults to `DEFAULT_TRASH_DIR` from the CLI) |

### CLI

```bash
# Basic pipeline
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,convert_jpg,cleanup_empty"

# Pipeline with a date operation
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,randomize_dates,cleanup_empty" \
  --start 2020-01-01 --end 2023-12-31 --no-dry-run

# Customize rename and convert settings
python -m pixsieve cli pipeline /path/to/photos \
  --steps "random_rename,convert_jpg" \
  --length 16 --quality 90 --delete-originals --no-dry-run
```

| Option | Description |
|--------|-------------|
| `directory` | Target directory (required) |
| `--steps` | Comma-separated list of steps (required) |
| `--start` | Start date for date operations (YYYY-MM-DD) |
| `--end` | End date for date operations (YYYY-MM-DD) |
| `--length` | Random name length for `random_rename` (default: 12) |
| `--quality` | JPG quality for `convert_jpg` (default: 95) |
| `--delete-originals` | Delete originals for `convert_jpg` |
| `--no-recursive` | Do not process subdirectories |
| `--dry-run` | Simulate without changes (default) |
| `--no-dry-run` | Actually perform the operation |

### API

```
POST /api/operations/pipeline
```

```json
{
  "directory": "/path/to/photos",
  "steps": ["random_rename", "convert_jpg", "cleanup_empty"],
  "startDate": "2020-01-01",
  "endDate": "2023-12-31",
  "nameLength": 12,
  "jpgQuality": 95,
  "deleteOriginals": false,
  "recursive": true,
  "trashDir": "/path/to/photos/.trash",
  "dryRun": true
}
```

`trashDir` is only required if `repair_corrupt` is included in `steps`; if omitted, it defaults to the built-in `DEFAULT_TRASH_DIR`.

### Result

Returns a dictionary mapping step names to their individual result dictionaries:

```json
{
  "random_rename": {"success": 50, "failed": 0, "errors": []},
  "convert_jpg": {"converted": 12, "deleted": 0, "failed": 0},
  "cleanup_empty": {"deleted": 3, "errors": 0}
}
```

### Notes

- Steps execute sequentially in the order specified
- The `randomize_dates` step requires `--start`/`--end` (or `startDate`/`endDate` via the API)
- Unknown step names cause the pipeline to abort before execution
- Each step prints progress with `[STEP X/N]` headers

---

## Python API Usage

All operations can be used directly as a Python library:

```python
from pixsieve.operations import (
    delete_empty_folders,
    move_to_parent,
    move_with_structure,
    rename_random,
    rename_by_parent,
    fix_extensions,
    batch_convert_to_jpg,
    randomize_dates,
    randomize_dates_per_folder,
    sort_alphabetical,
    sort_by_resolution,
    ColorImageSorter,
    run_pipeline,
    AVAILABLE_STEPS,
    scan_and_repair,
    RepairResult,
    CorruptionType,
    RepairStatus,
    strip_favorite_ratings,
)

# Move files to parent directory
stats = move_to_parent('/photos', dry_run=True)
print(f"Would move {stats['moved']} files")

# Random rename with 16-char names
stats = rename_random('/photos', name_length=16, workers=8, dry_run=True)

# Sort by dominant color
sorter = ColorImageSorter('/photos')
stats = sorter.sort_by_dominant_color(dry_run=True)

# Sort by resolution/orientation
stats = sort_by_resolution('/photos', dry_run=True)

# Convert images to JPG
stats = batch_convert_to_jpg('/photos', quality=90, dry_run=True)

# Randomize filesystem + EXIF dates
from datetime import datetime
stats = randomize_dates(
    '/photos', datetime(2020, 1, 1), datetime(2023, 12, 31),
    sync_exif=True, dry_run=True,
)

# Strip 5-star/favorite rating tags (requires exiftool on PATH)
stats = strip_favorite_ratings('/photos', dry_run=True)

# Scan for corrupt images and quarantine unfixable ones
stats = scan_and_repair('/photos', trash_folder='/photos/.trash', dry_run=True)
print(f"{stats['clean']} clean, {stats['repaired']} repaired, {stats['quarantined']} quarantined")

# Run a pipeline
results = run_pipeline(
    '/photos',
    steps=['random_rename', 'convert_jpg', 'cleanup_empty'],
    name_length=16,
    jpg_quality=90,
    dry_run=True,
)
```
