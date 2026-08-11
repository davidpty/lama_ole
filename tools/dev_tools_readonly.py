"""Readonly tools for lama_ole — filesystem, code, and git operations."""

__tool_readonly__ = True

import os
import re
import glob as glob_mod
import subprocess
import py_compile
from pathlib import Path
from typing import Optional, Any, Dict

from tool_base import tool
from tools_security.validate_path import validate_path as _validate_path


_DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{",
    r"\bwget\s+.*\||\bcurl\s+.*-o\b",
    r"\bchmod\s+-R\s+777\s+/",
    r">\s*/dev/",
]

DIRECTORIES_TO_AVOID = [
    ".git",
    "__pycache__",
]

def _validate_command(command: str) -> Optional[str]:
    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return (
                f"Blocked by safety check: command matches dangerous "
                f"pattern: {pattern}"
            )
    return None


def _read_file_entropy_checked(path: str) -> Optional[bytes]:
    """Read a file as bytes, or None if it fails the entropy check."""
    from security.entropychecker import EntropyChecker

    with open(path, "rb") as f:
        raw_content = f.read()

    if EntropyChecker().feed(raw_content).is_suspicious:
        return None
    return raw_content


def _append_skipped_files(content: str, skipped_files: list) -> str:
    """Append a summary of entropy-skipped files to a grep result string."""
    if not skipped_files:
        return content
    warning = (
        f"\n[Skipped {len(skipped_files)} file(s) due to entropy check]:\n"
        + "\n".join(f"  - {sf}" for sf in skipped_files)
    )
    return content + warning


@tool(description="Read the contents of a file")
def read_file(path: str) -> Dict[str, Any]:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        with open(path, "rb") as f:
            raw_content = f.read()

        from security.entropychecker import EntropyChecker

        result = EntropyChecker().feed(raw_content)
        if result.is_suspicious:
            return {
                "status": "error",
                "message": [f"File rejected by entropy check: {result.reason}"],
            }

        content = raw_content.decode("utf-8", errors="replace")
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
def grep(pattern: str, path: str = ".", include: str = "*", fixed = False) -> Dict[str, Any]:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    if fixed:
        pattern = re.escape(pattern)

    matches = []
    skipped_files = []
    try:
        if os.path.isfile(path):
            files_to_search = [path]
        elif os.path.isdir(path):
            files_to_search = []
            for root, _, files in os.walk(path):
                if any( [ x in DIRECTORIES_TO_AVOID for x in root.split('/')]):
                    continue
                for fname in files:
                    if glob_mod.fnmatch.fnmatch(fname, include):
                        files_to_search.append(os.path.join(root, fname))
        else:
            return {"status": "error", "message": [f"Path not found: {path}"]}

        for fpath in files_to_search:
            try:
                raw_content = _read_file_entropy_checked(fpath)
                if raw_content is None:
                    skipped_files.append(fpath)
                    continue

                content = raw_content.decode("utf-8", errors="replace")

                for i, line in enumerate(content.splitlines(), 1):
                    if re.search(pattern, line):
                        matches.append(f"{fpath}:{i}: {line.rstrip()}")
            except Exception:
                pass
        content = "\n".join(matches) if matches else "(no matches)"
        content = _append_skipped_files(content, skipped_files)
        return {"status": "success", "data": content}
    except Exception as e:
        return {"status": "error", "message": [str(e)]}

@tool(description="Search for a fixed string pattern in files under a path")
def grepF(pattern: str, path: str = ".", include: str = "*") -> Dict[str, Any]:
    return grep( pattern, path, include, fixed = True)

@tool(description="Search for range based regex pattern in a file, both patterns must exist in that file. If pattern_from is None or not provided, the start of the file is meant. If pattern_to is None or not provided, the end of the file is meant.")
def grep_range_based(pattern_from: str = None, pattern_to: str = None, path: str = ".", include: str = "*", fixed = False) -> Dict[str, Any]:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    if fixed:
        if pattern_from is not None:
            pattern_from = re.escape(pattern_from)
        if pattern_to is not None:
            pattern_to = re.escape(pattern_to)

    try:
        with open(path, "rb") as f:
            raw_content = f.read()

        from security.entropychecker import EntropyChecker

        result = EntropyChecker().feed(raw_content)
        if result.is_suspicious:
            return {
                "status": "error",
                "message": [f"File rejected by entropy check: {result.reason}"],
            }

        content = raw_content.decode("utf-8", errors="replace")

        if pattern_from is None:
            idx_from = 0
        else:
            i1 = re.finditer(pattern_from, content)
            try:
                m1 = next(i1)
                try:
                    next(i1)
                    return {"status": "error", "message": ["Error: pattern_from matches more than 1 time :"]}
                except StopIteration:
                    pass
            except StopIteration:
                return {"status": "error", "message": ["Error: pattern_from matches 0 times :"]}
            (idx_from, _) = m1.span()

        if pattern_to is None:
            idx_to = len(content)
        else:
            i2 = re.finditer(pattern_to, content)
            try:
                m2 = next(i2)
                try:
                    next(i2)
                    return {"status": "error", "message": ["Error: pattern_to matches more than 1 time :"]}
                except StopIteration:
                    pass
            except StopIteration:
                return {"status": "error", "message": ["Error: pattern_to matches 0 times :"]}
            (_, idx_to) = m2.span()

        return {"status": "success", "data": content[idx_from:idx_to]}
    except Exception as e:
        return {"status": "error", "message": [str(e)]}


@tool(description="Search for a fixed string range in files under a path. If pattern_from is None or not provided, the start of the file is meant. If pattern_to is None or not provided, the end of the file is meant.")
def grepF_range_based(pattern_from: str = None, pattern_to: str = None, path: str = ".", include: str = "*") -> Dict[str, Any]:
    return grep_range_based( pattern_from, pattern_to, path, include, fixed = True)


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

