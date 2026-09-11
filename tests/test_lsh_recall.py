"""
Empirical recall validation for LSH-vs-ground-truth perceptual duplicate matching.

docs/performance.md claims LSH achieves "Expected Recall >99.9%" at every
collection-size tier scanner/lsh.py's calculate_optimal_params() hardcodes
parameters for - but that figure was never derived from a measurement
anywhere in the codebase (see the duplicate-finding improvement plan, Phase 2).
This file is that measurement.

Design: recall of a *specific* candidate pair (does it collide in at least
one LSH table?) depends only on (Hamming distance, hash_bits, num_tables,
bits_per_table) - not on how many OTHER items share the index, since bucket
membership is purely bit-content-based. So real per-tier recall can be
measured with many independently-seeded synthetic pairs planted at a
controlled distance, without needing to build an actual N-image corpus -
which would be far too slow for a statistical sweep across every tier.

Each planted pair is checked against HammingLSH.get_all_candidate_pairs() -
this is the actual probabilistic step scanner/deduplication.py relies on
before its exact-Hamming-distance verification; if a true-duplicate pair
never becomes a candidate here, it is never found, no matter how good the
verification step is.

All assertions below are derived from repeated empirical measurement with
fixed seeds (fully deterministic, not flaky), not from the documentation's
unverified claim.
"""

from __future__ import annotations

import random

import numpy as np
import imagehash
import pytest

from pixsieve.lsh import HammingLSH, calculate_optimal_params

HASH_BITS = 256
_SIDE = 16  # sqrt(256) - imagehash.ImageHash expects a square bit array, matching hash_size=16 in production


def _random_hash(rng: random.Random) -> imagehash.ImageHash:
    bits = [rng.random() < 0.5 for _ in range(HASH_BITS)]
    return imagehash.ImageHash(np.array(bits, dtype=bool).reshape(_SIDE, _SIDE))


def _flip_bits(h: imagehash.ImageHash, distance: int, rng: random.Random) -> imagehash.ImageHash:
    """Return a copy of `h` with exactly `distance` bits flipped (i.e. at
    Hamming distance `distance` from `h`)."""
    flat = h.hash.flatten().copy()
    for pos in rng.sample(range(HASH_BITS), distance):
        flat[pos] = not flat[pos]
    return imagehash.ImageHash(flat.reshape(_SIDE, _SIDE))


def _measure_recall(num_tables: int, bits_per_table: int, distance: int, trials: int, seed: int) -> float:
    """
    Build a fresh HammingLSH, plant `trials` independent true-duplicate pairs
    at exactly `distance` Hamming distance apart, and return the fraction
    recovered by get_all_candidate_pairs().
    """
    rng = random.Random(seed)
    lsh = HammingLSH(num_tables=num_tables, bits_per_table=bits_per_table)
    for i in range(trials):
        base = _random_hash(rng)
        partner = _flip_bits(base, distance, rng) if distance > 0 else base
        lsh.add(i * 2, base)
        lsh.add(i * 2 + 1, partner)

    pairs = lsh.get_all_candidate_pairs()
    found = sum(1 for i in range(trials) if (i * 2, i * 2 + 1) in pairs)
    return found / trials


TRIALS = 2000

# (tier_size, threshold, distance) - tier_size picks a representative size
# within each calculate_optimal_params() bracket (<10K, <50K, <200K, <500K,
# >=500K); distance is swept near the threshold boundary, which is exactly
# where docs/performance.md's own caveat says LSH "may occasionally miss
# edge-case duplicates."
CASES = [
    (5_000, 5, 3), (5_000, 5, 4), (5_000, 5, 5),
    (5_000, 10, 8), (5_000, 10, 9), (5_000, 10, 10),
    (5_000, 15, 13), (5_000, 15, 14), (5_000, 15, 15),
    (30_000, 5, 5), (30_000, 10, 10), (30_000, 15, 15),
    (100_000, 5, 5), (100_000, 10, 10), (100_000, 15, 15),
    (300_000, 5, 5), (300_000, 10, 10), (300_000, 15, 15),
    (600_000, 5, 5), (600_000, 10, 10), (600_000, 15, 15),
]


@pytest.mark.parametrize("size,threshold,distance", CASES)
def test_lsh_recall_across_tiers_and_thresholds(size, threshold, distance):
    """
    For every calculate_optimal_params() tier and threshold/distance
    combination, measured recall must clear an empirically-derived floor.

    Empirically-derived floors (from repeated measurement, see
    test_lsh_recall_smallest_tier_below_documented_claim below for the
    detailed headline case): every combination measures at or above 99.5%,
    EXCEPT the smallest tier (<10K images, only 15 tables) at threshold=15
    exactly at the boundary distance, which settles around 99.1% - still
    assert a safety-margined 97% floor there rather than the higher bar.
    """
    num_tables, bits_per_table = calculate_optimal_params(size, threshold=threshold)
    recall = _measure_recall(
        num_tables, bits_per_table, distance, trials=TRIALS,
        seed=1_000_000 + size + threshold * 100 + distance,
    )
    if size < 10_000 and threshold == 15 and distance == 15:
        floor = 0.97
    else:
        floor = 0.995
    assert recall >= floor, (
        f"size={size} threshold={threshold} distance={distance} "
        f"tables={num_tables} bits_per_table={bits_per_table} recall={recall:.4f} < {floor}"
    )


def test_lsh_recall_smallest_tier_below_documented_claim():
    """
    Headline finding: docs/performance.md's "Expected Recall >99.9%" table
    does NOT hold uniformly. The smallest tier's parameters (<10K images ->
    15 tables, 20 bits/table), at threshold=15 (the top of the documented
    "recommended 5-15" range) and exactly at the threshold boundary distance,
    measure real recall around 99.1% - materially below >99.9%.

    This is the one test in the whole duplicate-finding test suite that can
    actually overturn a stated fact rather than just pin something already
    obvious from reading the source. If this assertion ever fails because
    measured recall improved to >=99.9% (e.g. calculate_optimal_params was
    retuned, or HammingLSH's algorithm changed), that is GOOD news - update
    docs/performance.md's recall table and boundary-case caveat together
    with this test rather than only loosening the assertion.
    """
    num_tables, bits_per_table = calculate_optimal_params(5_000, threshold=15)
    recall = _measure_recall(num_tables, bits_per_table, distance=15, trials=3000, seed=555)

    assert 0.97 <= recall < 0.999, (
        f"Expected measured recall for the smallest LSH tier at threshold=15, "
        f"exactly at the boundary, to be materially below the documented "
        f">99.9% claim; measured {recall:.4f}. See docstring for what to do "
        f"if this changes."
    )


def test_lsh_recall_well_below_threshold_boundary_is_near_perfect():
    """
    Sanity control: distances comfortably below the threshold (far from the
    boundary) should recall at ~100% even for the smallest/weakest tier -
    confirming the boundary-case gap above is specifically a boundary
    phenomenon, not a general weakness of the smallest tier's parameters.
    """
    num_tables, bits_per_table = calculate_optimal_params(5_000, threshold=15)
    recall = _measure_recall(num_tables, bits_per_table, distance=5, trials=2000, seed=777)
    assert recall >= 0.999, f"recall={recall:.4f}"


def test_lsh_recall_identical_hashes_always_found():
    """Distance-0 (byte-identical perceptual hash) pairs must always collide
    - the trivial floor every tier/threshold combination must clear."""
    num_tables, bits_per_table = calculate_optimal_params(5_000, threshold=15)
    recall = _measure_recall(num_tables, bits_per_table, distance=0, trials=500, seed=42)
    assert recall == 1.0
