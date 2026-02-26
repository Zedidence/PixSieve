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
    # Other formats
    '.ico', '.icns', '.psd', '.psb', '.xcf', '.svg', '.eps',
    '.heic', '.heif', '.avif', '.jxl',
    '.pbm', '.pgm', '.ppm', '.pnm',
    '.tga', '.dds', '.exr', '.hdr',
    '.jp2', '.j2k', '.jpf', '.jpx', '.jpm',
    '.fits', '.fit', '.fts',
    '.pcx', '.sgi', '.rgb', '.rgba', '.bw',
}

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
}

# Default similarity threshold for perceptual hashing
# Lower = stricter matching (0-64 range)
# Recommended: 5-15
DEFAULT_THRESHOLD = 10

# Default number of parallel workers for image analysis
# Auto-detect: cpu_count * 2, minimum 4, maximum 16
_cpu_count = os.cpu_count() or 1
DEFAULT_WORKERS = min(max(4, _cpu_count * 2), 16)

# Maximum image pixels before PIL raises DecompressionBombWarning
# Default PIL limit ~89MP; raised for high-res scans and panoramas
# Override via PIXSIEVE_MAX_IMAGE_PIXELS env variable or set directly
MAX_IMAGE_PIXELS = int(os.environ.get('PIXSIEVE_MAX_IMAGE_PIXELS', 500_000_000))

# LSH (Locality-Sensitive Hashing) configuration
# LSH provides O(n) performance vs O(n²) brute-force for perceptual matching
LSH_AUTO_THRESHOLD = 1000  # Auto-enable LSH when >= this many images
LSH_DEFAULT_TABLES = 20    # Number of hash tables (more = better recall)
LSH_DEFAULT_BITS = 16      # Bits per table (fewer = more candidates)

# Large library thresholds and tuning
LARGE_LIBRARY_THRESHOLD = 100_000                   # files — triggers large-library mode
LARGE_LIBRARY_WORKERS   = min(os.cpu_count() * 4, 32)  # more aggressive parallelism
WRITE_BATCH_SIZE        = 5_000                     # cache insert batch size before lock release
DISCOVERY_CHUNK_SIZE    = 1_000                     # files per discovery chunk

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