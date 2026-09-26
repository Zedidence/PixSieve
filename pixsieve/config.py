"""
Configuration constants for PixSieve.

This module contains all configurable settings including:
- Supported image extensions
- Format quality rankings for determining which image to keep
"""

import os

# All supported image extensions (comprehensive list)
IMAGE_EXTENSIONS = {
    # Common formats
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif',
    # RAW formats
    '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2',
    '.pef', '.srw', '.raf', '.3fr', '.dcr', '.kdc', '.mrw', '.nrw',
    '.srf', '.sr2', '.rwl',
    # Other formats
    '.ico', '.icns', '.psd', '.psb', '.xcf', '.svg', '.eps',
    '.heic', '.heif', '.avif', '.jxl',
    '.pbm', '.pgm', '.ppm', '.pnm',
    '.tga', '.dds', '.exr', '.hdr',
    '.jp2', '.j2k', '.jpf', '.jpx', '.jpm',
    '.fits', '.fit', '.fts',
    '.pcx', '.sgi', '.rgb', '.rgba', '.bw',
}

# Supported video extensions for the (opt-in) video duplicate-detection
# feature. Deliberately kept separate from IMAGE_EXTENSIONS rather than
# merged in - image-only operations (EXIF writes, color sorting, corruption
# repair, star-rating removal) filter by IMAGE_EXTENSIONS or narrower sets
# and must keep excluding video files without any changes to those modules.
# Requires opencv-python-headless (HAS_VIDEO_SUPPORT); see scanner/dependencies.py.
VIDEO_EXTENSIONS = {
    '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv',
    '.webm', '.m4v', '.mpg', '.mpeg', '.3gp',
}


# Every file operation (rename, move, sort, date changes, ...) is limited to
# these: PixSieve must never touch documents, archives or anything else that
# happens to live in a photo folder.
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def media_only(extensions) -> set:
    """
    Normalize `extensions` ('.JPG', 'png', ...) and drop anything that isn't
    an image or video type - the last line of defense against a caller- or
    user-supplied extension list pulling in other files.
    """
    normalized = set()
    for ext in extensions or ():
        ext = str(ext).strip().lower()
        if ext and not ext.startswith('.'):
            ext = '.' + ext
        normalized.add(ext)
    return normalized & MEDIA_EXTENSIONS


def is_media_file(path) -> bool:
    """True if `path` has an image or video extension."""
    return os.path.splitext(str(path))[1].lower() in MEDIA_EXTENSIONS


def resolve_extensions(base, include_videos=False, *, video_extensions=None, extra=None):
    """Resolve the working extension set for an operation.

    `extra` (an explicit caller-supplied override) replaces `base` entirely;
    `include_videos` then unions in `video_extensions` (default
    VIDEO_EXTENSIONS) on top of whichever set that leaves. This matches the
    Duplicates tab's existing "include videos ADDS video scanning" mental
    model rather than a silent replace, and is the single place that defines
    what `include_videos` means for every operation - CLI, API, and pipeline
    should all route their extension-set decision through this function.

    The result never contains anything but image/video extensions
    (media_only()), whatever `extra` asks for.
    """
    result = set(extra) if extra else set(base)
    if include_videos:
        result |= (video_extensions or VIDEO_EXTENSIONS)
    return media_only(result)


# Format quality ranking (higher = better quality potential)
# Lossless/RAW formats ranked higher
FORMAT_QUALITY_RANK = {
    # RAW - highest quality
    '.cr2': 100, '.cr3': 100, '.nef': 100, '.arw': 100, '.dng': 100,
    '.orf': 100, '.rw2': 100, '.pef': 100, '.srw': 100, '.raf': 100,
    '.3fr': 100, '.dcr': 100, '.kdc': 100, '.mrw': 100, '.nrw': 100,
    '.raw': 100,
    # Lossless
    '.tiff': 90, '.tif': 90,
    '.png': 85,
    '.bmp': 80,
    '.psd': 80, '.psb': 80,
    '.exr': 95, '.hdr': 95,
    # Modern efficient formats
    '.webp': 75,  # Can be lossy or lossless
    '.avif': 75,
    '.heic': 75, '.heif': 75,
    '.jxl': 80,
    # Lossy
    '.jpg': 60, '.jpeg': 60,
    '.gif': 50,
    # Other
    '.ico': 40, '.icns': 40,
    # Video containers (rough heuristic - actual quality depends far more on
    # codec/bitrate than container, but this keeps common containers from
    # falling back to the generic default)
    '.mov': 70, '.mkv': 65, '.mp4': 65, '.avi': 55, '.webm': 55,
    '.wmv': 50, '.flv': 45, '.m4v': 65, '.mpg': 50, '.mpeg': 50, '.3gp': 40,
}

# Default similarity threshold for perceptual hashing
# Lower = stricter matching. This is a Hamming distance over the full
# 256-bit pHash (hash_size=16 in scanner/hashing.py), not the 0-64 range
# of an 8x8 hash - see lsh.py's calculate_optimal_params(hash_bits=256).
# Recommended: 5-15
DEFAULT_THRESHOLD = 10

# Default number of parallel workers for image analysis
# Auto-detect: cpu_count * 2, minimum 4, maximum 16
_cpu_count = os.cpu_count() or 1
DEFAULT_WORKERS = min(max(4, _cpu_count * 2), 16)

# Worker counts for internal HDDs in utils/worker_policy.py's WORKER_TABLE.
# A spinning disk's throughput *drops* past a small number of concurrent
# random-access operations (seek thrashing), unlike CPU-bound work or SSDs
# where the CPU-scaled defaults above keep helping.
HDD_ANALYSIS_WORKERS = 4   # read-heavy: file discovery + image/video analysis
HDD_WRITE_WORKERS = 2      # write-heavy: move/rename/convert operations

# Default worker count for file operations (move/rename/metadata/repair)
# when the drive can't be classified or drive-aware tuning is off.
DEFAULT_OP_WORKERS = 4


def _env_flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ('0', 'false', 'no', 'off', '')


def _env_workers(name: str):
    try:
        value = int(os.environ.get(name, ''))
    except ValueError:
        return None
    return value if 1 <= value <= 32 else None


def _env_storage_overrides(name: str) -> dict:
    # 'E:=usb-hdd;/mnt/nas=network' -> {'E:': 'usb-hdd', '/mnt/nas': 'network'}
    # (a bare spec with no path applies to every path). Specs are validated
    # where they're used (utils/worker_policy.py), not here.
    result = {}
    for part in os.environ.get(name, '').split(';'):
        part = part.strip()
        if not part:
            continue
        prefix, _, spec = part.rpartition('=')
        result[prefix.strip() or '*'] = spec.strip().lower()
    return result


# Drive-aware worker tuning (utils/worker_policy.py). When on, operations
# pick worker counts from the storage they touch - HDD vs SSD vs NVMe, and
# SATA vs USB vs network. PIXSIEVE_AUTO_WORKERS=0 restores the fixed defaults.
AUTO_WORKERS = _env_flag('PIXSIEVE_AUTO_WORKERS')
# Force one worker count for every operation (an explicit -w still wins)
ENV_WORKERS = _env_workers('PIXSIEVE_WORKERS')
# Correct a misdetected drive, e.g. 'E:=usb-hdd;/mnt/nas=network'
STORAGE_OVERRIDES = _env_storage_overrides('PIXSIEVE_STORAGE_OVERRIDE')
# Short read-only speed test for drives whose type is ambiguous (utils/io_probe.py)
IO_PROBE_ENABLED = _env_flag('PIXSIEVE_IO_PROBE')
# Adjust worker counts while long operations run (utils/adaptive.py), only
# for jobs at least this large
ADAPTIVE_MIN_FILES_SCAN = 5_000
ADAPTIVE_MIN_FILES_OPS = 1_000

# Maximum image pixels before PIL raises DecompressionBombWarning
# Default PIL limit ~89MP; raised for high-res scans and panoramas
# Override via PIXSIEVE_MAX_IMAGE_PIXELS env variable or set directly
MAX_IMAGE_PIXELS = int(os.environ.get('PIXSIEVE_MAX_IMAGE_PIXELS', 500_000_000))

# LSH (Locality-Sensitive Hashing) configuration
# LSH provides O(n) performance vs O(n²) brute-force for perceptual matching.
# Table/bit-count tuning is NOT controlled here - scanner/lsh.py's
# calculate_optimal_params() hardcodes tiered (num_tables, bits_per_table)
# values by collection size instead; there is currently no manual override.
LSH_AUTO_THRESHOLD = 1000  # Auto-enable LSH when >= this many images

# Large library thresholds and tuning
LARGE_LIBRARY_THRESHOLD = 100_000                   # files — triggers large-library mode
LARGE_LIBRARY_WORKERS   = min(_cpu_count * 4, 32)      # more aggressive parallelism
WRITE_BATCH_SIZE        = 5_000                     # cache insert batch size before lock release
DISCOVERY_CHUNK_SIZE    = 1_000                     # files per discovery chunk

# Threshold for auto-disabling perceptual matching in the web GUI.
# Perceptual matching is O(n^2), so 50K images = 1.25 billion comparisons.
PERCEPTUAL_AUTO_DISABLE_THRESHOLD = 50_000

# Above this collection size, scanner/deduplication.py skips the "seen pairs"
# LSH-level dedup set (it would itself become too large) and relies on the
# Union-Find skip instead.
LSH_DEDUPE_SEEN_SET_MAX = 500_000

# Fixed worker count for web API operations when the drive can't be
# classified. Deliberately separate from DEFAULT_WORKERS above, which scales
# with the server's CPU count for the CLI, so API behavior doesn't vary by
# server hardware. (A request's `workers` field defaults to null = auto.)
DEFAULT_API_WORKERS = 4

# Bit depth mapping for different image modes
MODE_BIT_DEPTHS = {
    '1': 1, 'L': 8, 'P': 8, 'RGB': 24, 'RGBA': 32,
    'CMYK': 32, 'YCbCr': 24, 'LAB': 24, 'HSV': 24,
    'I': 32, 'F': 32, 'I;16': 16, 'I;16L': 16,
    'I;16B': 16, 'I;16N': 16,
}

# State/history file locations
STATE_FILE = os.path.join(os.path.expanduser('~'), '.duplicate_finder_state.json')
HISTORY_FILE = os.path.join(os.path.expanduser('~'), '.duplicate_finder_history.json')

# SQLite cache database location
# Stores analyzed image metadata for faster re-scans
CACHE_DB_FILE = os.path.join(os.path.expanduser('~'), '.duplicate_finder_cache.db')

# Log a WARNING if a single file's analysis (stat + decode + hash) takes at
# least this many seconds — flags pathological files (huge panoramas,
# near-decompression-bombs, flaky network/USB reads) that would otherwise
# silently stall a worker with no visible symptom besides a slow scan.
SLOW_FILE_WARN_SECONDS = 10.0

# How often (seconds) long-running, otherwise-silent loops emit a heartbeat
# progress log — e.g. the cache lookup stat pass over the full file list,
# which has no natural per-item progress callback.
HEARTBEAT_LOG_INTERVAL_SECONDS = 5.0

# =============================================================================
# MediaManager Operations Configuration
# =============================================================================

# Alphabetical sort groups for file organization
ALPHA_SORT_GROUPS = {
    "A-G": list("ABCDEFG"),
    "H-N": list("HIJKLMN"),
    "O-T": list("OPQRST"),
    "U-Z": list("UVWXYZ"),
    "0-9": list("0123456789"),
}

# EXIF-compatible extensions (supports EXIF metadata)
EXIF_EXTENSIONS = {'.jpg', '.jpeg', '.tiff', '.tif'}

# Extensions passed to exiftool for favorite/star-rating detection & removal.
# exiftool supports far more formats than piexif (EXIF_EXTENSIONS above is
# piexif-only), so this list is intentionally broader than EXIF_EXTENSIONS
# but narrower than the full IMAGE_EXTENSIONS set (excludes formats like
# .svg/.ico where a "star rating" concept isn't meaningful).
# Ported from the original faveRemover script's IMAGE_EXTENSIONS.
RATING_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.tiff', '.tif', '.heic', '.heif', '.webp', '.bmp',
    '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.raf', '.pef',
    '.nrw', '.srf', '.sr2', '.rwl',
}

# Image formats convertible to JPG
CONVERTIBLE_TO_JPG = {'.png', '.bmp', '.webp'}

# PIL format to file extension mapping
FORMAT_TO_EXT = {
    "JPEG": {"preferred": ".jpg", "valid": [".jpg", ".jpeg"]},
    "PNG":  {"preferred": ".png", "valid": [".png"]},
    "GIF":  {"preferred": ".gif", "valid": [".gif"]},
    "BMP":  {"preferred": ".bmp", "valid": [".bmp"]},
    "TIFF": {"preferred": ".tiff", "valid": [".tif", ".tiff"]},
    "WEBP": {"preferred": ".webp", "valid": [".webp"]},
    "ICO":  {"preferred": ".ico", "valid": [".ico"]},
    "HEIF": {"preferred": ".heic", "valid": [".heic"]},
}

# Corrupt image repair / quarantine
TRASH_FOLDER_NAME = ".pixsieve_trash"
DEFAULT_TRASH_DIR = os.path.join(os.path.expanduser("~"), ".pixsieve_trash")

# Windows-specific constraints
WINDOWS_RESERVED_NAMES = (
    ['CON', 'PRN', 'AUX', 'NUL']
    + [f'COM{i}' for i in range(1, 10)]
    + [f'LPT{i}' for i in range(1, 10)]
)
WINDOWS_MAX_PATH = 250

# Characters not allowed in Windows filenames, plus all ASCII control
# characters (0-31, e.g. a literal NUL/tab/newline byte) which Windows also
# rejects. Single source of truth for pixsieve/utils/operations.py's
# sanitize_filename() and pixsieve/api/schemas.py's RenameImageRequest
# validator - these used to be two independently-maintained character sets
# that had drifted (the CLI's version omitted control characters, so a
# "sanitized" filename could still contain one, while the API's rejected
# them outright).
WINDOWS_INVALID_FILENAME_CHARS = frozenset('<>:"/\\|?*') | frozenset(chr(c) for c in range(32))