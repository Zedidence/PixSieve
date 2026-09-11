"""
Selection strategy utilities for the PixSieve.

Provides automatic selection strategies to determine which images to keep
vs delete when duplicates are found.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Optional

from ..models import DuplicateGroup, ImageInfo


class SelectionStrategy(str, Enum):
    """
    Strategy for selecting which duplicate images to keep.

    Attributes:
        QUALITY: Keep the highest quality image (best resolution, format, etc.)
        LARGEST: Keep the largest file size
        SMALLEST: Keep the smallest file size
        NEWEST: Keep the most recently modified file
        OLDEST: Keep the oldest file
    """
    QUALITY = 'quality'
    LARGEST = 'largest'
    SMALLEST = 'smallest'
    NEWEST = 'newest'
    OLDEST = 'oldest'


def _get_capture_or_mtime(img: Any) -> float:
    """
    Get the timestamp to sort by for NEWEST-first ordering: EXIF capture date
    (img.capture_date) when available, falling back to filesystem mtime.

    Filesystem mtime is frequently wrong for "when was this actually taken" -
    a copy/sync/backup operation commonly updates mtime to the copy time,
    not the original capture time. EXIF DateTimeOriginal is the semantically
    correct field for a photo-management tool, when present. Videos and
    EXIF-stripped images have no capture_date, so this is purely additive:
    it never makes a previously-correct mtime-based decision worse, it just
    upgrades to a more accurate signal when one is available.

    Args:
        img: ImageInfo object

    Returns:
        Timestamp as float, or 0 on error (mtime unreadable and no capture_date)
    """
    if img.capture_date is not None:
        return img.capture_date
    try:
        return os.path.getmtime(img.path)
    except OSError:
        return 0.0


def _get_capture_or_mtime_reverse(img: Any) -> float:
    """
    Like _get_capture_or_mtime(), for OLDEST-first ordering (infinity instead
    of 0 on error, so an unreadable file sorts last rather than first).

    Args:
        img: ImageInfo object

    Returns:
        Timestamp as float, or infinity on error
    """
    if img.capture_date is not None:
        return img.capture_date
    try:
        return os.path.getmtime(img.path)
    except OSError:
        return float('inf')


def _apply_strategy_to_group(group: DuplicateGroup, strategy_enum: 'SelectionStrategy') -> dict[str, str]:
    """
    Determine keep/delete for a single group by sorting its images per strategy.

    Args:
        group: The duplicate group to resolve
        strategy_enum: Already-validated SelectionStrategy

    Returns:
        Dict mapping image path to 'keep' or 'delete' for this group's images
    """
    if strategy_enum == SelectionStrategy.LARGEST:
        sorted_images = sorted(group.images, key=lambda x: -x.file_size)
    elif strategy_enum == SelectionStrategy.SMALLEST:
        sorted_images = sorted(group.images, key=lambda x: x.file_size)
    elif strategy_enum == SelectionStrategy.NEWEST:
        sorted_images = sorted(group.images, key=lambda x: -_get_capture_or_mtime(x))
    elif strategy_enum == SelectionStrategy.OLDEST:
        sorted_images = sorted(group.images, key=_get_capture_or_mtime_reverse)
    else:
        # QUALITY (default/fallback): highest quality score
        sorted_images = sorted(group.images, key=lambda x: -x.quality_score)

    return {img.path: ('keep' if idx == 0 else 'delete') for idx, img in enumerate(sorted_images)}


def apply_selection_strategy(groups: list[DuplicateGroup], strategy: str) -> dict[str, str]:
    """
    Apply auto-selection strategy to determine which images to keep/delete.

    Args:
        groups: List of duplicate groups
        strategy: One of 'quality', 'largest', 'smallest', 'newest', 'oldest'

    Returns:
        Dict mapping image path to 'keep' or 'delete'

    Examples:
        >>> groups = [DuplicateGroup(...)]
        >>> selections = apply_selection_strategy(groups, 'quality')
        >>> selections['/path/to/image.jpg']
        'keep'
    """
    try:
        strategy_enum = SelectionStrategy(strategy)
    except ValueError:
        strategy_enum = SelectionStrategy.QUALITY

    selections = {}
    for group in groups:
        if not group.images:
            continue
        selections.update(_apply_strategy_to_group(group, strategy_enum))

    return selections


def resolve_group_selections(groups: list[DuplicateGroup], strategy: str) -> dict[str, str]:
    """
    Reference-aware selection: determines which images to keep/delete, giving
    the reference folder (if any images in a group belong to it) priority over
    the normal auto-select strategy.

    Per group:
      - If ANY image in the group has is_reference=True: all is_reference
        images are marked 'keep' and all non-reference images are marked
        'delete'. The strategy is not consulted for this group at all - the
        reference copy is treated as canonical truth.
      - Otherwise: falls back to the same per-strategy sort logic as
        apply_selection_strategy().

    Args:
        groups: List of duplicate groups
        strategy: One of 'quality', 'largest', 'smallest', 'newest', 'oldest'

    Returns:
        Dict mapping image path to 'keep' or 'delete'
    """
    try:
        strategy_enum = SelectionStrategy(strategy)
    except ValueError:
        strategy_enum = SelectionStrategy.QUALITY

    selections = {}
    for group in groups:
        if not group.images:
            continue

        if any(img.is_reference for img in group.images):
            # Reference-internal duplicates (no non-reference copy present)
            # intentionally all resolve to 'keep' - nothing to auto-delete.
            for img in group.images:
                selections[img.path] = 'keep' if img.is_reference else 'delete'
        else:
            selections.update(_apply_strategy_to_group(group, strategy_enum))

    return selections


def stamp_group_selections(groups: list[DuplicateGroup], selections: dict[str, str]) -> None:
    """
    Write resolve_group_selections()'s per-group "keep" choice back onto each
    group's DuplicateGroup.selected_keep field.

    DuplicateGroup.to_dict() falls back to the quality-only, non-reference-aware
    best_image for 'selected_keep' whenever selected_keep is unset - so any
    group serialized (state.py, API responses) or reported (exporters.py)
    before this stamp happens silently disagrees with what
    resolve_group_selections() actually decided. Call this immediately after
    resolve_group_selections(), before groups are serialized or reported, in
    every place a scan's groups are finalized.

    For a group with multiple 'keep' images (an all-reference group with no
    non-reference copy present), the first 'keep' image in the group's stored
    order becomes the representative - the same convention
    group_keep_and_delete() uses.
    """
    for group in groups:
        for img in group.images:
            if selections.get(img.path) == 'keep':
                group.selected_keep = img.path
                break


def group_keep_and_delete(
    group: DuplicateGroup, selections: dict[str, str]
) -> tuple[Optional[ImageInfo], list[ImageInfo]]:
    """
    Given a group and a selections map (from resolve_group_selections), return
    a representative "keep" image to treat as canonical plus the list of
    images marked for deletion.

    When the group contains reference images, the representative is the first
    reference image (stable group.images order) - not necessarily the highest
    quality one, since the point of a reference folder is "this is canonical
    truth", not "this is the best quality copy". When there is no reference
    image, the representative is whichever single image the strategy marked
    'keep'.

    Args:
        group: The duplicate group
        selections: Dict mapping image path to 'keep'/'delete', as produced by
            resolve_group_selections()

    Returns:
        Tuple of (representative_keep_image, list_of_delete_images)
    """
    keeps = [img for img in group.images if selections.get(img.path) == 'keep']
    deletes = [img for img in group.images if selections.get(img.path) == 'delete']
    representative = keeps[0] if keeps else group.best_image
    return representative, deletes


__all__ = [
    'SelectionStrategy',
    'apply_selection_strategy',
    'resolve_group_selections',
    'stamp_group_selections',
    'group_keep_and_delete',
]
