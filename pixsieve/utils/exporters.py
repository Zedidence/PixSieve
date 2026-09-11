"""
Export functionality for the PixSieve.

Provides functions to export duplicate detection results to various file formats
including TXT and CSV.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TextIO

from ..models import DuplicateGroup


def _export_txt(
    exact_groups: list[DuplicateGroup],
    perceptual_groups: list[DuplicateGroup],
    selections: dict[str, str],
    file_handle: TextIO
) -> None:
    """
    Export duplicate results to TXT format.

    Args:
        exact_groups: List of exact duplicate groups
        perceptual_groups: List of perceptual duplicate groups
        selections: Path -> 'keep'/'delete' map, as produced by
            utils.selection.resolve_group_selections() - this is what actually
            drives the delete action, so the report must reflect it rather
            than DuplicateGroup.best_image's separate, non-reference-aware
            quality-only ranking.
        file_handle: Open file handle to write to
    """
    file_handle.write("DUPLICATE IMAGE REPORT\n")
    file_handle.write("=" * 70 + "\n\n")

    # Exact duplicates section
    file_handle.write("EXACT DUPLICATES\n")
    file_handle.write("-" * 70 + "\n")
    for i, group in enumerate(exact_groups, 1):
        file_handle.write(f"\nGroup {i}:\n")
        for img in group.images:
            marker = "[KEEP]" if selections.get(img.path) == 'keep' else "[DUPE]"
            file_handle.write(f"  {marker} {img.path}\n")

    # Perceptual duplicates section
    file_handle.write("\n\nPERCEPTUAL DUPLICATES\n")
    file_handle.write("-" * 70 + "\n")
    for i, group in enumerate(perceptual_groups, 1):
        file_handle.write(f"\nGroup {i}:\n")
        for img in group.images:
            marker = "[KEEP]" if selections.get(img.path) == 'keep' else "[DUPE]"
            file_handle.write(f"  {marker} {img.path}\n")


def _export_csv(
    exact_groups: list[DuplicateGroup],
    perceptual_groups: list[DuplicateGroup],
    selections: dict[str, str],
    file_handle: TextIO
) -> None:
    """
    Export duplicate results to CSV format.

    Args:
        exact_groups: List of exact duplicate groups
        perceptual_groups: List of perceptual duplicate groups
        selections: Path -> 'keep'/'delete' map, as produced by
            utils.selection.resolve_group_selections() - see _export_txt for why.
        file_handle: Open file handle to write to

    Notes:
        CSV includes: group_id, match_type, status, path, width, height,
                     file_size, quality_score
    """
    # Hand-built rows previously used f-string interpolation with a literal
    # '"..."' quote around path -- a path containing a literal comma or
    # quote (both legal in real filenames) produced a malformed row that an
    # importer like Excel would misparse. csv.writer handles quoting/escaping
    # correctly for any field value.
    writer = csv.writer(file_handle, lineterminator='\n')
    writer.writerow(
        ['group_id', 'match_type', 'status', 'path', 'width', 'height', 'file_size', 'quality_score']
    )

    # Exact duplicates
    for i, group in enumerate(exact_groups, 1):
        for img in group.images:
            status = "keep" if selections.get(img.path) == 'keep' else "duplicate"
            writer.writerow(
                [i, 'exact', status, img.path, img.width, img.height,
                 img.file_size, f'{img.quality_score:.1f}']
            )

    # Perceptual duplicates (continue numbering from exact groups)
    for i, group in enumerate(perceptual_groups, len(exact_groups) + 1):
        for img in group.images:
            status = "keep" if selections.get(img.path) == 'keep' else "duplicate"
            writer.writerow(
                [i, 'perceptual', status, img.path, img.width, img.height,
                 img.file_size, f'{img.quality_score:.1f}']
            )


def export_results(
    exact_groups: list[DuplicateGroup],
    perceptual_groups: list[DuplicateGroup],
    output_path: Path,
    export_format: str = 'txt',
    selections: dict[str, str] | None = None,
) -> None:
    """
    Export duplicate detection results to a file.

    Args:
        exact_groups: List of exact duplicate groups
        perceptual_groups: List of perceptual duplicate groups
        output_path: Path to output file
        export_format: Export format ('txt' or 'csv'). Default: 'txt'
        selections: Path -> 'keep'/'delete' map, as produced by
            utils.selection.resolve_group_selections(). This must be computed
            with the same strategy/reference-directory context as the run
            being reported on, so the exported [KEEP]/[DUPE] markers agree
            with what any subsequent delete action actually does. Defaults to
            an empty dict (every image reported as a duplicate) only for
            backward compatibility - callers should always pass real
            selections.

    Raises:
        ValueError: If export_format is not 'txt' or 'csv'
        IOError: If file cannot be written

    Examples:
        >>> from ..utils.selection import resolve_group_selections
        >>> exact_groups = [group1, group2]
        >>> perceptual_groups = [group3, group4]
        >>> selections = resolve_group_selections(exact_groups + perceptual_groups, 'quality')
        >>> export_results(exact_groups, perceptual_groups, Path('results.txt'), 'txt', selections)
    """
    if export_format not in ('txt', 'csv'):
        raise ValueError(f"Unsupported export format: {export_format}. Use 'txt' or 'csv'.")

    if selections is None:
        selections = {}

    with open(output_path, 'w', encoding='utf-8') as f:
        if export_format == 'txt':
            _export_txt(exact_groups, perceptual_groups, selections, f)
        elif export_format == 'csv':
            _export_csv(exact_groups, perceptual_groups, selections, f)


__all__ = ['export_results']
