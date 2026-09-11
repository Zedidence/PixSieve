"""
Video deduplication module for the scanner package.

Finds perceptually similar videos by comparing their multi-frame pHash
sequences (produced by video_analysis.analyze_video()). Brute-force only:
video collections are expected to be orders of magnitude smaller than image
collections, so the O(n^2) comparison LSH exists to avoid in
deduplication.py is unnecessary here.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models import ImageInfo, DuplicateGroup
from .deduplication import _UnionFind, _collect_duplicate_groups, _parse_phash
from .video_analysis import VIDEO_HASH_SEPARATOR

_logger = logging.getLogger(__name__)


def _parse_video_hash(hash_str: str) -> list:
    """Split a "|"-joined video perceptual hash into per-frame imagehash objects."""
    hashes = []
    for segment in hash_str.split(VIDEO_HASH_SEPARATOR):
        if not segment:
            continue
        try:
            hashes.append(_parse_phash(segment))
        except Exception as e:
            _logger.debug(f"Failed to parse video frame hash segment {segment!r}: {e}")
    return hashes


def _average_frame_distance(hashes_a: list, hashes_b: list) -> Optional[float]:
    """
    Average Hamming distance across corresponding sampled-frame positions.

    Both sequences were sampled at the same evenly-spaced offsets (see
    video_analysis._sample_and_hash_frames), so position i in one
    corresponds to position i in the other as long as both videos have a
    similar duration. Uses min(len) pairs; returns None if there is no
    overlap (e.g. one video's frames all failed to decode).
    """
    n = min(len(hashes_a), len(hashes_b))
    if n == 0:
        return None
    total = sum(hashes_a[i] - hashes_b[i] for i in range(n))
    return total / n


def find_video_perceptual_duplicates(
    videos: list[ImageInfo],
    threshold: int = 10,
    start_id: int = 1,
) -> list[DuplicateGroup]:
    """
    Find perceptually similar videos using multi-frame pHash comparison.

    Args:
        videos: List of ImageInfo objects (media_type="video") to compare
        threshold: Maximum average per-frame Hamming distance for a match.
            Uses the same scale as image perceptual matching (both hash
            individual frames with imagehash.phash(hash_size=16)).
        start_id: Starting ID for duplicate groups

    Returns:
        List of DuplicateGroup objects (match_type="video-perceptual")

    Known limitation: frames are compared position-to-position across evenly
    spaced samples, so this will not detect trimmed, reordered, or heavily
    time-shifted duplicates - only the common case of the same source video
    re-encoded/resized/recompressed.
    """
    candidates = [v for v in videos if v.perceptual_hash]
    if len(candidates) < 2:
        return []

    parsed_hashes = [_parse_video_hash(v.perceptual_hash) for v in candidates]

    uf = _UnionFind(len(candidates))

    for i in range(len(candidates)):
        if not parsed_hashes[i]:
            continue
        for j in range(i + 1, len(candidates)):
            if not parsed_hashes[j]:
                continue
            distance = _average_frame_distance(parsed_hashes[i], parsed_hashes[j])
            if distance is not None and distance <= threshold:
                uf.union(i, j)

    return _collect_duplicate_groups(candidates, uf, start_id, match_type="video-perceptual")


__all__ = ['find_video_perceptual_duplicates']
