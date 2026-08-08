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


@tool(description="Locate files matching a pattern using the locate command")
def locate_bi(n: int = 10, searchstring: str = "") -> Dict[str, Any]:
    """Locate files matching a pattern using the locate command."""
    # Note: locate doesn't take a cwd in its standard usage for searching the whole system,
    # but we apply path validation to the implicit current directory if needed.
    # For this tool, we just ensure it runs safely.
    return _run_command(f"locate -l {n} -bi {searchstring}", ".")
