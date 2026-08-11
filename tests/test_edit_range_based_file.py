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

from tools.edit import edit_range_based_file

EDIT_CONTENT = "HEAD\nbody A\nbody B\nTAIL"
GREP_CONTENT = "START\nalpha\nbeta\nSTOP"


class TestEditRangeBasedFile(unittest.TestCase):
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

    def test_success(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, "body A", "TAIL", "alpha", "beta"
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["file"], self.edit_file)
        self.assertIn("-body A", result["diff"])
        self.assertIn("-TAIL", result["diff"])
        self.assertIn("+alpha", result["diff"])
        self.assertIn("+beta", result["diff"])
        self.assertEqual(self.read_edit(), "HEAD\nalpha\nbeta")

    def test_success_keyword_args(self):
        result = edit_range_based_file(
            path2edit=self.edit_file,
            path2grep=self.grep_file,
            pe_search_from="body A",
            pe_search_to="TAIL",
            pg_search_from="alpha",
            pg_search_to="beta",
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.read_edit(), "HEAD\nalpha\nbeta")

    def test_from_default_none_means_start_of_files(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, None, "body B", None, "beta"
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.read_edit(), "START\nalpha\nbeta\nTAIL")

    def test_to_default_none_means_end_of_files(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, "body A", None, "alpha", None
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.read_edit(), "HEAD\nalpha\nbeta\nSTOP")

    def test_all_defaults_none_replaces_whole_edit_file(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, None, None, None, None
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.read_edit(), GREP_CONTENT)

    def test_pe_search_from_zero_matches(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, "ghost", "TAIL", "alpha", "beta"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pe_search_from string matches not exactly 1 time :", 0]},
            result,
        )

    def test_pe_search_from_multiple_matches(self):
        edit_file = os.path.join(self.test_dir, "multi.txt")
        with open(edit_file, "w", encoding="utf-8") as f:
            f.write("A\nB\nA\nC\n")
        result = edit_range_based_file(
            edit_file, self.grep_file, "A", "C", "alpha", "beta"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pe_search_from string matches not exactly 1 time :", 2]},
            result,
        )

    def test_pe_search_to_zero_matches(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, "body A", "ghost", "alpha", "beta"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pe_search_to string matches not exactly 1 time :", 0]},
            result,
        )

    def test_pe_search_to_multiple_matches(self):
        edit_file = os.path.join(self.test_dir, "multi.txt")
        with open(edit_file, "w", encoding="utf-8") as f:
            f.write("A\nB\nB\nC\n")
        result = edit_range_based_file(
            edit_file, self.grep_file, "A", "B", "alpha", "beta"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pe_search_to string matches not exactly 1 time :", 2]},
            result,
        )

    def test_pg_search_from_zero_matches(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, "body A", "TAIL", "ghost", "beta"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pg_search_from string matches not exactly 1 time :", 0]},
            result,
        )

    def test_pg_search_from_multiple_matches(self):
        grep_file = os.path.join(self.test_dir, "multi_grep.txt")
        with open(grep_file, "w", encoding="utf-8") as f:
            f.write("A\nB\nA\nC\n")
        result = edit_range_based_file(
            self.edit_file, grep_file, "body A", "TAIL", "A", "C"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pg_search_from string matches not exactly 1 time :", 2]},
            result,
        )

    def test_pg_search_to_zero_matches(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, "body A", "TAIL", "alpha", "ghost"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pg_search_to string matches not exactly 1 time :", 0]},
            result,
        )

    def test_pg_search_to_multiple_matches(self):
        grep_file = os.path.join(self.test_dir, "multi_grep.txt")
        with open(grep_file, "w", encoding="utf-8") as f:
            f.write("A\nB\nB\nC\n")
        result = edit_range_based_file(
            self.edit_file, grep_file, "body A", "TAIL", "A", "B"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pg_search_to string matches not exactly 1 time :", 2]},
            result,
        )

    def test_edit_overlap(self):
        edit_file = os.path.join(self.test_dir, "overlap.txt")
        with open(edit_file, "w", encoding="utf-8") as f:
            f.write("AAABBB\n")
        result = edit_range_based_file(
            edit_file, self.grep_file, "AAA", "AAB", "alpha", "beta"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pe_search_from and pe_search_to overlap, or pe_search_from does not come before pe_search_to."]},
            result,
        )

    def test_edit_wrong_order(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, "TAIL", "body A", "alpha", "beta"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pe_search_from and pe_search_to overlap, or pe_search_from does not come before pe_search_to."]},
            result,
        )

    def test_grep_overlap(self):
        result = edit_range_based_file(
            self.edit_file, self.grep_file, "body A", "TAIL", "alpha", "alpha"
        )
        self.assertEqual(
            {"status": "error", "message": ["Error: pg_search_from and pg_search_to overlap, or pg_search_from does not come before pg_search_to."]},
            result,
        )

    def test_edit_file_not_found(self):
        result = edit_range_based_file(
            os.path.join(self.test_dir, "ghost.txt"), self.grep_file, "body A", "TAIL", "alpha", "beta"
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("does not exist", " ".join(result["message"]))

    def test_grep_file_not_found(self):
        result = edit_range_based_file(
            self.edit_file, os.path.join(self.test_dir, "ghost.txt"), "body A", "TAIL", "alpha", "beta"
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("does not exist", " ".join(result["message"]))

    def test_grep_file_not_provided(self):
        result = edit_range_based_file(self.edit_file, None, "body A", "TAIL", "alpha", "beta")
        self.assertEqual(result["status"], "error")

    def test_path_traversal_blocked(self):
        unsafe_path = os.path.join(self.test_dir, "..", "secret.txt")
        with open(unsafe_path, "w", encoding="utf-8") as f:
            f.write("secret")
        try:
            result = edit_range_based_file(
                unsafe_path, self.grep_file, "body A", "TAIL", "alpha", "beta"
            )
        finally:
            os.remove(unsafe_path)
        self.assertEqual(result["status"], "error")
        self.assertIn("Blocked by safety check", result["message"][0])

    def test_edit_file_unchanged_on_error(self):
        edit_range_based_file(
            self.edit_file, self.grep_file, "ghost", "TAIL", "alpha", "beta"
        )
        self.assertEqual(self.read_edit(), EDIT_CONTENT)


if __name__ == "__main__":
    unittest.main()
