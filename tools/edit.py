"""Tools for editing files."""


import os
from typing import Optional
from tool_base import tool
import re
import glob as glob_mod

read_lines_tuple3 = None

# Reusing safety logic from the provided example
def _validate_path(path: str) -> Optional[str]:
    import pathlib
    parts = pathlib.Path(path).parts
    if ".." in parts:
        return f"Blocked by safety check: path contains '..' traversal: {path}"

    normalized = os.path.normpath(path)
    # The .. check above handles the requirement. 
    # We can keep the rest for consistency if needed, but it's mostly redundant now.
    return None

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
        return {"status": "success", "data": f"Successfully applied patch to {path}."}

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
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "success", "data": f"Successfully created file {path}."}
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
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return {"status": "success", "data": f"Successfully appended to {path}."}
    except Exception as e:
        return {"status": "error", "message": [f"Error appending to file: {str(e)}"]}

@tool(description="Creates a directory and all necessary parent directories. Refuses absolute paths or paths containing '..' for safety.")
def makedirs(path: str):
    # 1. Safety Check
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

