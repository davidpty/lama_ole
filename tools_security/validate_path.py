"""Shared path validation for lama_ole tools.

Every tool that receives a filesystem path from the LLM validates it before
touching the filesystem. Historically each tool module carried its own private
copy of this check, and the copies drifted apart. This module is the single
source of truth for that validation.

The check rejects two classes of unsafe paths:

1. Absolute paths (``/etc/passwd``, ``C:\\foo``) — tools operate relative to
   the current working directory only.  Absolute paths are allowed if they fall
   within a registered basepath (see :func:`register_basepath`).
2. Path traversal via ``..`` — any ``..`` path component (``../secret``,
   ``a/../b``) could escape the working directory.

Usage::

    from tools_security.validate_path import validate_path

    error = validate_path(path)
    if error:
        return {"status": "error", "message": [error]}

Basepath allowlist (for tests using ``tempfile.mkdtemp()``)::

    from tools_security.validate_path import register_basepath, validate_path

    register_basepath("/tmp")  # allow absolute paths under /tmp
    error = validate_path("/tmp/some_file")  # now allowed
"""

import os
from typing import List, Optional


# ---------------------------------------------------------------------------
# Basepath allowlist
# ---------------------------------------------------------------------------
# Tools that receive paths from the LLM normally reject all absolute paths.
# Tests (and only tests) may register basepaths so that any absolute path
# underneath one of those roots is accepted.  This list is intentionally
# mutable at module level so callers can call ``register_basepath()`` before
# running tests.
_registered_basepaths: List[str] = []


def register_basepath(basepath: str) -> None:
    """Register *basepath* as an allowed root for absolute paths.

    Any absolute path that starts with *basepath* (followed by ``/`` or
    exactly equal) will pass the absolute-path check in :func:`validate_path`.

    Args:
        basepath: An absolute directory path to allow.  Duplicates are ignored.
    """
    abspath = os.path.abspath(basepath)
    if abspath not in _registered_basepaths:
        _registered_basepaths.append(abspath)


def validate_path(path: str) -> Optional[str]:
    """Validate that *path* is a safe relative path (or an allowed absolute path).

    Returns ``None`` if the path is safe to use, otherwise a human-readable
    error message describing why it was rejected.

    Args:
        path: Filesystem path supplied by a tool caller.

    Returns:
        ``None`` on success, or an error message string on failure.
    """
    if os.path.isabs(path):
        # Check against registered basepaths
        abspath = os.path.abspath(path)
        allowed = False
        for bp in _registered_basepaths:
            if abspath == bp or abspath.startswith(bp + os.sep):
                allowed = True
                break
        if not allowed:
            return f"Blocked by safety check: only relative paths are allowed: {path}"

    parts = path.split(os.sep)
    if ".." in parts:
        return f"Blocked by safety check: path contains '..' traversal: {path}"

    return None


# Private alias kept for tool modules that historically defined a local
# ``_validate_path`` helper; tools import this one to keep the call sites
# unchanged.
_validate_path = validate_path
