# Testing Guidelines

## Scope

This document defines the rules every test in `lama_ole/tests/` must follow.
It is written primarily for LLM agents that generate or modify tests, so the
rules are stated as hard requirements with copy-paste boilerplate.

## Rule 1 — Imports must never be prefixed with `lama_ole`

Tests live *inside* the `lama_ole` package. A test must import its subjects
relative to the package root, **not** as `from lama_ole.security.entropychecker`
or `from lama_ole.tools.dev_tools import ...`.

Why: the `lama_ole` prefix only works when the *parent* of the package is on
`sys.path` (e.g. the repo is installed as a package or pytest runs from the
workspace root). Tests run from inside `lama_ole/`, so a `lama_ole.`-prefixed
import breaks as soon as the test directory or package layout is touched.

### Allowed import styles (relative to the package root)

```python
from security.entropychecker import EntropyChecker          # sub-package
from tools.edit import read_lines                            # sub-package
from tool_base import _entropy_check_tool_result            # top-level module
import chat
```

### Forbidden import styles

```python
from lama_ole.security.entropychecker import EntropyChecker  # NEVER
from lama_ole.tools.dev_tools import read_file               # NEVER
import lama_ole                                              # NEVER
```

## Rule 2 — Every test file must bootstrap `sys.path`

Because the imports above are relative to the package root, each test module
must add the package root to `sys.path` *before* importing anything from the
package. Use exactly this block, right after the stdlib imports and *before*
the package imports:

```python
import os
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)
```

The `if ... not in sys.path` guard makes the block idempotent and safe when
pytest adds the root directory itself (rootdir-based collection).

## Rule 3 — Test file layout and naming

- Tests live in `lama_ole/tests/`, named `test_<area>.py`.
- Use pytest-style plain functions or `Test*` classes; assert with plain
  `assert` statements.
- Keep one conceptual area per file:
  - `test_entropychecker.py` — pure helper functions
  - `test_entropychecker_class.py` — `EntropyChecker` state / `feed` /
    `reset` / `get_output`
  - `test_entropychecker_integration.py` — tool integrations
- Non-test helper modules are allowed (`entropy_test_data.py`), but they must
  not be collected by pytest (do not start with `test_`).

## Rule 4 — Deprecated tool modules

Modules that were moved out of `tools/` into `tools_insecure_outdated_deprecated/`
are imported from their new location:

```python
from tools_insecure_outdated_deprecated.dev_tools import read_file, grep
from tools_insecure_outdated_deprecated.dev_tools_safer import read_file as safer_read_file
```

Do not import from `tools.dev_tools` / `tools.dev_tools_safer` — those paths no
longer exist.

## Rule 5 — Keep default-threshold behavior stable

The entropy checker defaults are intentional and tested (`test_default_thresholds`
in `test_entropychecker_class.py`). In particular:

- `unique_byte_threshold` defaults to **150**, not 64. 64 caused false
  positives on legitimate Unicode text (CJK/emoji windows exceed 64 distinct
  byte values). Do not "fix" the test back to 64 and do not lower the default.
- The compression test skips inputs shorter than `_MIN_COMPRESSION_BYTES`
  (100 bytes): zlib's fixed header inflates tiny text (ratio > 1.0), which
  would be a false positive. Tests may rely on short strings never being
  flagged by the compression path.

## Rule 6 — Entropy checker behavioral guarantees under test

The following invariants are covered by tests and must not regress:

1. A multi-byte UTF-8 character split across `feed()` calls (or straddling the
   sliding-window boundary) must **not** be flagged as suspicious. The trailing
   incomplete sequence is treated as pending/safe.
2. Binary / random bytes are rejected by the pattern check (safe-ratio and/or
   unique-byte count) even far below the zip-size limit.
3. Random *printable-ASCII-only* text is **not** reliably flagged (only ~94
   unique bytes, compresses ~0.83) — if a test needs a suspicious *str*, inject
   control bytes (e.g. `\x00`) to drop the safe-byte ratio below 0.85.

## Running the tests

```bash
cd lama_ole
python3 -m pytest tests/ -q
```

Run a single area:

```bash
python3 -m pytest tests/test_entropychecker_class.py -q
```

## Checklist for new/modified tests

- [ ] No import contains the `lama_ole.` prefix.
- [ ] The `sys.path` bootstrap block (Rule 2) is present and before package imports.
- [ ] Deprecated tool imports point at `tools_insecure_outdated_deprecated/`.
- [ ] The test does not hard-code the old `unique_byte_threshold == 64` default.
- [ ] `python3 -m pytest tests/ -q` is green.
