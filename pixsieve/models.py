"""
Data models for PixSieve.

Contains dataclasses for representing image metadata and duplicate groups.
"""

from dataclasses import dataclass, field
from typing import Optional
import os


def format_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


@dataclass
class ImageInfo:
    """
    Stores metadata about an image file.
    
    Attributes:
        path: Full path to the image file
        file_size: Size in bytes
        width: Image width in pixels
        height: Image height in pixels
        pixel_count: Total pixels (width * height)
        bit_depth: Color bit depth
        format: Image format (PNG, JPEG, etc.)
        file_hash: SHA256 hash of file contents
        perceptual_hash: Perceptual hash for similarity matching. For videos,
            a "|"-joined sequence of per-sampled-frame pHash hex strings
            rather than a single hash - see scanner/video_analysis.py.
        quality_score: Computed quality score for ranking
        dominant_color: Cached dominant RGB color as "R,G,B" string (G1 optimization)
        error: Error message if analysis failed
        media_type: "image" or "video"
        duration: Video duration in seconds (0.0 for images)
        sharpness_score: Cheap edge-variance sharpness proxy (0.0 if not
            computed, e.g. calculate_phash=False or non-image media). A weak
            tiebreaker only - see hashing.py::calculate_quality_score().
        capture_date: EXIF DateTimeOriginal as a Unix timestamp, or None if
            absent/unparseable/not an image. Used by utils/selection.py's
            NEWEST/OLDEST strategies in preference to filesystem mtime.
    """
    path: str
    file_size: int = 0
    width: int = 0
    height: int = 0
    pixel_count: int = 0
    bit_depth: int = 0
    format: str = ""
    file_hash: str = ""
    perceptual_hash: str = ""
    quality_score: float = 0.0
    dominant_color: Optional[str] = None  # "R,G,B" or None
    error: Optional[str] = None
    media_type: str = "image"  # "image" or "video"
    duration: float = 0.0  # seconds; 0.0 for images
    sharpness_score: float = 0.0
    capture_date: Optional[float] = None  # Unix timestamp, or None
    # Runtime-only: which scan root this image was discovered under was the
    # reference folder. Never persisted to the analysis cache (cache rows are
    # reused across unrelated scans, so this must be re-stamped per scan).
    is_reference: bool = False

    def __hash__(self):
        return hash(str(self.path))
    
    def __eq__(self, other):
        if not isinstance(other, ImageInfo):
            return False
        return str(self.path) == str(other.path)
    
    @property
    def filename(self) -> str:
        """Return just the filename portion of the path."""
        return os.path.basename(self.path)
    
    @property
    def directory(self) -> str:
        """Return the directory containing this image."""
        return os.path.dirname(self.path)
    
    @property
    def resolution(self) -> str:
        """Return resolution as 'WxH' string."""
        return f"{self.width}x{self.height}"
    
    @property
    def megapixels(self) -> float:
        """Return megapixel count."""
        return round(self.pixel_count / 1_000_000, 2)
    
    @property
    def file_size_formatted(self) -> str:
        """Return human-readable file size."""
        return format_size(self.file_size)

    @property
    def duration_formatted(self) -> str:
        """Return video duration as 'M:SS' (empty string for non-video/zero duration)."""
        if self.duration <= 0:
            return ""
        total_seconds = int(round(self.duration))
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'path': self.path,
            'filename': self.filename,
            'directory': self.directory,
            'file_size': self.file_size,
            'file_size_formatted': self.file_size_formatted,
            'width': self.width,
            'height': self.height,
            'resolution': self.resolution,
            'pixel_count': self.pixel_count,
            'megapixels': self.megapixels,
            'format': self.format,
            'quality_score': round(self.quality_score, 1),
            'error': self.error,
            'is_reference': self.is_reference,
            'media_type': self.media_type,
            'duration': self.duration,
            'duration_formatted': self.duration_formatted,
            'sharpness_score': round(self.sharpness_score, 1),
            'capture_date': self.capture_date,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ImageInfo':
        """Create ImageInfo from dictionary."""
        return cls(
            path=data['path'],
            file_size=data.get('file_size', 0),
            width=data.get('width', 0),
            height=data.get('height', 0),
            pixel_count=data.get('pixel_count', data.get('width', 0) * data.get('height', 0)),
            bit_depth=data.get('bit_depth', 0),
            format=data.get('format', ''),
            file_hash=data.get('file_hash', ''),
            perceptual_hash=data.get('perceptual_hash', ''),
            quality_score=data.get('quality_score', 0.0),
            error=data.get('error'),
            is_reference=data.get('is_reference', False),
            media_type=data.get('media_type', 'image'),
            duration=data.get('duration', 0.0),
            sharpness_score=data.get('sharpness_score', 0.0),
            capture_date=data.get('capture_date'),
        )


@dataclass
class DuplicateGroup:
    """
    A group of duplicate images.
    
    Attributes:
        id: Unique identifier for this group
        images: List of ImageInfo objects in this group
        match_type: How duplicates were detected ('exact', 'perceptual', or
            'video-perceptual')
        selected_keep: Path of image selected to keep (if user has chosen)
    """
    id: int
    images: list = field(default_factory=list)
    match_type: str = "unknown"  # "exact", "perceptual", or "video-perceptual"
    selected_keep: Optional[str] = None
    
    @property
    def best_image(self) -> Optional[ImageInfo]:
        """Returns the highest quality image in the group."""
        if not self.images:
            return None
        return max(self.images, key=lambda x: x.quality_score)
    
    @property
    def duplicates(self) -> list:
        """Returns all images except the best one."""
        best = self.best_image
        return [img for img in self.images if img != best]
    
    @property
    def image_count(self) -> int:
        """Number of images in this group."""
        return len(self.images)
    
    @property
    def potential_savings(self) -> int:
        """Bytes that could be saved by removing duplicates."""
        sorted_images = sorted(self.images, key=lambda x: -x.quality_score)
        if len(sorted_images) > 1:
            return sum(img.file_size for img in sorted_images[1:])
        return 0
    
    @property
    def potential_savings_formatted(self) -> str:
        """Human-readable potential savings."""
        return format_size(self.potential_savings)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        images_sorted = sorted(self.images, key=lambda x: -x.quality_score)
        best = images_sorted[0] if images_sorted else None
        return {
            'id': self.id,
            'match_type': self.match_type,
            'image_count': self.image_count,
            'images': [img.to_dict() for img in images_sorted],
            'best_path': best.path if best else None,
            'selected_keep': self.selected_keep or (best.path if best else None),
            'potential_savings': self.potential_savings,
            'potential_savings_formatted': self.potential_savings_formatted,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'DuplicateGroup':
        """Create DuplicateGroup from dictionary."""
        images = [ImageInfo.from_dict(img_data) for img_data in data.get('images', [])]
        return cls(
            id=data['id'],
            images=images,
            match_type=data.get('match_type', 'unknown'),
            selected_keep=data.get('selected_keep'),
        )
