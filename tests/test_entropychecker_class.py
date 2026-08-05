"""Unit tests for EntropyChecker class state management.

Tests the class behavior, initialization, feed method, reset, and get_output.
These tests verify the integration of core functions with stateful class logic.
"""

import os
import pytest
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

from security.entropychecker import (
    EntropyChecker,
    EntropyCheckResult,
)


class TestEntropyCheckerInit:
    """Test constructor and initialization."""

    def test_default_thresholds(self):
        """Test that default thresholds are set correctly."""
        checker = EntropyChecker()
        
        assert checker.window_size == 1024, "Default window size should be 1024"
        assert checker.safe_ratio_threshold == 0.85, "Default safe ratio threshold should be 0.85"
        assert checker.unique_byte_threshold == 150, "Default unique byte threshold should be 150"
        assert checker.zip_size_limit == 65536, "Default zip size limit should be 65536 (64 KiB)"
        assert checker.zip_ratio_threshold == 0.95, "Default zip ratio threshold should be 0.95"

    def test_custom_thresholds(self):
        """Test custom threshold configuration."""
        checker = EntropyChecker(
            window_size=2048,
            safe_ratio_threshold=0.9,
            unique_byte_threshold=100,
            zip_size_limit=32768,
            zip_ratio_threshold=0.9,
        )
        
        assert checker.window_size == 2048
        assert checker.safe_ratio_threshold == 0.9
        assert checker.unique_byte_threshold == 100
        assert checker.zip_size_limit == 32768
        assert checker.zip_ratio_threshold == 0.9

    def test_initial_state(self):
        """Test that initial state is clean."""
        checker = EntropyChecker()
        
        assert len(checker._window) == 0, "Initial window should be empty"
        assert checker._total_bytes == 0, "Initial total bytes should be 0"
        assert not checker._refused, "Should not be refused initially"
        assert checker._refusal_reason is None, "No refusal reason initially"

    def test_state_variables_initialized(self):
        """Test that all internal state variables are properly initialized."""
        checker = EntropyChecker()
        
        # Check window buffer
        assert hasattr(checker, '_window'), "Should have _window attribute"
        assert isinstance(checker._window, bytearray), "_window should be bytearray"
        
        # Check accumulated list
        assert hasattr(checker, '_accumulated'), "Should have _accumulated attribute"
        assert isinstance(checker._accumulated, list), "_accumulated should be list"
        
        # Check UTF-8 sequence tracking
        assert hasattr(checker, '_utf8_sequence'), "Should have _utf8_sequence attribute"
        assert isinstance(checker._utf8_sequence, list), "_utf8_sequence should be list"


class TestFeedValidInput:
    """Test feed method with valid (non-suspicious) input."""

    def test_small_text_passes(self):
        """Test that small valid text passes entropy check."""
        checker = EntropyChecker()
        result = checker.feed("Hello World!")
        
        assert not result.is_suspicious, "Small text should pass"
        assert result.reason is None, "No reason for passing input"

    def test_python_code_passes(self):
        """Test that Python code passes entropy check."""
        checker = EntropyChecker()
        code = '''def hello():
    print("Hello, world!")

if __name__ == "__main__":
    hello()
'''
        result = checker.feed(code)
        
        assert not result.is_suspicious, "Python code should pass"

    def test_markdown_passes(self):
        """Test that Markdown content passes entropy check."""
        checker = EntropyChecker()
        md = "# Title\n\nSome **bold** and *italic* text.\n"
        
        result = checker.feed(md)
        
        assert not result.is_suspicious, "Markdown should pass"

    def test_bytes_input(self):
        """Test that bytes input works correctly."""
        checker = EntropyChecker()
        result = checker.feed(b"Hello World!")
        
        assert not result.is_suspicious, "Bytes input should pass"

    def test_utf8_multibyte_characters(self):
        """Test that UTF-8 multibyte characters are handled."""
        checker = EntropyChecker()
        # Unicode characters (emoji, etc.)
        text = "Hello 世界 🌍"
        
        result = checker.feed(text)
        
        assert not result.is_suspicious, "UTF-8 multibyte should pass"

    def test_accumulated_bytes_count(self):
        """Test that byte counter is updated correctly."""
        checker = EntropyChecker()
        data = "Hello World!"
        
        result = checker.feed(data)
        
        expected_bytes = len(data.encode('utf-8'))
        assert result.bytes_processed == expected_bytes, \
            f"Expected {expected_bytes} bytes processed, got {result.bytes_processed}"

    def test_feed_returns_entropy_check_result(self):
        """Test that feed returns EntropyCheckResult instance."""
        checker = EntropyChecker()
        result = checker.feed("test")
        
        assert isinstance(result, EntropyCheckResult), \
            "feed should return EntropyCheckResult instance"

    def test_multiple_feed_calls_accumulate(self):
        """Test that multiple feed calls accumulate data correctly."""
        checker = EntropyChecker()
        
        result1 = checker.feed("Hello ")
        assert not result1.is_suspicious
        
        result2 = checker.feed("World")
        assert not result2.is_suspicious
        
        # Total bytes should be sum of both feeds
        expected_bytes = len("Hello ".encode('utf-8')) + len("World".encode('utf-8'))
        assert result2.bytes_processed == expected_bytes

    def test_short_text_passes_with_tiny_zip_limit(self):
        """Short strings below a meaningful zip size must not be flagged.

        zlib inflates tiny inputs (fixed header), so the compression test is
        skipped for short strings; the pattern check still applies and short
        valid text must pass.
        """
        checker = EntropyChecker(zip_size_limit=10)
        result = checker.feed("Hello World!")

        assert not result.is_suspicious, \
            f"Short text with tiny zip limit should pass, got: {result.reason}"

    def test_utf8_character_split_across_feed_calls(self):
        """A multi-byte UTF-8 character split across feed calls must not be a
        false positive.

        When a byte stream is delivered in chunks, a character boundary can
        fall inside a multi-byte sequence. The trailing incomplete sequence
        must be treated as pending, not as binary data.
        """
        # 'é' = U+00E9 = b'\xc3\xa9', split between two feeds
        checker = EntropyChecker()
        result1 = checker.feed(b"caf\xc3")
        assert not result1.is_suspicious, \
            f"Feed ending mid-character should not be suspicious: {result1.reason}"

        result2 = checker.feed(b"\xa9 \xf0\x9f")
        assert not result2.is_suspicious, \
            f"Continuation feed should not be suspicious: {result2.reason}"

        result3 = checker.feed(b"\x98\x80 done")
        assert not result3.is_suspicious, \
            f"Completed stream should not be suspicious: {result3.reason}"
        assert checker.get_output() == "café 😀 done"

    def test_many_unicode_characters_pass(self):
        """Text with many distinct Unicode characters must pass.

        The default unique-byte threshold (150) must accommodate real text
        whose window contains many distinct UTF-8 byte values (CJK, emoji),
        otherwise legitimate files would be flagged as random.
        """
        # 200 distinct CJK characters (3 UTF-8 bytes each) + emoji
        text = "".join(chr(0x4E00 + i) for i in range(200)) + " ".join(
            ["😀", "🌍", "🚀", "🎉"]
        ) * 20
        checker = EntropyChecker()
        result = checker.feed(text)

        assert not result.is_suspicious, \
            f"Unicode-heavy text should pass, got: {result.reason}"


class TestFeedInvalidInput:
    """Test feed method with invalid (suspicious) input."""

    def test_random_bytes_fails(self):
        """Test that random bytes fail entropy check."""
        checker = EntropyChecker()
        data = os.urandom(10000)  # Random bytes
        
        result = checker.feed(data)
        
        assert result.is_suspicious, "Random bytes should be suspicious"
        assert result.reason is not None, "Should have reason for failure"

    def test_binary_file_header_fails(self):
        """Test that binary file headers fail entropy check."""
        # PNG header: \x89PNG\r\n\x1a\n
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        
        checker = EntropyChecker()
        result = checker.feed(png_header)
        
        assert result.is_suspicious, "PNG header should be suspicious"

    def test_zip_archive_fails(self):
        """Test that ZIP archive data fails entropy check."""
        # ZIP local file header starts with PK\x03\x04
        zip_header = b"PK\x03\x04" + b"\x00" * 100
        
        checker = EntropyChecker()
        result = checker.feed(zip_header)
        
        assert result.is_suspicious, "ZIP header should be suspicious"

    def test_null_bytes_fail(self):
        """Test that null bytes fail entropy check."""
        checker = EntropyChecker()
        data = b"\x00" * 1000
        
        result = checker.feed(data)
        
        assert result.is_suspicious, "Null bytes should be suspicious"

    def test_mixed_content_eventually_fails(self):
        """Test that mixed content with enough binary fails."""
        checker = EntropyChecker()
        
        # Start with valid text
        checker.feed("Valid text " * 100)
        
        # Add random bytes to push over threshold
        result = checker.feed(os.urandom(5000))
        
        assert result.is_suspicious, "Mixed content with enough binary should fail"

    def test_compression_test_triggers(self):
        """Test that compression test activates at size limit."""
        # Use small zip_size_limit for testing
        checker = EntropyChecker(zip_size_limit=1000)
        
        # Feed random data to trigger compression check
        data = os.urandom(2000)
        result = checker.feed(data)
        
        assert result.is_suspicious, "Random data exceeding zip limit should fail"

    def test_compression_test_passes_for_text(self):
        """Test that text passes compression check."""
        # Use small zip_size_limit for testing
        checker = EntropyChecker(zip_size_limit=1000)
        
        # Feed repetitive text (compressible)
        data = "a" * 2000
        
        result = checker.feed(data)
        
        assert not result.is_suspicious, "Repetitive text should pass compression check"

    def test_safe_ratio_threshold_enforced(self):
        """Test that safe ratio threshold is enforced."""
        # Use strict threshold for testing
        checker = EntropyChecker(safe_ratio_threshold=0.99)
        
        # Feed data with some control bytes (below 99% safe)
        mixed_data = b"Hello\x00World\x01!" * 50
        
        result = checker.feed(mixed_data)
        
        assert result.is_suspicious, "Data below safe ratio threshold should fail"

    def test_unique_byte_threshold_enforced(self):
        """Test that unique byte threshold is enforced."""
        # Use strict threshold for testing
        checker = EntropyChecker(unique_byte_threshold=10)
        
        # Feed data with many unique bytes
        data = bytes(range(256))  # All 256 byte values
        
        result = checker.feed(data)
        
        assert result.is_suspicious, "Data exceeding unique byte threshold should fail"


class TestReset:
    """Test reset method for state cleanup."""

    def test_reset_clears_window(self):
        """Test that reset clears the sliding window."""
        checker = EntropyChecker()
        checker.feed("Hello World!")
        
        assert len(checker._window) > 0, "Window should have data after feed"
        
        checker.reset()
        
        assert len(checker._window) == 0, "Window should be empty after reset"

    def test_reset_clears_accumulated(self):
        """Test that reset clears accumulated data."""
        checker = EntropyChecker()
        checker.feed("Hello World!")
        
        assert checker._total_bytes > 0, "Total bytes should be set after feed"
        
        checker.reset()
        
        assert checker._total_bytes == 0, "Total bytes should be 0 after reset"

    def test_reset_clears_refusal_flag(self):
        """Test that reset clears refusal state."""
        checker = EntropyChecker()
        
        # Make it fail first
        checker.feed(os.urandom(1000))
        assert checker._refused, "Should be refused after random data"
        
        # Reset should clear this
        checker.reset()
        assert not checker._refused, "Should not be refused after reset"

    def test_reset_clears_utf8_sequence(self):
        """Test that reset clears UTF-8 sequence tracking."""
        checker = EntropyChecker()
        
        # Feed some data with potential UTF-8 sequences
        checker.feed("Hello 世界")  # Contains multibyte characters
        
        # Reset should clear the sequence tracking
        checker.reset()
        assert len(checker._utf8_sequence) == 0, "UTF-8 sequence should be cleared"

    def test_reset_allows_new_input(self):
        """Test that reset allows new input to be checked."""
        checker = EntropyChecker(zip_size_limit=100)  # Small limit
        
        # First file: random bytes (should fail)
        result1 = checker.feed(os.urandom(200))
        assert result1.is_suspicious, "First feed should fail"
        
        # Reset for new file
        checker.reset()
        
        # Second file: valid text (should pass)
        result2 = checker.feed("Valid text")
        assert not result2.is_suspicious, "Second feed after reset should pass"

    def test_reset_is_idempotent(self):
        """Test that multiple resets don't cause issues."""
        checker = EntropyChecker()
        
        # Reset multiple times
        for _ in range(5):
            checker.reset()
        
        # Should still work normally after multiple resets
        result = checker.feed("test")
        assert not result.is_suspicious, "Should work after multiple resets"


class TestGetOutput:
    """Test get_output method."""

    def test_get_output_returns_text(self):
        """Test that get_output returns the input as string."""
        checker = EntropyChecker()
        data = "Hello World!"
        
        checker.feed(data)
        
        output = checker.get_output()
        assert output == data, f"Expected '{data}', got '{output}'"

    def test_get_output_after_reset(self):
        """Test that get_output is empty after reset."""
        checker = EntropyChecker()
        checker.feed("Hello")
        checker.reset()
        
        output = checker.get_output()
        assert output == "", "Output should be empty after reset"

    def test_get_output_concatenates_blocks(self):
        """Test that get_output concatenates multiple feed calls."""
        checker = EntropyChecker()
        checker.feed("Hello ")
        checker.feed("World")
        
        output = checker.get_output()
        assert output == "Hello World", f"Expected 'Hello World', got '{output}'"

    def test_get_output_returns_empty_for_refused(self):
        """Test that get_output returns empty string for refused data."""
        checker = EntropyChecker()
        
        # Feed suspicious data to trigger refusal
        checker.feed(os.urandom(1000))
        
        output = checker.get_output()
        assert output == "", "Output should be empty for refused data"

    def test_get_output_handles_unicode(self):
        """Test that get_output handles Unicode correctly."""
        checker = EntropyChecker()
        data = "Hello 世界 🌍"
        
        checker.feed(data)
        
        output = checker.get_output()
        assert output == data, f"Expected '{data}', got '{output}'"

    def test_get_output_with_bytes_input(self):
        """Test that get_output works with bytes input."""
        checker = EntropyChecker()
        data = b"Hello World!"
        
        checker.feed(data)
        
        output = checker.get_output()
        assert isinstance(output, str), "Output should be string"
        assert output == "Hello World!", f"Expected 'Hello World!', got '{output}'"


class TestEntropyCheckResult:
    """Test EntropyCheckResult dataclass."""

    def test_result_creation(self):
        """Test creating EntropyCheckResult instance."""
        result = EntropyCheckResult(
            is_suspicious=True,
            reason="Test failure",
            bytes_processed=100
        )
        
        assert result.is_suspicious == True
        assert result.reason == "Test failure"
        assert result.bytes_processed == 100

    def test_result_defaults(self):
        """Test default values for EntropyCheckResult."""
        result = EntropyCheckResult(is_suspicious=False)
        
        assert result.is_suspicious == False
        assert result.reason is None, "Default reason should be None"
        assert result.bytes_processed == 0, "Default bytes_processed should be 0"

    def test_result_is_dataclass(self):
        """Test that EntropyCheckResult is a dataclass."""
        import dataclasses
        
        assert dataclasses.is_dataclass(EntropyCheckResult), \
            "EntropyCheckResult should be a dataclass"
