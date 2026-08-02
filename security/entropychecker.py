"""Entropy Checker Module

Prevents binary/random data from leaking into the LLM context by analyzing
incoming byte streams for entropy patterns. Uses a two-layer approach:
1. Pattern analysis via sliding window (safe byte ratio, unique byte count)
2. Compression test (zlib ratio check for incompressible data)

Usage:
    checker = EntropyChecker()
    result = checker.feed(data_bytes_or_string)
    
    if result.is_suspicious:
        # Handle rejection - binary data detected
        raise ValueError(f"Entropy check failed: {result.reason}")
    
    safe_text = checker.get_output()

Integration Points (see documentation/security/entropychecker/004_integration_points.md):
    - dev_tools.py::read_file() — Primary defense against binary file reads
    - dev_tools.py::grep()/grepF() — Prevent binary matches from entering context
    - chat.py::/feed command — Safety net for user input
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Constants and Configuration
# ============================================================================

# Safe byte ranges: printable ASCII (32-126) plus common whitespace/control codes
_SAFE_BYTE_SET = set(range(32, 127))  # '!' through '~'
_SAFE_BYTE_SET.update({9, 10, 13})    # tab, newline, carriage return

# UTF-8 continuation byte range: 10xxxxxx (binary) = 128-191 (decimal)
_UTF8_CONTINUATION_MIN = 128
_UTF8_CONTINUATION_MAX = 191


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class EntropyCheckResult:
    """Result of an entropy check operation.
    
    Attributes:
        is_suspicious: True if the data failed entropy validation (too random)
        reason: Explanation of why it failed, or None if passed
        bytes_processed: Total number of bytes processed so far in this stream
    """
    is_suspicious: bool
    reason: Optional[str] = None
    bytes_processed: int = 0


# ============================================================================
# Core Analysis Functions (Pure Logic - Testable Independently)
# ============================================================================

def _classify_byte(byte_val: int) -> str:
    """Classify a single byte value into safe or control categories.
    
    Args:
        byte_val: Integer value of the byte (0-255)
        
    Returns:
        "safe" if the byte is printable ASCII or common whitespace/control code
        "control" otherwise (including high bytes and invalid UTF-8 continuations)
        
    Examples:
        >>> _classify_byte(ord('A'))
        'safe'
        >>> _classify_byte(0)  # null byte
        'control'
        >>> _classify_byte(128)  # start of multi-byte UTF-8 sequence
        'control'
    """
    if byte_val in _SAFE_BYTE_SET:
        return "safe"
    
    # Bytes >= 128 are either UTF-8 continuation bytes or high bytes
    # For standalone classification, we mark all as control since they need context
    return "control"


def _validate_utf8_continuation(byte_val: int, prev_bytes: int) -> bool:
    """Check if a byte is a valid UTF-8 continuation byte.
    
    In UTF-8, continuation bytes always have the form 10xxxxxx (binary),
    which corresponds to decimal values 128-191.
    
    Args:
        byte_val: Current byte value to validate (0-255)
        prev_bytes: Number of previous bytes in the current multi-byte sequence
        
    Returns:
        True if the byte is a valid continuation byte for the given context
        
    Examples:
        >>> _validate_utf8_continuation(160, 1)  # valid continuation
        True
        >>> _validate_utf8_continuation(200, 1)  # not a continuation byte
        False
        >>> _validate_utf8_continuation(65, 0)   # first byte is never continuation
        False
    """
    if prev_bytes == 0:
        # First byte in sequence cannot be a continuation byte
        return False
    
    # Continuation bytes must be in range 128-191 (10xxxxxx binary)
    return _UTF8_CONTINUATION_MIN <= byte_val <= _UTF8_CONTINUATION_MAX


def _analyze_window(window: bytes) -> dict:
    """Analyze a sliding window of bytes for entropy patterns.
    
    Computes the ratio of safe-to-total bytes and counts unique byte values.
    This is the primary pattern analysis mechanism.
    
    Args:
        window: Bytes to analyze (should be at least window_size long for meaningful results)
        
    Returns:
        Dictionary with analysis results:
            - "safe_ratio": Fraction of safe bytes (0.0 to 1.0)
            - "unique_bytes": Number of unique byte values in the window
            - "byte_distribution": Frequency count of each byte value
            
    Examples:
        >>> result = _analyze_window(b"Hello World!")
        >>> result["safe_ratio"]
        1.0
        >>> result = _analyze_window(b"\\x00\\x01\\x02")
        >>> result["safe_ratio"]
        0.0
    """
    if not window:
        return {
            "safe_ratio": 1.0,
            "unique_bytes": 0,
            "byte_distribution": {},
        }
    
    safe_count = sum(1 for b in window if _classify_byte(b) == "safe")
    unique_bytes = len(set(window))
    
    # Build byte frequency distribution using a dict (faster than Counter for this use case)
    byte_dist: dict[int, int] = {}
    for b in window:
        byte_dist[b] = byte_dist.get(b, 0) + 1
    
    return {
        "safe_ratio": safe_count / len(window),
        "unique_bytes": unique_bytes,
        "byte_distribution": byte_dist,
    }


def _utf8_sequence_length(lead_byte: int) -> int:
    """Return the number of bytes in a UTF-8 sequence from its lead byte.

    Valid lead bytes start 2-4 byte sequences:
        110xxxxx (0xC2-0xDF) -> 2 bytes
        1110xxxx (0xE0-0xEF) -> 3 bytes
        11110xxx (0xF0-0xF4) -> 4 bytes

    Other byte values (ASCII, continuation bytes 0x80-0xBF, invalid leads
    0xC0/0xC1/0xF5-0xFF) are not valid lead bytes -> returns 0.

    Args:
        lead_byte: Integer value of the candidate lead byte (0-255)

    Returns:
        Total length of the UTF-8 sequence in bytes, or 0 if not a lead byte.
    """
    if 0xC2 <= lead_byte <= 0xDF:
        return 2
    if 0xE0 <= lead_byte <= 0xEF:
        return 3
    if 0xF0 <= lead_byte <= 0xF4:
        return 4
    return 0


def _analyze_window_utf8(window: bytes) -> dict:
    """Analyze a byte window, counting valid UTF-8 sequences as safe.

    Works like _analyze_window but treats valid multi-byte UTF-8 sequences
    (a lead byte followed by the required number of continuation bytes) as
    safe. This lets Unicode text pass the pattern check while genuine binary
    junk still gets flagged as control bytes.

    Returns the same dict shape as _analyze_window:
        - "safe_ratio": Fraction of safe bytes (0.0 to 1.0)
        - "unique_bytes": Number of unique byte values in the window
        - "byte_distribution": Frequency count of each byte value
    """
    if not window:
        return {
            "safe_ratio": 1.0,
            "unique_bytes": 0,
            "byte_distribution": {},
        }

    n = len(window)
    safe_count = 0
    unique = set()
    byte_dist: dict[int, int] = {}
    i = 0
    while i < n:
        b = window[i]
        byte_dist[b] = byte_dist.get(b, 0) + 1
        unique.add(b)
        if b < 128:
            if b in _SAFE_BYTE_SET:
                safe_count += 1
            i += 1
        else:
            length = _utf8_sequence_length(b)
            if length > 0 and i + length <= n and all(
                _UTF8_CONTINUATION_MIN <= window[i + k] <= _UTF8_CONTINUATION_MAX
                for k in range(1, length)
            ):
                for k in range(1, length):
                    cb = window[i + k]
                    byte_dist[cb] = byte_dist.get(cb, 0) + 1
                    unique.add(cb)
                safe_count += length
                i += length
            else:
                i += 1

    return {
        "safe_ratio": safe_count / n,
        "unique_bytes": len(unique),
        "byte_distribution": byte_dist,
    }


def _check_compression(data: bytes, threshold: float) -> tuple[bool, str]:
    """Check if data is compressible (text-like) using zlib compression.
    
    Valid text has redundancy and compresses well (low ratio).
    Random binary data does not compress and has high ratio (~1.0).
    
    Args:
        data: Bytes to compress and test
        threshold: Maximum acceptable compression ratio (e.g., 0.95)
                   Above this → data is too random
        
    Returns:
        Tuple of (is_suspicious, reason):
            - is_suspicious: True if compression ratio exceeds threshold
            - reason: Explanation string (empty if not suspicious)
            
    Examples:
        >>> _check_compression(b"a" * 10000, 0.95)
        (False, '')
        >>> import os
        >>> _check_compression(b"a" * 10000, 0.95)
        (False, '')
        >>> suspicious, reason = _check_compression(os.urandom(1000), 0.95)
        >>> suspicious
        True
    """
    if not data:
        return False, ""
    
    try:
        compressed = zlib.compress(data)
        ratio = len(compressed) / len(data)
        
        if ratio > threshold:
            reason = (
                f"Compression ratio {ratio:.3f} exceeds threshold "
                f"{threshold:.3f} — data appears incompressible/random"
            )
            return True, reason
        
        return False, ""
    
    except Exception as e:
        # If compression fails for any reason, flag as suspicious
        return True, f"Compression check failed: {e}"


# ============================================================================
# Public Class: EntropyChecker
# ============================================================================

class EntropyChecker:
    """Stateful entropy checker for byte streams.
    
    Maintains a sliding window for pattern analysis and accumulates data
    for compression testing. Per-instance state ensures isolation between
    different input sources (files, stdin, etc.).
    
    Not thread-safe — each instance is intended for single-threaded use on one stream.
    
    Attributes:
        window_size: Size of sliding window for pattern analysis (bytes)
        safe_ratio_threshold: Minimum fraction of safe bytes to pass pattern check
        unique_byte_threshold: Max unique byte values before flagging
        zip_size_limit: Accumulated bytes after which compression test activates
        zip_ratio_threshold: Above this → data is too random, refuse
        
    Examples:
        >>> checker = EntropyChecker()
        >>> result = checker.feed("Hello World!")
        >>> result.is_suspicious
        False
        >>> import os
        >>> checker.reset()
        >>> result = checker.feed(os.urandom(1000))
        >>> result.is_suspicious
        True
    """
    
    def __init__(
        self,
        window_size: int = 1024,
        safe_ratio_threshold: float = 0.85,
        unique_byte_threshold: int = 150,
        zip_size_limit: int = 65536,
        zip_ratio_threshold: float = 0.95,
    ):
        """Initialize entropy checker with configurable thresholds.
        
        Args:
            window_size: Sliding window size for pattern analysis (default: 1024)
            safe_ratio_threshold: Minimum safe byte ratio to pass (default: 0.85)
            unique_byte_threshold: Max unique bytes in window before flagging (default: 64)
            zip_size_limit: Bytes accumulated before compression test activates (default: 64 KiB)
            zip_ratio_threshold: Maximum acceptable compression ratio (default: 0.95)
        """
        # Configuration thresholds
        self.window_size = window_size
        self.safe_ratio_threshold = safe_ratio_threshold
        self.unique_byte_threshold = unique_byte_threshold
        self.zip_size_limit = zip_size_limit
        self.zip_ratio_threshold = zip_ratio_threshold
        
        # Sliding window buffer for pattern analysis
        self._window: bytearray = bytearray()
        
        # Accumulated data chunks for compression test
        self._accumulated: list[bytes] = []
        self._total_bytes: int = 0
        
        # UTF-8 sequence tracking state
        self._utf8_sequence: list[int] = []
        
        # Refusal state (once refused, stays refused until reset)
        self._refused: bool = False
        self._refusal_reason: Optional[str] = None
    
    def feed(self, data: bytes | str) -> EntropyCheckResult:
        """Feed data into the checker and return analysis result.
        
        Processes input blockwise, updating the sliding window for pattern
        analysis and accumulating data for compression testing when size limit is reached.
        
        Args:
            data: Input data as bytes or string (strings are encoded to UTF-8)
            
        Returns:
            EntropyCheckResult indicating whether the stream is still valid
            
        Raises:
            UnicodeEncodeError: If string input cannot be encoded to UTF-8
            
        Examples:
            >>> checker = EntropyChecker()
            >>> result = checker.feed("Valid text")
            >>> result.is_suspicious
            False
            
            >>> import os
            >>> checker.reset()
            >>> result = checker.feed(os.urandom(1000))
            >>> result.is_suspicious
            True
        """
        # Encode string input to bytes if necessary
        if isinstance(data, str):
            try:
                data_bytes = data.encode('utf-8')
            except UnicodeEncodeError as e:
                return EntropyCheckResult(
                    is_suspicious=True,
                    reason=f"UTF-8 encoding error: {e}",
                    bytes_processed=self._total_bytes,
                )
        else:
            data_bytes = data
        
        # Update sliding window (maintain fixed size)
        self._window.extend(data_bytes)
        if len(self._window) > self.window_size:
            # Keep only the last window_size bytes
            self._window = self._window[-self.window_size:]
        
        # Accumulate data for compression test
        self._accumulated.append(data_bytes)
        self._total_bytes += len(data_bytes)
        
        # Perform pattern analysis on whatever data is currently buffered.
        # The window holds at most window_size bytes; partial windows are
        # analyzed too so small binary inputs (e.g. a PNG header) are caught.
        if len(self._window) > 0:
            analysis = _analyze_window_utf8(bytes(self._window))
            
            # Check safe byte ratio
            if analysis["safe_ratio"] < self.safe_ratio_threshold:
                reason = (
                    f"Safe byte ratio {analysis['safe_ratio']:.3f} "
                    f"below threshold {self.safe_ratio_threshold:.3f}"
                )
                self._refused = True
                self._refusal_reason = reason
                return EntropyCheckResult(
                    is_suspicious=True,
                    reason=reason,
                    bytes_processed=self._total_bytes,
                )
            
            # Check unique byte count
            if analysis["unique_bytes"] > self.unique_byte_threshold:
                reason = (
                    f"Too many unique byte values: {analysis['unique_bytes']} "
                    f"(threshold: {self.unique_byte_threshold})"
                )
                self._refused = True
                self._refusal_reason = reason
                return EntropyCheckResult(
                    is_suspicious=True,
                    reason=reason,
                    bytes_processed=self._total_bytes,
                )
        
        # Perform compression test (when accumulated size exceeds limit)
        if self._total_bytes >= self.zip_size_limit and not self._refused:
            all_data = b''.join(self._accumulated)
            is_suspicious, reason = _check_compression(
                all_data, 
                self.zip_ratio_threshold
            )
            if is_suspicious:
                self._refused = True
                self._refusal_reason = reason
                return EntropyCheckResult(
                    is_suspicious=True,
                    reason=reason,
                    bytes_processed=self._total_bytes,
                )
        
        # All checks passed
        return EntropyCheckResult(
            is_suspicious=False,
            reason=None,
            bytes_processed=self._total_bytes,
        )
    
    def get_output(self) -> str:
        """Return the accumulated safe text output.
        
        Only returns data if the stream has not been refused.
        Returns empty string if refused or no data fed.
        
        Returns:
            Concatenated input data as a UTF-8 decoded string, or empty string
            
        Examples:
        >>> checker = EntropyChecker()
        >>> _ = checker.feed("Hello ")
        >>> _ = checker.feed("World")
        >>> checker.get_output()
        'Hello World'
            
            >>> checker.reset()
            >>> checker.get_output()
            ''
        """
        if self._refused:
            return ""
        
        # Concatenate all accumulated chunks and decode to string
        try:
            return b''.join(self._accumulated).decode('utf-8')
        except UnicodeDecodeError:
            # If decoding fails, return with replacement characters
            return b''.join(self._accumulated).decode('utf-8', errors='replace')
    
    def reset(self) -> None:
        """Reset the checker state for a new input source.
        
        Clears all internal buffers, counters, and refusal flags.
        Call this before processing a new file or stdin stream.
        
        Examples:
        >>> checker = EntropyChecker()
        >>> _ = checker.feed("data")
        >>> checker.reset()
        >>> len(checker._window) == 0
        True
            >>> checker._total_bytes == 0
            True
            >>> not checker._refused
            True
        """
        # Clear sliding window buffer
        self._window.clear()
        
        # Clear accumulated data list
        self._accumulated.clear()
        
        # Reset byte counter
        self._total_bytes = 0
        
        # Clear UTF-8 sequence tracking state
        self._utf8_sequence.clear()
        
        # Clear refusal flags
        self._refused = False
        self._refusal_reason = None


# ============================================================================
# Module-Level Convenience Function
# ============================================================================

def check_entropy(data: bytes | str, 
                  window_size: int = 1024,
                  safe_ratio_threshold: float = 0.85,
                  unique_byte_threshold: int = 64,
                  zip_size_limit: int = 65536,
                  zip_ratio_threshold: float = 0.95) -> bool:
    """Quick entropy check for one-shot usage.
    
    Creates an EntropyChecker, feeds the data, and returns whether it's suspicious.
    Useful for simple validation without managing checker state.
    
    Args:
        data: Input data as bytes or string
        window_size: Sliding window size (default: 1024)
        safe_ratio_threshold: Minimum safe byte ratio (default: 0.85)
        unique_byte_threshold: Max unique bytes (default: 64)
        zip_size_limit: Compression test trigger size (default: 64 KiB)
        zip_ratio_threshold: Maximum compression ratio (default: 0.95)
        
    Returns:
        True if data is suspicious (too random), False if valid text
        
    Examples:
        >>> check_entropy("Hello World!")
        False
        >>> import os
        >>> check_entropy(os.urandom(1000))
        True
    """
    checker = EntropyChecker(
        window_size=window_size,
        safe_ratio_threshold=safe_ratio_threshold,
        unique_byte_threshold=unique_byte_threshold,
        zip_size_limit=zip_size_limit,
        zip_ratio_threshold=zip_ratio_threshold,
    )
    result = checker.feed(data)
    return result.is_suspicious
