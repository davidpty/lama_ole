# Plan: Range-Based Editing Tool — `edit_range_based`

## Objective

Add a new tool `edit_range_based` to `lama_ole/tools/edit.py` that replaces a
**contiguous range** of text in a file with a single replacement string.

The range is delimited by two anchor strings:

- `search_from` — the start of the range (inclusive).
- `search_to` — the end of the range (inclusive).

Everything from `search_from` up to **and including** `search_to` is replaced by
`replace`. It is the range-based companion of the existing single-anchor
`edit` tool:

```python
@tool(description="...")
def edit_range_based( path: str, search_from: str, search_to: str, replace: str) -> str:
    ...
```

## Behavior

### Preconditions (must all hold, otherwise return an error)

1. **Unambiguous `search_from`** — the string occurs exactly **once** in the
   file (mirrors the `edit` tool's single-match rule, avoiding destructive
   ambiguity).
2. **Unambiguous `search_to`** — the string occurs exactly **once** in the file.
3. **No overlap** — the matched range of `search_from` and the matched range of
   `search_to` must not intersect.
4. **Ordering** — `search_from` must come **before** `search_to` in the file.

> Note: conditions 3 and 4 are checked together by requiring the end of the
> `search_from` match to be `<=` the start of the `search_to` match
> (`from_end <= to_start`). This simultaneously rules out overlap, the two
> anchors being the same string, one anchor being nested inside the other, and
> `search_from` appearing after `search_to`.

### On success

- The span `[from_start, to_end)` (i.e. `search_from .. search_to`, both
  inclusive) is replaced by `replace`.
- The result is written back to the file.
- The return dict follows the mandatory tool format with a compact unified diff
  (reusing `_unified_diff`), exactly like `edit`.

### Return format (per AGENTS.md Tool Implementation Standards)

- **Success:** `{"status": "success", "data": "Successfully applied patch to <path>.", "file": <path>, "diff": <diff>}`
- **Error:** `{"status": "error", "message": [<reason>]}` with a descriptive reason, e.g.:

| Failure | `message` |
|---|---|
| File missing | `["File", <path>, "does not exist."]` |
| Safety check failed | `[<safety_error>]` |
| `search_from` ambiguous | `["Error: search_from string matches not exactly 1 time :", <count>]` |
| `search_to` ambiguous | `["Error: search_to string matches not exactly 1 time :", <count>]` |
| Overlap / wrong order | `["Error: search_from and search_to overlap, or search_from does not come before search_to."]` |
| Write failure | `["Error applying patch:", <str(e)>]` |

## Safety

- Path is validated via `_validate_path` (the single source of truth for path
  validation, per the `tools_security` package) **before** any read/write.
- The file must already exist (no implicit creation).
- All validation happens before any mutation, so a failed edit never touches the
  file.

## Implementation Steps

1. **Add the tool** to `lama_ole/tools/edit.py`, placed directly after `edit`:
   - File-exists + safety checks (mirror `edit`).
   - Read `original_text`.
   - `from_count = original_text.count(search_from)`; error unless `== 1`.
   - `to_count = original_text.count(search_to)`; error unless `== 1`.
   - Compute `from_start/from_end` and `to_start/to_end` via `str.index`.
   - Reject when `from_end > to_start` (overlap / ordering).
   - Build `edited_text = original_text[:from_start] + replace + original_text[to_end:]`.
   - Write, return success dict with `_unified_diff(original_text, edited_text, path)`.
   - Wrap the write in try/except like `edit`.
   - No new imports are needed (reuses `os`, `_validate_path`, `_unified_diff`).
2. **Add tests** to `lama_ole/tests/test_edit_tools.py` (unittest-style, same
   fixture file as `edit`):
   - Success: replace a whole middle range, verify file content + diff.
   - `search_from` matches multiple times → error, file unchanged.
   - `search_to` matches multiple times → error, file unchanged.
   - Overlap (`search_to` nested in `search_from` and vice versa) → error.
   - Same anchor string for both → error.
   - `search_from` after `search_to` (wrong order) → error.
3. **Run the suite:** `python3 tests/run_all_tests.py`.

## Files Changed

| File | Change |
|---|---|
| `lama_ole/tools/edit.py` | Add `edit_range_based` tool. |
| `lama_ole/tests/test_edit_tools.py` | Add `edit_range_based` tests. |
| `lama_ole/llm_blueprint/tools/editing/range_based_editing.md` | This plan. |

## Example

Input file content:

```
Hello world!
This is a test.
Goodbye world!
```

Call: `edit_range_based(path, "Hello world!", "Goodbye world!", "Replaced!")`

- `from_start = 0`, `from_end = 12`, `to_start = 29`, `to_end = 43`
- `from_end (12) <= to_start (29)` → preconditions pass
- Result file content: `Replaced!`
