"""
File sorting operations.

Provides sorting strategies:
- Alphabetical grouping (A-G, H-N, etc.)
- Color-based sorting using K-means clustering
- Resolution-based sorting with orientation sub-folders
"""

from __future__ import annotations

import os
import shutil
import logging
import warnings
from pathlib import Path
from typing import Callable

from PIL import Image
import numpy as np

from ..config import IMAGE_EXTENSIONS, ALPHA_SORT_GROUPS
from ..utils import get_unique_path, make_progress_bar
from ..database import get_cache

logger = logging.getLogger(__name__)

# Suppress sklearn warnings if present
warnings.filterwarnings('ignore', category=UserWarning)


# ---------------------------------------------------------------------------
# Alphabetical sort
# ---------------------------------------------------------------------------

def sort_alphabetical(
    directory: str | Path,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Sort files into subfolders based on first character.

    Creates folders: A-G, H-N, O-T, U-Z, 0-9

    Args:
        directory: Directory containing files to sort
        dry_run: If True, only report what would be sorted (default: False)

    Returns:
        Dictionary with statistics:
            - moved: Number of files moved (or would be moved)
            - skipped: Number of files skipped (no matching group)
            - errors: Number of errors encountered

    Examples:
        >>> stats = sort_alphabetical('/photos', dry_run=True)
        >>> print(f"Would move {stats['moved']} files")

    Notes:
        - Only sorts files in top-level directory (not recursive)
        - Groups based on first character of filename
        - Case-insensitive grouping
    """
    base_dir = Path(directory).resolve()
    stats = {'moved': 0, 'skipped': 0, 'errors': 0}

    def _get_group(char: str) -> str | None:
        """Get alphabetical group for a character."""
        ch = char.upper()
        for group, chars in ALPHA_SORT_GROUPS.items():
            if ch in chars:
                return group
        return None

    # Create group folders
    if not dry_run:
        for folder in ALPHA_SORT_GROUPS:
            (base_dir / folder).mkdir(exist_ok=True)

    for filename in os.listdir(base_dir):
        full_path = base_dir / filename
        if not full_path.is_file():
            continue

        group = _get_group(filename[0])
        if not group:
            logger.info(f"Skipped (no group): {filename}")
            stats['skipped'] += 1
            continue

        dest = base_dir / group / filename

        if dry_run:
            logger.info(f"[DRY RUN] {filename} -> {group}/")
            stats['moved'] += 1
            continue

        try:
            shutil.move(str(full_path), str(dest))
            logger.info(f"Moved: {filename} -> {group}/")
            stats['moved'] += 1
        except PermissionError:
            logger.error(f"Permission denied: {filename}")
            stats['errors'] += 1

    return stats


# ---------------------------------------------------------------------------
# Color sort
# ---------------------------------------------------------------------------

class ColorImageSorter:
    """
    Sort images by dominant color using K-means clustering.

    Provides multiple color-based sorting strategies:
    - Dominant color (single most prevalent color)
    - Color vs Black & White classification
    - Color palette signatures (multiple colors)
    """

    def __init__(self, source_dir: str | Path = ".", use_cache: bool = True):
        """
        Initialize sorter.

        Args:
            source_dir: Directory containing images to sort
            use_cache: G1 - if True, look up / store dominant colors in the
                image analysis cache, avoiding repeated K-means computation
                across sort runs.
        """
        self.source_dir = Path(source_dir)
        self.supported = {
            '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp',
        }
        # G1: lazy cache reference — avoids import-time side effects
        self._cache = get_cache() if use_cache else None

    def get_image_files(self) -> list[Path]:
        """
        Get list of supported image files in source directory.

        Returns:
            List of image file paths
        """
        return [
            p for p in self.source_dir.iterdir()
            if p.is_file() and p.suffix.lower() in self.supported
        ]

    def get_dominant_color(
        self,
        image_path: Path,
        n_colors: int = 1
    ) -> tuple[int, int, int] | list[tuple[int, int, int]] | None:
        """
        Extract dominant color(s) using K-means clustering.

        G1: For single-color lookups, checks the image analysis cache first.
        On cache miss, computes via K-means and stores the result back so
        subsequent sort runs avoid re-opening and re-analyzing every image.

        Args:
            image_path: Path to image file
            n_colors: Number of dominant colors to extract (default: 1)

        Returns:
            RGB tuple for single color, or list of RGB tuples for multiple colors
            None if processing fails

        Notes:
            - Resizes image to 150x150 for performance
            - Uses scikit-learn K-means clustering
        """
        # G1: single-color fast path through DB cache
        if n_colors == 1 and self._cache is not None:
            cached_info = self._cache.get(str(image_path))
            if cached_info is not None and cached_info.dominant_color:
                try:
                    parts = cached_info.dominant_color.split(',')
                    return (int(parts[0]), int(parts[1]), int(parts[2]))
                except Exception:
                    pass  # malformed value — fall through to recompute

        try:
            from sklearn.cluster import KMeans
        except ImportError as exc:
            logger.error(f"Missing dependency for color sort: {exc}")
            return None

        try:
            with Image.open(image_path) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                # 32×32 is statistically sufficient for dominant-color extraction
                # and is ~22× less data than 150×150 with identical K-means results.
                img.thumbnail((32, 32), Image.Resampling.LANCZOS)
                pixels = np.array(img).reshape(-1, 3)
                kmeans = KMeans(n_clusters=n_colors, random_state=42, n_init=3)
                kmeans.fit(pixels)
                colors = kmeans.cluster_centers_.astype(int)
                if n_colors == 1:
                    color = tuple(colors[0])
                    return color
                return [tuple(c) for c in colors]
        except Exception as exc:
            logger.error(f"Error processing {image_path}: {exc}")
            return None

    def is_grayscale(self, image_path: Path, threshold: int = 10) -> bool:
        """
        Detect if image is grayscale/black & white.

        Args:
            image_path: Path to image file
            threshold: Sensitivity threshold (lower = stricter, default: 10)

        Returns:
            True if image appears to be grayscale

        Notes:
            - Checks color channel variation
            - Resizes to 100x100 for performance
        """
        try:
            with Image.open(image_path) as img:
                if img.mode == 'L':
                    return True
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                # 32×32 matches get_dominant_color's thumbnail size and is
                # statistically equivalent for channel-difference std deviation.
                img.thumbnail((32, 32), Image.Resampling.LANCZOS)
                arr = np.array(img)
                r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
                avg_std = (np.std(r - g) + np.std(g - b) + np.std(r - b)) / 3
                return avg_std < threshold
        except Exception:
            return False

    @staticmethod
    def get_color_name(rgb: tuple[int, int, int]) -> str:
        """
        Map RGB values to color name.

        Args:
            rgb: RGB tuple (0-255 each)

        Returns:
            Color name string

        Supported colors:
            black, white, gray, red, orange, yellow, green,
            cyan, blue, purple, pink, brown
        """
        r, g, b = rgb
        brightness = (r + g + b) / 3
        max_val, min_val = max(r, g, b), min(r, g, b)
        saturation = 0 if max_val == 0 else (max_val - min_val) / max_val

        # Low saturation = grayscale
        if saturation < 0.15:
            if brightness < 50:
                return "black"
            elif brightness < 128:
                return "gray"
            return "white"

        # High saturation = color
        if r > g and r > b:
            if g > b * 1.5:
                return "orange" if r > g * 1.2 else "yellow"
            return "red" if r > b * 1.5 else "pink"
        elif g > r and g > b:
            if r > b * 1.5:
                return "yellow"
            elif b > r * 1.3:
                return "cyan"
            return "green"
        elif b > r and b > g:
            if r > g * 1.3:
                return "purple"
            elif g > r * 1.3:
                return "cyan"
            return "blue"
        return "brown"

    def _move_or_copy(self, src: Path, dest: Path, copy: bool) -> None:
        """Move or copy file."""
        if copy:
            shutil.copy2(src, dest)
        else:
            shutil.move(str(src), str(dest))

    def sort_by_color_bw(
        self,
        copy_files: bool = False,
        dry_run: bool = False
    ) -> dict[str, int]:
        """
        Sort images into 'color' vs 'black_and_white' folders.

        Args:
            copy_files: Copy instead of move (default: False)
            dry_run: Only report, don't actually sort (default: False)

        Returns:
            Dictionary with statistics:
                - color: Number of color images
                - bw: Number of black & white images
                - skipped: Number of failed classifications
        """
        image_files = self.get_image_files()
        stats = {'color': 0, 'bw': 0, 'skipped': 0}

        base = self.source_dir / "sorted_by_color_type"
        if not dry_run:
            base.mkdir(exist_ok=True)

        for fp in make_progress_bar(image_files, desc="Classifying"):
            try:
                is_bw = self.is_grayscale(fp)
            except Exception:
                stats['skipped'] += 1
                continue

            category = "black_and_white" if is_bw else "color"
            dest_folder = base / category
            if not dry_run:
                dest_folder.mkdir(exist_ok=True)

            dest = get_unique_path(dest_folder, fp.name)

            if dry_run:
                logger.info(f"[DRY RUN] {fp.name} -> {category}/")
            else:
                self._move_or_copy(fp, dest, copy_files)
                logger.info(f"{fp.name} -> {category}/")

            stats['color' if category == 'color' else 'bw'] += 1

        return stats

    def sort_by_dominant_color(
        self,
        copy_files: bool = False,
        dry_run: bool = False
    ) -> dict[str, int]:
        """
        Sort images by dominant color category.

        Args:
            copy_files: Copy instead of move (default: False)
            dry_run: Only report, don't actually sort (default: False)

        Returns:
            Dictionary with statistics:
                - processed: Number of images successfully processed
                - skipped: Number of images skipped (errors)
        """
        image_files = self.get_image_files()
        color_groups: dict[str, list[Path]] = {}
        stats = {'processed': 0, 'skipped': 0}
        # Collect (color_str, path) pairs to flush as a single batch write.
        cache_updates: list[tuple[str, str]] = []

        for fp in make_progress_bar(image_files, desc="Analyzing colors"):
            dominant = self.get_dominant_color(fp)
            if dominant:
                name = self.get_color_name(dominant)
                color_groups.setdefault(name, []).append(fp)
                stats['processed'] += 1
                # G1: accumulate for batch cache write
                if self._cache is not None:
                    cache_updates.append((f"{dominant[0]},{dominant[1]},{dominant[2]}", str(fp)))
            else:
                stats['skipped'] += 1

        # G1: flush all dominant-color updates in one executemany call
        if cache_updates and self._cache is not None:
            try:
                self._cache.set_dominant_color_batch(cache_updates)
            except Exception:
                pass  # cache write failure is non-fatal

        base = self.source_dir / "sorted_by_dominant_color"
        if not dry_run:
            base.mkdir(exist_ok=True)

        for color_name, paths in sorted(color_groups.items()):
            folder = base / color_name
            if not dry_run:
                folder.mkdir(exist_ok=True)
            logger.info(f"{color_name.capitalize()}: {len(paths)} files")

            for fp in paths:
                dest = get_unique_path(folder, fp.name)
                if dry_run:
                    logger.info(f"[DRY RUN] {fp.name} -> {color_name}/")
                else:
                    self._move_or_copy(fp, dest, copy_files)

        return stats

    def sort_by_palette(
        self,
        copy_files: bool = False,
        n_colors: int = 3,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """
        Sort images by multi-color palette signature.

        Args:
            copy_files: Copy instead of move (default: False)
            n_colors: Number of colors in palette (default: 3)
            dry_run: Only report, don't actually sort (default: False)

        Returns:
            Dictionary with statistics:
                - processed: Number of images successfully processed
                - skipped: Number of images skipped (errors)
        """
        image_files = self.get_image_files()
        palette_groups: dict[str, list[Path]] = {}
        stats = {'processed': 0, 'skipped': 0}

        for fp in make_progress_bar(image_files, desc="Extracting palettes"):
            colors = self.get_dominant_color(fp, n_colors=n_colors)
            if colors:
                names = [self.get_color_name(c) for c in colors]
                sig = "_".join(sorted(set(names)))
                palette_groups.setdefault(sig, []).append(fp)
                stats['processed'] += 1
            else:
                stats['skipped'] += 1

        base = self.source_dir / "sorted_by_color_palette"
        if not dry_run:
            base.mkdir(exist_ok=True)

        for sig, paths in sorted(palette_groups.items(), key=lambda x: len(x[1]), reverse=True):
            folder = base / sig
            if not dry_run:
                folder.mkdir(exist_ok=True)
            logger.info(f"{sig}: {len(paths)} files")

            for fp in paths:
                dest = get_unique_path(folder, fp.name)
                if dry_run:
                    logger.info(f"[DRY RUN] {fp.name} -> {sig}/")
                else:
                    self._move_or_copy(fp, dest, copy_files)

        return stats

    def analyze_colors(self) -> dict[str, int | dict[str, int]]:
        """
        Return color distribution stats without moving files.

        Returns:
            Dictionary with statistics:
                - total: Total images analyzed
                - color: Number of color images
                - bw: Number of black & white images
                - distribution: Dict mapping color names to counts
        """
        image_files = self.get_image_files()
        distribution: dict[str, int] = {}
        bw_count = color_count = 0

        for fp in make_progress_bar(image_files, desc="Analyzing"):
            if self.is_grayscale(fp):
                bw_count += 1
            else:
                color_count += 1

            dominant = self.get_dominant_color(fp)
            if dominant:
                name = self.get_color_name(dominant)
                distribution[name] = distribution.get(name, 0) + 1

        return {
            'total': len(image_files),
            'color': color_count,
            'bw': bw_count,
            'distribution': distribution,
        }


# ---------------------------------------------------------------------------
# Resolution sort
# ---------------------------------------------------------------------------

# Resolution categories: (name, min_px_inclusive, max_px_exclusive)
# Category is determined by the longer edge of the image.
_RESOLUTION_CATEGORIES = [
    ('8k_plus',   7680, float('inf')),
    ('4k',        3840, 7680),
    ('2k',        2560, 3840),
    ('hd',        1920, 2560),
    ('large',     1280, 1920),
    ('medium',     640, 1280),
    ('small',      300,  640),
    ('thumbnail',  100,  300),
    ('tiny',         0,  100),
]


def _get_resolution_category(width: int, height: int) -> str:
    """Return the resolution category name based on the longer edge."""
    max_dim = max(width, height)
    for name, lo, hi in _RESOLUTION_CATEGORIES:
        if lo <= max_dim < hi:
            return name
    return 'unknown'


def _get_orientation(width: int, height: int) -> str:
    """Return 'landscape', 'portrait', or 'square'."""
    if height == 0:
        return 'landscape'
    ratio = width / height
    if 0.95 <= ratio <= 1.05:
        return 'square'
    return 'landscape' if width > height else 'portrait'


def sort_by_resolution(
    directory: str | Path,
    copy_files: bool = False,
    dry_run: bool = False,
    on_progress: Callable[[int, str], None] | None = None,
) -> dict:
    """
    Sort images into subfolders by resolution category and orientation.

    Creates a ``sorted_by_resolution/`` folder inside *directory* with the
    structure: ``<category>/<orientation>/<filename>``.

    Resolution categories (based on the longer edge):
        tiny       : < 100 px
        thumbnail  : 100–299 px
        small      : 300–639 px
        medium     : 640–1279 px
        large      : 1280–1919 px
        hd         : 1920–2559 px  (1080p+)
        2k         : 2560–3839 px
        4k         : 3840–7679 px
        8k_plus    : 7680 px+

    Orientation sub-folders:
        landscape  : width > height  (ratio > 1.05)
        portrait   : height > width  (ratio < 0.95)
        square     : roughly 1:1     (0.95 ≤ ratio ≤ 1.05)

    Args:
        directory: Directory whose top-level images will be sorted.
        copy_files: Copy files instead of moving them (default: False).
        dry_run: Report what would happen without modifying files (default: False).
        on_progress: Optional callback ``(percent: int, message: str) -> None``
            called during processing to relay progress to callers.

    Returns:
        Dictionary with keys:
            - processed: files successfully moved/copied
            - skipped:   files that could not be read
            - errors:    files that failed during move/copy
            - by_category: dict mapping ``"<category>/<orientation>"`` to file count
    """
    source_dir = Path(directory).resolve()

    image_files = [
        p for p in source_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]

    total = len(image_files)
    stats: dict = {'processed': 0, 'skipped': 0, 'errors': 0}
    category_stats: dict[str, int] = {}

    base = source_dir / 'sorted_by_resolution'
    if not dry_run:
        base.mkdir(exist_ok=True)

    for i, fp in enumerate(make_progress_bar(image_files, desc="Sorting by resolution"), 1):
        if on_progress and total > 0:
            pct = int((i - 1) / total * 100)
            on_progress(pct, f"Processing {i}/{total}: {fp.name}")

        try:
            with Image.open(fp) as img:
                width, height = img.size
        except Exception as exc:
            logger.warning(f"Cannot read {fp.name}: {exc}")
            stats['skipped'] += 1
            continue

        category = _get_resolution_category(width, height)
        orientation = _get_orientation(width, height)

        dest_folder = base / category / orientation
        folder_key = f"{category}/{orientation}"

        if not dry_run:
            dest_folder.mkdir(parents=True, exist_ok=True)

        dest = get_unique_path(dest_folder, fp.name)

        if dry_run:
            logger.info(f"[DRY RUN] {fp.name} ({width}x{height}) -> {folder_key}/")
            category_stats[folder_key] = category_stats.get(folder_key, 0) + 1
            stats['processed'] += 1
            continue

        try:
            if copy_files:
                shutil.copy2(fp, dest)
            else:
                shutil.move(str(fp), str(dest))
            logger.info(f"{fp.name} -> {folder_key}/")
            category_stats[folder_key] = category_stats.get(folder_key, 0) + 1
            stats['processed'] += 1
        except Exception as exc:
            logger.error(f"Error moving {fp.name}: {exc}")
            stats['errors'] += 1

    if on_progress:
        on_progress(100, f"Complete — {stats['processed']} files processed")

    return {**stats, 'by_category': category_stats}


__all__ = ['sort_alphabetical', 'ColorImageSorter', 'sort_by_resolution']
