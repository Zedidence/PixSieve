"""
Deduplication module for the scanner package.

Provides algorithms for finding exact and perceptual duplicate images using
Union-Find data structures and optional LSH acceleration.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from functools import lru_cache
from typing import Optional, Callable, Any

from ..config import LSH_AUTO_THRESHOLD
from ..lsh import HammingLSH, calculate_optimal_params, estimate_comparison_reduction
from ..models import ImageInfo, DuplicateGroup
from .dependencies import imagehash, HAS_TQDM, _tqdm_class


# B2: LRU cache for parsed perceptual hashes — avoids re-parsing the same hex
# string on repeated runs or across both brute-force and LSH code paths.
@lru_cache(maxsize=None)
def _parse_phash(hex_str: str):
    """Parse a hex perceptual hash string to an imagehash object (cached)."""
    return imagehash.hex_to_hash(hex_str)


def find_exact_duplicates(
    images: list[ImageInfo],
    start_id: int = 1,
) -> list[DuplicateGroup]:
    """
    Find exact duplicate images based on file hash.

    Args:
        images: List of ImageInfo objects to check
        start_id: Starting ID for duplicate groups

    Returns:
        List of DuplicateGroup objects containing exact duplicates
    """
    # Group by file hash
    hash_groups: dict[str, list[ImageInfo]] = defaultdict(list)

    for img in images:
        if img.file_hash:
            hash_groups[img.file_hash].append(img)

    # Create duplicate groups
    groups = []
    group_id = start_id

    for file_hash, group_images in hash_groups.items():
        if len(group_images) > 1:
            group = DuplicateGroup(
                id=group_id,
                images=group_images,
                match_type="exact"
            )
            groups.append(group)
            group_id += 1

    return groups


def find_perceptual_duplicates(
    images: list[ImageInfo],
    threshold: int = 10,
    exclude_hashes: Optional[set[str]] = None,
    start_id: int = 1,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    show_progress: bool = True,
    use_lsh: Optional[bool] = None,
    logger: Optional[logging.Logger] = None,
) -> list[DuplicateGroup]:
    """
    Find perceptually similar images using pHash.

    Args:
        images: List of ImageInfo objects to check
        threshold: Maximum Hamming distance for similarity (0-64)
        exclude_hashes: Set of file hashes to skip (e.g., exact duplicates)
        start_id: Starting ID for duplicate groups
        progress_callback: Optional callback(current, total) for progress
        show_progress: Whether to show tqdm progress bar
        use_lsh: Force LSH on/off, or None for auto-select based on collection size
        logger: Optional logger for status messages

    Returns:
        List of DuplicateGroup objects containing similar images
    """
    exclude_hashes = exclude_hashes or set()

    # Filter to images with perceptual hashes that aren't already exact duplicates
    candidates = [
        img for img in images
        if img.perceptual_hash and img.file_hash not in exclude_hashes
    ]

    if len(candidates) < 2:
        return []

    # Auto-select LSH for large collections
    if use_lsh is None:
        use_lsh = len(candidates) >= LSH_AUTO_THRESHOLD
        if use_lsh and logger:
            logger.info(f"Using LSH optimization for {len(candidates):,} images")

    if use_lsh:
        return _find_perceptual_duplicates_lsh(
            candidates=candidates,
            threshold=threshold,
            start_id=start_id,
            progress_callback=progress_callback,
            show_progress=show_progress,
            logger=logger,
        )
    else:
        return _find_perceptual_duplicates_bruteforce(
            candidates=candidates,
            threshold=threshold,
            start_id=start_id,
            progress_callback=progress_callback,
            show_progress=show_progress,
        )


def _find_perceptual_duplicates_bruteforce(
    candidates: list[ImageInfo],
    threshold: int = 10,
    start_id: int = 1,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    show_progress: bool = True,
) -> list[DuplicateGroup]:
    """
    Brute-force O(n^2) perceptual duplicate finding.

    Best for small collections (< 5000 images).
    """
    # B2: Use cached hash parser
    parsed_hashes = []
    for img in candidates:
        try:
            parsed_hashes.append(_parse_phash(img.perceptual_hash))
        except Exception:
            parsed_hashes.append(None)

    # Union-Find for efficient grouping
    parent = list(range(len(candidates)))
    # B1: union-by-rank to keep tree height O(log n)
    rank = [0] * len(candidates)

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])  # Path compression
        return parent[x]

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            # B1: union by rank
            if rank[px] < rank[py]:
                parent[px] = py
            elif rank[px] > rank[py]:
                parent[py] = px
            else:
                parent[py] = px
                rank[px] += 1

    # Compare all pairs O(n^2)
    total_comparisons = (len(candidates) * (len(candidates) - 1)) // 2

    pbar: Optional[Any] = None
    if HAS_TQDM and show_progress and total_comparisons > 1000 and _tqdm_class is not None:
        pbar = _tqdm_class(total=total_comparisons, desc="Comparing images", unit="cmp", ncols=80)

    # B3: time-based progress throttling (0.5 s) instead of fixed-count
    last_pbar_time = time.monotonic()
    last_callback_time = time.monotonic()
    pbar_pending = 0
    _THROTTLE_S = 0.5

    comparison_count = 0
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if parsed_hashes[i] is not None and parsed_hashes[j] is not None:
                distance = parsed_hashes[i] - parsed_hashes[j]
                if distance <= threshold:
                    union(i, j)

            comparison_count += 1
            pbar_pending += 1

            # B3: throttle tqdm updates
            if pbar is not None:
                now = time.monotonic()
                if now - last_pbar_time >= _THROTTLE_S:
                    pbar.update(pbar_pending)
                    pbar_pending = 0
                    last_pbar_time = now

            # B3: throttle progress_callback updates
            if progress_callback:
                now = time.monotonic()
                if now - last_callback_time >= _THROTTLE_S:
                    progress_callback(comparison_count, total_comparisons)
                    last_callback_time = now

    # Flush remaining pbar updates
    if pbar is not None:
        if pbar_pending > 0:
            pbar.update(pbar_pending)
        pbar.close()

    # Final callback
    if progress_callback:
        progress_callback(comparison_count, total_comparisons)

    # Collect groups
    return _collect_duplicate_groups(candidates, parent, start_id)


def _find_perceptual_duplicates_lsh(
    candidates: list[ImageInfo],
    threshold: int = 10,
    start_id: int = 1,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    show_progress: bool = True,
    logger: Optional[logging.Logger] = None,
) -> list[DuplicateGroup]:
    """
    LSH-accelerated perceptual duplicate finding.

    Uses Locality-Sensitive Hashing to reduce comparisons from O(n^2) to O(n).
    Best for large collections (>= 5000 images).
    """
    n = len(candidates)

    # B2: Use cached hash parser
    parsed_hashes = []
    for img in candidates:
        try:
            parsed_hashes.append(_parse_phash(img.perceptual_hash))
        except Exception:
            parsed_hashes.append(None)

    # Calculate optimal LSH parameters based on collection size
    num_tables, bits_per_table = calculate_optimal_params(n, threshold)

    if logger:
        estimate = estimate_comparison_reduction(n, num_tables, bits_per_table)
        logger.info(
            f"LSH params: {num_tables} tables, {bits_per_table} bits/table "
            f"(~{estimate['speedup_factor']:.0f}x speedup expected)"
        )

    # Build LSH index
    pbar_build: Optional[Any] = None
    if HAS_TQDM and show_progress and _tqdm_class is not None:
        pbar_build = _tqdm_class(total=n, desc="Building LSH index", unit="img", ncols=80)

    lsh = HammingLSH(
        num_tables=num_tables,
        bits_per_table=bits_per_table,
        hash_bits=256,  # hash_size=16 produces 256-bit hashes
    )

    for idx, phash in enumerate(parsed_hashes):
        if phash is not None:
            lsh.add(idx, phash)
        if pbar_build is not None:
            pbar_build.update(1)

    if pbar_build is not None:
        pbar_build.close()

    # Union-Find for grouping (defined early so we can use it for deduplication)
    parent = list(range(len(candidates)))
    rank = [0] * len(candidates)  # Union by rank for better performance

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            # Union by rank
            if rank[px] < rank[py]:
                parent[px] = py
            elif rank[px] > rank[py]:
                parent[py] = px
            else:
                parent[py] = px
                rank[px] += 1

    # Estimate candidate pairs for progress reporting (without materializing)
    estimated_candidates = lsh.estimate_candidate_pairs()
    brute_force_comparisons = (n * (n - 1)) // 2

    if logger:
        estimated_reduction = 1 - (estimated_candidates / max(1, brute_force_comparisons))
        logger.info(
            f"LSH estimated comparisons: {brute_force_comparisons:,} -> ~{estimated_candidates:,} "
            f"(~{estimated_reduction:.1%} reduction)"
        )

    # A1: use LSH-level deduplication for moderate-sized collections to reduce
    # redundant Union-Find lookups. For very large collections (>500K), the seen
    # set itself becomes too large; rely on the Union-Find skip instead.
    lsh_deduplicate = n <= 500_000

    # Compare only LSH candidates using memory-efficient iterator
    pbar: Optional[Any] = None
    if HAS_TQDM and show_progress and estimated_candidates > 1000 and _tqdm_class is not None:
        pbar = _tqdm_class(total=estimated_candidates, desc="Comparing candidates", unit="cmp", ncols=80)

    # B3: time-based progress throttling
    last_pbar_time = time.monotonic()
    last_callback_time = time.monotonic()
    pbar_pending = 0
    _THROTTLE_S = 0.5

    comparison_count = 0
    actual_comparisons = 0
    matches_found = 0

    for i, j in lsh.iter_candidate_pairs(deduplicate=lsh_deduplicate):
        comparison_count += 1
        pbar_pending += 1

        # Skip if already in the same group (handles table-collision duplicates
        # when lsh_deduplicate=False, and provides a safety net otherwise)
        if find(i) == find(j):
            # B3: still throttle pbar for skipped pairs
            if pbar is not None:
                now = time.monotonic()
                if now - last_pbar_time >= _THROTTLE_S:
                    pbar.update(pbar_pending)
                    pbar_pending = 0
                    last_pbar_time = now
            continue

        if parsed_hashes[i] is not None and parsed_hashes[j] is not None:
            distance = parsed_hashes[i] - parsed_hashes[j]
            actual_comparisons += 1

            if distance <= threshold:
                union(i, j)
                matches_found += 1

        # B3: throttle tqdm updates
        if pbar is not None:
            now = time.monotonic()
            if now - last_pbar_time >= _THROTTLE_S:
                pbar.update(pbar_pending)
                pbar_pending = 0
                last_pbar_time = now

        # B3: throttle progress_callback updates
        if progress_callback:
            now = time.monotonic()
            if now - last_callback_time >= _THROTTLE_S:
                progress_callback(comparison_count, estimated_candidates)
                last_callback_time = now

    if pbar is not None:
        if pbar_pending > 0:
            pbar.update(pbar_pending)
        pbar.close()

    # Final callback
    if progress_callback:
        progress_callback(comparison_count, estimated_candidates)

    if logger:
        logger.info(
            f"Found {matches_found:,} matching pairs "
            f"({actual_comparisons:,} actual comparisons, "
            f"{comparison_count - actual_comparisons:,} skipped as already grouped)"
        )

    # Collect groups
    return _collect_duplicate_groups(candidates, parent, start_id)


def _collect_duplicate_groups(
    candidates: list[ImageInfo],
    parent: list[int],
    start_id: int,
) -> list[DuplicateGroup]:
    """
    Collect duplicate groups from Union-Find parent array.

    Helper function shared by brute-force and LSH implementations.
    """
    # Find with path compression
    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        # Path compression
        while parent[x] != root:
            next_x = parent[x]
            parent[x] = root
            x = next_x
        return root

    # Collect groups
    groups: dict[int, list[ImageInfo]] = defaultdict(list)
    for i, img in enumerate(candidates):
        root = find(i)
        groups[root].append(img)

    # Filter to only duplicates and create DuplicateGroup objects
    duplicate_groups: list[DuplicateGroup] = []
    group_id = start_id
    for group_images in groups.values():
        if len(group_images) > 1:
            group = DuplicateGroup(
                id=group_id,
                images=group_images,
                match_type="perceptual"
            )
            duplicate_groups.append(group)
            group_id += 1

    return duplicate_groups


__all__ = [
    'find_exact_duplicates',
    'find_perceptual_duplicates',
]
