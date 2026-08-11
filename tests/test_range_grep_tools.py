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

from tools.dev_tools_readonly import grep_range_based, grepF_range_based


class TestGrepRangeBased(unittest.TestCase):
    """Tests for the regex-based range grep tool."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.test_dir, "sample.txt")
        self.content = "aaa\nbbb\nccc\nddd\n"
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write(self.content)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_success(self):
        result = grep_range_based("bbb", "ddd", self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], "bbb\nccc\nddd")

    def test_from_match_zero(self):
        result = grep_range_based("xxx", "ddd", self.test_file)
        self.assertEqual(
            {"status": "error", "message": ["Error: pattern_from matches 0 times :"]},
            result,
        )

    def test_from_match_multiple(self):
        result = grep_range_based("a", "ddd", self.test_file)
        self.assertEqual(
            {"status": "error", "message": ["Error: pattern_from matches more than 1 time :"]},
            result,
        )

    def test_to_match_zero(self):
        result = grep_range_based("bbb", "zzz", self.test_file)
        self.assertEqual(
            {"status": "error", "message": ["Error: pattern_to matches 0 times :"]},
            result,
        )

    def test_to_match_multiple(self):
        result = grep_range_based("bbb", "d", self.test_file)
        self.assertEqual(
            {"status": "error", "message": ["Error: pattern_to matches more than 1 time :"]},
            result,
        )

    def test_from_default_none_means_start_of_file(self):
        result = grep_range_based(None, "ccc", self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], "aaa\nbbb\nccc")

    def test_from_default_none_means_start_of_file_keyword(self):
        result = grep_range_based(pattern_from=None, pattern_to="ccc", path=self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], "aaa\nbbb\nccc")

    def test_to_default_none_means_end_of_file(self):
        result = grep_range_based("bbb", None, self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], "bbb\nccc\nddd\n")

    def test_to_default_none_means_end_of_file_keyword(self):
        result = grep_range_based(pattern_from="bbb", pattern_to=None, path=self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], "bbb\nccc\nddd\n")

    def test_both_defaults_none_means_whole_file(self):
        result = grep_range_based(None, None, self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], self.content)

    def test_path_traversal_blocked(self):
        result = grep_range_based("bbb", "ddd", os.path.join(self.test_dir, "..", "secret.txt"))
        self.assertEqual(result["status"], "error")
        self.assertIn("Blocked by safety check", result["message"][0])

    def test_file_not_found(self):
        result = grep_range_based("bbb", "ddd", os.path.join(self.test_dir, "ghost.txt"))
        self.assertEqual(result["status"], "error")

    def test_entropy_rejected(self):
        binary_file = os.path.join(self.test_dir, "random.bin")
        with open(binary_file, "wb") as f:
            f.write(os.urandom(1024))
        result = grep_range_based(None, None, binary_file)
        self.assertEqual(result["status"], "error")
        self.assertIn("entropy check", result["message"][0])


class TestGrepFRangeBased(unittest.TestCase):
    """Tests for the fixed-string range grep tool."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.test_dir, "sample.txt")
        self.content = "ab\ncd\nab\nef\n"
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write(self.content)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_success(self):
        result = grepF_range_based("cd", "ef", self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], "cd\nab\nef")

    def test_fixed_treats_regex_metacharacters_literally(self):
        # '.' must NOT act as a regex wildcard: 'a.b' does not occur literally
        # in 'aXb', so it must be reported as 0 matches.
        regex_file = os.path.join(self.test_dir, "regex.txt")
        with open(regex_file, "w", encoding="utf-8") as f:
            f.write("aXb\n")
        result = grepF_range_based("a.b", None, regex_file)
        self.assertEqual(
            {"status": "error", "message": ["Error: pattern_from matches 0 times :"]},
            result,
        )

    def test_from_match_zero(self):
        result = grepF_range_based("zz", "ef", self.test_file)
        self.assertEqual(
            {"status": "error", "message": ["Error: pattern_from matches 0 times :"]},
            result,
        )

    def test_from_match_multiple(self):
        result = grepF_range_based("ab", "ef", self.test_file)
        self.assertEqual(
            {"status": "error", "message": ["Error: pattern_from matches more than 1 time :"]},
            result,
        )

    def test_to_match_zero(self):
        result = grepF_range_based("cd", "zz", self.test_file)
        self.assertEqual(
            {"status": "error", "message": ["Error: pattern_to matches 0 times :"]},
            result,
        )

    def test_to_match_multiple(self):
        result = grepF_range_based("cd", "ab", self.test_file)
        self.assertEqual(
            {"status": "error", "message": ["Error: pattern_to matches more than 1 time :"]},
            result,
        )

    def test_from_default_none_means_start_of_file(self):
        result = grepF_range_based(None, "ef", self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], "ab\ncd\nab\nef")

    def test_to_default_none_means_end_of_file(self):
        result = grepF_range_based("cd", None, self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], "cd\nab\nef\n")

    def test_both_defaults_none_means_whole_file(self):
        result = grepF_range_based(None, None, self.test_file)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], self.content)

    def test_path_traversal_blocked(self):
        result = grepF_range_based("cd", "ef", os.path.join(self.test_dir, "..", "secret.txt"))
        self.assertEqual(result["status"], "error")
        self.assertIn("Blocked by safety check", result["message"][0])

    def test_file_not_found(self):
        result = grepF_range_based("cd", "ef", os.path.join(self.test_dir, "ghost.txt"))
        self.assertEqual(result["status"], "error")

    def test_entropy_rejected(self):
        binary_file = os.path.join(self.test_dir, "random.bin")
        with open(binary_file, "wb") as f:
            f.write(os.urandom(1024))
        result = grepF_range_based(None, None, binary_file)
        self.assertEqual(result["status"], "error")
        self.assertIn("entropy check", result["message"][0])


if __name__ == "__main__":
    unittest.main()
