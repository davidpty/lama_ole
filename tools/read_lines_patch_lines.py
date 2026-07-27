"""Tools for applying patches to files using read_lines and patch_lines."""

import os
from typing import Optional
from tool_base import tool
import re
import glob as glob_mod

# Reusing safety logic from the provided example
def _validate_path(path: str) -> Optional[str]:
    normalized = os.path.normpath(path)
    parts = normalized.split(os.sep)
    if ".." in parts:
        return (
            f"Blocked by safety check: path contains '..' traversal: {path}"
        )
    return None

@tool(description="Searches for a regex pattern in a given file (parameter path), outputs zero indexed linenumbers applicable for the tools read_lines and patch_lines")
def grep0_from_file(pattern: str, path: str) -> str:

    safety_error = _validate_path(path)
    if safety_error:
        return safety_error

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    matches = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f.readlines()):
                if re.search(pattern, line):
                    matches.append(f"{i}: {line.rstrip()}")
    except Exception as e:
        return f"Error applying patch: {str(e)}"
    return "\n".join(matches) if matches else "(no matches)"


@tool(description="""Reads a range of zero-indexed lines from a file, starting at 'from_line' up to (but not including) 'to_line'. The 'to_line' parameter can exceed the total number of lines in the file. Example: from_line 3, to_line: 4 selectts the 4th line (1 indexed) in the file, Example: from_line 2, to_line: 2 selects 0 lines""")

def read_lines(path: str, from_line: int, to_line: int) -> str:
    safety_error = _validate_path(path)
    if safety_error:
        return safety_error

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    if from_line >to_line :
        return f"Error: from_line >to_line"

    with open(path, "r", encoding="utf-8") as f:
        # return ''.join( f.readlines( to_line - from_line)[from_line:]) # readlines is buggy (0 reads all lines)
        return ''.join( f.readlines()[from_line:to_line])
    

@tool(description="""Replaces a range of zero-indexed lines [from_line, to_line) in a file with the provided patch string. The 'to_line' parameter must be greater than or equal to 'from_line'. Example: from_line 3, to_line: 4 replaces the line after the the first 3 lines of the file, from_line 2, Example: to_line: 2 fills in new lines after the first 2 lines in the file. """)

def patch_lines(path: str, from_line: int, to_line: int, patch_string: str) -> str:
    # 1. Safety Check
    safety_error = _validate_path(path)
    if safety_error:
        return safety_error

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    try:
        # 2. Read original content
        with open(path, "r", encoding="utf-8") as f:
            original_text = f.readlines()

        original_text[ from_line:to_line] = [patch_string]

        with open(path, "w", encoding="utf-8") as f:
            f.write( ''.join( original_text))
        return f"Successfully applied patch to {path}."

    except Exception as e:
        return f"Error applying patch: {str(e)}"
