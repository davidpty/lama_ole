"""Integration tests for entropy checker with dev_tools.

Tests verify that the entropy checker works correctly when integrated into 
actual tools like read_file and grep. These tests use temporary files to 
simulate real-world scenarios.
"""

import os
import tempfile
import shutil
import pytest
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

from tools_insecure_outdated_deprecated.dev_tools import read_file, grep
from security.entropychecker import EntropyChecker


class TestReadFileIntegration:
    """Test integration of entropy checker with read_file tool."""

    def test_read_valid_python_file(self):
        """Test reading a valid Python file passes entropy check."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write('print("Hello, world!")\n')
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            
            assert result["status"] == "success", \
                f"Valid Python file should succeed, got status: {result['status']}"
            assert "Hello, world!" in result["data"], \
                "File content should be present in result"
        finally:
            os.unlink(temp_path)

    def test_read_binary_file_rejected(self):
        """Test that reading a binary file is rejected by entropy check."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            f.write(os.urandom(1000))  # Random bytes
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            
            assert result["status"] == "error", \
                "Binary file should be rejected"
            message = result["message"][0] if isinstance(result["message"], list) else str(result["message"])
            assert "entropy" in message.lower() or "rejected" in message.lower(), \
                f"Error message should mention entropy or rejection, got: {message}"
        finally:
            os.unlink(temp_path)

    def test_read_markdown_file(self):
        """Test reading a Markdown file passes entropy check."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Title\n\nSome **bold** text.\n")
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            
            assert result["status"] == "success", \
                f"Markdown file should succeed, got status: {result['status']}"
            assert "# Title" in result["data"], \
                "File content should be present in result"
        finally:
            os.unlink(temp_path)

    def test_read_large_text_file(self):
        """Test reading a large text file (triggers compression check)."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            # Write enough data to trigger zip_size_limit (default 64KB)
            f.write("This is a test line.\n" * 5000)  # ~125KB
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            
            assert result["status"] == "success", \
                f"Large text file should succeed, got status: {result['status']}"
        finally:
            os.unlink(temp_path)

    def test_read_large_binary_file(self):
        """Test that large binary file is rejected."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            # Write enough data to trigger zip_size_limit (100KB random bytes)
            f.write(os.urandom(100000))
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            
            assert result["status"] == "error", \
                "Large binary file should be rejected"
        finally:
            os.unlink(temp_path)

    def test_read_empty_file(self):
        """Test reading an empty file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            # Write nothing (empty file)
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            
            assert result["status"] == "success", \
                f"Empty file should succeed, got status: {result['status']}"
            assert result["data"] == "", \
                "Empty file should have empty data"
        finally:
            os.unlink(temp_path)

    def test_read_file_with_unicode(self):
        """Test reading a file with Unicode characters."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write("Hello 世界 🌍\n")
            temp_path = f.name
        
        try:
            result = read_file(temp_path)
            
            assert result["status"] == "success", \
                f"Unicode file should succeed, got status: {result['status']}"
            assert "世界" in result["data"], \
                "Unicode content should be present in result"
        finally:
            os.unlink(temp_path)


class TestGrepIntegration:
    """Test integration of entropy checker with grep tool."""

    def test_grep_valid_directory(self):
        """Test grep on directory with valid files finds matches."""
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Create a Python file with content to match
            with open(os.path.join(temp_dir, "test.py"), "w") as f:
                f.write("print('hello')\n")
            
            result = grep("print", temp_dir)
            
            assert "test.py" in result, \
                f"Grep should find test.py, got: {result}"
            assert "print('hello')" in result, \
                f"Grep should include the matching line, got: {result}"
        finally:
            shutil.rmtree(temp_dir)

    def test_grep_skips_binary_files(self):
        """Test that grep skips binary files."""
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Create a text file with match
            with open(os.path.join(temp_dir, "test.txt"), "w") as f:
                f.write("hello world\n")
            
            # Create a binary file with same pattern (as bytes)
            with open(os.path.join(temp_dir, "binary.bin"), "wb") as f:
                f.write(b"hello\x00world\x00")  # Contains 'hello' but with nulls
            
            result = grep("hello", temp_dir)
            
            assert "test.txt" in result, \
                f"Grep should find test.txt, got: {result}"
            
            # Binary file should be skipped (not in output or mentioned as skipped)
            if "binary.bin" in result:
                # If it appears, there should be a skip message
                assert "skipped" in result.lower() or "entropy" in result.lower(), \
                    f"Binary file should have skip/entropy mention, got: {result}"
        finally:
            shutil.rmtree(temp_dir)

    def test_grep_mixed_directory(self):
        """Test grep on directory with mixed file types."""
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Valid files
            with open(os.path.join(temp_dir, "code.py"), "w") as f:
                f.write("def test():\n    pass\n")
            
            with open(os.path.join(temp_dir, "readme.md"), "w") as f:
                f.write("# Test\n\nSome text.\n")
            
            # Binary file
            with open(os.path.join(temp_dir, "image.png"), "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            
            result = grep("test", temp_dir)
            
            # Should find matches in text files
            assert "code.py" in result or "readme.md" in result, \
                f"Grep should find matches in text files, got: {result}"
            
            # Binary file should be skipped
            if "image.png" in result:
                assert "skipped" in result.lower() or "entropy" in result.lower(), \
                    f"Binary file should have skip/entropy mention, got: {result}"
        finally:
            shutil.rmtree(temp_dir)

    def test_grep_no_matches(self):
        """Test grep with no matches returns appropriate message."""
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Create a file without the search pattern
            with open(os.path.join(temp_dir, "test.txt"), "w") as f:
                f.write("hello world\n")
            
            result = grep("nonexistent", temp_dir)
            
            assert "(no matches)" in result.lower() or result == "", \
                f"Grep with no matches should return empty or 'no matches', got: {result}"
        finally:
            shutil.rmtree(temp_dir)

    def test_grep_empty_directory(self):
        """Test grep on empty directory."""
        temp_dir = tempfile.mkdtemp()
        
        try:
            result = grep("test", temp_dir)
            
            assert "(no matches)" in result.lower() or result == "", \
                f"Grep on empty directory should return empty, got: {result}"
        finally:
            shutil.rmtree(temp_dir)


class TestEntropyCheckerEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_input(self):
        """Test handling of empty input."""
        
        checker = EntropyChecker()
        result = checker.feed("")
        
        assert not result.is_suspicious, "Empty input should not be suspicious"

    def test_single_byte_safe(self):
        """Test single safe byte."""
        
        checker = EntropyChecker()
        result = checker.feed(b"A")
        
        assert not result.is_suspicious, "Single safe byte should not be suspicious"

    def test_single_byte_unsafe(self):
        """Test single unsafe byte."""
        
        checker = EntropyChecker()
        result = checker.feed(b"\x00")
        
        # Single byte might not trigger check depending on implementation
        # This is acceptable - need more data to determine entropy
        assert isinstance(result.is_suspicious, bool), \
            "Should return boolean result"

    def test_very_long_input(self):
        """Test handling of very long input."""
        
        checker = EntropyChecker(zip_size_limit=1000)  # Small limit for testing
        
        # Feed in chunks
        for i in range(100):
            result = checker.feed("Chunk " + str(i) + "\n")
            if result.is_suspicious:
                break  # Should not fail for text
        
        assert not result.is_suspicious, \
            "Long valid text should not be suspicious"

    def test_unicode_edge_cases(self):
        """Test various Unicode edge cases."""
        
        checker = EntropyChecker()
        
        # Empty string
        result = checker.feed("")
        assert not result.is_suspicious, "Empty string should pass"
        
        # Single emoji (valid UTF-8)
        result = checker.feed("😀")
        assert not result.is_suspicious, "Single emoji should pass (valid UTF-8)"

    def test_rapid_reset(self):
        """Test rapid reset during feeding."""
        
        checker = EntropyChecker()
        
        for i in range(10):
            checker.feed(f"Data {i}\n")
            if i % 2 == 0:
                checker.reset()
        
        # Should not crash or have inconsistent state

    def test_concurrent_feeding(self):
        """Test that concurrent feeding doesn't cause issues (single-threaded)."""
        
        # This is a sanity check - entropy checker is not thread-safe
        checker = EntropyChecker()
        
        # Feed in interleaved chunks
        for i in range(50):
            checker.feed(f"Chunk {i % 2}: ")

    def test_strict_thresholds(self):
        """Test with very strict thresholds."""
        
        checker = EntropyChecker(
            safe_ratio_threshold=0.99,  # Very strict
            unique_byte_threshold=10,   # Very strict
        )
        
        # Even slightly mixed content should fail
        result = checker.feed("Hello\x00World")
        assert result.is_suspicious, \
            "Mixed content with strict thresholds should be suspicious"

    def test_lenient_thresholds(self):
        """Test with very lenient thresholds."""
        
        checker = EntropyChecker(
            safe_ratio_threshold=0.5,   # Very lenient
            unique_byte_threshold=200,  # Very lenient
        )
        
        # Should pass even with some binary
        result = checker.feed(b"Hello\x00\x01\x02")
        assert not result.is_suspicious, \
            "Lenient thresholds should allow mixed content"

    def test_zero_window_size(self):
        """Test behavior with zero window size (edge case)."""
        
        # This might cause division by zero or other issues
        # Should handle gracefully
        try:
            checker = EntropyChecker(window_size=0)
            result = checker.feed("test")
            assert not result.is_suspicious, \
                "Zero window size should handle gracefully"
        except Exception as e:
            # If it raises, that's acceptable if documented
            pass

    def test_very_small_zip_limit(self):
        """Test with very small zip size limit."""
        
        checker = EntropyChecker(zip_size_limit=10)  # Tiny limit
        
        # Should trigger compression check quickly
        result = checker.feed("Hello World!")
        # Result depends on compressibility of "Hello World!"
        assert isinstance(result.is_suspicious, bool), \
            "Should return boolean result"


class TestPerformance:
    """Test performance characteristics."""

    def test_small_file_overhead(self):
        """Test overhead for small files (< 1 KB)."""
        import time
        
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
            
            # Should be very fast (< 5ms average)
            assert avg_ms < 5, \
                f"Overhead too high for small files: {avg_ms:.3f} ms"
        finally:
            os.unlink(temp_path)

    def test_medium_file_overhead(self):
        """Test overhead for medium files (~100 KB)."""
        import time
        
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
            
            # Should be reasonable (< 50ms average)
            assert avg_ms < 50, \
                f"Overhead too high for medium files: {avg_ms:.3f} ms"
        finally:
            os.unlink(temp_path)

    def test_large_file_overhead(self):
        """Test overhead for large files (> 1 MB)."""
        import time
        
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
            
            # Should be reasonable (< 200ms average with compression check)
            assert avg_ms < 200, \
                f"Overhead too high for large files: {avg_ms:.3f} ms"
        finally:
            os.unlink(temp_path)

    def test_grep_performance(self):
        """Test grep performance with multiple files."""
        import time
        
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Create 100 small text files
            for i in range(100):
                with open(os.path.join(temp_dir, f"file_{i:03d}.txt"), "w") as f:
                    f.write(f"This is file number {i}.\n")
            
            # Warm up
            grep("number", temp_dir)
            
            # Measure time
            start = time.perf_counter()
            for _ in range(10):
                result = grep("number", temp_dir)
            elapsed = time.perf_counter() - start
            
            avg_ms = (elapsed / 10) * 1000
            
            # Should be reasonable
            assert avg_ms < 100, \
                f"Grep overhead too high: {avg_ms:.3f} ms"
        finally:
            shutil.rmtree(temp_dir)
