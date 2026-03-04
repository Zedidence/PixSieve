"""
Dependency initialization for the scanner package.

Handles PIL, imagehash, HEIC/HEIF support, and tqdm imports with proper
error handling and configuration.
"""

from __future__ import annotations

import warnings
import logging
from typing import Optional, Any

# Module-level logger
_logger = logging.getLogger(__name__)

# Check for required dependencies
try:
    from PIL import Image
    import imagehash
except ImportError:
    raise ImportError(
        "Required packages not found!\n"
        "Install with: pip install Pillow imagehash"
    )

# Register HEIC/HEIF support via pillow-heif
# This must be done before opening any HEIC files
HAS_HEIF_SUPPORT = False
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HAS_HEIF_SUPPORT = True
    _logger.debug("HEIC/HEIF support enabled via pillow-heif")
except ImportError:
    _logger.warning(
        "pillow-heif not installed - HEIC/HEIF files will not be processed. "
        "Install with: pip install pillow-heif"
    )

# C3: Configurable decompression bomb limit (via config or PIXSIEVE_MAX_IMAGE_PIXELS env var)
# Default PIL limit is ~89MP; raised to handle large scans, panoramas, and aerial imagery.
# NOTE: Image.MAX_IMAGE_PIXELS is a class-level global. Scoping it to a per-call
# context manager would require a lock covering the entire Image.open() call, which
# would serialise all parallel image opens and defeat the ThreadPoolExecutor.
# The setting is applied here (scanner package init) rather than at the top-level
# __init__, limiting its scope to analysis paths that actually need it.
from ..config import MAX_IMAGE_PIXELS as _MAX_IMAGE_PIXELS
Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS

# Do NOT suppress DecompressionBombWarning globally; individual image-open sites
# use warnings.catch_warnings() to filter it locally so the filter doesn't bleed
# into unrelated PIL usage in the same process.

# Optional: tqdm for progress bars
# Store as Optional[Any] to satisfy type checkers when tqdm is not installed
HAS_TQDM = False
_tqdm_class: Optional[Any] = None

try:
    from tqdm import tqdm as _tqdm_import
    HAS_TQDM = True
    _tqdm_class = _tqdm_import
except ImportError:
    pass


__all__ = [
    'Image',
    'imagehash',
    'HAS_HEIF_SUPPORT',
    'HAS_TQDM',
    '_tqdm_class',
    '_logger',
]
