import unittest
import os
import tempfile
import shutil
import sys
import contextlib

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

# Register /tmp as an allowed basepath so that absolute paths from
# tempfile.mkdtemp() pass validate_path.
from tools_security.validate_path import register_basepath

register_basepath("/tmp")

from tools.edit import edit, edit_range_based, create_new_file, makedirs


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)

class TestEditTool(unittest.TestCase):
    def setUp(self):
        """Create a temporary directory and a sample file for testing."""
        self.test_dir = tempfile.mkdtemp()
        self.test_file_path = os.path.join(self.test_dir, "sample.txt")
        self.original_content = "Hello world!\nThis is a test.\nGoodbye world!"
        with open(self.test_file_path, "w", encoding="utf-8") as f:
            f.write(self.original_content)

    def tearDown(self):
        """Remove the temporary directory and all its contents."""
        shutil.rmtree(self.test_dir)

    def test_edit_success(self):
        """Test successful replacement of a unique string."""
        search_str = "Hello world!"
        replace_str = "Hello universe!"
        
        result = edit(self.test_file_path, search_str, replace_str)
        
        # Verify return message
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["data"],
            "Successfully applied patch to " + self.test_file_path + ".",
        )
        self.assertEqual(result["file"], self.test_file_path)
        self.assertIn("-Hello world!", result["diff"])
        self.assertIn("+Hello universe!", result["diff"])
        
        # Verify file content change
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        self.assertEqual(content, "Hello universe!\nThis is a test.\nGoodbye world!")

#     def test_edit_error_file_not_found(self):
#         """Test error when the file does not exist."""
#         non_existent_path = os.path.join(self.test_dir, "ghost.txt")
#         result = edit(non_existent_path, "search", "replace")
#         self.assertIn("Error: File", result)
#         self.assertIn("does not exist", result)
# 
    def test_edit_error_multiple_matches(self):
        """Test error when the search string appears more than once."""
        # 'world!' appears twice in our setup (once in line 1, once in line 3)
        search_str = "world!"
        replace_str = "universe!"
        result = edit(self.test_file_path, search_str, replace_str)
        
        self.assertEqual( {'status': 'error', 'message': ['Error: search string matches not exactly 1 time :', 2]} , result)
#
    def test_edit_range_based_success(self):
        """Test successful replacement of a contiguous range."""
        search_from = "Hello world!"
        search_to = "Goodbye world!"
        replace_str = "Replaced!"

        result = edit_range_based(self.test_file_path, search_from, search_to, replace_str)

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["data"],
            "Successfully applied patch to " + self.test_file_path + ".",
        )
        self.assertEqual(result["file"], self.test_file_path)
        self.assertIn("-Hello world!", result["diff"])
        self.assertIn("-Goodbye world!", result["diff"])
        self.assertIn("+Replaced!", result["diff"])

        with open(self.test_file_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "Replaced!")

    def test_edit_range_based_multiple_from_matches(self):
        """Error when search_from appears more than once."""
        result = edit_range_based(self.test_file_path, "world!", "a test.", "x")
        self.assertEqual(
            {"status": "error", "message": ["Error: search_from string matches not exactly 1 time :", 2]},
            result,
        )

    def test_edit_range_based_multiple_to_matches(self):
        """Error when search_to appears more than once."""
        result = edit_range_based(self.test_file_path, "Hello world!", "world!", "x")
        self.assertEqual(
            {"status": "error", "message": ["Error: search_to string matches not exactly 1 time :", 2]},
            result,
        )

    def test_edit_range_based_overlap_to_inside_from(self):
        """Error when search_to is nested inside search_from."""
        search_from = "This is a test.\nGoodbye world!"
        search_to = "Goodbye"
        result = edit_range_based(self.test_file_path, search_from, search_to, "x")
        self.assertEqual(
            {"status": "error", "message": ["Error: search_from and search_to overlap, or search_from does not come before search_to."]},
            result,
        )

    def test_edit_range_based_overlap_from_inside_to(self):
        """Error when search_from is nested inside search_to."""
        search_from = "Hello world"
        search_to = "Hello world!\nThis is a test."
        result = edit_range_based(self.test_file_path, search_from, search_to, "x")
        self.assertEqual(
            {"status": "error", "message": ["Error: search_from and search_to overlap, or search_from does not come before search_to."]},
            result,
        )

    def test_edit_range_based_same_anchor(self):
        """Error when search_from and search_to are the same string."""
        result = edit_range_based(self.test_file_path, "test", "test", "x")
        self.assertEqual(
            {"status": "error", "message": ["Error: search_from and search_to overlap, or search_from does not come before search_to."]},
            result,
        )

    def test_edit_range_based_wrong_order(self):
        """Error when search_from comes after search_to."""
        result = edit_range_based(self.test_file_path, "Goodbye world!", "Hello world!", "x")
        self.assertEqual(
            {"status": "error", "message": ["Error: search_from and search_to overlap, or search_from does not come before search_to."]},
            result,
        )

    def test_edit_range_based_file_unchanged_on_error(self):
        """Failed range edits must leave the file untouched."""
        edit_range_based(self.test_file_path, "world!", "a test.", "x")
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), self.original_content)

    def test_create_new_file_bare_filename(self):
        """Bare relative filenames must not crash (regression: makedirs(''))."""
        with _cwd(self.test_dir):
            result = create_new_file("bare.txt", "hello\n")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["file"], "bare.txt")
        self.assertIn("+hello", result["diff"])
        with open(os.path.join(self.test_dir, "bare.txt"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello\n")

    def test_create_new_file_nested_dirs(self):
        """Missing parent directories are created automatically."""
        with _cwd(self.test_dir):
            result = create_new_file("a/b/c.txt", "nested\n")
        self.assertEqual(result["status"], "success")
        with open(os.path.join(self.test_dir, "a", "b", "c.txt"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "nested\n")

    def test_makedirs_empty_path(self):
        """Empty/whitespace paths must be refused, not crash."""
        self.assertEqual(makedirs("")["status"], "error")
        self.assertEqual(makedirs("   ")["status"], "error")
# 
#     def test_edit_error_zero_matches(self):
#         """Test error when the search string is not found."""
#         search_str = "nonexistent_string"
#         result = edit(self.test_file_path, search_str, "replace")
#         
#         self.assertIn("Error: search string matches not exactly 1 time", result)
# 
#     def test_edit_safety_traversal(self):
#         """Test that path traversal attempts (..) are blocked."""
#         # Construct a path that looks like it's trying to escape the temp dir
#         unsafe_path = os.path.join(self.test_dir, "..", "secret.txt")
#         result = edit(unsafe_path, "search", "replace")
#         
#         self.assertIn("Blocked by safety check", result)
# 
#     def test_edit_special_characters(self):
#         """Test that UTF-8 characters are handled correctly."""
#         utf8_file = os.path.join(self.test_dir, "utf8.txt")
#         with open(utf8_file, "w", encoding="utf-8") as f:
#             f.write("Café content.")
#             
#         result = edit(utf8_file, "Café", "Coffee")
#         self.assertIn("Successfully", result)
#         
#         with open(utf8_file, "r", encoding="utf-8") as f:
#             content = f.read()
#         self.assertEqual(content, "Coffee content.")

if __name__ == "__main__":
    unittest.main()
