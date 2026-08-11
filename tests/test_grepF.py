import unittest
import os
import tempfile
import shutil
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

# Register /tmp as an allowed basepath so that absolute paths from
# tempfile.mkdtemp() pass validate_path.
from tools_security.validate_path import register_basepath

register_basepath("/tmp")

from tools.dev_tools_readonly import grepF


class TestGrepF(unittest.TestCase):
    """Tests for the fixed-string grep tool."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.test_dir, "sample.txt")
        self.content = "alpha\nbeta\nfoobar\n"
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write(self.content)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_single_file_success(self):
        result = grepF("beta", self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], f"{self.test_file}:2: beta")

    def test_default_path_is_dot(self):
        old_cwd = os.getcwd()
        try:
            os.chdir(self.test_dir)
            result = grepF("beta")
            self.assertEqual(result["status"], "success")
            self.assertIn(":2: beta", result["data"])
        finally:
            os.chdir(old_cwd)

    def test_no_match(self):
        result = grepF("ghost", self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], "(no matches)")

    def test_fixed_treats_dot_literally(self):
        # '.' must NOT act as a regex wildcard: 'a.b' does not match 'aXb'.
        regex_file = os.path.join(self.test_dir, "regex.txt")
        with open(regex_file, "w", encoding="utf-8") as f:
            f.write("aXb\na.b\n")
        result = grepF("a.b", regex_file)
        self.assertEqual(result["status"], "success")
        lines = result["data"].splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(":2: a.b", lines[0])

    def test_fixed_treats_brackets_literally(self):
        # '[ab]' as a regex would match 'a' or 'b'; as a fixed string it only
        # matches the literal text '[ab]'.
        file = os.path.join(self.test_dir, "brackets.txt")
        with open(file, "w", encoding="utf-8") as f:
            f.write("alpha\n[ab]\n")
        result = grepF("[ab]", file)
        self.assertEqual(result["status"], "success")
        lines = result["data"].splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(":2: [ab]", lines[0])

    def test_directory_search_recursive(self):
        subdir = os.path.join(self.test_dir, "sub")
        os.makedirs(subdir)
        nested = os.path.join(subdir, "nested.txt")
        with open(nested, "w", encoding="utf-8") as f:
            f.write("needle here\n")
        result = grepF("needle", self.test_dir)
        self.assertEqual(result["status"], "success")
        self.assertIn(f"{nested}:1: needle here", result["data"])

    def test_directory_include_filter(self):
        txt_file = os.path.join(self.test_dir, "keep.txt")
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("needle in txt\n")
        py_file = os.path.join(self.test_dir, "skip.py")
        with open(py_file, "w", encoding="utf-8") as f:
            f.write("needle in py\n")
        result = grepF("needle", self.test_dir, include="*.txt")
        self.assertEqual(result["status"], "success")
        self.assertIn(f"{txt_file}:1: needle in txt", result["data"])
        self.assertNotIn("skip.py", result["data"])

    def test_avoids_dotgit(self):
        git_dir = os.path.join(self.test_dir, ".git")
        os.makedirs(git_dir)
        secret = os.path.join(git_dir, "config")
        with open(secret, "w", encoding="utf-8") as f:
            f.write("needle inside git\n")
        result = grepF("needle", self.test_dir)
        self.assertEqual(result["status"], "success")
        self.assertNotIn("config", result["data"])
        self.assertEqual(result["data"], "(no matches)")

    def test_path_traversal_blocked(self):
        result = grepF("beta", os.path.join(self.test_dir, "..", "secret.txt"))
        self.assertEqual(result["status"], "error")
        self.assertIn("Blocked by safety check", result["message"][0])

    def test_path_not_found(self):
        result = grepF("beta", os.path.join(self.test_dir, "ghost.txt"))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], [f"Path not found: {os.path.join(self.test_dir, 'ghost.txt')}"])

    def test_entropy_rejected_files_are_skipped(self):
        binary_file = os.path.join(self.test_dir, "random.bin")
        with open(binary_file, "wb") as f:
            f.write(os.urandom(1024))
        result = grepF("anything", self.test_dir)
        self.assertEqual(result["status"], "success")
        self.assertIn("skipped", result["data"].lower())
        self.assertIn(binary_file, result["data"])
        self.assertNotIn("random.bin:", result["data"])


if __name__ == "__main__":
    unittest.main()
