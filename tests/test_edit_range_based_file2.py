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

from tools.edit import edit_range_based_file2

EDIT_CONTENT = "HEAD\nbody A\nbody B\nTAIL"
GREP_CONTENT = "START\nalpha\nbeta\nSTOP"


class TestEditRangeBasedFile2(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.edit_file = os.path.join(self.test_dir, "edit.txt")
        self.grep_file = os.path.join(self.test_dir, "grep.txt")
        with open(self.edit_file, "w", encoding="utf-8") as f:
            f.write(EDIT_CONTENT)
        with open(self.grep_file, "w", encoding="utf-8") as f:
            f.write(GREP_CONTENT)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def read_edit(self):
        with open(self.edit_file, "r", encoding="utf-8") as f:
            return f.read()

    def write_edit(self, content):
        with open(self.edit_file, "w", encoding="utf-8") as f:
            f.write(content)

    def test_success(self):
        self.write_edit("def foo():\n    old_line1\n    old_line2\nTAIL")
        with open(self.grep_file, "w", encoding="utf-8") as f:
            f.write("def bar():\n    new_line1\n    new_line2\nEND")
        result = edit_range_based_file2(
            self.edit_file,
            self.grep_file,
            pe_search_from1="def foo():\n",
            pe_search_from2="    old_line1",
            pe_search_to1="    old_line2",
            pe_search_to2="\nTAIL",
            pg_search_from1="def bar():\n",
            pg_search_from2="    new_line1",
            pg_search_to1="    new_line2",
            pg_search_to2="\nEND",
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["file"], self.edit_file)
        self.assertIn("-    old_line1", result["diff"])
        self.assertIn("-    old_line2", result["diff"])
        self.assertIn("+    new_line1", result["diff"])
        self.assertIn("+    new_line2", result["diff"])
        self.assertEqual(self.read_edit(), "def foo():\n    new_line1\n    new_line2\nTAIL")

    def test_split_keeps_outer_parts(self):
        result = edit_range_based_file2(
            self.edit_file,
            self.grep_file,
            pe_search_from1="body",
            pe_search_from2=" A",
            pe_search_to1="body",
            pe_search_to2=" B",
            pg_search_from1="alpha",
            pg_search_from2=None,
            pg_search_to1="beta",
            pg_search_to2=None,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.read_edit(), "HEAD\nbody\nbeta B\nTAIL")

    def test_from_default_none_means_start_of_files(self):
        result = edit_range_based_file2(
            self.edit_file,
            self.grep_file,
            pe_search_from1=None,
            pe_search_from2=None,
            pe_search_to1="body",
            pe_search_to2=" B",
            pg_search_from1=None,
            pg_search_from2=None,
            pg_search_to1="beta",
            pg_search_to2=None,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.read_edit(), "START\nalpha\nbeta B\nTAIL")

    def test_to_default_none_means_end_of_files(self):
        result = edit_range_based_file2(
            self.edit_file,
            self.grep_file,
            pe_search_from1="body",
            pe_search_from2=" A",
            pe_search_to1=None,
            pe_search_to2=None,
            pg_search_from1="alpha",
            pg_search_from2=None,
            pg_search_to1=None,
            pg_search_to2=None,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.read_edit(), "HEAD\nbody\nbeta\nSTOP")

    def test_all_defaults_none_replaces_whole_edit_file(self):
        result = edit_range_based_file2(
            self.edit_file,
            self.grep_file,
            None, None, None, None, None, None, None, None,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.read_edit(), GREP_CONTENT)

    def test_one_part_none_treated_as_empty(self):
        result = edit_range_based_file2(
            self.edit_file,
            self.grep_file,
            pe_search_from1=None,
            pe_search_from2=" A",
            pe_search_to1="body",
            pe_search_to2=" B",
            pg_search_from1="alpha",
            pg_search_from2=None,
            pg_search_to1="beta",
            pg_search_to2=None,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.read_edit(), "HEAD\nbody\nbeta B\nTAIL")

    def test_pe_search_from_zero_matches(self):
        result = edit_range_based_file2(
            self.edit_file, self.grep_file,
            "ghost", " X", "body", " B", "alpha", None, "beta", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error:", "pe_search_from1+pe_search_from2 string matches not exactly 1 time : 0"]},
            result,
        )

    def test_pe_search_from_multiple_matches(self):
        self.write_edit("A\nB\nA\nC\n")
        result = edit_range_based_file2(
            self.edit_file, self.grep_file,
            "A", None, "C", None, "alpha", None, "beta", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error:", "pe_search_from1+pe_search_from2 string matches not exactly 1 time : 2"]},
            result,
        )

    def test_pe_search_to_zero_matches(self):
        result = edit_range_based_file2(
            self.edit_file, self.grep_file,
            "body", " A", "ghost", None, "alpha", None, "beta", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error:", "pe_search_to1+pe_search_to2 string matches not exactly 1 time : 0"]},
            result,
        )

    def test_pe_search_to_multiple_matches(self):
        self.write_edit("A\nB\nB\nC\n")
        result = edit_range_based_file2(
            self.edit_file, self.grep_file,
            "A", None, "B", None, "alpha", None, "beta", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error:", "pe_search_to1+pe_search_to2 string matches not exactly 1 time : 2"]},
            result,
        )

    def test_pg_search_from_zero_matches(self):
        result = edit_range_based_file2(
            self.edit_file, self.grep_file,
            "body", " A", "body", " B", "ghost", None, "beta", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error:", "pg_search_from1+pg_search_from2 string matches not exactly 1 time : 0"]},
            result,
        )

    def test_pg_search_from_multiple_matches(self):
        grep_file = os.path.join(self.test_dir, "multi_grep.txt")
        with open(grep_file, "w", encoding="utf-8") as f:
            f.write("A\nB\nA\nC\n")
        result = edit_range_based_file2(
            self.edit_file, grep_file,
            "body", " A", "body", " B", "A", None, "C", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error:", "pg_search_from1+pg_search_from2 string matches not exactly 1 time : 2"]},
            result,
        )

    def test_pg_search_to_zero_matches(self):
        result = edit_range_based_file2(
            self.edit_file, self.grep_file,
            "body", " A", "body", " B", "alpha", None, "ghost", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error:", "pg_search_to1+pg_search_to2 string matches not exactly 1 time : 0"]},
            result,
        )

    def test_pg_search_to_multiple_matches(self):
        grep_file = os.path.join(self.test_dir, "multi_grep.txt")
        with open(grep_file, "w", encoding="utf-8") as f:
            f.write("A\nB\nB\nC\n")
        result = edit_range_based_file2(
            self.edit_file, grep_file,
            "body", " A", "body", " B", "A", None, "B", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error:", "pg_search_to1+pg_search_to2 string matches not exactly 1 time : 2"]},
            result,
        )

    def test_edit_overlap(self):
        self.write_edit("AAABBB\n")
        result = edit_range_based_file2(
            self.edit_file, self.grep_file,
            "AAA", None, "A", "AB", "alpha", None, "beta", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pe_search_from and pe_search_to overlap, or pe_search_from does not come before pe_search_to."]},
            result,
        )

    def test_edit_wrong_order(self):
        result = edit_range_based_file2(
            self.edit_file, self.grep_file,
            "TAIL", None, "body", " A", "alpha", None, "beta", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pe_search_from and pe_search_to overlap, or pe_search_from does not come before pe_search_to."]},
            result,
        )

    def test_grep_overlap(self):
        result = edit_range_based_file2(
            self.edit_file, self.grep_file,
            "body", " A", "body", " B", "alpha", None, "alpha", None,
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pg_search_from and pg_search_to overlap, or pg_search_from does not come before pg_search_to."]},
            result,
        )

    def test_edit_file_not_found(self):
        result = edit_range_based_file2(
            os.path.join(self.test_dir, "ghost.txt"), self.grep_file,
            "body", " A", "body", " B", "alpha", None, "beta", None,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("does not exist", " ".join(result["message"]))

    def test_grep_file_not_found(self):
        result = edit_range_based_file2(
            self.edit_file, os.path.join(self.test_dir, "ghost.txt"),
            "body", " A", "body", " B", "alpha", None, "beta", None,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("does not exist", " ".join(result["message"]))

    def test_grep_file_not_provided(self):
        result = edit_range_based_file2(
            self.edit_file, None,
            "body", " A", "body", " B", "alpha", None, "beta", None,
        )
        self.assertEqual(result["status"], "error")

    def test_path_traversal_blocked(self):
        unsafe_path = os.path.join(self.test_dir, "..", "secret.txt")
        with open(unsafe_path, "w", encoding="utf-8") as f:
            f.write("secret")
        try:
            result = edit_range_based_file2(
                unsafe_path, self.grep_file,
                "body", " A", "body", " B", "alpha", None, "beta", None,
            )
        finally:
            os.remove(unsafe_path)
        self.assertEqual(result["status"], "error")
        self.assertIn("Blocked by safety check", result["message"][0])

    def test_edit_file_unchanged_on_error(self):
        edit_range_based_file2(
            self.edit_file, self.grep_file,
            "ghost", " X", "body", " B", "alpha", None, "beta", None,
        )
        self.assertEqual(self.read_edit(), EDIT_CONTENT)


if __name__ == "__main__":
    unittest.main()
