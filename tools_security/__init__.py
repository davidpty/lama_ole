"""Shared security helpers for lama_ole tools.

Houses the single implementation of path validation that tool modules import
instead of re-defining it locally (see ``validate_path.py``).
"""

from .validate_path import validate_path, _validate_path

__all__ = [
    "validate_path",
    "_validate_path",
]
