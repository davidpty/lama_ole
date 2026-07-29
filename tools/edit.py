"""Tools for editing files."""


import os
from typing import Optional
from tool_base import tool
import re
import glob as glob_mod

read_lines_tuple3 = None

# Reusing safety logic from the provided example
def _validate_path(path: str) -> Optional[str]:
    normalized = os.path.normpath(path)
    parts = normalized.split(os.sep)
    if ".." in parts:
        return (
            f"Blocked by safety check: path contains '..' traversal: {path}"
        )
    return None

@tool(description="Returns a description of this module.")
def edit_help():
    return """Tools for editing files."""
    

@tool(description="""Replaces the 'search' string with the 'replace' string in the file at 'path'. The 'search' string must match exactly once in the file to ensure it is unambiguous.""")

def edit( path: str, search: str, replace: str) -> str:
    # 1. Safety Check

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    safety_error = _validate_path(path)
    if safety_error:
        return safety_error

    # 2. Read original content
    with open(path, "r", encoding="utf-8") as f:
        original_text = f.read()

    match_count = original_text.count( search)

    if match_count != 1:
        return f"Error: search string matches not exactly 1 time : {match_count}."

    edited_text = original_text.replace( search, replace)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write( edited_text)
        return f"Successfully applied patch to {path}."

    except Exception as e:
        return f"Error applying patch: {str(e)}"

