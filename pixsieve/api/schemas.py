"""
Pydantic request schemas for PixSieve API endpoints.

Use :func:`parse_request` to validate ``request.json`` against a schema and
return a typed model instance (or an HTTP 400 response on validation failure).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from flask import jsonify
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ..config import WINDOWS_RESERVED_NAMES, WINDOWS_INVALID_FILENAME_CHARS


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _format_validation_error(exc: ValidationError) -> str:
    """
    Render a pydantic ValidationError as one short, human-readable sentence.

    str(exc) dumps pydantic's full multi-line, implementation-leaking
    representation (error count header, a "For further information visit
    https://errors.pydantic.dev/..." URL per error) -- fine for a developer
    console, not for an API's `error` field, which every ad-hoc-validated
    route in this file already returns as a single clean sentence.
    """
    parts = []
    for err in exc.errors():
        loc = '.'.join(str(p) for p in err['loc'])
        parts.append(f"{loc}: {err['msg']}" if loc else err['msg'])
    return '; '.join(parts)


def parse_request(model_cls: type[BaseModel], data: dict | None) -> tuple[BaseModel | None, Any]:
    """Validate *data* against *model_cls*.

    Returns ``(instance, None)`` on success or ``(None, error_response)`` on
    failure, where *error_response* is a Flask ``(jsonify(...), 400)`` tuple
    ready to be returned directly from a route.
    """
    if data is None:
        return None, (jsonify({'error': 'Request body required'}), 400)
    try:
        return model_cls.model_validate(data), None
    except ValidationError as exc:
        return None, (jsonify({'error': _format_validation_error(exc)}), 400)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

class ScanDirectoryEntry(BaseModel):
    path: str = Field(..., min_length=1)
    isReference: bool = False


class ScanRequest(BaseModel):
    directories: list[ScanDirectoryEntry] = Field(..., min_length=1)
    threshold: int = Field(10, ge=0, le=64)
    exactOnly: bool = False
    perceptualOnly: bool = False
    recursive: bool = True
    useCache: bool = True
    useLsh: Optional[bool] = None
    # null = pick automatically from the drive type (utils/worker_policy.py)
    workers: Optional[int] = Field(None, ge=1, le=32)
    resolveSymlinks: bool = True
    autoSelectStrategy: str = 'quality'
    includeVideos: bool = False

    @field_validator('directories')
    @classmethod
    def validate_directories(cls, v: list[ScanDirectoryEntry]) -> list[ScanDirectoryEntry]:
        if sum(1 for d in v if d.isReference) > 1:
            raise ValueError('At most one directory may be marked as reference')

        stripped = [d.path.strip() for d in v]
        if any(not p for p in stripped):
            raise ValueError('Directory paths must not be empty')

        # Resolve to catch case-insensitive / trailing-slash / relative-vs-
        # absolute duplicates rather than silently deduping them.
        try:
            resolved = [str(Path(p).resolve()) for p in stripped]
        except OSError as exc:
            raise ValueError(f'Invalid directory path: {exc}') from exc
        if len(resolved) != len(set(resolved)):
            raise ValueError('The same directory was specified more than once')

        return v


# ---------------------------------------------------------------------------
# Selections / Strategy
# ---------------------------------------------------------------------------

class SelectionsRequest(BaseModel):
    # Constrained to the only two values any caller (group_keep_and_delete,
    # exporters, the frontend) ever checks for -- previously a plain
    # dict[str, str], so a bad value (typo, unexpected client) was silently
    # accepted and persisted as "neither keep nor delete" everywhere it's
    # read, instead of being rejected at the API boundary.
    selections: dict[str, Literal['keep', 'delete']] = Field(default_factory=dict)


class ApplyStrategyRequest(BaseModel):
    strategy: str = Field('quality', min_length=1)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class DeleteRequest(BaseModel):
    files: list[str] = Field(..., min_length=1)
    trashDir: str = Field(..., min_length=1)

    @field_validator('files')
    @classmethod
    def files_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError('files list must not be empty')
        return v


# ---------------------------------------------------------------------------
# Batch Operation
# ---------------------------------------------------------------------------

class SaveImageRequest(BaseModel):
    """Body for POST /api/image/save — an edited image to persist to disk."""
    path: str = Field(..., min_length=1)
    dataUrl: str = Field(..., min_length=1)


_RENAME_INVALID_CHARS = WINDOWS_INVALID_FILENAME_CHARS


class RenameImageRequest(BaseModel):
    """Body for POST /api/image/rename — rename one file within its own directory."""
    path: str = Field(..., min_length=1)
    newName: str = Field(..., min_length=1)

    @field_validator('newName')
    @classmethod
    def validate_new_name(cls, v: str) -> str:
        v = v.strip()
        # Windows silently strips trailing dots/spaces when creating a file,
        # so a name that keeps them here would end up on disk under a
        # different name than what's echoed back in the response (path/
        # filename are built from this string, not re-read from disk).
        # Stripping here keeps what we validate/rename to in sync with what
        # Windows actually creates.
        v = v.rstrip('. ')
        if not v:
            raise ValueError('newName must not be empty')
        if v in ('.', '..'):
            raise ValueError('newName is not a valid file name')
        if any(ch in _RENAME_INVALID_CHARS for ch in v):
            raise ValueError('newName contains characters not allowed in file names')
        # Windows reserves these device names for both the bare name and any
        # name with an extension (e.g. "CON" and "CON.jpg" both fail) —
        # rejecting them here avoids a raw OS-level rename failure surfacing
        # as an unhandled 500 later.
        stem = v.rsplit('.', 1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise ValueError(f'"{v}" is a reserved name on Windows and cannot be used')
        return v


class BatchOperationRequest(BaseModel):
    operation: str = Field(..., min_length=1)
    files: list[str] = Field(..., min_length=1)
    destination: Optional[str] = None
    quality: int = Field(90, ge=1, le=100)

    @model_validator(mode='after')
    def destination_required_for_move(self) -> 'BatchOperationRequest':
        if self.operation == 'move' and not self.destination:
            raise ValueError('destination is required for move operation')
        return self


# ---------------------------------------------------------------------------
# Operations routes
# ---------------------------------------------------------------------------

class DirectoryRequest(BaseModel):
    """Generic request with a single directory field."""
    directory: str = Field(..., min_length=1)
    dryRun: bool = True


class MediaDirectoryRequest(DirectoryRequest):
    """Directory + dryRun + includeVideos (images only unless includeVideos)."""
    includeVideos: bool = False


class MoveRequest(DirectoryRequest):
    destination: str = Field(..., min_length=1)
    overwrite: bool = False
    includeVideos: bool = False
    # null = pick automatically from the drive type (utils/worker_policy.py)
    workers: Optional[int] = Field(None, ge=1, le=32)


class MoveToParentRequest(DirectoryRequest):
    includeVideos: bool = False
    extensions: Optional[list[str]] = None
    # null = pick automatically from the drive type (utils/worker_policy.py)
    workers: Optional[int] = Field(None, ge=1, le=32)


class RenameRandomRequest(DirectoryRequest):
    nameLength: int = Field(12, ge=4, le=64)
    extensions: Optional[list[str]] = None
    recursive: bool = True
    # null = pick automatically from the drive type (utils/worker_policy.py)
    workers: Optional[int] = Field(None, ge=1, le=16)
    includeVideos: bool = False


class ConvertRequest(DirectoryRequest):
    quality: int = Field(95, ge=1, le=100)
    deleteOriginals: bool = False
    recursive: bool = True
    includeVideos: bool = False


class DateRangeRequest(DirectoryRequest):
    startDate: str = Field(..., min_length=10, max_length=10)
    endDate: str = Field(..., min_length=10, max_length=10)
    recursive: bool = True
    # Defaults to False: this route used to be filesystem-timestamp-only
    # under its old name (randomize_file_dates) -- see operations_routes.py.
    syncExif: bool = False
    includeVideos: bool = False
    # null = pick automatically from the drive type (utils/worker_policy.py)
    workers: Optional[int] = Field(None, ge=1, le=32)

    @model_validator(mode='after')
    def start_before_end(self) -> 'DateRangeRequest':
        if self.startDate >= self.endDate:
            raise ValueError('startDate must be before endDate')
        return self


class FolderDateRange(BaseModel):
    folder: str = Field(..., min_length=1)
    startDate: str = Field(..., min_length=10, max_length=10)
    endDate: str = Field(..., min_length=10, max_length=10)
    name: Optional[str] = None


class RandomizeDatesPerFolderRequest(BaseModel):
    folderRanges: list[FolderDateRange] = Field(..., min_length=1)
    dryRun: bool = True
    syncExif: bool = False
    includeVideos: bool = False
    # null = pick automatically from the drive type (utils/worker_policy.py)
    workers: Optional[int] = Field(None, ge=1, le=32)


class RecursiveVideoRequest(DirectoryRequest):
    """Shared shape for fix-extensions and strip-ratings: directory + dryRun
    + recursive + includeVideos, nothing else."""
    recursive: bool = True
    includeVideos: bool = False


class SortColorRequest(DirectoryRequest):
    includeVideos: bool = False
    method: Literal['dominant', 'bw', 'palette', 'analyze'] = 'dominant'
    copyFiles: bool = False
    nColors: int = Field(3, ge=2, le=16)


class SortResolutionRequest(DirectoryRequest):
    includeVideos: bool = False
    copyFiles: bool = False


class PipelineRequest(DirectoryRequest):
    steps: list[str] = Field(..., min_length=1)
    startDate: Optional[str] = Field(None, min_length=10, max_length=10)
    endDate: Optional[str] = Field(None, min_length=10, max_length=10)
    trashDir: Optional[str] = None
    nameLength: int = Field(12, ge=4, le=64)
    jpgQuality: int = Field(95, ge=1, le=100)
    deleteOriginals: bool = False
    recursive: bool = True
    includeVideos: bool = False
    # null = pick automatically from the drive type (utils/worker_policy.py)
    workers: Optional[int] = Field(None, ge=1, le=16)


class RepairRequest(BaseModel):
    directory: str = Field(..., min_length=1)
    trashFolder: str = Field(..., min_length=1)
    dryRun: bool = True
    attemptRepair: bool = True
    quarantineUnfixable: bool = True
    # null = pick automatically from the drive type (utils/worker_policy.py)
    workers: Optional[int] = Field(None, ge=1, le=16)
    includeVideos: bool = False
