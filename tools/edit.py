"""Tools for editing files."""


import os
import difflib
from tool_base import tool
import re
import glob as glob_mod
from tools_security.validate_path import validate_path as _validate_path

read_lines_tuple3 = None


def _unified_diff(old: str, new: str, path: str) -> str:
    """Return a compact unified diff between two file contents (or '' if none)."""
    if old == new:
        return ""
    diff_lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )
    return _trim_diff("".join(diff_lines).rstrip())


def _trim_diff(diff: str) -> str:
    """Strip common indentation from changed lines so the patch stays compact."""
    if not diff:
        return diff
    lines = diff.split("\n")
    content_lines = [
        line
        for line in lines
        if line and line[0] in "+- " and not line.startswith("---") and not line.startswith("+++")
    ]
    if not content_lines:
        return diff

    min_indent = None
    for line in content_lines:
        content = line[1:]
        if content.strip():
            indent = len(content) - len(content.lstrip(" "))
            min_indent = indent if min_indent is None else min(min_indent, indent)
    if not min_indent:
        return diff

    out = []
    for line in lines:
        if line and line[0] in "+- " and not line.startswith("---") and not line.startswith("+++"):
            out.append(line[0] + line[1:][min_indent:])
        else:
            out.append(line)
    return "\n".join(out)

@tool(description="Returns a description of this module.")
def edit_help():
    return """Tools for editing files."""
    

@tool(description="""Replaces the 'search' string with the 'replace' string in the file at 'path'. The 'search' string must match exactly once in the file to ensure it is unambiguous.""")
def edit( path: str, search: str, replace: str) -> str:
    # 1. Safety Check

    if not os.path.exists(path):
        return {"status": "error", "message": ["File", path, "does not exist."]}

    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    # 2. Read original content
    with open(path, "r", encoding="utf-8") as f:
        original_text = f.read()

    match_count = original_text.count( search)

    if match_count != 1:
        return {"status": "error", "message": ["Error: search string matches not exactly 1 time :", match_count]}

    edited_text = original_text.replace( search, replace)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write( edited_text)
        return {
            "status": "success",
            "data": f"Successfully applied patch to {path}.",
            "file": path,
            "diff": _unified_diff(original_text, edited_text, path),
        }

    except Exception as e:
        return {"status": "error", "message": ["Error applying patch:", str(e)]}

@tool(description="Creates a new file with the specified content at the given path. Fails if the file already exists.")
def create_new_file(path: str, content: str):
    # 1. Safety Check
    if os.path.exists(path):
        return {"status": "error", "message": [f"File {path} already exists."]}

    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {
            "status": "success",
            "data": f"Successfully created file {path}.",
            "file": path,
            "diff": _unified_diff("", content, path),
        }
    except Exception as e:
        return {"status": "error", "message": [f"Error creating file: {str(e)}"]}

@tool(description="Appends the specified content to the end of the file at the given path.")
def append_to_file(path: str, content: str):
    # 1. Safety Check
    if not os.path.exists(path):
        return {"status": "error", "message": [f"File {path} does not exist."]}

    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        with open(path, "r", encoding="utf-8") as f:
            original_text = f.read()
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return {
            "status": "success",
            "data": f"Successfully appended to {path}.",
            "file": path,
            "diff": _unified_diff(original_text, original_text + content, path),
        }
    except Exception as e:
        return {"status": "error", "message": [f"Error appending to file: {str(e)}"]}

@tool(description="Creates a directory and all necessary parent directories. Refuses absolute paths or paths containing '..' for safety.")
def makedirs(path: str):
    # 1. Safety Check
    if not path or not path.strip():
        return {"status": "error", "message": ["Refused: Path must not be empty."]}

    if os.path.isabs(path):
        return {"status": "error", "message": [f"Refused: Path must be relative, not absolute: {path}"]}

    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        os.makedirs(path, exist_ok=True)
        return {"status": "success", "data": f"Successfully created directory chain: {path}"}
    except Exception as e:
        return {"status": "error", "message": [f"Error creating directories: {str(e)}"]}

