"""
API package for the PixSieve.

Provides Flask routes and scan orchestration for the web interface,
plus operations routes for media file management.
"""

from __future__ import annotations

from .routes import api
from .operations_routes import operations_bp

__all__ = ['api', 'operations_bp']
