from __future__ import annotations

import re
import subprocess
from typing import Optional, Any

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


@tool(description="Execute a shell command and return its output")
def run_command(command: str, timeout: int = 30) -> dict[str, Any]:
    """Execute a shell command and return its output."""

    # sometimes LLMs give the wrong datatype
    if isinstance( timeout, str):
       timeout = int( timeout)


    error = _validate_command(command)
    if error:
        return {"status": "error", "message": [error]}

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
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

        if result.returncode != 0:
            return {"status": "error", "message": [output]}

        return {"status": "success", "data": output}

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": [f"Error: command timed out after {timeout}s"]}
    except Exception as e:
        return {"status": "error", "message": [f"Error: {e}"]}

