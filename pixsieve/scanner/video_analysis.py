"""
Video analysis module for the scanner package.

Provides single-video analysis functionality mirroring analysis.py's
analyze_image(), but sourcing metadata and perceptual hashes from sampled
video frames via opencv instead of PIL.

Perceptual matching for video is frame-position based: N evenly-spaced
frames are sampled across each video's duration and hashed individually
(reusing the same pHash routine used for images). This catches the common
case of a duplicate video that was re-encoded, resized, or recompressed,
but will NOT detect trimmed, reordered, or heavily time-shifted duplicates
- that is a known limitation, not a bug.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from ..models import ImageInfo
from .dependencies import Image, imagehash, HAS_VIDEO_SUPPORT, cv2, _logger
from .hashing import calculate_file_hash, calculate_quality_score, _ensure_phash_mode

# Hex-hash segments within ImageInfo.perceptual_hash are joined with this
# separator (also used by scanner/video_deduplication.py to split them back
# apart). "|" cannot appear in an imagehash hex digest so this is unambiguous.
VIDEO_HASH_SEPARATOR = "|"

# Skip this fraction of the video's total frames at the start/end when
# sampling, to avoid black frames, title cards, or fade-in/out artifacts
# that are common at the very start/end of a clip.
_SAMPLE_MARGIN = 0.05


def analyze_video(
    filepath: str | Path,
    calculate_phash: bool = True,
    calculate_hash: bool = True,
    num_frames: int = 5,
) -> ImageInfo:
    """
    Analyze a video file and extract metadata.

    Args:
        filepath: Path to the video file
        calculate_phash: Whether to compute a multi-frame perceptual hash
        calculate_hash: Whether to compute file hash (SHA-256)
        num_frames: Number of evenly-spaced frames to sample for the
            perceptual hash

    Returns:
        ImageInfo object with all extracted metadata (media_type="video")
    """
    filepath = str(filepath)
    info = ImageInfo(path=filepath, media_type="video")

    try:
        if not os.path.exists(filepath):
            info.error = "File not found"
            return info

        if not os.access(filepath, os.R_OK):
            info.error = "File not readable (permission denied)"
            return info

        info.file_size = os.path.getsize(filepath)

        # Calculate file hash first (only if needed), matching analyze_image's
        # ordering - so exact-duplicate detection still works even if the
        # video can't be decoded.
        if calculate_hash:
            info.file_hash = calculate_file_hash(filepath)

        ext = os.path.splitext(filepath)[1].lower()
        info.format = ext.lstrip('.').upper()

        if not HAS_VIDEO_SUPPORT:
            info.error = "Video support not installed (pip install opencv-python-headless)"
            return info

        cap = cv2.VideoCapture(filepath)
        try:
            if not cap.isOpened():
                info.error = "Could not open video file"
                return info

            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            info.width = width
            info.height = height
            info.pixel_count = width * height
            info.bit_depth = 24  # decoded frames are effectively 8-bit/channel RGB
            info.duration = (frame_count / fps) if fps > 0 else 0.0

            if width <= 0 or height <= 0 or frame_count <= 0:
                info.error = "Could not read video metadata (corrupt or unsupported codec)"
                return info

            if calculate_phash:
                info.perceptual_hash = _sample_and_hash_frames(cap, frame_count, num_frames)

        finally:
            cap.release()

        info.quality_score = calculate_quality_score(info)

    except Exception as e:
        info.error = str(e)

    return info


def _sample_and_hash_frames(cap, frame_count: int, num_frames: int) -> str:
    """
    Sample `num_frames` evenly-spaced frames (skipping the leading/trailing
    margin) and return their pHash hex digests joined by VIDEO_HASH_SEPARATOR.

    Frames that fail to decode are skipped; if none decode, returns "".
    """
    margin = int(frame_count * _SAMPLE_MARGIN)
    lo, hi = margin, max(margin + 1, frame_count - margin - 1)

    if num_frames <= 1:
        positions = [frame_count // 2]
    else:
        positions = [
            lo + round((hi - lo) * i / (num_frames - 1))
            for i in range(num_frames)
        ]

    hashes: list[str] = []
    for pos in positions:
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            # OpenCV decodes frames as BGR; PIL/imagehash expect RGB.
            rgb_frame = frame[:, :, ::-1]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                img = Image.fromarray(rgb_frame)
                img = _ensure_phash_mode(img)
                img.thumbnail((256, 256), Image.Resampling.LANCZOS)
                phash = imagehash.phash(img, hash_size=16)
                hashes.append(str(phash))
        except Exception as e:
            _logger.debug(f"Frame hash failed at position {pos}: {e}")
            continue

    return VIDEO_HASH_SEPARATOR.join(hashes)


__all__ = ['analyze_video', 'VIDEO_HASH_SEPARATOR']
