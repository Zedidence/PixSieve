"""
Report formatting and display for the CLI interface.

Provides functions to format and print duplicate detection results in a
human-readable format.
"""

from __future__ import annotations

import logging

from ..models import DuplicateGroup, format_size
from ..utils.selection import resolve_group_selections


def _format_group_header(group_number: int, group: DuplicateGroup) -> str:
    """
    Format a group header line.

    Args:
        group_number: The group number (1-indexed)
        group: The duplicate group

    Returns:
        Formatted header string
    """
    return f"\nGroup {group_number} ({len(group.images)} files):"


def _print_image_in_group(img, is_keep: bool) -> None:
    """
    Print a single image entry in a duplicate group.

    Args:
        img: ImageInfo object
        is_keep: True if this image is marked to be kept
    """
    if is_keep and img.is_reference:
        marker = "  [REFERENCE]"
    elif is_keep:
        marker = "  [KEEP]"
    else:
        marker = "  [DUPE]"
    print(f"{marker} {img.path}")
    print(f"         {img.width}x{img.height} | {format_size(img.file_size)} | "
          f"Score: {img.quality_score:.1f}")


def _calculate_statistics(groups: list[DuplicateGroup], selections: dict[str, str]) -> dict[str, int]:
    """
    Calculate statistics for duplicate groups.

    Args:
        groups: List of duplicate groups
        selections: path -> 'keep'/'delete' map from resolve_group_selections()

    Returns:
        Dictionary with statistics:
        - total_duplicates: Number of files marked for deletion
        - total_groups: Number of groups
        - total_waste: Total file size of files marked for deletion (bytes)
    """
    total_duplicates = 0
    total_waste = 0
    for g in groups:
        for img in g.images:
            if selections.get(img.path) == 'delete':
                total_duplicates += 1
                total_waste += img.file_size

    return {
        'total_duplicates': total_duplicates,
        'total_groups': len(groups),
        'total_waste': total_waste,
    }


def _print_section_header(title: str) -> None:
    """Print a section header with divider lines."""
    print("\n" + "-" * 70)
    print(title)
    print("-" * 70)


def _print_duplicate_groups(
    groups: list[DuplicateGroup],
    section_title: str,
    selections: dict[str, str],
) -> None:
    """
    Print a section of duplicate groups.

    Args:
        groups: List of duplicate groups to print
        section_title: Title for the section (e.g., "EXACT DUPLICATES")
        selections: path -> 'keep'/'delete' map from resolve_group_selections()
    """
    if not groups:
        return

    _print_section_header(section_title)

    for i, group in enumerate(groups, 1):
        print(_format_group_header(i, group))

        # Sort by quality score (highest first)
        for img in sorted(group.images, key=lambda x: -x.quality_score):
            _print_image_in_group(img, selections.get(img.path) == 'keep')


def print_duplicate_report(
    exact_groups: list[DuplicateGroup],
    perceptual_groups: list[DuplicateGroup],
    logger: logging.Logger,
    strategy: str = 'quality',
) -> None:
    """
    Print a comprehensive report of found duplicates.

    Args:
        exact_groups: List of exact duplicate groups
        perceptual_groups: List of perceptual duplicate groups
        logger: Logger instance (currently unused, kept for compatibility)
        strategy: Selection strategy for groups with no reference image —
            matches whatever handle_duplicates() will actually use, so the
            report accurately reflects what an action would do.

    Notes:
        - Prints to stdout with formatted sections
        - Shows statistics summary at top
        - Groups are numbered starting from 1
        - Images sorted by quality score within each group
        - Kept image marked with [KEEP] ([REFERENCE] if in the reference
          folder), others with [DUPE] — mirrors resolve_group_selections()
    """
    # Header
    print("\n" + "=" * 70)
    print("DUPLICATE IMAGE REPORT")
    print("=" * 70)

    all_groups = exact_groups + perceptual_groups
    selections = resolve_group_selections(all_groups, strategy)

    # Statistics summary
    exact_stats = _calculate_statistics(exact_groups, selections)
    perceptual_stats = _calculate_statistics(perceptual_groups, selections)

    print(f"\nExact duplicates found: {exact_stats['total_duplicates']} files in "
          f"{exact_stats['total_groups']} groups")
    print(f"Perceptual duplicates found: {perceptual_stats['total_duplicates']} files in "
          f"{perceptual_stats['total_groups']} groups")

    # Exact duplicates section
    _print_duplicate_groups(exact_groups, "EXACT DUPLICATES (identical files)", selections)

    # Perceptual duplicates section
    _print_duplicate_groups(perceptual_groups, "PERCEPTUAL DUPLICATES (visually similar)", selections)

    # Footer with total space recoverable
    total_waste = exact_stats['total_waste'] + perceptual_stats['total_waste']
    print("\n" + "=" * 70)
    print(f"Total space recoverable: {format_size(total_waste)}")
    print("=" * 70)


__all__ = ['print_duplicate_report']
