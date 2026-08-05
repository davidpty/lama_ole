# Entropy Checker — Implementation Plan

## Overview

This document outlines the ordered task list for implementing the entropy checker module.
Tasks are sequenced to build incrementally, with each step being testable before moving to the next.

## Task Sequence

### Phase 1: Foundation (Core Analysis Functions)

#### Task 1.1: Create Module Structure
**File:** `lama_ole/security/__init__.py` and `lama_ole/security/entropychecker.py`

- [ ] Create `security/` directory under `lama_ole/`
- [ ] Create empty `__init__.py` in security directory
- [ ] Create initial `entropychecker.py` with module docstring
- [ ] Add basic imports: `zlib`, `dataclasses`, `typing`

**Estimated Time:** 5 minutes

---

#### Task 1.2: Implement `_classify_byte()` Function
**File:** `lama_ole/security/entropychecker.py`

- [ ] Define safe character set (printable ASCII + common programming chars)
- [ ] Implement byte classification logic
- [ ] Handle UTF-8 continuation bytes
- [ ] Add type hints and docstring

**Key Logic:**
```python
SAFE_BYTES = set(range(32, 127))  # Printable ASCII
# Plus specific control codes: tab (9), newline (10), carriage return (13)
SAFE_BYTES.update({9, 10, 13})
```

**Estimated Time:** 15 minutes

---

#### Task 1.3: Implement `_validate_utf8_continuation()` Function
**File:** `lama_ole/security/entropychecker.py`

- [ ] Define UTF-8 continuation byte patterns (10xxxxxx)
- [ ] Track multi-byte sequence state
- [ ] Validate byte sequences

**Key Logic:**
```python
def _validate_utf8_continuation(byte_val: int, prev_bytes: int) -> bool:
    """Check if byte is valid UTF-8 continuation."""
    # Continuation bytes must be 10xxxxxx (128-191)
    return 128 <= byte_val <= 191
```

**Estimated Time:** 20 minutes

---

#### Task 1.4: Implement `_analyze_window()` Function
**File:** `lama_ole/security/entropychecker.py`

- [ ] Calculate safe-to-total byte ratio
- [ ] Count unique byte values
- [ ] Build byte frequency distribution
- [ ] Return analysis results as dictionary

**Key Logic:**
```python
def _analyze_window(window: bytes) -> dict:
    """Analyze entropy patterns in a byte window."""
    if not window:
        return {"safe_ratio": 1.0, "unique_bytes": 0}
    
    safe_count = sum(1 for b in window if _classify_byte(b) == "safe")
    unique_bytes = len(set(window))
    
    return {
        "safe_ratio": safe_count / len(window),
        "unique_bytes": unique_bytes,
        "byte_distribution": dict(Counter(window)),
    }
```

**Estimated Time:** 25 minutes

---

#### Task 1.5: Implement `_check_compression()` Function
**File:** `lama_ole/security/entropychecker.py`

- [ ] Compress data using zlib
- [ ] Calculate compression ratio
- [ ] Compare against threshold
- [ ] Return suspicion result with reason

**Key Logic:**
```python
def _check_compression(data: bytes, threshold: float) -> tuple[bool, str]:
    """Check if data is compressible (text-like)."""
    if not data:
        return False, ""
    
    compressed = zlib.compress(data)
    ratio = len(compressed) / len(data)
    
    if ratio > threshold:
        return True, f"Compression ratio {ratio:.3f} exceeds threshold {threshold}"
    return False, ""
```

**Estimated Time:** 20 minutes

---

### Phase 2: Class Implementation (State Management)

#### Task 2.1: Define `EntropyCheckResult` Dataclass
**File:** `lama_ole/security/entropychecker.py`

- [ ] Create dataclass with required fields
- [ ] Add type hints
- [ ] Add docstring

**Estimated Time:** 10 minutes

---

#### Task 2.2: Implement `EntropyChecker.__init__()`
**File:** `lama_ole/security/entropychecker.py`

- [ ] Store configuration parameters
- [ ] Initialize internal state variables
- [ ] Set up sliding window buffer
- [ ] Reset counters and flags

**Estimated Time:** 15 minutes

---

#### Task 2.3: Implement `EntropyChecker.feed()` Method
**File:** `lama_ole/security/entropychecker.py`

- [ ] Handle string vs bytes input
- [ ] Update sliding window (maintain fixed size)
- [ ] Track UTF-8 sequence state
- [ ] Perform pattern analysis on window
- [ ] Check if zip test should run
- [ ] Return `EntropyCheckResult`

**Key Logic:**
```python
def feed(self, data: bytes | str) -> EntropyCheckResult:
    """Feed data and check entropy."""
    # Encode string to bytes
    if isinstance(data, str):
        try:
            data = data.encode('utf-8')
        except UnicodeEncodeError as e:
            return EntropyCheckResult(True, f"UTF-8 encode error: {e}")
    
    # Update sliding window
    self._window.extend(data)
    if len(self._window) > self.window_size:
        self._window = self._window[-self.window_size:]
    
    # Accumulate data for zip test
    self._accumulated.append(data)
    self._total_bytes += len(data)
    
    # Pattern analysis (if window is large enough)
    if len(self._window) >= self.window_size:
        analysis = _analyze_window(bytes(self._window))
        
        if analysis["safe_ratio"] < self.safe_ratio_threshold:
            return EntropyCheckResult(
                True, 
                f"Safe byte ratio {analysis['safe_ratio']:.3f} below threshold"
            )
        
        if analysis["unique_bytes"] > self.unique_byte_threshold:
            return EntropyCheckResult(
                True,
                f"Too many unique bytes: {analysis['unique_bytes']}"
            )
    
    # Compression test (if accumulated size exceeds limit)
    if self._total_bytes >= self.zip_size_limit and not self._refused:
        all_data = b''.join(self._accumulated)
        is_suspicious, reason = _check_compression(
            all_data, 
            self.zip_ratio_threshold
        )
        if is_suspicious:
            self._refused = True
            self._refusal_reason = reason
            return EntropyCheckResult(True, reason)
    
    return EntropyCheckResult(False, None, self._total_bytes)
```

**Estimated Time:** 45 minutes

---

#### Task 2.4: Implement `EntropyChecker.get_output()` Method
**File:** `lama_ole/security/entropychecker.py`

- [ ] Concatenate accumulated data
- [ ] Return as string (decode UTF-8)
- [ ] Handle refusal case

**Estimated Time:** 10 minutes

---

#### Task 2.5: Implement `EntropyChecker.reset()` Method
**File:** `lama_ole/security/entropychecker.py`

- [ ] Clear sliding window
- [ ] Clear accumulated data list
- [ ] Reset byte counter
- [ ] Clear UTF-8 state
- [ ] Clear refusal flags

**Estimated Time:** 10 minutes

---

### Phase 3: Integration & Testing Support

#### Task 3.1: Add Module-Level Convenience Function
**File:** `lama_ole/security/entropychecker.py`

- [ ] Create `check_entropy(data: bytes) -> bool` helper
- [ ] One-liner that creates checker, feeds data, returns result

**Estimated Time:** 10 minutes

---

#### Task 3.2: Add Type Hints and Documentation Strings
**File:** `lama_ole/security/entropychecker.py`

- [ ] Add module docstring explaining purpose
- [ ] Document all public functions and classes
- [ ] Add inline comments for complex logic

**Estimated Time:** 20 minutes

---

#### Task 3.3: Create Unit Tests
**File:** `lama_ole/tests/test_entropychecker.py`

- [ ] Test `_classify_byte()` with various byte values
- [ ] Test `_validate_utf8_continuation()` with UTF-8 sequences
- [ ] Test `_analyze_window()` with known patterns
- [ ] Test `_check_compression()` with text vs binary
- [ ] Test `EntropyChecker` state management
- [ ] Test edge cases (empty input, large input, mixed content)

**Estimated Time:** 60 minutes

---

## Implementation Order Summary

1. **Phase 1: Core Functions** (Tasks 1.1-1.5) — ~1 hour 15 minutes
   - Build the pure analysis logic first
   - Each function is independently testable
   
2. **Phase 2: Class Implementation** (Tasks 2.1-2.5) — ~1 hour 30 minutes
   - Wrap functions in stateful class
   - Add reset and output methods
   
3. **Phase 3: Polish & Test** (Tasks 3.1-3.3) — ~1 hour 40 minutes
   - Add convenience helpers
   - Comprehensive testing

**Total Estimated Time:** ~4 hours 25 minutes

## Dependencies Between Tasks

```
Task 1.1 (module structure)
    ↓
Task 1.2 (_classify_byte) ← Task 1.3, 1.4 depend on this
    ↓                              ↓
Task 1.3 (UTF-8 validation)     Task 1.4 (window analysis)
    ↓                              ↓
Task 1.5 (compression check)    Task 1.5 (compression check)
    ↓                              ↓
Task 2.1 (result dataclass) ← All Phase 2 tasks depend on Phase 1
    ↓
Task 2.2 (__init__) 
    ↓
Task 2.3 (feed method) ← Core integration of all functions
    ↓
Task 2.4 (get_output) and Task 2.5 (reset) ← Independent, can be parallel
```

## Success Criteria

- [ ] All Phase 1 functions pass unit tests independently
- [ ] EntropyChecker class correctly identifies binary files as suspicious
- [ ] Valid text files (Python, Markdown, etc.) are accepted
- [ ] Compression test correctly handles edge cases
- [ ] Reset functionality works for sequential file processing
- [ ] No performance degradation for typical file sizes (< 1 MB)

## Next Steps After Implementation

Once the entropy checker module is complete:

1. **Integration:** Wire it into `dev_tools.py` (see `004_integration_points.md`)
2. **Testing:** Run integration tests with real binary files
3. **Documentation:** Update AGENTS.md with security tool usage
4. **Performance:** Profile and optimize if needed for large files
