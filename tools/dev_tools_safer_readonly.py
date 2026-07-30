"""Readonly tools for lama_ole — filesystem, code, and git operations."""

import os
import re
import glob as glob_mod
import subprocess
import py_compile
from pathlib import Path
from typing import Optional, Any, Dict

from tool_base import tool


_DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{",
    r"\bwget\s+.*\||\bcurl\s+.*-o\b",
    r"\bchmod\s+-R\s+777\s+/",
    r">\s*/dev/",
]


def _validate_command(command: str) -> Optional[str]:
    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return (
                f"Blocked by safety check: command matches dangerous "
                f"pattern: {pattern}"
            )
    return None


def _validate_path(path: str) -> Optional[str]:
    normalized = os.path.normpath(path)
    parts = normalized.split(os.sep)
    if ".." in parts:
        return (
            f"Blocked by safety check: path contains '..' traversal: {path}"
        )
    return None


@tool(description="Read the contents of a file")
def read_file(path: str) -> Dict[str, Any]:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"status": "success", "data": content}
    except Exception as e:
        return {"status": "error", "message": [str(e)]}

@tool(description="List entries in a directory")
def list_dir(path: str = ".") -> Dict[str, Any]:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        entries = os.listdir(path)
        lines = []
        for name in sorted(entries):
            full = os.path.join(path, name)
            if os.path.isdir(full):
                lines.append(f"{name}/")
            else:
                size = os.path.getsize(full)
                lines.append(f"{name}  ({size} bytes)")
        content = "\n".join(lines) if lines else "(empty directory)"
        return {"status": "success", "data": content}
    except Exception as e:
        return {"status": "error", "message": [str(e)]}


@tool(description="Search for a regex pattern in files under a path")
def grep(pattern: str, path: str = ".", include: str = "*") -> Dict[str, Any]:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    matches = []
    try:
        if os.path.isfile(path):
            files_to_search = [path]
        elif os.path.isdir(path):
            files_to_search = []
            for root, _, files in os.walk(path):
                for fname in files:
                    if glob_mod.fnmatch.fnmatch(fname, include):
                        files_to_search.append(os.path.join(root, fname))
        else:
            return {"status": "error", "message": [f"Path not found: {path}"]}

        for fpath in files_to_search:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if re.search(pattern, line):
                            matches.append(f"{fpath}:{i}: {line.rstrip()}")
            except Exception:
                pass
        content = "\n".join(matches) if matches else "(no matches)"
        return {"status": "success", "data": content}
    except Exception as e:
        return {"status": "error", "message": [str(e)]}

@tool(description="Search for a fixed string pattern in files under a path")
def grepF(pattern: str, path: str = ".", include: str = "*") -> Dict[str, Any]:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    matches = []
    try:
        if os.path.isfile(path):
            files_to_search = [path]
        elif os.path.isdir(path):
            files_to_search = []
            for root, _dirs, files in os.walk(path):
                for fname in files:
                    if glob_mod.fnmatch.fnmatch(fname, include):
                        files_to_search.append(os.path.join(root, fname))
        else:
            return {"status": "error", "message": [f"Path not found: {path}"]}

        for fpath in files_to_search:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if re.search(re.escape(pattern), line):
                            matches.append(f"{fpath}:{i}: {line.rstrip()}")
            except Exception:
                pass
        content = "\n".join(matches) if matches else "(no matches)"
        return {"status": "success", "data": content}
    except Exception as e:
        return {"status": "error", "message": [str(e)]}


@tool(description="Find files matching a glob pattern")
def glob_pattern(pattern: str) -> Dict[str, Any]:
    try:
        results = glob_mod.glob(pattern, recursive=True)
        content = "\n".join(sorted(results)) if results else "(no matches)"
        return {"status": "success", "data": content}
    except Exception as e:
        return {"status": "error", "message": [str(e)]}


@tool(description="Get metadata about a file or directory")
def file_info(path: str) -> Dict[str, Any]:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    p = Path(path)
    if not p.exists():
        return {"status": "error", "message": [f"{path} does not exist"]}
    stat = p.stat()
    lines = [
        f"Path: {p.resolve()}",
        f"Type: {'directory' if p.is_dir() else 'file'}",
        f"Size: {stat.st_size} bytes",
        f"Modified: {stat.st_mtime}",
        f"Permissions: {oct(stat.st_mode & 0o777)}",
    ]
    return {"status": "success", "data": "\n".join(lines)}


@tool(description="Check a Python file for syntax errors")
def syntax_check(path: str) -> Dict[str, Any]:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        py_compile.compile(path, doraise=True)
        return {"status": "success", "data": f"{path}: syntax OK"}
    except py_compile.PyCompileError as e:
        return {"status": "error", "message": [f"{path}: {e}"]}
    except Exception as e:
        return {"status": "error", "message": [str(e)]}

