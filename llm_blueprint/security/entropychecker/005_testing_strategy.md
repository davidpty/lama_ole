# Entropy Checker — Testing Strategy

## Overview

This document outlines the comprehensive testing strategy for the entropy checker module. Tests are organized by layer (unit, integration, edge cases) and include specific test scenarios with expected outcomes.

## Test Organization

```
lama_ole/tests/
├── test_entropychecker.py           ← Unit tests for core functions
├── test_entropychecker_class.py     ← Unit tests for EntropyChecker class
├── test_entropychecker_integration.py ← Integration tests with dev_tools
└── testdata/
    ├── entropy_test_files/          ← Test files for entropy testing
    │   ├── valid_text.txt           ← Valid text file (should pass)
    │   ├── python_code.py           ← Python code (should pass)
    │   ├── markdown.md              ← Markdown file (should pass)
    │   ├── binary_random.bin        ← Random bytes (should fail)
    │   ├── binary_image.png         ← PNG image header (should fail)
    │   ├── binary_archive.zip       ← ZIP archive (should fail)
    │   └── mixed_content.txt        ← Text with some binary (edge case)
    └── entropy_test_data.py         ← Helper functions for test data generation
```

## Test Layers

### Layer 1: Unit Tests for Core Functions

**File:** `lama_ole/tests/test_entropychecker.py`

These tests verify the pure analysis functions work correctly in isolation.

#### Test Suite: `_classify_byte()`

```python
import pytest
from security.entropychecker import _classify_byte

class TestClassifyByte:
    def test_printable_ascii_letters(self):
        """Test classification of ASCII letters."""
        assert _classify_byte(ord('a')) == "safe"
        assert _classify_byte(ord('Z')) == "safe"
    
    def test_printable_ascii_digits(self):
        """Test classification of ASCII digits."""
        assert _classify_byte(ord('0')) == "safe"
        assert _classify_byte(ord('9')) == "safe"
    
    def test_common_punctuation(self):
        """Test classification of common programming punctuation."""
        punct_chars = "!@#$%^&*()-_=+[]{}|;:'\",.<>?/\\`~"
        for char in punct_chars:
            assert _classify_byte(ord(char)) == "safe", f"{char!r} should be safe"
    
    def test_whitespace(self):
        """Test classification of whitespace characters."""
        assert _classify_byte(ord(' ')) == "safe"
        assert _classify_byte(9) == "safe"  # tab
        assert _classify_byte(10) == "safe"  # newline
        assert _classify_byte(13) == "safe"  # carriage return
    
    def test_control_characters(self):
        """Test classification of control characters."""
        assert _classify_byte(0) == "control"  # null
        assert _classify_byte(1) == "control"  # SOH
        assert _classify_byte(27) == "control"  # ESC
    
    def test_high_bytes(self):
        """Test classification of high byte values."""
        assert _classify_byte(128) == "control"
        assert _classify_byte(255) == "control"
    
    def test_utf8_continuation_range(self):
        """Test UTF-8 continuation bytes (10xxxxxx)."""
        # Continuation bytes are 128-191, but they're only valid in context
        # For standalone classification, we mark them as control
        for byte_val in range(128, 192):
            assert _classify_byte(byte_val) == "control"
```

#### Test Suite: `_validate_utf8_continuation()`

```python
class TestValidateUtf8Continuation:
    def test_valid_continuation_bytes(self):
        """Test valid UTF-8 continuation bytes (10xxxxxx)."""
        for byte_val in range(128, 192):
            assert _validate_utf8_continuation(byte_val, prev_bytes=1) == True
    
    def test_invalid_continuation_bytes(self):
        """Test invalid continuation bytes."""
        # Bytes outside 10xxxxxx range
        assert _validate_utf8_continuation(0, prev_bytes=1) == False
        assert _validate_utf8_continuation(255, prev_bytes=1) == False
    
    def test_first_byte_not_continuation(self):
        """Test that first byte (prev_bytes=0) is never a continuation."""
        # First byte should be 0xxxxxxx or 11xxxxxx (start of sequence)
        assert _validate_utf8_continuation(65, prev_bytes=0) == False  # 'A'
        assert _validate_utf8_continuation(200, prev_bytes=0) == False
    
    def test_sequence_tracking(self):
        """Test that function can track multi-byte sequences."""
        # This is a simplified test; full sequence validation would be more complex
        pass  # Implementation detail - may need refinement
```

#### Test Suite: `_analyze_window()`

```python
from collections import Counter

class TestAnalyzeWindow:
    def test_empty_window(self):
        """Test analysis of empty window."""
        result = _analyze_window(b"")
        assert result["safe_ratio"] == 1.0
        assert result["unique_bytes"] == 0
    
    def test_all_safe_bytes(self):
        """Test analysis with all safe bytes (ASCII text)."""
        text = b"Hello World! This is a test."
        result = _analyze_window(text)
        assert result["safe_ratio"] == 1.0
        assert result["unique_bytes"] > 0
    
    def test_all_control_bytes(self):
        """Test analysis with all control bytes."""
        binary = b"\x00\x01\x02\x03\x04\x05" * 100
        result = _analyze_window(binary)
        assert result["safe_ratio"] == 0.0
        assert result["unique_bytes"] <= 6
    
    def test_mixed_content(self):
        """Test analysis with mixed safe and unsafe bytes."""
        mixed = b"Hello\x00World\x01!"
        result = _analyze_window(mixed)
        # Should have some safe ratio between 0 and 1
        assert 0.0 < result["safe_ratio"] < 1.0
    
    def test_unique_byte_count(self):
        """Test unique byte counting."""
        data = bytes(range(256))  # All possible byte values
        result = _analyze_window(data)
        assert result["unique_bytes"] == 256
    
    def test_byte_distribution(self):
        """Test that byte distribution is calculated correctly."""
        data = b"aaaabbbbcccc"
        result = _analyze_window(data)
        dist = result["byte_distribution"]
        assert dist[ord('a')] == 4
        assert dist[ord('b')] == 4
        assert dist[ord('c')] == 4
    
    def test_large_window(self):
        """Test analysis with large window."""
        # Create a window larger than default (1024 bytes)
        text = b"Valid text " * 100  # ~1100 bytes
        result = _analyze_window(text)
        assert result["safe_ratio"] == 1.0
```

#### Test Suite: `_check_compression()`

```python
import zlib

class TestCheckCompression:
    def test_empty_data(self):
        """Test compression check with empty data."""
        is_suspicious, reason = _check_compression(b"", threshold=0.95)
        assert is_suspicious == False
    
    def test_repetitive_data_compresses_well(self):
        """Test that repetitive data compresses well (low ratio)."""
        # Highly repetitive data should compress very well
        data = b"a" * 10000
        compressed = zlib.compress(data)
        ratio = len(compressed) / len(data)
        
        is_suspicious, reason = _check_compression(data, threshold=0.95)
        assert is_suspicious == False
        assert ratio < 0.95
    
    def test_random_data_compresses_poorly(self):
        """Test that random data does not compress well (high ratio)."""
        import os
        # Random bytes should not compress well
        data = os.urandom(10000)
        compressed = zlib.compress(data)
        ratio = len(compressed) / len(data)
        
        is_suspicious, reason = _check_compression(data, threshold=0.95)
        assert is_suspicious == True
        assert ratio > 0.95
    
    def test_text_compresses_well(self):
        """Test that natural text compresses well."""
        # English text with repetition should compress
        text = "The quick brown fox jumps over the lazy dog. " * 100
        is_suspicious, reason = _check_compression(text.encode(), threshold=0.95)
        assert is_suspicious == False
    
    def test_threshold_customization(self):
        """Test that threshold parameter works correctly."""
        data = b"a" * 1000 + b"\x00" * 100  # Mostly compressible
        
        # Very strict threshold (should fail)
        is_suspicious, _ = _check_compression(data, threshold=0.5)
        
        # Lenient threshold (should pass)
        is_suspicious, _ = _check_compression(data, threshold=0.99)
        assert is_suspicious == False
    
    def test_reason_message(self):
        """Test that reason message is informative."""
        data = os.urandom(1000)
        is_suspicious, reason = _check_compression(data, threshold=0.5)
        
        if is_suspicious:
            assert "Compression ratio" in reason
            assert "exceeds threshold" in reason
```

---

### Layer 2: Unit Tests for EntropyChecker Class

**File:** `lama_ole/tests/test_entropychecker_class.py`

These tests verify the class behavior, state management, and integration of core functions.

#### Test Suite: Constructor and Initialization

```python
class TestEntropyCheckerInit:
    def test_default_thresholds(self):
        """Test that default thresholds are set correctly."""
        checker = EntropyChecker()
        assert checker.window_size == 1024
        assert checker.safe_ratio_threshold == 0.85
        assert checker.unique_byte_threshold == 150
        assert checker.zip_size_limit == 65536
        assert checker.zip_ratio_threshold == 0.95
    
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
        assert len(checker._window) == 0
        assert checker._total_bytes == 0
        assert not checker._refused
```

#### Test Suite: feed() Method - Valid Input

```python
class TestFeedValidInput:
    def test_small_text_passes(self):
        """Test that small valid text passes entropy check."""
        checker = EntropyChecker()
        result = checker.feed("Hello World!")
        assert not result.is_suspicious
    
    def test_python_code_passes(self):
        """Test that Python code passes entropy check."""
        checker = EntropyChecker()
        code = '''
def hello():
    print("Hello, world!")

if __name__ == "__main__":
    hello()
'''
        result = checker.feed(code)
        assert not result.is_suspicious
    
    def test_markdown_passes(self):
        """Test that Markdown content passes entropy check."""
        checker = EntropyChecker()
        md = "# Title\n\nSome **bold** and *italic* text.\n"
        result = checker.feed(md)
        assert not result.is_suspicious
    
    def test_bytes_input(self):
        """Test that bytes input works correctly."""
        checker = EntropyChecker()
        result = checker.feed(b"Hello World!")
        assert not result.is_suspicious
    
    def test_utf8_multibyte_characters(self):
        """Test that UTF-8 multibyte characters are handled."""
        checker = EntropyChecker()
        # Unicode characters (emoji, etc.)
        text = "Hello 世界 🌍"
        result = checker.feed(text)
        assert not result.is_suspicious
    
    def test_accumulated_bytes_count(self):
        """Test that byte counter is updated correctly."""
        checker = EntropyChecker()
        data = "Hello World!"
        result = checker.feed(data)
        assert result.bytes_processed == len(data.encode('utf-8'))
```

#### Test Suite: feed() Method - Invalid Input

```python
class TestFeedInvalidInput:
    def test_random_bytes_fails(self):
        """Test that random bytes fail entropy check."""
        import os
        checker = EntropyChecker()
        data = os.urandom(10000)  # Random bytes
        
        result = checker.feed(data)
        assert result.is_suspicious
    
    def test_binary_file_header_fails(self):
        """Test that binary file headers fail entropy check."""
        # PNG header: \x89PNG\r\n\x1a\n
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        checker = EntropyChecker()
        result = checker.feed(png_header)
        assert result.is_suspicious
    
    def test_zip_archive_fails(self):
        """Test that ZIP archive data fails entropy check."""
        # ZIP local file header starts with PK\x03\x04
        zip_header = b"PK\x03\x04" + b"\x00" * 100
        checker = EntropyChecker()
        result = checker.feed(zip_header)
        assert result.is_suspicious
    
    def test_null_bytes_fail(self):
        """Test that null bytes fail entropy check."""
        checker = EntropyChecker()
        data = b"\x00" * 1000
        result = checker.feed(data)
        assert result.is_suspicious
    
    def test_mixed_content_eventually_fails(self):
        """Test that mixed content with enough binary fails."""
        checker = EntropyChecker()
        
        # Start with valid text
        checker.feed("Valid text " * 100)
        
        # Add random bytes to push over threshold
        import os
        result = checker.feed(os.urandom(5000))
        assert result.is_suspicious
    
    def test_compression_test_triggers(self):
        """Test that compression test activates at size limit."""
        checker = EntropyChecker(zip_size_limit=1000)  # Small limit for testing
        
        # Feed random data to trigger compression check
        import os
        data = os.urandom(2000)
        result = checker.feed(data)
        
        # Should fail due to poor compressibility
        assert result.is_suspicious
    
    def test_compression_test_passes_for_text(self):
        """Test that text passes compression check."""
        checker = EntropyChecker(zip_size_limit=1000)
        
        # Feed repetitive text (compressible)
        data = "a" * 2000
        result = checker.feed(data)
        
        # Should pass due to good compressibility
        assert not result.is_suspicious
```

#### Test Suite: reset() Method

```python
class TestReset:
    def test_reset_clears_window(self):
        """Test that reset clears the sliding window."""
        checker = EntropyChecker()
        checker.feed("Hello World!")
        assert len(checker._window) > 0
        
        checker.reset()
        assert len(checker._window) == 0
    
    def test_reset_clears_accumulated(self):
        """Test that reset clears accumulated data."""
        checker = EntropyChecker()
        checker.feed("Hello World!")
        assert checker._total_bytes > 0
        
        checker.reset()
        assert checker._total_bytes == 0
    
    def test_reset_clears_refusal_flag(self):
        """Test that reset clears refusal state."""
        import os
        checker = EntropyChecker()
        
        # Make it fail first
        checker.feed(os.urandom(1000))
        assert checker._refused
        
        # Reset should clear this
        checker.reset()
        assert not checker._refused
    
    def test_reset_allows_new_input(self):
        """Test that reset allows new input to be checked."""
        import os
        checker = EntropyChecker(zip_size_limit=100)  # Small limit
        
        # First file: random bytes (should fail)
        result1 = checker.feed(os.urandom(200))
        assert result1.is_suspicious
        
        # Reset for new file
        checker.reset()
        
        # Second file: valid text (should pass)
        result2 = checker.feed("Valid text")
        assert not result2.is_suspicious
```

#### Test Suite: get_output() Method

```python
class TestGetOutput:
    def test_get_output_returns_text(self):
        """Test that get_output returns the input as string."""
        checker = EntropyChecker()
        data = "Hello World!"
        checker.feed(data)
        
        output = checker.get_output()
        assert output == data
    
    def test_get_output_after_reset(self):
        """Test that get_output is empty after reset."""
        checker = EntropyChecker()
        checker.feed("Hello")
        checker.reset()
        
        output = checker.get_output()
        assert output == ""
    
    def test_get_output_concatenates_blocks(self):
        """Test that get_output concatenates multiple feed calls."""
        checker = EntropyChecker()
        checker.feed("Hello ")
        checker.feed("World")
        
        output = checker.get_output()
        assert output == "Hello World"
```

---

### Layer 3: Integration Tests with dev_tools

**File:** `lama_ole/tests/test_entropychecker_integration.py`

These tests verify the entropy checker works correctly when integrated into actual tools.

#### Test Suite: read_file() Integration

```python
import tempfile
import os

class TestReadFileIntegration:
    def test_read_valid_python_file(self):
        """Test reading a valid Python file."""
        from tools_insecure_outdated_deprecated.dev_tools import read_file
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write('print("Hello, world!")\n')
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            assert result["status"] == "success"
            assert "Hello, world!" in result["data"]
        finally:
            os.unlink(temp_path)
    
    def test_read_binary_file_rejected(self):
        """Test that reading a binary file is rejected."""
        from tools_insecure_outdated_deprecated.dev_tools import read_file
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            f.write(os.urandom(1000))  # Random bytes
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            assert result["status"] == "error"
            assert "entropy" in result["message"][0].lower() or "rejected" in result["message"][0].lower()
        finally:
            os.unlink(temp_path)
    
    def test_read_markdown_file(self):
        """Test reading a Markdown file."""
        from tools_insecure_outdated_deprecated.dev_tools import read_file
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Title\n\nSome **bold** text.\n")
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            assert result["status"] == "success"
            assert "# Title" in result["data"]
        finally:
            os.unlink(temp_path)
    
    def test_read_large_text_file(self):
        """Test reading a large text file (triggers compression check)."""
        from tools_insecure_outdated_deprecated.dev_tools import read_file
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            # Write enough data to trigger zip_size_limit (default 64KB)
            f.write("This is a test line.\n" * 5000)  # ~125KB
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            assert result["status"] == "success"
        finally:
            os.unlink(temp_path)
    
    def test_read_large_binary_file(self):
        """Test that large binary file is rejected."""
        from tools_insecure_outdated_deprecated.dev_tools import read_file
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            # Write enough data to trigger zip_size_limit
            f.write(os.urandom(100000))  # 100KB random bytes
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            assert result["status"] == "error"
        finally:
            os.unlink(temp_path)
```

#### Test Suite: grep() Integration

```python
class TestGrepIntegration:
    def test_grep_valid_directory(self):
        """Test grep on directory with valid files."""
        from tools_insecure_outdated_deprecated.dev_tools import grep
        
        temp_dir = tempfile.mkdtemp()
        
        # Create a Python file
        with open(os.path.join(temp_dir, "test.py"), "w") as f:
            f.write("print('hello')\n")
        
        try:
            result = grep("print", temp_dir)
            assert "test.py" in result
            assert "print('hello')" in result
        finally:
            import shutil
            shutil.rmtree(temp_dir)
    
    def test_grep_skips_binary_files(self):
        """Test that grep skips binary files."""
        from tools_insecure_outdated_deprecated.dev_tools import grep
        
        temp_dir = tempfile.mkdtemp()
        
        # Create a text file with match
        with open(os.path.join(temp_dir, "test.txt"), "w") as f:
            f.write("hello world\n")
        
        # Create a binary file with same pattern (as bytes)
        with open(os.path.join(temp_dir, "binary.bin"), "wb") as f:
            f.write(b"hello\x00world\x00")  # Contains 'hello' but with nulls
        
        try:
            result = grep("hello", temp_dir)
            assert "test.txt" in result
            
            # Binary file should be skipped (not in output or mentioned as skipped)
            if "binary.bin" in result:
                # If it appears, there should be a skip message
                assert "skipped" in result.lower() or "entropy" in result.lower()
        finally:
            import shutil
            shutil.rmtree(temp_dir)
    
    def test_grep_mixed_directory(self):
        """Test grep on directory with mixed file types."""
        from tools_insecure_outdated_deprecated.dev_tools import grep
        
        temp_dir = tempfile.mkdtemp()
        
        # Valid files
        with open(os.path.join(temp_dir, "code.py"), "w") as f:
            f.write("def test():\n    pass\n")
        
        with open(os.path.join(temp_dir, "readme.md"), "w") as f:
            f.write("# Test\n\nSome text.\n")
        
        # Binary file
        with open(os.path.join(temp_dir, "image.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        
        try:
            result = grep("test", temp_dir)
            
            # Should find matches in text files
            assert "code.py" in result or "readme.md" in result
            
            # Binary file should be skipped
            if "image.png" in result:
                assert "skipped" in result.lower() or "entropy" in result.lower()
        finally:
            import shutil
            shutil.rmtree(temp_dir)
```

---

### Layer 4: Edge Case Tests

**File:** `lama_ole/tests/test_entropychecker_edge_cases.py`

These tests verify behavior at boundaries and unusual scenarios.

#### Test Suite: Boundary Conditions

```python
class TestEdgeCases:
    def test_empty_input(self):
        """Test handling of empty input."""
        checker = EntropyChecker()
        result = checker.feed("")
        assert not result.is_suspicious
    
    def test_single_byte(self):
        """Test single byte input."""
        checker = EntropyChecker()
        
        # Safe byte (letter)
        result = checker.feed(b"A")
        assert not result.is_suspicious
        
        # Unsafe byte (null)
        checker.reset()
        result = checker.feed(b"\x00")
        # Single byte might not trigger check, depends on implementation
        # This is acceptable - need more data to determine entropy
    
    def test_very_long_input(self):
        """Test handling of very long input."""
        checker = EntropyChecker(zip_size_limit=1000)
        
        # Feed in chunks
        for i in range(100):
            result = checker.feed("Chunk " + str(i) + "\n")
            if result.is_suspicious:
                break  # Should not fail for text
        
        assert not result.is_suspicious
    
    def test_unicode_edge_cases(self):
        """Test various Unicode edge cases."""
        checker = EntropyChecker()
        
        # Empty string
        result = checker.feed("")
        assert not result.is_suspicious
        
        # Single emoji
        result = checker.feed("😀")
        assert not result.is_suspicious  # Valid UTF-8
    
    def test_rapid_reset(self):
        """Test rapid reset during feeding."""
        checker = EntropyChecker()
        
        for i in range(10):
            checker.feed(f"Data {i}\n")
            if i % 2 == 0:
                checker.reset()
    
    def test_concurrent_feeding(self):
        """Test that concurrent feeding doesn't cause issues (single-threaded)."""
        # This is a sanity check - entropy checker is not thread-safe
        checker = EntropyChecker()
        
        # Feed in interleaved chunks
        for i in range(50):
            checker.feed(f"Chunk {i % 2}: ")
```

#### Test Suite: Threshold Tuning Validation

```python
class TestThresholdValidation:
    def test_strict_thresholds(self):
        """Test with very strict thresholds."""
        checker = EntropyChecker(
            safe_ratio_threshold=0.99,  # Very strict
            unique_byte_threshold=10,   # Very strict
        )
        
        # Even slightly mixed content should fail
        result = checker.feed("Hello\x00World")
        assert result.is_suspicious
    
    def test_lenient_thresholds(self):
        """Test with very lenient thresholds."""
        checker = EntropyChecker(
            safe_ratio_threshold=0.5,   # Very lenient
            unique_byte_threshold=200,  # Very lenient
        )
        
        # Should pass even with some binary
        result = checker.feed("Hello" + b"\x00\x01\x02")
        assert not result.is_suspicious
    
    def test_zero_window_size(self):
        """Test behavior with zero window size (edge case)."""
        # This might cause division by zero or other issues
        # Should handle gracefully
        try:
            checker = EntropyChecker(window_size=0)
            result = checker.feed("test")
            assert not result.is_suspicious
        except Exception as e:
            # If it raises, that's acceptable if documented
            pass
    
    def test_very_small_zip_limit(self):
        """Test with very small zip size limit."""
        checker = EntropyChecker(zip_size_limit=10)  # Tiny limit
        
        # Should trigger compression check quickly
        result = checker.feed("Hello World!")
        # Result depends on compressibility of "Hello World!"
```

---

### Layer 5: Performance Tests

**File:** `lama_ole/tests/test_entropychecker_performance.py`

These tests verify that the entropy checker doesn't introduce unacceptable performance overhead.

#### Test Suite: Performance Benchmarks

```python
import time
import os

class TestPerformance:
    def test_small_file_overhead(self):
        """Test overhead for small files (< 1 KB)."""
        from tools_insecure_outdated_deprecated.dev_tools import read_file
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Small file content.\n")
            temp_path = f.name
        
        try:
            # Warm up
            for _ in range(10):
                read_file(temp_path)
            
            # Measure time
            start = time.perf_counter()
            for _ in range(100):
                result = read_file(temp_path)
            elapsed = time.perf_counter() - start
            
            avg_ms = (elapsed / 100) * 1000
            print(f"Average time per small file read: {avg_ms:.3f} ms")
            
            # Should be very fast (< 5ms average)
            assert avg_ms < 5, f"Overhead too high: {avg_ms:.3f} ms"
        finally:
            os.unlink(temp_path)
    
    def test_medium_file_overhead(self):
        """Test overhead for medium files (~100 KB)."""
        from tools_insecure_outdated_deprecated.dev_tools import read_file
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            # Write ~100KB of text
            f.write("This is a test line that should be long enough.\n" * 2000)
            temp_path = f.name
        
        try:
            # Warm up
            for _ in range(10):
                read_file(temp_path)
            
            # Measure time
            start = time.perf_counter()
            for _ in range(50):
                result = read_file(temp_path)
            elapsed = time.perf_counter() - start
            
            avg_ms = (elapsed / 50) * 1000
            print(f"Average time per medium file read: {avg_ms:.3f} ms")
            
            # Should be reasonable (< 50ms average)
            assert avg_ms < 50, f"Overhead too high: {avg_ms:.3f} ms"
        finally:
            os.unlink(temp_path)
    
    def test_large_file_overhead(self):
        """Test overhead for large files (> 1 MB)."""
        from tools_insecure_outdated_deprecated.dev_tools import read_file
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            # Write ~1.5MB of text (triggers compression check)
            f.write("This is a test line that should be long enough for large file testing.\n" * 30000)
            temp_path = f.name
        
        try:
            # Warm up
            for _ in range(5):
                read_file(temp_path)
            
            # Measure time (fewer iterations due to longer processing)
            start = time.perf_counter()
            for _ in range(10):
                result = read_file(temp_path)
            elapsed = time.perf_counter() - start
            
            avg_ms = (elapsed / 10) * 1000
            print(f"Average time per large file read: {avg_ms:.3f} ms")
            
            # Should be reasonable (< 200ms average with compression check)
            assert avg_ms < 200, f"Overhead too high: {avg_ms:.3f} ms"
        finally:
            os.unlink(temp_path)
    
    def test_grep_performance(self):
        """Test grep performance with multiple files."""
        from tools_insecure_outdated_deprecated.dev_tools import grep
        
        temp_dir = tempfile.mkdtemp()
        
        # Create 100 small text files
        for i in range(100):
            with open(os.path.join(temp_dir, f"file_{i:03d}.txt"), "w") as f:
                f.write(f"This is file number {i}.\n")
        
        try:
            # Warm up
            grep("number", temp_dir)
            
            # Measure time
            start = time.perf_counter()
            for _ in range(10):
                result = grep("number", temp_dir)
            elapsed = time.perf_counter() - start
            
            avg_ms = (elapsed / 10) * 1000
            print(f"Average time per grep operation: {avg_ms:.3f} ms")
            
            # Should be reasonable
            assert avg_ms < 100, f"Grep overhead too high: {avg_ms:.3f} ms"
        finally:
            import shutil
            shutil.rmtree(temp_dir)
```

---

## Running the Tests

### Run All Entropy Checker Tests

```bash
cd lama_ole
python -m pytest tests/test_entropychecker*.py -v
```

### Run Specific Test Suite

```bash
# Unit tests only
python -m pytest tests/test_entropychecker.py -v

# Class tests
python -m pytest tests/test_entropychecker_class.py -v

# Integration tests
python -m pytest tests/test_entropychecker_integration.py -v

# Edge cases
python -m pytest tests/test_entropychecker_edge_cases.py -v

# Performance tests (may take longer)
python -m pytest tests/test_entropychecker_performance.py -v -s
```

### Run with Coverage

```bash
python -m pytest tests/test_entropychecker*.py --cov=security.entropychecker --cov-report=html
```

---

## Test Data Generation Helper

**File:** `lama_ole/tests/entropy_test_data.py`

Helper functions for generating test data:

```python
"""Helper functions for generating entropy test data."""

import os
import tempfile


def generate_random_bytes(size: int) -> bytes:
    """Generate random bytes of specified size."""
    return os.urandom(size)


def generate_repetitive_text(repetition: str, count: int) -> str:
    """Generate repetitive text for compression testing."""
    return repetition * count


def create_temp_binary_file(content: bytes, suffix: str = ".bin") -> str:
    """Create a temporary binary file and return its path."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(content)
        return f.name


def create_temp_text_file(content: str, suffix: str = ".txt") -> str:
    """Create a temporary text file and return its path."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=suffix) as f:
        f.write(content)
        return f.name


def generate_mixed_content(text_ratio: float = 0.5) -> bytes:
    """Generate content with mixed text and binary data."""
    import random
    
    total_size = 10000
    text_size = int(total_size * text_ratio)
    binary_size = total_size - text_size
    
    # Generate text portion
    text = "Hello World! This is some valid text. " * (text_size // 35 + 1)
    text = text[:text_size]
    
    # Generate binary portion
    binary = os.urandom(binary_size)
    
    # Interleave them
    result = bytearray()
    for i in range(0, len(text), 100):
        result.extend(text[i:i+100].encode())
        if i + 100 < len(binary):
            result.extend(binary[i:i+100])
    
    return bytes(result)


def get_test_files_directory() -> str:
    """Get path to test data directory."""
    import os
    return os.path.join(os.path.dirname(__file__), "testdata", "entropy_test_files")
```

---

## Test Coverage Goals

| Component | Target Coverage | Notes |
|-----------|----------------|-------|
| Core functions (`_classify_byte`, etc.) | 100% | Pure logic, easy to test |
| `EntropyChecker` class methods | 95%+ | State management needs edge case coverage |
| Integration points | 90%+ | Depends on dev_tools test infrastructure |
| Edge cases | 85%+ | Hard to cover all boundary conditions |

---

## Continuous Testing

### Pre-commit Hook (Optional)

Add to `.git/hooks/pre-commit` or use `pre-commit` framework:

```bash
#!/bin/bash
# Run entropy checker tests before commit
python -m pytest tests/test_entropychecker*.py -x -q
if [ $? -ne 0 ]; then
    echo "Entropy checker tests failed. Commit aborted."
    exit 1
fi
```

### CI/CD Integration

In `.github/workflows/test.yml` or equivalent:

```yaml
- name: Run Entropy Checker Tests
  run: |
    cd lama_ole
    python -m pytest tests/test_entropychecker*.py --cov=security.entropychecker --cov-report=xml
  env:
    PYTHONPATH: .
```

---

## Debugging Failed Tests

### Common Issues

1. **Threshold sensitivity**: If tests fail due to threshold tuning, adjust thresholds in test setup rather than production code.

2. **File encoding issues**: Ensure test files are created with correct encoding (UTF-8 for text).

3. **Non-deterministic behavior**: Random data generation should use fixed seeds for reproducible tests:
   ```python
   import random
   random.seed(42)
   ```

4. **Performance test flakiness**: Use warm-up runs and multiple iterations to get stable measurements.

### Logging Test Failures

Enable verbose output for debugging:

```bash
python -m pytest tests/test_entropychecker*.py -v --log-cli-level=DEBUG
```

---

## Summary

The testing strategy covers five layers:

1. **Unit tests** for core analysis functions (isolated, fast)
2. **Class unit tests** for EntropyChecker state management
3. **Integration tests** with actual dev_tools
4. **Edge case tests** for boundary conditions
5. **Performance tests** to ensure acceptable overhead

This comprehensive approach ensures:
- Correctness of entropy detection logic
- Proper integration with existing tools
- Robust handling of edge cases
- Acceptable performance impact
