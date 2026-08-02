"""Helper functions for generating entropy test data.

Provides utilities for creating various types of test data including:
- Random bytes (binary)
- Repetitive text (compressible)
- Mixed content (text + binary)
- Temporary files for integration testing
"""

from __future__ import annotations

import os
import tempfile


def generate_random_bytes(size: int) -> bytes:
    """Generate random bytes of specified size.
    
    Uses os.urandom() which provides cryptographically secure random data.
    This is ideal for testing entropy detection since random data should
    fail the compression test.
    
    Args:
        size: Number of random bytes to generate
        
    Returns:
        Bytes object containing random data
        
    Examples:
        >>> data = generate_random_bytes(100)
        >>> len(data)
        100
        >>> isinstance(data, bytes)
        True
    """
    return os.urandom(size)


def generate_repetitive_text(repetition: str, count: int) -> str:
    """Generate repetitive text for compression testing.
    
    Creates text by repeating a base string multiple times. This produces
    highly compressible data that should pass the entropy check.
    
    Args:
        repetition: Base string to repeat
        count: Number of times to repeat
        
    Returns:
        String with repeated content
        
    Examples:
        >>> text = generate_repetitive_text("test ", 5)
        >>> text
        'test test test test test '
        >>> len(text)
        25
    """
    return repetition * count


def create_temp_binary_file(content: bytes, suffix: str = ".bin") -> str:
    """Create a temporary binary file and return its path.
    
    Creates a file with the given content in a temporary directory.
    The file is not automatically deleted - caller must clean up.
    
    Args:
        content: Bytes to write to the file
        suffix: File extension (default: ".bin")
        
    Returns:
        Path to the created temporary file
        
    Examples:
        >>> path = create_temp_binary_file(b"\\x00\\x01\\x02")
        >>> os.path.exists(path)
        True
        >>> # Remember to delete when done
        >>> os.unlink(path)
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(content)
        return f.name


def create_temp_text_file(content: str, suffix: str = ".txt") -> str:
    """Create a temporary text file and return its path.
    
    Creates a UTF-8 encoded text file in a temporary directory.
    The file is not automatically deleted - caller must clean up.
    
    Args:
        content: String content to write (will be UTF-8 encoded)
        suffix: File extension (default: ".txt")
        
    Returns:
        Path to the created temporary file
        
    Examples:
        >>> path = create_temp_text_file("Hello World!")
        >>> os.path.exists(path)
        True
        >>> # Remember to delete when done
        >>> os.unlink(path)
    """
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=suffix, encoding='utf-8') as f:
        f.write(content)
        return f.name


def generate_mixed_content(text_ratio: float = 0.5) -> bytes:
    """Generate content with mixed text and binary data.
    
    Creates a byte sequence that interleaves valid text with random binary data.
    The text_ratio parameter controls the proportion of text vs binary (0.0-1.0).
    
    Args:
        text_ratio: Fraction of content that should be text (default: 0.5)
                   0.0 = all binary, 1.0 = all text
        
    Returns:
        Bytes object with mixed content
        
    Examples:
        >>> data = generate_mixed_content(0.8)
        >>> len(data) > 0
        True
        >>> isinstance(data, bytes)
        True
    """
    total_size = 10000
    text_size = int(total_size * text_ratio)
    binary_size = total_size - text_size
    
    # Generate text portion (repetitive English-like text)
    text = "Hello World! This is some valid text. " * (text_size // 35 + 1)
    text = text[:text_size]
    
    # Generate binary portion (random bytes)
    binary = os.urandom(binary_size)
    
    # Interleave them in chunks of 100 bytes
    result = bytearray()
    for i in range(0, len(text), 100):
        result.extend(text[i:i+100].encode())
        if i + 100 < len(binary):
            result.extend(binary[i:i+100])
    
    return bytes(result)


def get_test_files_directory() -> str:
    """Get path to test data directory.
    
    Returns the absolute path to a 'testdata/entropy_test_files' subdirectory
    relative to this file's location. Creates the directory if it doesn't exist.
    
    Returns:
        Absolute path to test files directory
        
    Examples:
        >>> dir_path = get_test_files_directory()
        >>> os.path.isdir(dir_path)
        True
    """
    import os
    
    # Get directory where this file is located
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Create test data subdirectory path
    test_data_dir = os.path.join(current_dir, "testdata", "entropy_test_files")
    
    # Create directory if it doesn't exist
    os.makedirs(test_data_dir, exist_ok=True)
    
    return test_data_dir


def create_sample_text_files(directory: str | None = None) -> list[str]:
    """Create sample text files for testing.
    
    Creates a set of standard text files (Python, Markdown, etc.) in the 
    specified directory or the default test data directory.
    
    Args:
        directory: Directory to create files in (default: test data dir)
        
    Returns:
        List of paths to created files
        
    Examples:
        >>> paths = create_sample_text_files()
        >>> len(paths) > 0
        True
        >>> all(os.path.exists(p) for p in paths)
        True
    """
    if directory is None:
        directory = get_test_files_directory()
    
    os.makedirs(directory, exist_ok=True)
    
    files = []
    
    # Python file
    py_path = os.path.join(directory, "sample.py")
    with open(py_path, 'w') as f:
        f.write('def hello():\n    print("Hello, world!")\n\nif __name__ == "__main__":\n    hello()\n')
    files.append(py_path)
    
    # Markdown file
    md_path = os.path.join(directory, "sample.md")
    with open(md_path, 'w') as f:
        f.write("# Sample Document\n\nThis is a **sample** markdown file.\n\n- Item 1\n- Item 2\n")
    files.append(md_path)
    
    # Text file
    txt_path = os.path.join(directory, "sample.txt")
    with open(txt_path, 'w') as f:
        f.write("This is a plain text file.\nIt has multiple lines.\nFor testing purposes.\n")
    files.append(txt_path)
    
    # JSON file
    json_path = os.path.join(directory, "sample.json")
    with open(json_path, 'w') as f:
        f.write('{"name": "test", "value": 42, "active": true}\n')
    files.append(json_path)
    
    return files


def create_sample_binary_files(directory: str | None = None) -> list[str]:
    """Create sample binary files for testing.
    
    Creates various types of binary files (random data, image headers, etc.) 
    in the specified directory or the default test data directory.
    
    Args:
        directory: Directory to create files in (default: test data dir)
        
    Returns:
        List of paths to created files
        
    Examples:
        >>> paths = create_sample_binary_files()
        >>> len(paths) > 0
        True
        >>> all(os.path.exists(p) for p in paths)
        True
    """
    if directory is None:
        directory = get_test_files_directory()
    
    os.makedirs(directory, exist_ok=True)
    
    files = []
    
    # Random binary file
    bin_path = os.path.join(directory, "random.bin")
    with open(bin_path, 'wb') as f:
        f.write(os.urandom(1024))
    files.append(bin_path)
    
    # PNG-like header (not a real PNG, just the magic bytes)
    png_path = os.path.join(directory, "fake_image.png")
    with open(png_path, 'wb') as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    files.append(png_path)
    
    # ZIP-like header (PK signature)
    zip_path = os.path.join(directory, "fake_archive.zip")
    with open(zip_path, 'wb') as f:
        f.write(b"PK\x03\x04" + b"\x00" * 100)
    files.append(zip_path)
    
    # ELF-like header (Linux executable)
    elf_path = os.path.join(directory, "fake_binary.elf")
    with open(elf_path, 'wb') as f:
        f.write(b"\x7fELF" + b"\x00" * 100)
    files.append(elf_path)
    
    return files


def cleanup_test_files(paths: list[str]) -> None:
    """Delete test files.
    
    Removes the specified files from disk. Silently ignores errors if 
    files don't exist (e.g., already deleted).
    
    Args:
        paths: List of file paths to delete
        
    Examples:
        >>> paths = create_sample_text_files()
        >>> cleanup_test_files(paths)
        >>> all(not os.path.exists(p) for p in paths)
        True
    """
    import os
    
    for path in paths:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            # Silently ignore deletion errors
            pass
