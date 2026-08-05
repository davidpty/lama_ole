# Entropy Checker — Class Outline & API

## Module Location

`lama_ole/security/entropychecker.py`

## Public API

```python
from security.entropychecker import EntropyChecker, EntropyCheckResult

# Create instance (per input source)
checker = EntropyChecker()

# Feed data blockwise (bytes or str)
result = checker.feed(b"some data")  # returns EntropyCheckResult

# Check if stream is still valid
if result.is_suspicious:
    raise ValueError(f"Entropy check failed: {result.reason}")

# Get accumulated safe output
safe_text = checker.get_output()

# Reset for next input source
checker.reset()
```

## Class: `EntropyChecker`

### Constructor

```python
class EntropyChecker:
    def __init__(
        self,
        window_size: int = 1024,
        safe_ratio_threshold: float = 0.85,
        unique_byte_threshold: int = 150,
        zip_size_limit: int = 65536,
        zip_ratio_threshold: float = 0.95,
    ):
        """Initialize entropy checker with configurable thresholds."""
```

### Methods

#### `feed(data: bytes | str) -> EntropyCheckResult`

Feeds data into the checker and returns a result indicating whether the stream is still valid.

**Parameters:**
- `data`: Input data as bytes or string (will be encoded to UTF-8 if string)

**Returns:**
- `EntropyCheckResult` with fields:
  - `is_suspicious: bool` — True if entropy check failed
  - `reason: str | None` — Explanation of why it failed (if suspicious)
  - `bytes_processed: int` — Total bytes processed so far

**Behavior:**
1. Encodes string input to UTF-8 bytes
2. Updates sliding window with new bytes
3. Performs pattern analysis on current window
4. If accumulated size exceeds `zip_size_limit`, performs zip compression test
5. Returns result based on analysis

#### `get_output() -> str`

Returns the accumulated safe text output (only if not refused).

**Returns:**
- The concatenated input data as a string, or empty string if refused

#### `reset()`

Resets the checker state for a new input source.

**Behavior:**
- Clears sliding window buffer
- Clears accumulated bytes
- Resets all counters and flags

### Class: `EntropyCheckResult` (dataclass)

```python
@dataclass
class EntropyCheckResult:
    is_suspicious: bool
    reason: str | None = None
    bytes_processed: int = 0
```

## Internal Helper Functions

These are module-level functions that the class uses internally. They can also be tested independently.

### `_classify_byte(byte_val: int) -> str`

Classifies a single byte value into categories.

**Parameters:**
- `byte_val`: Integer value of byte (0-255)

**Returns:**
- `"safe"` — Printable ASCII or valid UTF-8 continuation
- `"control"` — Control character or invalid byte

### `_analyze_window(window: bytes) -> dict`

Analyzes a sliding window of bytes for entropy patterns.

**Parameters:**
- `window`: Bytes to analyze (should be at least `window_size` long)

**Returns:**
- Dictionary with analysis results:
  - `"safe_ratio"` — Fraction of safe bytes (0.0 to 1.0)
  - `"unique_bytes"` — Number of unique byte values
  - `"byte_distribution"` — Frequency count of each byte value

### `_check_compression(data: bytes, threshold: float) -> tuple[bool, str]`

Performs zip compression test on data.

**Parameters:**
- `data`: Bytes to compress and test
- `threshold`: Maximum acceptable compression ratio

**Returns:**
- Tuple of `(is_suspicious, reason)` where:
  - `is_suspicious`: True if compression ratio exceeds threshold
  - `reason`: Explanation string (empty if not suspicious)

### `_validate_utf8_continuation(byte_val: int, prev_bytes: int) -> bool`

Checks if a byte is a valid UTF-8 continuation byte.

**Parameters:**
- `byte_val`: Current byte value
- `prev_bytes`: Number of previous bytes in current multi-byte sequence

**Returns:**
- True if the byte is valid in the current UTF-8 context

## State Management

The `EntropyChecker` class maintains internal state:

```python
class EntropyChecker:
    def __init__(self, ...):
        # Configuration
        self.window_size = window_size
        self.safe_ratio_threshold = safe_ratio_threshold
        self.unique_byte_threshold = unique_byte_threshold
        self.zip_size_limit = zip_size_limit
        self.zip_ratio_threshold = zip_ratio_threshold
        
        # Sliding window buffer
        self._window: bytearray = bytearray()
        
        # Accumulated data for compression test
        self._accumulated: list[bytes] = []
        self._total_bytes: int = 0
        
        # UTF-8 state tracking
        self._utf8_sequence: list[int] = []
        
        # Refusal flag (once refused, stays refused)
        self._refused: bool = False
        self._refusal_reason: str | None = None
```

## Error Handling

The entropy checker raises `ValueError` when:
- Input data cannot be encoded to UTF-8 (if string input contains invalid characters)
- The stream is determined to be too random (via `is_suspicious` flag in result)

Integration code should catch these and handle gracefully (e.g., log warning, skip the file, etc.).

## Thread Safety

The current design is **not** thread-safe. Each `EntropyChecker` instance is intended for single-threaded use on one input stream. If multiple threads need entropy checking, each must have its own instance.
