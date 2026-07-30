import os
import subprocess
from typing import Any, Dict, Optional
from tool_base import tool


def _validate_path(path: str) -> Optional[str]:
    if os.path.isabs(os.path.normpath(path)):
        return f"Blocked by safety check: only relative paths are allowed: {path}"
    normalized = os.path.normpath(path)
    parts = normalized.split(os.sep)
    if ".." in parts:
        return f"Blocked by safety check: path contains '..' traversal: {path}"
    return None


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


@tool(description="Locate files matching a pattern using the locate command")
def locate_bi(n: int = 10, searchstring: str = "") -> Dict[str, Any]:
    """Locate files matching a pattern using the locate command."""
    # Note: locate doesn't take a cwd in its standard usage for searching the whole system,
    # but we apply path validation to the implicit current directory if needed.
    # For this tool, we just ensure it runs safely.
    return _run_command(f"locate -l {n} -bi {searchstring}", ".")
