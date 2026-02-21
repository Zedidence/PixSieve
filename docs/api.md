# API Reference

PixSieve exposes a REST API for programmatic access and can also be used directly as a Python library.

**Base URL**: `http://localhost:5000` (default)

---

## Table of Contents

1. [REST API — Duplicate Detection](#rest-api--duplicate-detection)
   - [Scan Operations](#scan-operations)
   - [Status and Progress](#status-and-progress)
   - [Results Management](#results-management)
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
  "directory": "/path/to/images",
  "threshold": 10,
  "exactOnly": false,
  "perceptualOnly": false
}
```

**Request Body:**
- `directory` (string, required): Absolute path to directory
- `threshold` (integer, optional): Perceptual hash threshold (0–64), default: 10
- `exactOnly` (boolean, optional): Only find exact duplicates, default: false
- `perceptualOnly` (boolean, optional): Only find perceptual duplicates, default: false

**Response (200):**
```json
{ "status": "started" }
```

**Errors:** `400` invalid parameters, `404` directory not found

**Notes:** Scan runs in a background thread. Poll `/api/status` for progress. Only one scan can run at a time.

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

#### Delete Selected Files

```http
POST /api/delete
Content-Type: application/json

{
  "files": ["/path/to/img2.jpg", "/path/to/img3.jpg"],
  "trashDir": "/path/to/trash"
}
```

**Response (200):** `{ "moved": 2, "errors": 0 }`

Files are moved (not permanently deleted). Filename conflicts are handled automatically.

---

#### Clear Session State

```http
POST /api/clear
```

**Response (200):** `{ "status": "cleared" }` — resets scan state to idle and deletes the state file.

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

**Response (200):** `{ "missing_removed": 45, "stale_removed": 128 }`

Removes entries for deleted files and entries older than `max_age_days`. Compacts the database automatically.

---

### Utility Endpoints

#### Get Image File

```http
GET /api/image?path=/path/to/image.jpg
```

Returns binary image data. Only serves images from the current scan results (security check). Returns `403` for unauthorized paths.

---

#### Health Check

```http
GET /api/ping
```

**Response (200):** `{ "status": "ok", "time": "2026-01-22T15:30:45.123456" }`

---

## REST API — File Operations

All 14 file operation endpoints use the same pattern:

```http
POST /api/operations/<operation>
Content-Type: application/json

{
  "directory": "/absolute/path",
  "dryRun": true,
  ...operation-specific fields
}
```

Operations run in a background thread. Poll `/api/operations/status` for progress:

```json
// Idle
{ "status": "idle", "operation": null, "result": null, "error": null }

// Running
{ "status": "running", "operation": "rename-random", "result": null, "error": null }

// Complete
{ "status": "complete", "operation": "rename-random", "result": { "success": 50, "failed": 0 }, "error": null }
```

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/operations/status` | GET | Poll operation progress |
| `/api/operations/available` | GET | List available pipeline steps |
| `/api/operations/move-to-parent` | POST | Flatten directory hierarchy |
| `/api/operations/move` | POST | Move files with structure |
| `/api/operations/rename/random` | POST | Rename to random alphanumeric names |
| `/api/operations/rename/parent` | POST | Rename by parent folder name |
| `/api/operations/sort/alpha` | POST | Sort into alphabetical groups |
| `/api/operations/sort/color` | POST | Sort by color properties |
| `/api/operations/fix-extensions` | POST | Fix wrong file extensions |
| `/api/operations/convert` | POST | Convert images to JPG |
| `/api/operations/metadata/randomize-exif` | POST | Randomize EXIF dates |
| `/api/operations/metadata/randomize-dates` | POST | Randomize file timestamps |
| `/api/operations/cleanup` | POST | Delete empty folders |
| `/api/operations/pipeline` | POST | Run a multi-step pipeline |

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
requests.post(f"{base_url}/api/scan", json={"directory": "/path/to/photos", "threshold": 10})

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
  -d '{"directory":"/path/to/photos","threshold":10}'

# Get status
curl http://localhost:5000/api/status

# Get results
curl http://localhost:5000/api/groups

# Clear cache
curl -X POST http://localhost:5000/api/cache/clear
```

---

**Notes:** No authentication or rate limiting is implemented. The API is designed for local use only. Do not expose it to the internet without adding authentication and HTTPS.
