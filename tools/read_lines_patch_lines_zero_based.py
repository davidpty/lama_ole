"""Tools for applying patches to files using read_lines0 and patch_lines0."""


import os
from typing import Optional
from tool_base import tool
import re
import glob as glob_mod
from tools_security.validate_path import validate_path as _validate_path

read_lines_tuple3 = None


def _entropy_reason(path: str) -> Optional[str]:
    """Return the entropy rejection reason for a file, or None if it passes."""
    try:
        with open(path, "rb") as f:
            raw_content = f.read()
    except Exception:
        return None

    from security.entropychecker import EntropyChecker

    result = EntropyChecker().feed(raw_content)
    if result.is_suspicious:
        return result.reason
    return None

@tool(description="Provides help and usage instructions for the read_lines0 and patch_lines0 tools.")
def read_lines_patch_lines_zero_based_help() :
    return """To edit a file using these tools (zero-indexed version), follow this workflow:
1. Use `grep0_from_file` (for regex) or `grep0F_from_file` (for fixed strings) to find the line numbers and content of the parts you want to change. These tools return zero-indexed line numbers.
2. Use `read_lines0` with the identified line numbers to read the specific section of the file. This prepares the tool for patching.
3. Use `patch_lines0` with your new content to replace the previously read lines.

Note: 
- `grep0_from_file` and `grep0F_from_file` return zero-indexed line numbers.
- `read_lines0` uses these zero-indexed numbers.
- `patch_lines0` must be called immediately after a successful `read_lines0` call for the same file; otherwise, it will fail.
"""

@tool(description="Searches for a regex pattern in a file and returns zero-indexed line numbers and their content (format: 'line_number: content'). These indices can be used with the `read_lines0` tool.")
def grep0_from_file(pattern: str, path: str) -> str:

    safety_error = _validate_path(path)
    if safety_error:
        return safety_error

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    matches = []
    try:
        reason = _entropy_reason(path)
        if reason is not None:
            return f"Error: File {path} rejected by entropy check: {reason}"
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f.readlines()):
                if re.search(pattern, line):
                    matches.append(f"{i}: {line.rstrip()}")
    except Exception as e:
        return f"Error applying patch: {str(e)}"
    return "\n".join(matches) if matches else "(no matches)"

@tool(description="Searches for a fixed string needle in a given file (parameter path), returns zero-indexed line numbers and their content (format: 'line_number: content'). Case sensitive. These indices can be used with the `read_lines0` tool.")
def grep0F_from_file(needle: str, path: str) -> str:

    safety_error = _validate_path(path)
    if safety_error:
        return safety_error

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    matches = []
    try:
        reason = _entropy_reason(path)
        if reason is not None:
            return f"Error: File {path} rejected by entropy check: {reason}"
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f.readlines()):
                if re.search(re.escape(needle), line):
                    matches.append(f"{i}: {line.rstrip()}")
    except Exception as e:
        return f"Error applying patch: {str(e)}"
    return "\n".join(matches) if matches else "(no matches)"


@tool(description="""Reads a range of zero-indexed lines from a file, starting at 'from_line' up to (but not including) 'to_line'. The 'to_line' parameter can exceed the total number of lines in the file. Example: from_line 3, to_line 4 reads the 4th line (1-indexed). Example: from_line 2, to_line 2 returns an empty string.""")
def read_lines0(path: str, from_line: int, to_line: int) -> str:
    safety_error = _validate_path(path)
    if safety_error:
        return safety_error

    globals()[ 'read_lines_tuple3'] = ( path, from_line, to_line)

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    if 0 > from_line  :
        return f"Error: 0 > from_line"

    if from_line >to_line :
        return f"Error: from_line >to_line"

    reason = _entropy_reason(path)
    if reason is not None:
        return f"Error: File {path} rejected by entropy check: {reason}"

    with open(path, "r", encoding="utf-8") as f:
        # return ''.join( f.readlines( to_line - from_line)[from_line:]) # readlines is buggy (0 reads all lines)
        return ''.join( f.readlines()[from_line:to_line])
    

@tool(description="""Replaces the selected lines (selected via read_lines0) with the provided patch string. An end of line character is automatically added at the string. """)

def patch_lines0(patch_string: str) -> str:
    # 1. Safety Check

    if globals( ) ['read_lines_tuple3'] is None :
       return f"Error: read_lines0 was not called"

    ( path, from_line, to_line) = globals( ) ['read_lines_tuple3']

    globals( ) ['read_lines_tuple3'] = None

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    # 2. Read original content
    with open(path, "r", encoding="utf-8") as f:
        original_text = f.readlines()


    try:
        original_text[ from_line:to_line] = [patch_string, '\n']

        with open(path, "w", encoding="utf-8") as f:
            f.write( ''.join( original_text))
        return f"Successfully applied patch to {path}."

    except Exception as e:
        return f"Error applying patch: {str(e)}"

