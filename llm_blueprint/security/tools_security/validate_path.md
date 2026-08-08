# validate_path

## Purpose

Shared path validation for `lama_ole` tools. Every tool that receives a filesystem path from the LLM validates it before touching the filesystem. Historically each tool module carried its own private copy of this check, and the copies drifted apart. This module is the single source of truth for that validation.

## What It Checks

The check rejects two classes of unsafe paths:

1. **Absolute paths** (`/etc/passwd`, `C:\foo`) — tools operate relative to the current working directory only. Absolute paths are allowed if they fall within a registered basepath (see *Basepath allowlist* below).
2. **Path traversal via `..`** — any `..` path component (`../secret`, `a/../b`) could escape the working directory.

## Usage

### Basic validation

```python
from tools_security.validate_path import validate_path

error = validate_path(path)
if error:
    return {"status": "error", "message": [error]}
```

### Basepath allowlist (for tests using `tempfile.mkdtemp()`)

Tests that create temporary files via `tempfile.mkdtemp()` produce absolute paths under `/tmp`. Register `/tmp` as an allowed basepath so these paths pass validation:

```python
from tools_security.validate_path import register_basepath, validate_path

register_basepath("/tmp")  # allow absolute paths under /tmp
error = validate_path("/tmp/some_file")  # now allowed
```

## API

### `validate_path(path: str) -> Optional[str]`

Validate that *path* is a safe relative path (or an allowed absolute path).

**Returns:** `None` if the path is safe to use, otherwise a human-readable error message describing why it was rejected.

**Args:**
- `path`: Filesystem path supplied by a tool caller.

### `register_basepath(basepath: str) -> None`

Register *basepath* as an allowed root for absolute paths. Any absolute path that starts with *basepath* (followed by `/` or exactly equal) will pass the absolute-path check in `validate_path`.

**Args:**
- `basepath`: An absolute directory path to allow. Duplicates are ignored.

## Migration

Tools that previously defined their own `_validate_path` helper should:

1. Remove the local `_validate_path` function definition.
2. Add `from tools_security.validate_path import validate_path as _validate_path`.
3. Keep all call sites unchanged — the imported function is aliased to `_validate_path` for compatibility.

## Files

- **Implementation:** `lama_ole/tools_security/validate_path.py`
- **Documentation:** `lama_ole/llm_blueprint/security/tools_security/validate_path.md`
