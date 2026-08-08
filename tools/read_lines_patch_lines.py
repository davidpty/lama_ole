"""Tools for applying patches to files using read_lines and patch_lines."""


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

@tool(description="Provides help and usage instructions for the read_lines and patch_lines tools.")
def read_lines_patch_lines_help() :
    return """To edit a file using these tools, follow this workflow:
1. Use `grep_from_file` (for regex) or `grepF_from_file` (for fixed strings) to find the line numbers and content of the parts you want to change. These tools return one-indexed line numbers.
2. Use `read_lines` with the identified line numbers to read the specific section of the file. This prepares the tool for patching.
3. Use `patch_lines` with your new content to replace the previously read lines.

Note: 
- `grep_from_file` and `grepF_from_file` return one-indexed line numbers.
- `read_lines` uses these one-indexed numbers.
- `patch_lines` must be called immediately after a successful `read_lines` call for the same file; otherwise, it will fail.
"""

@tool(description="Searches for a regex pattern in a file and returns one-indexed line numbers and their content (format: 'line_number: content'). These indices can be used with the `read_lines` tool.")
def grep_from_file(pattern: str, path: str) -> str:

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
            for i, line in enumerate(f.readlines(), 1):
                if re.search(pattern, line):
                    matches.append(f"{i}: {line.rstrip()}")
    except Exception as e:
        return f"Error applying patch: {str(e)}"
    return "\n".join(matches) if matches else "(no matches)"

@tool(description="Searches for a fixed string needle in a given file (parameter path), returns one-indexed line numbers and their content (format: 'line_number: content'). Case sensitive. These indices can be used with the `read_lines` tool.")
def grepF_from_file(needle: str, path: str) -> str:

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
            for i, line in enumerate(f.readlines(), 1):
                if re.search(re.escape(needle), line):
                    matches.append(f"{i}: {line.rstrip()}")
    except Exception as e:
        return f"Error applying patch: {str(e)}"
    return "\n".join(matches) if matches else "(no matches)"


@tool(description="Reads a range of one-indexed lines from a file, starting at 'from_line' up to (but not including) 'to_line'. This tool must be called before 'patch_lines' to select the lines for replacement or insertion.")
def read_lines(path: str, from_line: int, to_line: int) -> str:
    safety_error = _validate_path(path)
    if safety_error:
        return safety_error

    from_line = from_line-1 # one indexed to zero indexed
    globals()[ 'read_lines_tuple3'] = ( path, from_line, to_line)

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    if 1 > from_line +1 :
        return f"Error: 1 > from_line : {from_line + 1}"

    if from_line >to_line :
        return f"Error: from_line >to_line"

    reason = _entropy_reason(path)
    if reason is not None:
        return f"Error: File {path} rejected by entropy check: {reason}"

    with open(path, "r", encoding="utf-8") as f:
        # return ''.join( f.readlines( to_line - from_line)[from_line:]) # readlines is buggy (0 reads all lines)
        return ''.join( f.readlines()[from_line:to_line])
    

@tool(description="Replaces the lines previously selected by 'read_lines' with the provided patch string. An end-of-line character is automatically appended to the patch string.")
def patch_lines(patch_string: str) -> str:
    # 1. Safety Check

    if globals( ) ['read_lines_tuple3'] is None :
       return f"Error: read_lines was not called"

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

