#!/usr/bin/env python3
"""Run the full lama_ole test suite.

This helper runs BOTH test frameworks used in ``tests/``:

  1. ``python3 -m unittest discover -s tests -p "test_*.py"``
     -> unittest-style files (``test_edit_tools.py``, ``test_true.py``).
  2. ``python3 -m pytest tests/ -q``
     -> the full suite (pytest also collects unittest classes, so nothing
        is skipped).

Usage (from ``lama_ole/``):

    python3 tests/run_all_tests.py
    python3 tests/run_all_tests.py -v      # verbose unittest output

Exits with a non-zero status if any test run fails.
"""

import os
import subprocess
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

TESTS_DIR = os.path.join(lama_ole_dir, "tests")


def _run(cmd, label):
    print("=" * 72)
    print("### %s" % label)
    print("=" * 72)
    result = subprocess.run(cmd, cwd=lama_ole_dir)
    print()
    return result.returncode


def main():
    verbose = "-v" in sys.argv[1:] or "--verbose" in sys.argv[1:]

    unittest_cmd = [
        sys.executable, "-m", "unittest", "discover",
        "-s", TESTS_DIR, "-p", "test_*.py",
    ]
    if verbose:
        unittest_cmd.append("-v")

    pytest_cmd = [sys.executable, "-m", "pytest", TESTS_DIR, "-q"]

    returncode = 0
    returncode |= _run(unittest_cmd, "unittest-style tests (unittest discover)")
    returncode |= _run(pytest_cmd, "full suite (pytest, includes unittest classes)")

    if returncode == 0:
        print("ALL TEST SUITES PASSED.")
    else:
        print("SOME TESTS FAILED -- see output above.")
    return returncode


if __name__ == "__main__":
    sys.exit(main())
