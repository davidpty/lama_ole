import os
import subprocess
from typing import Any, Dict
from tool_base import tool
from tools_security.validate_path import validate_path as _validate_path


def _run_command(cmd: str, cwd: str) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=cwd,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n--- stderr ---\n"
            output += result.stderr
        if result.returncode != 0:
            output += f"\n(exit code: {result.returncode})"

        if not output:
            output = "(no output)"

        return {"status": "success", "data": output}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tool(description="Show git status for a repository")
def git_status(path: str = ".") -> Dict[str, Any]:
    """Show git status for a repository."""
    validation_error = _validate_path(path)
    if validation_error:
        return {"status": "error", "message": validation_error}
    return _run_command("git status", path)


@tool(description="Show unstaged git diff for a repository")
def git_diff(path: str = ".") -> Dict[str, Any]:
    """Show unstaged git diff for a repository."""
    validation_error = _validate_path(path)
    if validation_error:
        return {"status": "error", "message": validation_error}
    return _run_command("git diff", path)


@tool(description="Show recent git log entries")
def git_log(n: int = 10, path: str = ".") -> Dict[str, Any]:
    """Show recent git log entries."""
    validation_error = _validate_path(path)
    if validation_error:
        return {"status": "error", "message": validation_error}
    return _run_command(f"git log --oneline -{n}", path)
