"""Readonly tools for lama_ole — filesystem, code, and git operations."""

import os
import re
import glob as glob_mod
import subprocess
import py_compile
from pathlib import Path
from typing import Optional

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
def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

@tool(description="List entries in a directory")
def list_dir(path: str = ".") -> str:
    entries = os.listdir(path)
    lines = []
    for name in sorted(entries):
        full = os.path.join(path, name)
        if os.path.isdir(full):
            lines.append(f"{name}/")
        else:
            size = os.path.getsize(full)
            lines.append(f"{name}  ({size} bytes)")
    return "\n".join(lines) if lines else "(empty directory)"


@tool(description="Search for a regex pattern in files under a directory")
def grep(pattern: str, path: str = ".", include: str = "*") -> str:
    matches = []
    for root, _dirs, files in os.walk(path):
        for fname in files:
            if not glob_mod.fnmatch.fnmatch(fname, include):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if re.search(pattern, line):
                            matches.append(f"{fpath}:{i}: {line.rstrip()}")
            except Exception:
                pass
    return "\n".join(matches) if matches else "(no matches)"

@tool(description="Search for a fixed string pattern in files under a directory")
def grepF(pattern: str, path: str = ".", include: str = "*") -> str:
    matches = []
    for root, _dirs, files in os.walk(path):
        for fname in files:
            if not glob_mod.fnmatch.fnmatch(fname, include):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if re.search( re.escape( pattern), line):
                            matches.append(f"{fpath}:{i}: {line.rstrip()}")
            except Exception:
                pass
    return "\n".join(matches) if matches else "(no matches)"


@tool(description="Find files matching a glob pattern")
def glob_pattern(pattern: str) -> str:
    results = glob_mod.glob(pattern, recursive=True)
    return "\n".join(sorted(results)) if results else "(no matches)"


@tool(description="Get metadata about a file or directory")
def file_info(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"Error: {path} does not exist"
    stat = p.stat()
    lines = [
        f"Path: {p.resolve()}",
        f"Type: {'directory' if p.is_dir() else 'file'}",
        f"Size: {stat.st_size} bytes",
        f"Modified: {stat.st_mtime}",
        f"Permissions: {oct(stat.st_mode & 0o777)}",
    ]
    return "\n".join(lines)


@tool(description="Check a Python file for syntax errors")
def syntax_check(path: str) -> str:
    try:
        py_compile.compile(path, doraise=True)
        return f"{path}: syntax OK"
    except py_compile.PyCompileError as e:
        return f"{path}: {e}"


# @tool(description="Show git status for a repository")
# def git_status(path: str = ".") -> str:
#     return _git_cmd("status", path)
# 
# 
# @tool(description="Show unstaged git diff for a repository")
# def git_diff(path: str = ".") -> str:
#     return _git_cmd("diff", path)
# 
# 
# @tool(description="Show recent git log entries")
# def git_log(n: int = 10, path: str = ".") -> str:
#     return _git_cmd(f"log --oneline -{n}", path)
# 
# 
# def _git_cmd(args: str, path: str) -> str:
#     try:
#         result = subprocess.run(
#             f"git {args}",
#             shell=True,
#             capture_output=True,
#             text=True,
#             timeout=15,
#             cwd=path,
#         )
#         output = ""
#         if result.stdout:
#             output += result.stdout
#         if result.stderr:
#             if output:
#                 output += "\n--- stderr ---\n"
#             output += result.stderr
#         if result.returncode != 0:
#             output += f"\n(exit code: {result.returncode})"
#         return output if output else "(no output)"
#     except Exception as e:
#         return f"Error: {e}"
# 
# 
# @tool(description="locate -l 10 -bi ...")
# def locate_bi(n: int = 10, searchstring: str = "") -> str:
#     return _locate_cmd(f" -l {n} -bi {searchstring}", path = ".")
# 
# def _locate_cmd(args: str, path: str) -> str:
#     try:
#         result = subprocess.run(
#             f"locate {args}",
#             shell=True,
#             capture_output=True,
#             text=True,
#             timeout=15,
#             cwd=path,
#         )
#         output = ""
#         if result.stdout:
#             output += result.stdout
#         if result.stderr:
#             if output:
#                 output += "\n--- stderr ---\n"
#             output += result.stderr
#         if result.returncode != 0:
#             output += f"\n(exit code: {result.returncode})"
#         return output if output else "(no output)"
#     except Exception as e:
#         return f"Error: {e}"
