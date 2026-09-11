# API Reference

PixSieve exposes a REST API for programmatic access and can also be used directly as a Python library.

**Base URL**: `http://localhost:5000` (default)

---

## Table of Contents

1. [REST API — Duplicate Detection](#rest-api--duplicate-detection)
   - [Scan Operations](#scan-operations)
   - [Status and Progress](#status-and-progress)
   - [Results Management](#results-management)
   - [Image Editor](#image-editor)
   - [Cache Management](#cache-management)
   - [Utility Endpoints](#utility-endpoints)
2. [REST API — File Operations](#rest-api--file-operations)
3. [Python Library API](#python-library-api)
4. [Error Handling](#error-handling)
5. [Examples](#examples)

---

## REST API — Duplicate Detection

### Scan Operations

#### Start a New Scan

```http
POST /api/scan
Content-Type: application/json

{
  "directories": [
    { "path": "/path/to/images", "isReference": false },
    { "path": "/path/to/other-images", "isReference": true }
  ],
  "threshold": 10,
  "exactOnly": false,
  "perceptualOnly": false,
  "recursive": true,
  "useCache": true,
  "useLsh": null,
  "workers": 4,
  "resolveSymlinks": true,
  "autoSelectStrategy": "quality",
  "includeVideos": false
}
```

**Request Body** (validated by `ScanRequest` Pydantic model):
- `directories` (array, **required**, min 1 entry): Directories to scan. Each entry has:
  - `path` (string, **required**): Absolute path to scan
  - `isReference` (boolean, default `false`): Mark this directory as the canonical/reference folder — its images are never modified, and any duplicate group containing a reference image auto-keeps all reference copies. At most one directory may set this
- `threshold` (integer, 0–64, default `10`): Perceptual hash distance threshold
- `exactOnly` (boolean, default `false`): Exact duplicates only
- `perceptualOnly` (boolean, default `false`): Perceptual duplicates only
- `recursive` (boolean, default `true`): Scan subdirectories
- `useCache` (boolean, default `true`): Use the SQLite analysis cache
- `useLsh` (boolean|null, default `null`): `null` = auto, `true` = force on, `false` = force off
- `workers` (integer, 1–32, default `4`): Parallel analysis threads
- `resolveSymlinks` (boolean, default `true`): Canonicalize symlinks during discovery (disable for a small speedup on drives with no symlinks)
- `autoSelectStrategy` (string, default `"quality"`): Which image to auto-keep (`quality`, `largest`, `smallest`, `newest`, `oldest`). `quality` now includes a small (≤10 point) sharpness tiebreaker computed from the same thumbnail already produced for perceptual hashing - see `docs/performance.md`. `newest`/`oldest` prefer EXIF `DateTimeOriginal` over filesystem modification time when present, since mtime is frequently wrong after a copy/sync/backup; falls back to mtime when EXIF is absent (videos, PNGs, EXIF-stripped files) or was itself randomized by `metadata randomize-dates`.
- `includeVideos` (boolean, default `false`): Also scan/deduplicate video files (requires `opencv-python-headless`)

**Response (200):**
```json
{ "status": "started" }
```

**Errors:** `400` validation error (including duplicate/empty directory paths or more than one `isReference: true`), `409` scan already running

**Notes:** Scan runs in a background thread. Poll `/api/status` (or subscribe to `GET /api/scan/stream`, an SSE endpoint) for progress. Only one scan can run at a time. Use `POST /api/cancel`, `POST /api/pause`, and `POST /api/resume` to control an in-progress scan.

**Large-library auto-disable (API/web only):** if a live scan's discovered-file count exceeds 50,000 (`PERCEPTUAL_AUTO_DISABLE_THRESHOLD` in `api/orchestrator.py`) and `useLsh` wasn't explicitly forced to `true`, perceptual matching is automatically disabled for the rest of that scan (equivalent to `exactOnly: true`) — a warning is surfaced via `/api/status`'s `message` field when this happens. **This behavior does not exist in the CLI** (`pixsieve cli duplicates`) — its own `LARGE_LIBRARY_THRESHOLD` (100,000 files) only scales worker count and never disables perceptual matching. The same directory scanned via the web UI vs. the CLI can therefore silently produce different classes of duplicates at very large scale; this divergence is intentional-by-omission rather than a deliberate design choice, and is called out here so it isn't a surprise.

---

#### List Subfolders

```http
POST /api/list-subfolders
Content-Type: application/json

{ "directory": "/path/to/images" }
```

**Response (200):** `{ "folders": ["Vacation2023", "Family"], "parent": "/path/to/images" }` — used by the directory picker UI for folder autocomplete.

---

#### Cancel / Pause / Resume a Scan

```http
POST /api/cancel
POST /api/pause
POST /api/resume
```

Each returns `{ "status": "..." }` reflecting the new state (`cancel_requested`, `paused`, `resumed`, or `no_scan_running`/`not_paused` if there's nothing to act on).

---

### Status and Progress

#### Get Current Scan Status

```http
GET /api/status
```

**Response (200):**
```json
{
  "status": "analyzing",
  "progress": 45,
  "message": "Analyzing images: 4,500/10,000 (125/sec, ~44s remaining)",
  "total_files": 10000,
  "analyzed": 4500,
  "directory": "/path/to/images",
  "has_results": false,
  "group_count": 0,
  "error_count": 0
}
```

**Status values:** `idle`, `scanning`, `analyzing`, `comparing`, `complete`, `error`

---

#### Stream Scan Progress (SSE)

```http
GET /api/scan/stream
```

Server-Sent Events stream of the same payload as `GET /api/status`, pushed every 0.5s. The connection closes automatically once the scan reaches a terminal state (`complete`, `error`, `cancelled`, or `idle`). Prefer this over polling `/api/status` in a UI.

---

#### Get Scan History

```http
GET /api/history
```

**Response (200):** Returns last 10 scanned directories (most recent first), used for UI autocomplete.

```json
{
  "directories": ["/path/to/images", "/another/path"]
}
```

---

### Results Management

#### Get Duplicate Groups

```http
GET /api/groups
```

**Response (200):**
```json
{
  "groups": [
    {
      "id": 1,
      "match_type": "exact",
      "image_count": 3,
      "images": [
        {
          "path": "/path/to/img1.jpg",
          "filename": "img1.jpg",
          "directory": "/path/to",
          "file_size": 2048576,
          "file_size_formatted": "2.0 MB",
          "width": 1920,
          "height": 1080,
          "resolution": "1920x1080",
          "pixel_count": 2073600,
          "megapixels": 2.07,
          "format": "JPEG",
          "quality_score": 75.5,
          "media_type": "image",
          "error": null
        }
      ],
      "best_path": "/path/to/img1.jpg",
      "selected_keep": "/path/to/img1.jpg",
      "potential_savings": 4096000,
      "potential_savings_formatted": "3.9 MB"
    }
  ],
  "selections": {
    "/path/to/img1.jpg": "keep",
    "/path/to/img2.jpg": "delete"
  },
  "directory": "/path/to/images",
  "error_images": []
}
```

Images within each group are sorted by quality score (best first). `best_path` is the recommended image to keep.

---

#### Save User Selections

```http
POST /api/selections
Content-Type: application/json

{
  "selections": {
    "/path/to/img1.jpg": "keep",
    "/path/to/img2.jpg": "delete"
  }
}
```

**Response (200):** `{ "status": "saved" }`

---

#### Apply an Auto-Selection Strategy

```http
POST /api/apply_strategy
Content-Type: application/json

{ "strategy": "quality" }
```

Re-runs auto-selection (`quality`, `largest`, `smallest`, `newest`, `oldest`) against the current scan results and overwrites the saved selections.

**Response (200):**
```json
{ "status": "applied", "selections": { "/path/to/img1.jpg": "keep", "/path/to/img2.jpg": "delete" } }
```

**Errors:** `400` if there are no groups to apply a strategy to.

---

#### Delete Selected Files

```http
POST /api/delete
Content-Type: application/json

{
  "files": ["/path/to/img2.jpg", "/path/to/img3.jpg"],
  "trashDir": "/path/to/trash"
}
```

Files are moved to `trashDir` (not permanently deleted), one at a time in a background thread — this returns immediately, not after the move completes. Filename conflicts are handled automatically.

**Response (200):** `{ "status": "started", "total": 2 }`

Poll `GET /api/delete/status` (or subscribe to `GET /api/delete/stream`, an SSE endpoint pushed every 0.3s) for progress:

```json
{
  "status": "running",
  "total": 2,
  "moved": 1,
  "errors": 0,
  "error_details": [],
  "current_file": "/path/to/img3.jpg"
}
```

`status` becomes `"complete"` when done. Only one move-to-trash operation may run at a time — a second `POST /api/delete` while one is running returns `409`.

**Errors:** `400` validation error, `403` file outside scan directory or in the protected reference folder, `409` a delete is already in progress.

---

#### Batch Operation on Specific Files

Run a file operation on a hand-picked list of files (e.g. all files marked for deletion) without leaving the results view.

```http
POST /api/batch-operation
Content-Type: application/json

{
  "operation": "convert_jpg",
  "files": ["/path/to/dup1.png", "/path/to/dup2.bmp"],
  "quality": 90
}
```

**Request Body** (validated by `BatchOperationRequest` Pydantic model):
- `operation` (string, **required**): `convert_jpg` or `move`
- `files` (array of strings, **required**): Absolute paths to operate on — all must be within the current scan directory
- `destination` (string): Required for `move`; absolute path to target directory (created if absent)
- `quality` (integer, 1–100, default `90`): JPEG quality for `convert_jpg`

**Response (200):**
```json
{ "converted": 2, "skipped": 0, "errors": 0, "error_details": [] }
// or for move:
{ "moved": 2, "errors": 0, "error_details": [] }
```

**Errors:** `400` validation error, `403` path outside scan directory

---

#### Clear Session State

```http
POST /api/clear
```

**Response (200):** `{ "status": "cleared" }` — resets scan state to idle and deletes the state file.

---

### Image Editor

Backs the lightbox's built-in rotate/flip/crop/rename/delete editor. All three endpoints below apply the same security checks as `/api/image`: the path must fall inside a currently scanned directory, and (for save/rename) must not be inside the protected reference folder.

#### Get Image File

```http
GET /api/image?path=/path/to/image.jpg
```

Returns binary image data. Only serves images from the current scan results. Returns `403` for unauthorized paths, `404` if the file doesn't exist.

---

#### Get Thumbnail

```http
GET /api/thumbnail?path=/path/to/image.jpg
```

Returns a cached 300px-max-dimension JPEG thumbnail, generating and caching it to disk on first request. For video files, grabs a representative frame (~10% into the video) instead; falls back to a placeholder poster SVG if `opencv-python-headless` isn't installed or the frame can't be decoded.

---

#### Save an Edited Image

```http
POST /api/image/save
Content-Type: application/json

{
  "path": "/path/to/image.jpg",
  "dataUrl": "data:image/png;base64,iVBORw0KGgo..."
}
```

Accepts a canvas `toDataURL()` payload from the lightbox editor, re-validates it as a real image via Pillow (the server never trusts raw client bytes), and writes it atomically (temp file + `os.replace`). Only `.jpg`, `.jpeg`, `.png`, `.webp` may be edited — enforced server-side independent of the client's toolbar restriction.

**Response (200):**
```json
{ "status": "saved", "path": "/path/to/image.jpg", "mtime": 1735689600.0, "size": 204800, "width": 1920, "height": 1080 }
```

**Errors:** `400` unsupported file type or invalid image data, `403` outside scan directory or in the reference folder, `404` file not found.

---

#### Rename an Image

```http
POST /api/image/rename
Content-Type: application/json

{ "path": "/path/to/image.jpg", "newName": "sunset.jpg" }
```

Renames a single file within its current directory only (no path separators allowed in `newName`). Rejects Windows-reserved names (`CON`, `PRN`, etc.), invalid characters, and trailing dots/spaces (stripped to match what Windows would actually create).

**Response (200):** `{ "status": "renamed", "path": "/path/to/sunset.jpg", "filename": "sunset.jpg" }`

**Errors:** `400` invalid name, `403` outside scan directory or in the reference folder, `404` file not found, `409` a file with that name already exists.

---

### Cache Management

#### Get Cache Statistics

```http
GET /api/cache/stats
```

**Response (200):**
```json
{
  "total_entries": 15234,
  "db_size_bytes": 3145728,
  "db_size_mb": 3.0,
  "db_path": "/home/user/.duplicate_finder_cache.db"
}
```

---

#### Clear All Cache

```http
POST /api/cache/clear
```

**Response (200):** `{ "status": "cleared" }` — removes all cached data; the next scan will be slower.

---

#### Cleanup Cache

```http
POST /api/cache/cleanup
Content-Type: application/json

{ "max_age_days": 30 }
```

**Response (200):** `{ "missing_removed": 45, "stale_removed": 128, "thumbs_removed": 210 }`

Removes entries for deleted files and entries older than `max_age_days`, compacts the database (`VACUUM`), and also clears the on-disk thumbnail cache.

---

### Utility Endpoints

#### Interactive API Docs (Swagger UI)

```
GET /docs/
```

Opens the Flasgger-powered Swagger UI with all documented endpoints. Machine-readable OpenAPI 3.0 spec available at `/apispec.json`.

---

#### Health Check

```http
GET /api/ping
```

**Response (200):** `{ "status": "ok", "time": "2026-01-22T15:30:45.123456" }`

---

## REST API — File Operations

The 14 file operation endpoints below mostly follow the same pattern:

```http
POST /api/operations/<operation>
Content-Type: application/json

{
  "directory": "/absolute/path",
  "dryRun": true,
  ...operation-specific fields
}
```

(`repair` uses `trashFolder` instead of nested operation fields, and `pipeline` takes `steps` instead of a fixed shape — see below.)

Operations run in a background thread. Poll `/api/operations/status`, or subscribe to `GET /api/operations/stream` (SSE, pushed every 0.5s, closes automatically on a terminal state):

```json
// Idle
{ "status": "idle", "operation": null, "result": null, "error": null, "progress": null, "progress_text": "" }

// Running
{ "status": "running", "operation": "rename-random", "result": null, "error": null, "progress": null, "progress_text": "" }

// Complete
{ "status": "complete", "operation": "rename-random", "result": { "success": 50, "failed": 0 }, "error": null, "progress": 100, "progress_text": "" }
```

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/operations/status` | GET | Poll operation progress |
| `/api/operations/stream` | GET | SSE stream of operation progress |
| `/api/operations/available` | GET | List available pipeline steps |
| `/api/operations/move-to-parent` | POST | Flatten directory hierarchy |
| `/api/operations/move` | POST | Move files with structure |
| `/api/operations/rename/random` | POST | Rename to random alphanumeric names |
| `/api/operations/rename/parent` | POST | Rename by parent folder name |
| `/api/operations/sort/alpha` | POST | Sort into alphabetical groups |
| `/api/operations/sort/color` | POST | Sort by color properties |
| `/api/operations/sort/resolution` | POST | Sort by resolution category + orientation |
| `/api/operations/fix-extensions` | POST | Fix wrong file extensions |
| `/api/operations/convert` | POST | Convert images to JPG |
| `/api/operations/metadata/randomize-dates` | POST | Randomize filesystem (+ optionally EXIF) dates |
| `/api/operations/metadata/randomize-dates-per-folder` | POST | Same, with a separate date range per folder |
| `/api/operations/metadata/strip-ratings` | POST | Strip 5-star/favorite rating tags |
| `/api/operations/cleanup` | POST | Delete empty folders |
| `/api/operations/repair` | POST | Scan, repair, and quarantine corrupt images |
| `/api/operations/pipeline` | POST | Run a multi-step pipeline |

The count above (14) excludes the three meta-endpoints (`status`, `stream`, `available`). There is no `/api/operations/metadata/randomize-exif` endpoint — EXIF-date randomization was merged into `randomize-dates` behind a `syncExif` flag (see [operations.md](operations.md#randomize-dates)).

For detailed request/response schemas for each operation, see [operations.md](operations.md).

---

## Python Library API

```python
from pixsieve import (
    find_image_files,
    analyze_image,
    analyze_images_parallel,
    find_exact_duplicates,
    find_perceptual_duplicates,
    get_cache,
    HammingLSH,
    LSH_AUTO_THRESHOLD,
    has_heif_support,
)

# Check for HEIC support
if not has_heif_support():
    print("Warning: HEIC/HEIF support not available")

# Find and analyze images (caching enabled by default)
images = find_image_files("/path/to/photos")
analyzed, cache_stats = analyze_images_parallel(images)
print(f"Cache: {cache_stats.hit_rate:.1f}% hit rate")

# Skip file hashing for perceptual-only scans (10-20% faster)
analyzed, cache_stats = analyze_images_parallel(images, calculate_hash=False)

# Find duplicates (auto-enables LSH for large collections)
exact_groups = find_exact_duplicates(analyzed)
perceptual_groups = find_perceptual_duplicates(
    analyzed,
    threshold=10,
    use_lsh=None,  # None = auto, True/False = force
)

# Work with results
for group in exact_groups:
    print(f"Found {len(group.images)} identical files")
    print(f"Best quality: {group.best_image.path}")
    print(f"Can save: {group.potential_savings_formatted}")

# Direct LSH usage
from pixsieve import HammingLSH, calculate_optimal_params

num_tables, bits_per_table = calculate_optimal_params(len(images), threshold=10)
lsh = HammingLSH(num_tables=num_tables, bits_per_table=bits_per_table)

for idx, phash in enumerate(parsed_hashes):
    lsh.add(idx, phash)

# Memory-efficient iteration (recommended for large collections)
for i, j in lsh.iter_candidate_pairs():
    pass  # process candidate pair

estimated_pairs = lsh.estimate_candidate_pairs()

# Video duplicate detection (opt-in; not exported from the top-level pixsieve
# package — import from pixsieve.scanner, and requires opencv-python-headless)
from pixsieve.scanner import analyze_video, find_video_perceptual_duplicates, has_video_support

if has_video_support():
    video_info = analyze_video("/path/to/clip.mp4")
    video_groups = find_video_perceptual_duplicates([video_info], threshold=10)

# Cache management
cache = get_cache()
print(cache.get_stats())    # {'total_entries': 1000, 'db_size_mb': 2.5, ...}
cache.cleanup_missing()     # Remove entries for deleted files
cache.cleanup_stale(30)     # Remove entries not accessed in 30 days
```

---

## Error Handling

All endpoints may return standard error responses:

```json
{ "error": "Descriptive error message" }
```

| Status | Meaning |
|--------|---------|
| `400 Bad Request` | Invalid parameters |
| `403 Forbidden` | Access denied (e.g., path not in scan results) |
| `404 Not Found` | Resource or directory not found |
| `500 Internal Server Error` | Unexpected server error |

---

## Examples

### Python

```python
import requests, time

base_url = "http://localhost:5000"

# Start scan
requests.post(f"{base_url}/api/scan", json={
    "directories": [{"path": "/path/to/photos", "isReference": False}],
    "threshold": 10,
})

# Poll until complete
while True:
    status = requests.get(f"{base_url}/api/status").json()
    print(f"Progress: {status['progress']}% - {status['message']}")
    if status['status'] in ('complete', 'error'):
        break
    time.sleep(2)

# Get and process results
groups = requests.get(f"{base_url}/api/groups").json()
files_to_delete = [img['path'] for g in groups['groups'] for img in g['images'][1:]]
result = requests.post(f"{base_url}/api/delete", json={"files": files_to_delete, "trashDir": "/path/to/trash"})
print(f"Moved {result.json()['moved']} files")
```

### cURL

```bash
# Start scan
curl -X POST http://localhost:5000/api/scan \
  -H "Content-Type: application/json" \
  -d '{"directories":[{"path":"/path/to/photos","isReference":false}],"threshold":10}'

# Get status
curl http://localhost:5000/api/status

# Get results
curl http://localhost:5000/api/groups

# Clear cache
curl -X POST http://localhost:5000/api/cache/clear
```

---

**Notes:** No authentication or rate limiting is implemented. The API is designed for local use only. Do not expose it to the internet without adding authentication and HTTPS.
