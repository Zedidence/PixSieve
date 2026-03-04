"""
Image analysis module for the scanner package.

Provides single-image analysis functionality with comprehensive error handling,
metadata extraction, and hash calculation.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from ..config import MODE_BIT_DEPTHS
from ..models import ImageInfo
from .dependencies import Image, imagehash, HAS_HEIF_SUPPORT, _logger
from .hashing import calculate_file_hash, calculate_quality_score, _ensure_phash_mode


def analyze_image(
    filepath: str | Path,
    calculate_phash: bool = True,
    calculate_hash: bool = True,
) -> ImageInfo:
    """
    Analyze an image file and extract metadata.

    Args:
        filepath: Path to the image file
        calculate_phash: Whether to compute perceptual hash
        calculate_hash: Whether to compute file hash (SHA-256)

    Returns:
        ImageInfo object with all extracted metadata
    """
    filepath = str(filepath)
    info = ImageInfo(path=filepath)

    try:
        # File size - check file exists and is accessible first
        if not os.path.exists(filepath):
            info.error = "File not found"
            return info

        if not os.access(filepath, os.R_OK):
            info.error = "File not readable (permission denied)"
            return info

        info.file_size = os.path.getsize(filepath)

        # Calculate file hash (only if needed for exact duplicate detection)
        if calculate_hash:
            info.file_hash = calculate_file_hash(filepath)

        # Check for HEIC without support
        ext = os.path.splitext(filepath)[1].lower()
        if ext in {'.heic', '.heif'} and not HAS_HEIF_SUPPORT:
            info.error = "HEIC/HEIF support not installed (pip install pillow-heif)"
            return info

        # Open image and extract metadata.
        #
        # When calculate_phash=True (the common scan path), thumbnail() loads
        # pixel data which naturally raises for truncated/corrupt files — so a
        # separate verify() pass is unnecessary.  OSError from thumbnail is
        # re-raised as a corruption error below.
        #
        # When calculate_phash=False no pixel data is loaded, so we still need
        # an explicit verify() pass (which exhausts the handle, requiring a
        # reopen for metadata extraction).
        if not calculate_phash:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                    with Image.open(filepath) as _verify_img:
                        _verify_img.verify()
            except Image.UnidentifiedImageError as e:
                info.error = f"Not a valid image file: {e}"
                return info
            except Exception as load_err:
                info.error = f"Corrupt or truncated image: {load_err}"
                return info

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                with Image.open(filepath) as img:
                    info.width = img.width
                    info.height = img.height
                    info.pixel_count = img.width * img.height
                    info.format = img.format or ""
                    info.bit_depth = MODE_BIT_DEPTHS.get(img.mode, 24)

                    if calculate_phash:
                        try:
                            # C1: shared helper removes duplicated mode-conversion logic
                            phash_img = _ensure_phash_mode(img)
                            # Pre-downscale before hashing: hash is identical at any
                            # resolution >= 256×256 but cost differs by orders of magnitude.
                            # thumbnail() loads pixel data — OSError here means truncation.
                            phash_img.thumbnail((256, 256), Image.Resampling.LANCZOS)
                            phash = imagehash.phash(phash_img, hash_size=16)
                            info.perceptual_hash = str(phash)
                        except OSError as phash_err:
                            # OSError from thumbnail/load signals truncated pixel data.
                            info.error = f"Corrupt or truncated image: {phash_err}"
                            return info
                        except Exception as phash_err:
                            _logger.debug(f"Perceptual hash failed for {filepath}: {phash_err}")
                            info.perceptual_hash = ""

        except Image.UnidentifiedImageError as e:
            info.error = f"Not a valid image file: {e}"
            return info
        except Exception as e:
            info.error = f"Failed to open image: {e}"
            return info

        # Calculate quality score
        info.quality_score = calculate_quality_score(info)

    except Exception as e:
        info.error = str(e)

    return info


__all__ = ['analyze_image']
