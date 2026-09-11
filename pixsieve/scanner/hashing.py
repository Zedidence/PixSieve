"""
Hashing module for the scanner package.

Provides functions for calculating cryptographic hashes, perceptual hashes,
and quality scores for images.
"""

from __future__ import annotations

import hashlib
import os
import warnings
from pathlib import Path
from typing import Optional

from ..config import FORMAT_QUALITY_RANK
from ..models import ImageInfo
from .dependencies import Image, imagehash, _logger


def _ensure_phash_mode(img):
    """
    C1: Shared helper — ensure image is in a mode/orientation suitable for
    perceptual hashing.

    Returns the image (possibly a new converted object) in RGB or L mode,
    with EXIF orientation normalized. Raises on conversion failure so the
    caller can handle the error.

    EXIF orientation normalization: many phone/camera photos store rotation
    as an EXIF Orientation tag rather than physically rotating pixels. pHash
    operates on the raw pixel buffer as decoded and is not rotation-invariant,
    so without this step, a photo and its EXIF-rotated twin hash completely
    differently and are never caught as duplicates at any threshold.
    ImageOps.exif_transpose() applies the rotation/flip the tag describes and
    strips the tag, so downstream hashing always sees "already upright"
    pixels regardless of how the source file stored orientation. Video frames
    (from cv2, via PIL.Image.fromarray) carry no EXIF at all, so this is a
    no-op for them - safe to call unconditionally from every call site.

    CMYK images from print-origin JPEGs often use Adobe-encoded channels that
    are stored inverted relative to what PIL's direct convert() expects,
    producing a "negative" RGB result and therefore a wrong perceptual hash.
    We invert before converting as a heuristic correction.
    """
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img) or img

    if img.mode == 'CMYK':
        img = ImageOps.invert(img).convert('RGB')
    elif img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    return img


def calculate_file_hash(filepath: str | Path, algorithm: str = 'sha256') -> str:
    """
    Calculate cryptographic hash of a file.

    Args:
        filepath: Path to the file
        algorithm: Hash algorithm to use (default: sha256)

    Returns:
        Hex digest of the file hash, or empty string on error
    """
    hasher = hashlib.new(algorithm)
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        _logger.debug(f"File hash calculation failed for {filepath}: {e}")
        return ""


def calculate_perceptual_hash(filepath: str | Path, hash_size: int = 16) -> Optional[str]:
    """
    Calculate perceptual hash of an image.

    Uses pHash algorithm which is most accurate for photos.

    Args:
        filepath: Path to the image
        hash_size: Size of the hash (default 16, resulting in 256-bit hash)

    Returns:
        String representation of the perceptual hash, or None on error
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(filepath) as img:
                # C1: Use shared helper to convert to a phash-compatible mode
                try:
                    img = _ensure_phash_mode(img)
                except Exception as conv_err:
                    _logger.debug(f"Image mode conversion failed for {filepath} (mode={img.mode}): {conv_err}")
                    return None

                # Pre-downscale: pHash is effectively identical at any resolution
                # >= 256×256, but memory and CPU cost differ by orders of magnitude
                # for large images.  A 256×256 thumbnail costs ~22× less than a
                # full 4K image yet produces an identical hash.
                img.thumbnail((256, 256), Image.Resampling.LANCZOS)

                phash = imagehash.phash(img, hash_size=hash_size)
                return str(phash)
    except Exception as e:
        _logger.debug(f"Perceptual hash calculation failed for {filepath}: {e}")
        return None


def calculate_sharpness_score(img) -> float:
    """
    Cheap, PIL-only sharpness proxy: variance of an edge-filtered version of
    an already-in-hand image (the caller should pass the same 256x256
    thumbnail already produced for perceptual hashing - no second decode).

    Higher variance = more high-frequency edge content = a sharper-looking
    image. This is a WEAK tiebreaker only, not a real blur/quality detector:
    both candidates being compared are already downscaled to the same small
    thumbnail, which blunts anything that would only be visible at native
    resolution (e.g. detecting a fake-upscaled image). A real discriminating
    detector would need a second, larger decode per image - not worth the
    added per-image cost given this codebase's explicit scale-sensitivity
    (LARGE_LIBRARY_THRESHOLD, PERCEPTUAL_AUTO_DISABLE_THRESHOLD, LSH tiering
    all exist because per-image cost at scale is a first-order concern here).

    Args:
        img: A PIL Image (any mode/size - typically the phash thumbnail)

    Returns:
        Raw edge-variance value (unbounded; see calculate_quality_score for
        how this gets clamped into a small tiebreaker weight), or 0.0 on
        any failure.
    """
    try:
        from PIL import ImageFilter, ImageStat
        gray = img.convert('L')
        edges = gray.filter(ImageFilter.FIND_EDGES)
        return ImageStat.Stat(edges).var[0]
    except Exception as e:
        _logger.debug(f"Sharpness score calculation failed: {e}")
        return 0.0


def calculate_quality_score(info: ImageInfo) -> float:
    """
    Calculate a quality score for an image.
    Higher score = better quality.

    Factors considered:
    - Resolution (pixel count) - up to 50 points
    - File size (larger often means more detail) - up to 30 points
    - Bit depth - up to 10 points
    - Format quality ranking - up to 20 points
    - Sharpness (cheap edge-variance proxy, weak tiebreaker only) - up to 10 points

    Args:
        info: ImageInfo object with metadata

    Returns:
        Quality score (typically 0-120 range)
    """
    score = 0.0

    # Resolution score (normalized, max ~50 points for 50MP+)
    if info.pixel_count > 0:
        # Log scale to prevent huge images from dominating
        resolution_score = min(50, (info.pixel_count / 1_000_000) * 2)
        score += resolution_score

    # File size score (normalized, max ~30 points)
    if info.file_size > 0:
        size_mb = info.file_size / (1024 * 1024)
        size_score = min(30, size_mb * 3)
        score += size_score

    # Bit depth score (max 10 points)
    if info.bit_depth > 0:
        depth_score = min(10, info.bit_depth / 3.2)
        score += depth_score

    # Format quality score (max 20 points)
    ext = os.path.splitext(info.path)[1].lower()
    format_rank = FORMAT_QUALITY_RANK.get(ext, 50)
    format_score = format_rank / 5  # Scale to 0-20
    score += format_score

    # Sharpness score (max 10 points) - see calculate_sharpness_score() for
    # why this is a weak tiebreaker, not a real quality detector. Divisor of
    # 500 was calibrated empirically: a realistic sharp photo-like image
    # measures ~2500-3000 raw variance (saturating this to ~5-6 points),
    # its blurred twin ~400 (under 1 point) - a meaningful but modest signal.
    if info.sharpness_score > 0:
        score += min(10, info.sharpness_score / 500)

    return score


__all__ = [
    'calculate_file_hash',
    'calculate_perceptual_hash',
    'calculate_quality_score',
    'calculate_sharpness_score',
]
