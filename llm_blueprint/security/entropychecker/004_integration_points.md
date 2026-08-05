# Entropy Checker — Integration Points

## Overview

This document specifies exactly where and how to integrate the entropy checker into existing lama_ole code. Each integration point includes:
- **Location**: File and function/method
- **Trigger**: When the check should run
- **Implementation**: Code snippet showing integration
- **Error Handling**: What to do when entropy check fails

## Integration Points Summary

| # | Location | Trigger | Priority |
|---|----------|---------|----------|
| 1 | `dev_tools.py::read_file()` | After file read, before return | High |
| 2 | `dev_tools.py::grep()` | For each file match | High |
| 3 | `dev_tools.py::grepF()` | For each file match | High |
| 4 | `chat.py::ChatState._handle_feed()` | After stdin/file input | Medium |
| 5 | `tool_base.py::run_with_tools()` | Before adding tool result to messages | Low (defensive) |

---

## Integration Point 1: `dev_tools.py::read_file()`

### Why Here?
This is the most critical integration point. The `read_file` tool is the primary way files enter the LLM context. If a user accidentally reads a binary file (image, archive, compiled object), it would be fed directly to the model.

### When to Check
After reading the file content, before returning it.

### Implementation

```python
@tool(description="Read the contents of a file")
def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Entropy check integration
    from security.entropychecker import EntropyChecker
    
    checker = EntropyChecker()
    result = checker.feed(content.encode('utf-8'))
    
    if result.is_suspicious:
        return {
            "status": "error", 
            "message": [f"File rejected by entropy check: {result.reason}"]
        }
    
    return {"status": "success", "data": content}
```

### Error Handling
- Return error dictionary (consistent with other tool errors)
- Log warning to stderr for debugging
- Do NOT expose internal entropy details to user (security through obscurity)

---

## Integration Point 2: `dev_tools.py::grep()` and `grepF()`

### Why Here?
Grep operations can match binary files. Each match line could contain garbage bytes that would be fed to the LLM.

### When to Check
For each file being searched, after reading it but before processing matches.

### Implementation

```python
@tool(description="Search for a regex pattern in files under a path")
def grep(pattern: str, path: str = ".", include: str = "*") -> str:
    from security.entropychecker import EntropyChecker
    
    checker = EntropyChecker()
    matches = []
    
    if os.path.isfile(path):
        files_to_search = [path]
    elif os.path.isdir(path):
        files_to_search = []
        for root, _, files in os.walk(path):
            for fname in sorted(files):  # Sort for deterministic order
                if glob_mod.fnmatch.fnmatch(fname, include):
                    files_to_search.append(os.path.join(root, fname))
    else:
        return "(no matches or path not found)"

    skipped_files = []
    
    for fpath in files_to_search:
        try:
            with open(fpath, "rb") as f:  # Read as bytes first
                raw_content = f.read()
            
            # Entropy check on raw bytes
            result = checker.feed(raw_content)
            
            if result.is_suspicious:
                skipped_files.append(f"{fpath} (entropy check failed)")
                continue
            
            # Decode and process normally
            content = raw_content.decode('utf-8', errors='replace')
            for i, line in enumerate(content.splitlines(), 1):
                if re.search(pattern, line):
                    matches.append(f"{fpath}:{i}: {line.rstrip()}")
        
        except Exception:
            pass
    
    # Report skipped files
    if skipped_files:
        warning = f"\n[Skipped {len(skipped_files)} file(s) due to entropy check]:\n"
        for sf in skipped_files:
            warning += f"  - {sf}\n"
        return "\n".join(matches) + warning if matches else warning
    
    return "\n".join(matches) if matches else "(no matches)"
```

### Notes
- Read file as bytes (`"rb"` mode) for entropy check
- Decode with `errors='replace'` only after passing entropy check
- Report skipped files in output (transparency without exposing internals)

---

## Integration Point 3: `chat.py::ChatState._handle_feed()` or `/feed` Command

### Why Here?
The `/feed` command allows users to inject arbitrary text into the conversation. While this is user-initiated, it could still accidentally include binary data (e.g., pasting from a binary file).

### When to Check
After receiving input but before adding to message history.

### Implementation

```python
def _handle_feed(self, args: str) -> str:
    """Handle /feed command - feed text into conversation."""
    if not args.strip():
        return "Error: /feed requires text argument"
    
    from security.entropychecker import EntropyChecker
    
    checker = EntropyChecker()
    result = checker.feed(args.encode('utf-8'))
    
    if result.is_suspicious:
        return f"Input rejected by entropy check: {result.reason}"
    
    # Add to message history as before
    self.messages.append({
        "role": "user", 
        "content": args,
        "_source": "feed"  # Mark source for debugging
    })
    
    return f"Feeding {len(args)} characters into conversation."
```

### Notes
- This is a safety net; user-initiated `/feed` is generally trusted
- The entropy check prevents accidental binary injection
- Mark message source for traceability

---

## Integration Point 4: `tool_base.py::run_with_tools()` (Defensive)

### Why Here?
As a final defensive layer, we can check all tool results before they enter the conversation. This catches any tool that might bypass the entropy checker (future tools added without integration).

### When to Check
After getting tool result, before adding to messages.

### Implementation

```python
# In run_with_tools(), after computing 'result' dict:
if isinstance(result, dict) and result.get("status") == "success":
    content_str = result.get("data", "")
    
    # Defensive entropy check (only for large results or if explicitly enabled)
    if verbose >= 2 or os.environ.get("LAMA_OLE_ENTROPY_CHECK"):
        from security.entropychecker import EntropyChecker
        
        checker = EntropyChecker()
        check_result = checker.feed(content_str.encode('utf-8'))
        
        if check_result.is_suspicious:
            print(
                f"[WARNING] Tool '{tool_name}' result failed entropy check: "
                f"{check_result.reason}",
                file=sys.stderr,
            )
            # Option 1: Truncate and warn
            content_str = content_str[:1000] + "... [TRUNCATED BY ENTROPY CHECK]"
            # Option 2: Skip entirely (more conservative)
            # continue  # Skip this tool result
```

### Notes
- This is optional/defensive; primary integration should be in individual tools
- Use verbose logging or environment variable to enable (don't slow down normal operation)
- Provides defense-in-depth for future tool development

---

## Configuration & Tuning

### Making Thresholds Configurable

For production use, consider making thresholds configurable via CLI args or config file:

```python
# In lama_ole.py argparse setup:
parser.add_argument(
    "--entropy-window-size", 
    type=int, 
    default=1024,
    help="Sliding window size for entropy checker"
)
parser.add_argument(
    "--entropy-safe-ratio", 
    type=float, 
    default=0.85,
    help="Minimum safe byte ratio (0.0-1.0)"
)

# Pass to tools via environment or global config
import os
os.environ["ENTROPY_WINDOW_SIZE"] = str(args.entropy_window_size)
```

### Logging Configuration

Add entropy check logging for debugging:

```python
import logging

logger = logging.getLogger("lama_ole.security")

# In integration points:
if result.is_suspicious:
    logger.warning(
        "Entropy check failed in %s: %s", 
        tool_name, 
        result.reason
    )
```

---

## Testing Integration Points

### Unit Tests for Each Integration

Each integration point should have a corresponding test:

1. **test_read_file_entropy.py**: Test with binary files (PNG, ZIP, etc.)
2. **test_grep_entropy.py**: Test grep on directories containing mixed files
3. **test_feed_command.py**: Test /feed with various inputs
4. **test_defensive_check.py**: Test the defensive layer in run_with_tools

### Integration Test Scenarios

```python
# Scenario 1: Read a Python file (should pass)
result = read_file("lama_ole/tool_base.py")
assert result["status"] == "success"

# Scenario 2: Read a binary file (should fail)
import tempfile, os
with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
    f.write(os.urandom(1000))  # Random bytes
    temp_path = f.name

result = read_file(temp_path)
assert result["status"] == "error"
os.unlink(temp_path)

# Scenario 3: Grep on directory with binary files
import tempfile, shutil
temp_dir = tempfile.mkdtemp()
with open(os.path.join(temp_dir, "test.py"), "w") as f:
    f.write("print('hello')\n")
with open(os.path.join(temp_dir, "binary.bin"), "wb") as f:
    f.write(b"\x00\x01\x02\x03" * 100)

result = grep("print", temp_dir)
assert "test.py" in result
# Should mention skipped binary file or not include it
shutil.rmtree(temp_dir)
```

---

## Migration Strategy

### Phase 1: Core Integration (High Priority)
- [ ] Integrate into `read_file()` 
- [ ] Integrate into `grep()` and `grepF()`
- [ ] Add basic tests

### Phase 2: User Input Protection (Medium Priority)
- [ ] Integrate into `/feed` command
- [ ] Test with various user inputs

### Phase 3: Defensive Layer (Low Priority, Optional)
- [ ] Add defensive check in `run_with_tools()`
- [ ] Make it opt-in via CLI flag or env var
- [ ] Performance test to ensure no slowdown

---

## Security Considerations

### What the Entropy Checker Does NOT Protect Against

1. **Encoded binary data**: Base64-encoded binaries will pass entropy check (they're valid text)
2. **Prompt injection in text**: The checker only validates format, not content semantics
3. **Compressed payloads**: zlib/gzip compressed data might pass if ratio is low enough

### Mitigations

- The safety system prompt already warns about untrusted tool results
- The nonce wrapping provides structural separation
- Future enhancement: Add Base64 detection as additional check

---

## Performance Impact

### Expected Overhead

| Operation | Estimated Overhead | Notes |
|-----------|-------------------|-------|
| `read_file()` (< 1 KB) | < 1 ms | Minimal for small files |
| `read_file()` (100 KB) | ~5-10 ms | Sliding window analysis only |
| `read_file()` (> 64 KB) | +20-50 ms | Compression test adds overhead |
| `grep()` per file | Similar to read_file | Per-file cost |

### Optimization Opportunities

1. **Skip check for known-safe paths**: Configurable whitelist (e.g., `/usr/include/`)
2. **Early exit on small files**: Files < 100 bytes might not need full analysis
3. **Async compression test**: Run zip test in background thread for large files

### Profiling Strategy

```python
import time
from contextlib import contextmanager

@contextmanager
def profile_entropy_check(operation_name: str):
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    logger.debug("Entropy check %s took %.3f ms", operation_name, elapsed * 1000)
```

---

## Summary

The entropy checker should be integrated at **four key points**:

1. **`read_file()`** — Primary defense against binary file reads (HIGH PRIORITY)
2. **`grep()` / `grepF()`** — Prevent binary matches from entering context (HIGH PRIORITY)
3. **`/feed` command** — Safety net for user input (MEDIUM PRIORITY)
4. **`run_with_tools()`** — Defensive layer for future tools (LOW PRIORITY, OPTIONAL)

Each integration follows the same pattern:
1. Create `EntropyChecker()` instance
2. Call `.feed(data)` with the content
3. Check `result.is_suspicious`
4. Handle failure appropriately (error return, skip, or truncate)

The implementation is incremental and can be rolled out phase by phase without breaking existing functionality.
