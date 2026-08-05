"""Unit tests for core entropy analysis functions.

Tests the pure logic functions independently of class state management.
These are fast, isolated tests that verify the fundamental analysis logic.
"""

import pytest
from collections import Counter
import os
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)


from security.entropychecker import (
    _classify_byte,
    _validate_utf8_continuation,
    _analyze_window,
    _check_compression,
)


class TestClassifyByte:
    """Test byte classification into safe/control categories."""

    def test_printable_ascii_letters(self):
        """Test classification of ASCII letters (a-z, A-Z)."""
        assert _classify_byte(ord('a')) == "safe"
        assert _classify_byte(ord('z')) == "safe"
        assert _classify_byte(ord('A')) == "safe"
        assert _classify_byte(ord('Z')) == "safe"

    def test_printable_ascii_digits(self):
        """Test classification of ASCII digits (0-9)."""
        for digit in range(10):
            assert _classify_byte(ord(str(digit))) == "safe", f"Digit {digit} should be safe"

    def test_common_punctuation(self):
        """Test classification of common programming punctuation."""
        punct_chars = "!@#$%^&*()-_=+[]{}|;:'\",.<>?/\\`~"
        for char in punct_chars:
            assert _classify_byte(ord(char)) == "safe", f"{char!r} should be safe"

    def test_whitespace_characters(self):
        """Test classification of whitespace characters."""
        # Space character
        assert _classify_byte(ord(' ')) == "safe"
        
        # Tab (9), newline (10), carriage return (13)
        assert _classify_byte(9) == "safe", "Tab should be safe"
        assert _classify_byte(10) == "safe", "Newline should be safe"
        assert _classify_byte(13) == "safe", "Carriage return should be safe"

    def test_control_characters(self):
        """Test classification of control characters (0-8, 11-12, 14-31)."""
        # Null byte
        assert _classify_byte(0) == "control", "Null byte should be control"
        
        # Start of Heading (SOH)
        assert _classify_byte(1) == "control", "SOH should be control"
        
        # Escape character
        assert _classify_byte(27) == "control", "ESC should be control"
        
        # Other control characters in range 0-31 (excluding safe ones: 9, 10, 13)
        for i in range(32):
            if i not in {9, 10, 13}:
                assert _classify_byte(i) == "control", f"Control char {i} should be control"

    def test_high_bytes(self):
        """Test classification of high byte values (>= 128)."""
        # All bytes >= 128 are marked as control for standalone classification
        assert _classify_byte(128) == "control"
        assert _classify_byte(200) == "control"
        assert _classify_byte(255) == "control"

    def test_utf8_continuation_range(self):
        """Test UTF-8 continuation bytes (10xxxxxx = 128-191)."""
        # For standalone classification, all are marked as control
        for byte_val in range(128, 192):
            assert _classify_byte(byte_val) == "control", f"Byte {byte_val} should be control"

    def test_boundary_values(self):
        """Test boundary values (0, 31, 32, 127, 128, 255)."""
        assert _classify_byte(0) == "control"      # Null byte
        assert _classify_byte(31) == "control"     # Unit separator (control)
        assert _classify_byte(32) == "safe"        # Space (printable)
        assert _classify_byte(127) == "control"    # DEL character (not in safe set range(32,127))
        assert _classify_byte(128) == "control"    # Start of multi-byte UTF-8


class TestValidateUtf8Continuation:
    """Test UTF-8 continuation byte validation."""

    def test_valid_continuation_bytes(self):
        """Test valid UTF-8 continuation bytes (10xxxxxx = 128-191)."""
        for byte_val in range(128, 192):
            assert _validate_utf8_continuation(byte_val, prev_bytes=1) == True, \
                f"Byte {byte_val} should be valid continuation with prev_bytes=1"

    def test_invalid_continuation_bytes_below_range(self):
        """Test bytes below continuation range (0-127)."""
        # Bytes 0-127 are never continuation bytes
        for byte_val in range(128):
            assert _validate_utf8_continuation(byte_val, prev_bytes=1) == False, \
                f"Byte {byte_val} should not be valid continuation"

    def test_invalid_continuation_bytes_above_range(self):
        """Test bytes above continuation range (192-255)."""
        # Bytes 192-255 are never continuation bytes
        for byte_val in range(192, 256):
            assert _validate_utf8_continuation(byte_val, prev_bytes=1) == False, \
                f"Byte {byte_val} should not be valid continuation"

    def test_first_byte_not_continuation(self):
        """Test that first byte (prev_bytes=0) is never a continuation."""
        # First byte in sequence cannot be a continuation byte
        assert _validate_utf8_continuation(65, prev_bytes=0) == False  # 'A' (ASCII letter)
        assert _validate_utf8_continuation(200, prev_bytes=0) == False  # High byte
        assert _validate_utf8_continuation(160, prev_bytes=0) == False  # Would be continuation if not first

    def test_sequence_context_matters(self):
        """Test that validation depends on sequence context."""
        byte_val = 160  # Valid continuation byte (10100000 binary)
        
        # With prev_bytes=1, it's valid
        assert _validate_utf8_continuation(byte_val, prev_bytes=1) == True
        
        # With prev_bytes=0, it's invalid (would be start of sequence)
        assert _validate_utf8_continuation(byte_val, prev_bytes=0) == False


class TestAnalyzeWindow:
    """Test sliding window entropy analysis."""

    def test_empty_window(self):
        """Test analysis of empty window returns safe defaults."""
        result = _analyze_window(b"")
        assert result["safe_ratio"] == 1.0, "Empty window should have safe ratio 1.0"
        assert result["unique_bytes"] == 0, "Empty window should have 0 unique bytes"
        assert result["byte_distribution"] == {}, "Empty window should have empty distribution"

    def test_all_safe_bytes(self):
        """Test analysis with all safe bytes (ASCII text)."""
        text = b"Hello World! This is a test."
        result = _analyze_window(text)
        
        assert result["safe_ratio"] == 1.0, "All ASCII text should have safe ratio 1.0"
        assert result["unique_bytes"] > 0, "Should have some unique bytes"
        assert len(result["byte_distribution"]) == result["unique_bytes"], \
            "Distribution keys should match unique byte count"

    def test_all_control_bytes(self):
        """Test analysis with all control bytes."""
        binary = b"\x00\x01\x02\x03\x04\x05" * 100
        result = _analyze_window(binary)
        
        assert result["safe_ratio"] == 0.0, "All control bytes should have safe ratio 0.0"
        assert result["unique_bytes"] <= 6, f"Should have at most 6 unique bytes, got {result['unique_bytes']}"

    def test_mixed_content(self):
        """Test analysis with mixed safe and unsafe bytes."""
        mixed = b"Hello\x00World\x01!"
        result = _analyze_window(mixed)
        
        # Should have some safe ratio between 0 and 1
        assert 0.0 < result["safe_ratio"] < 1.0, \
            f"Mixed content should have partial safe ratio, got {result['safe_ratio']}"

    def test_unique_byte_count(self):
        """Test unique byte counting."""
        data = bytes(range(256))  # All possible byte values
        result = _analyze_window(data)
        
        assert result["unique_bytes"] == 256, "Should have all 256 unique byte values"

    def test_byte_distribution(self):
        """Test that byte frequency distribution is calculated correctly."""
        data = b"aaaabbbbcccc"
        result = _analyze_window(data)
        
        dist = result["byte_distribution"]
        assert dist[ord('a')] == 4, f"Expected 4 'a's, got {dist.get(ord('a'), 0)}"
        assert dist[ord('b')] == 4, f"Expected 4 'b's, got {dist.get(ord('b'), 0)}"
        assert dist[ord('c')] == 4, f"Expected 4 'c's, got {dist.get(ord('c'), 0)}"

    def test_large_window(self):
        """Test analysis with large window (larger than default 1024)."""
        text = b"Valid text " * 100  # ~1100 bytes
        result = _analyze_window(text)
        
        assert result["safe_ratio"] == 1.0, "All ASCII should have safe ratio 1.0"

    def test_whitespace_included_in_safe(self):
        """Test that whitespace characters are counted as safe."""
        data = b"Hello\n\t\r World"
        result = _analyze_window(data)
        
        assert result["safe_ratio"] == 1.0, "Should include tab/newline/CR as safe"

    def test_unicode_bytes_not_safe(self):
        """Test that UTF-8 multi-byte sequences are not counted as safe."""
        # Unicode character: é = U+00E9 = bytes C3 A9 in UTF-8
        data = "é".encode('utf-8')  # b'\xc3\xa9'
        result = _analyze_window(data)
        
        assert result["safe_ratio"] == 0.0, \
            f"UTF-8 multi-byte should not be safe, got ratio {result['safe_ratio']}"


class TestCheckCompression:
    """Test compression-based entropy checking."""

    def test_empty_data(self):
        """Test compression check with empty data returns False."""
        is_suspicious, reason = _check_compression(b"", threshold=0.95)
        assert is_suspicious == False, "Empty data should not be suspicious"
        assert reason == "", "Empty data should have empty reason"

    def test_repetitive_data_compresses_well(self):
        """Test that highly repetitive data compresses well (low ratio)."""
        # Highly repetitive data should compress very well
        data = b"a" * 10000
        
        is_suspicious, reason = _check_compression(data, threshold=0.95)
        
        assert is_suspicious == False, "Repetitive data should not be suspicious"
        
        # Verify actual compression ratio is low
        import zlib
        compressed = zlib.compress(data)
        ratio = len(compressed) / len(data)
        assert ratio < 0.95, f"Compression ratio {ratio:.3f} should be below threshold 0.95"

    def test_random_data_compresses_poorly(self):
        """Test that random data does not compress well (high ratio)."""
        import os
        
        # Random bytes should not compress well
        data = os.urandom(10000)
        
        is_suspicious, reason = _check_compression(data, threshold=0.95)
        
        assert is_suspicious == True, "Random data should be suspicious"
        assert "Compression ratio" in reason, "Reason should mention compression ratio"
        assert "exceeds threshold" in reason, "Reason should mention exceeding threshold"

    def test_text_compresses_well(self):
        """Test that natural English text compresses well."""
        # English text with repetition should compress
        text = "The quick brown fox jumps over the lazy dog. " * 100
        
        is_suspicious, reason = _check_compression(text.encode(), threshold=0.95)
        
        assert is_suspicious == False, "English text should not be suspicious"

    def test_threshold_customization(self):
        """Test that threshold parameter works correctly."""
        import os
        
        # Mostly compressible data (1000 'a' + 1000 random -> ratio ~0.5)
        data = b"a" * 1000 + os.urandom(1000)
        
        # Very strict threshold (should fail)
        is_suspicious, _ = _check_compression(data, threshold=0.5)
        assert is_suspicious == True, "Should fail with very strict threshold"
        
        # Lenient threshold (should pass)
        is_suspicious, _ = _check_compression(data, threshold=0.99)
        assert is_suspicious == False, "Should pass with lenient threshold"

    def test_reason_message_format(self):
        """Test that reason message contains expected information."""
        import os
        
        data = os.urandom(1000)
        is_suspicious, reason = _check_compression(data, threshold=0.5)
        
        if is_suspicious:
            assert "Compression ratio" in reason, "Reason should mention compression ratio"
            assert "exceeds threshold" in reason, "Reason should mention exceeding threshold"
            # Should contain numeric values
            assert any(c.isdigit() for c in reason), "Reason should contain numbers"

    def test_single_byte_data(self):
        """Test with single byte data."""
        is_suspicious, _ = _check_compression(b"A", threshold=0.95)
        # Single byte can't really be compressed or decompressed meaningfully
        # zlib might handle it differently, but should not crash
        assert isinstance(is_suspicious, bool), "Should return boolean"

    def test_short_strings_not_flagged_by_compression(self):
        """Short strings cannot be meaningfully zipped.

        zlib adds a fixed-size header, so tiny inputs inflate (ratio > 1.0)
        even when they are plain text. The compression test must skip data
        below the minimum meaningful size instead of raising a false positive.
        """
        for short in (b"A", b"hi", b"hello", b"Hello World!", "short text".encode()):
            is_suspicious, reason = _check_compression(short, threshold=0.95)
            assert is_suspicious == False, \
                f"Short text {short!r} must not be flagged: {reason}"
            assert reason == "", f"Short text {short!r} should have empty reason"

    def test_short_random_bytes_skipped_by_compression(self):
        """Very short random data is skipped by the compression test.

        The pattern analysis (not the compression test) is responsible for
        catching small binary inputs, so the compression helper must not
        crash or misfire on them.
        """
        import os
        is_suspicious, reason = _check_compression(os.urandom(16), threshold=0.95)
        assert is_suspicious == False, \
            f"Short random data should be skipped by compression, got: {reason}"

    def test_very_large_data(self):
        """Test with very large data to ensure no memory issues."""
        import os
        
        # 1 MB of random data
        data = os.urandom(1024 * 1024)
        
        is_suspicious, reason = _check_compression(data, threshold=0.95)
        
        assert isinstance(is_suspicious, bool), "Should return boolean"
        if is_suspicious:
            assert len(reason) > 0, "Should have reason for suspicious data"
