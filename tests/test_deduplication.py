"""
Unit tests for pixsieve/scanner/deduplication.py internals.

Before this file, find_perceptual_duplicates()'s internal brute-force/LSH
grouping functions, _UnionFind, and _collect_duplicate_groups had zero direct
test coverage - see the duplicate-finding improvement plan (Phase 4, item
#18, folded into Phase 3 alongside the chain-drift fix itself).
"""

from __future__ import annotations

import random

import numpy as np
import imagehash
import pytest

from pixsieve.models import ImageInfo
from pixsieve.scanner.deduplication import (
    _UnionFind,
    _collect_duplicate_groups,
    _group_diameter,
    _split_group_by_complete_linkage,
    _split_high_diameter_groups,
    find_perceptual_duplicates,
)

HASH_BITS = 256
_SIDE = 16


def _random_hash(rng: random.Random) -> imagehash.ImageHash:
    bits = [rng.random() < 0.5 for _ in range(HASH_BITS)]
    return imagehash.ImageHash(np.array(bits, dtype=bool).reshape(_SIDE, _SIDE))


def _flip_bits(h: imagehash.ImageHash, distance: int, rng: random.Random) -> imagehash.ImageHash:
    flat = h.hash.flatten().copy()
    for pos in rng.sample(range(HASH_BITS), distance):
        flat[pos] = not flat[pos]
    return imagehash.ImageHash(flat.reshape(_SIDE, _SIDE))


def _img(path: str, phash) -> ImageInfo:
    return ImageInfo(path=path, file_hash=f"filehash-{path}", perceptual_hash=str(phash))


class TestUnionFind:
    def test_union_and_connected(self):
        uf = _UnionFind(5)
        uf.union(0, 1)
        uf.union(1, 2)
        assert uf.connected(0, 2)
        assert not uf.connected(0, 3)

    def test_find_is_stable_after_path_compression(self):
        uf = _UnionFind(4)
        uf.union(0, 1)
        uf.union(2, 3)
        uf.union(1, 2)
        root = uf.find(0)
        for i in range(4):
            assert uf.find(i) == root

    def test_union_same_set_is_noop(self):
        uf = _UnionFind(3)
        uf.union(0, 1)
        uf.union(0, 1)
        assert uf.connected(0, 1)


class TestCollectDuplicateGroups:
    def test_groups_only_multi_member_roots(self):
        images = [_img(f"/{i}.jpg", _random_hash(random.Random(i))) for i in range(4)]
        uf = _UnionFind(4)
        uf.union(0, 1)  # 2 and 3 stay singletons
        groups = _collect_duplicate_groups(images, uf, start_id=1)
        assert len(groups) == 1
        assert {img.path for img in groups[0].images} == {"/0.jpg", "/1.jpg"}

    def test_no_groups_when_all_singletons(self):
        images = [_img(f"/{i}.jpg", _random_hash(random.Random(i))) for i in range(3)]
        uf = _UnionFind(3)
        assert _collect_duplicate_groups(images, uf, start_id=1) == []


class TestChainDriftMitigation:
    """
    Regression tests for the transitive chain-drift fix: union-find alone
    merges A~B~C into one group even when A and C individually exceed
    threshold. _split_high_diameter_groups() (called from
    _collect_duplicate_groups when threshold is given) must split such a
    group so every remaining pair is genuinely within threshold.
    """

    THRESHOLD = 10

    def _build_chain(self):
        """
        A 3-item chain: A~B (distance 7), B~C (distance 7, using a disjoint
        bit-flip set so distances add rather than cancel), A~C (distance 14 -
        beyond THRESHOLD=10). Classic burst-photo drift shape.
        """
        rng = random.Random(1)
        a = _random_hash(rng)
        b = _flip_bits(a, 7, random.Random(100))
        c = _flip_bits(b, 7, random.Random(200))
        return a, b, c

    def test_union_find_alone_merges_the_whole_chain(self):
        """Characterization test: proves the drift actually happens today at
        the Union-Find level, before any diameter check is applied."""
        a, b, c = self._build_chain()
        images = [_img("/a.jpg", a), _img("/b.jpg", b), _img("/c.jpg", c)]

        distance_ab = a - b
        distance_bc = b - c
        distance_ac = a - c
        assert distance_ab <= self.THRESHOLD
        assert distance_bc <= self.THRESHOLD
        assert distance_ac > self.THRESHOLD  # the actual drift

        uf = _UnionFind(3)
        # Union exactly the qualifying pairs, mirroring what the real
        # brute-force loop would do based on threshold alone.
        if distance_ab <= self.THRESHOLD:
            uf.union(0, 1)
        if distance_bc <= self.THRESHOLD:
            uf.union(1, 2)
        if distance_ac <= self.THRESHOLD:
            uf.union(0, 2)

        # No threshold passed -> no diameter check -> old (buggy) behavior
        groups = _collect_duplicate_groups(images, uf, start_id=1)
        assert len(groups) == 1
        assert len(groups[0].images) == 3  # A, B, and C all incorrectly merged

    def test_diameter_check_splits_the_chain(self):
        """With threshold passed through, the over-diameter group must be
        split so the genuinely-close pair (A, B) survives as a group and the
        drifted member (C) is excluded rather than silently included."""
        a, b, c = self._build_chain()
        images = [_img("/a.jpg", a), _img("/b.jpg", b), _img("/c.jpg", c)]

        uf = _UnionFind(3)
        uf.union(0, 1)  # A-B qualifies
        uf.union(1, 2)  # B-C qualifies
        # (A-C was never unioned directly - it only shares a root transitively)

        groups = _collect_duplicate_groups(images, uf, start_id=1, threshold=self.THRESHOLD)

        # Exactly one group survives, containing A and B only - C (which
        # only shared a root with A transitively, never a direct qualifying
        # pair with A) is correctly excluded rather than silently kept.
        assert len(groups) == 1
        surviving_paths = {img.path for img in groups[0].images}
        assert surviving_paths == {"/a.jpg", "/b.jpg"}

    def test_tight_cluster_is_not_split(self):
        """A genuinely tight cluster (every pair well within threshold) must
        NOT be split - the diameter check should only act on groups that
        actually violate the threshold contract."""
        rng = random.Random(42)
        base = _random_hash(rng)
        members = [base] + [_flip_bits(base, 2, random.Random(seed)) for seed in range(300, 303)]
        images = [_img(f"/{i}.jpg", h) for i, h in enumerate(members)]

        uf = _UnionFind(4)
        for i in range(4):
            for j in range(i + 1, 4):
                d = imagehash.hex_to_hash(images[i].perceptual_hash) - imagehash.hex_to_hash(images[j].perceptual_hash)
                if d <= self.THRESHOLD:
                    uf.union(i, j)

        groups = _collect_duplicate_groups(images, uf, start_id=1, threshold=self.THRESHOLD)
        assert len(groups) == 1
        assert len(groups[0].images) == 4

    def test_exact_and_video_match_types_are_never_split(self):
        """The diameter check must only apply to match_type='perceptual' -
        exact-duplicate groups have no diameter concept, and video-perceptual
        groups use a different (average per-frame) distance metric this
        Hamming-distance-based check cannot evaluate."""
        a, b, c = self._build_chain()
        images = [_img("/a.jpg", a), _img("/b.jpg", b), _img("/c.jpg", c)]
        uf = _UnionFind(3)
        uf.union(0, 1)
        uf.union(1, 2)

        exact_groups = _collect_duplicate_groups(images, uf, start_id=1, match_type="exact", threshold=self.THRESHOLD)
        assert len(exact_groups) == 1 and len(exact_groups[0].images) == 3

        video_groups = _collect_duplicate_groups(
            images, uf, start_id=1, match_type="video-perceptual", threshold=self.THRESHOLD
        )
        assert len(video_groups) == 1 and len(video_groups[0].images) == 3


class TestGroupDiameterAndCompleteLinkage:
    def test_group_diameter_of_pair(self):
        rng = random.Random(9)
        a = _random_hash(rng)
        b = _flip_bits(a, 6, rng)
        images = [_img("/a.jpg", a), _img("/b.jpg", b)]
        assert _group_diameter(images) == 6

    def test_split_respects_threshold_in_every_resulting_cluster(self):
        rng = random.Random(11)
        a = _random_hash(rng)
        b = _flip_bits(a, 5, random.Random(21))
        c = _flip_bits(b, 5, random.Random(22))  # a-c likely > threshold
        images = [_img("/a.jpg", a), _img("/b.jpg", b), _img("/c.jpg", c)]

        clusters = _split_group_by_complete_linkage(images, threshold=8)
        for cluster in clusters:
            paths = {img.path for img in cluster}
            for img_x in cluster:
                for img_y in cluster:
                    if img_x is img_y:
                        continue
                    dx = imagehash.hex_to_hash(img_x.perceptual_hash)
                    dy = imagehash.hex_to_hash(img_y.perceptual_hash)
                    assert (dx - dy) <= 8, f"cluster {paths} violates threshold"


class TestFindPerceptualDuplicatesBruteforceVsLsh:
    """Brute-force and LSH must produce the same groups (as path-sets) for
    the same input - LSH is a candidate-generation optimization only, exact
    Hamming verification happens either way."""

    def _build_dataset(self, n_clean_singles=20):
        rng = random.Random(7)
        images = []

        # 3 true-duplicate pairs at a safely-sub-threshold distance
        for pair_idx in range(3):
            base = _random_hash(rng)
            twin = _flip_bits(base, 3, rng)
            images.append(_img(f"/pair{pair_idx}_a.jpg", base))
            images.append(_img(f"/pair{pair_idx}_b.jpg", twin))

        # unrelated singles (each independently random - vanishingly unlikely
        # to coincidentally collide within threshold=10 out of 256 bits)
        for i in range(n_clean_singles):
            images.append(_img(f"/single{i}.jpg", _random_hash(rng)))

        return images

    def _group_path_sets(self, groups):
        return sorted(frozenset(img.path for img in g.images) for g in groups)

    def test_bruteforce_and_lsh_agree(self):
        images = self._build_dataset()

        bruteforce_groups = find_perceptual_duplicates(images, threshold=10, use_lsh=False, show_progress=False)
        lsh_groups = find_perceptual_duplicates(images, threshold=10, use_lsh=True, show_progress=False)

        assert self._group_path_sets(bruteforce_groups) == self._group_path_sets(lsh_groups)
        assert len(bruteforce_groups) == 3  # exactly the 3 planted pairs


class TestParsePhashFailureDoesNotCrash:
    """
    Regression test: both _find_perceptual_duplicates_bruteforce and
    _find_perceptual_duplicates_lsh used to reference an undefined `logger`
    (bruteforce path) or call .debug() on a possibly-None `logger` parameter
    (LSH path) inside their hash-parse-failure handler - a NameError/
    AttributeError instead of a graceful debug log. Both now use the
    module-level _logger, so a malformed perceptual_hash degrades to a
    skipped comparison instead of crashing the whole scan.
    """

    def test_malformed_hash_does_not_crash_bruteforce(self):
        images = [
            _img("/a.jpg", _random_hash(random.Random(1))),
            ImageInfo(path="/bad.jpg", file_hash="x", perceptual_hash="not-a-valid-hash"),
        ]
        # Must not raise.
        groups = find_perceptual_duplicates(images, threshold=10, use_lsh=False, show_progress=False)
        assert groups == []

    def test_malformed_hash_does_not_crash_lsh(self):
        images = [
            _img("/a.jpg", _random_hash(random.Random(1))),
            ImageInfo(path="/bad.jpg", file_hash="x", perceptual_hash="not-a-valid-hash"),
        ]
        # Force the LSH path explicitly (no logger passed - covers the
        # AttributeError-on-None-logger variant of the bug too).
        groups = find_perceptual_duplicates(images, threshold=10, use_lsh=True, show_progress=False)
        assert groups == []
