"""
File sorting operations.

Provides sorting strategies:
- Alphabetical grouping (A-G, H-N, etc.)
- Color-based sorting using K-means clustering
"""

from __future__ import annotations

import os
import shutil
import logging
import warnings
from pathlib import Path

from PIL import Image
import numpy as np

from ..config import IMAGE_EXTENSIONS, ALPHA_SORT_GROUPS
from ..utils import get_unique_path, make_progress_bar

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

    def __init__(self, source_dir: str | Path = "."):
        """
        Initialize sorter.

        Args:
            source_dir: Directory containing images to sort
        """
        self.source_dir = Path(source_dir)
        self.supported = {
            '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp',
        }

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
        try:
            from sklearn.cluster import KMeans
        except ImportError as exc:
            logger.error(f"Missing dependency for color sort: {exc}")
            return None

        try:
            with Image.open(image_path) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img = img.resize((150, 150))
                pixels = np.array(img).reshape(-1, 3)
                kmeans = KMeans(n_clusters=n_colors, random_state=42, n_init=10)
                kmeans.fit(pixels)
                colors = kmeans.cluster_centers_.astype(int)
                if n_colors == 1:
                    return tuple(colors[0])
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
                img = img.resize((100, 100))
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

        for fp in make_progress_bar(image_files, desc="Analyzing colors"):
            dominant = self.get_dominant_color(fp)
            if dominant:
                name = self.get_color_name(dominant)
                color_groups.setdefault(name, []).append(fp)
                stats['processed'] += 1
            else:
                stats['skipped'] += 1

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


__all__ = ['sort_alphabetical', 'ColorImageSorter']
