"""Tool for running tests."""

import os
import sys

# Ensure the directory containing 'tool_base.py' is in sys.path
current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import subprocess
from typing import Optional, Union, Dict, Any
from tool_base import tool


@tool(description="Runs a python test file and returns its output.")
def run_test(path: str) -> Union[str, Dict[str, Any]]:
    """Runs a python test file using unittest and returns the result."""
    if not os.path.exists(path):
        return {"status": "error", "message": [f"File {path} does not exist."]}

    # Prepare environment for running tests
    env = os.environ.copy()
    # Add current directory to PYTHONPATH so that 'lama_ole' can be found if it's a package
    current_dir = os.getcwd()
    env["PYTHONPATH"] = current_dir + os.pathsep + env.get("PYTHONPATH", "")

    try:
        # We use -m unittest to run the test file as a script/module
        process = subprocess.run(
            [sys.executable, "-m", "unittest", path],
            capture_output=True,
            text=True,
            env=env
        )

        if process.returncode == 0:
            # unittest output can be in stdout or stderr depending on how it's run, 
            # but usually stdout for successful runs.
            output = process.stdout if process.stdout else ""
            return {"status": "success", "data": f"Tests passed:\n{output}"}
        else:
            # unittest puts errors in stderr and sometimes stdout
            output = (process.stderr + "\n" + process.stdout).strip()
            return {"status": "error", "message": [f"Tests failed:\n{output}"]}

    except Exception as e:
        return {"status": "error", "message": [f"An error occurred while running tests: {str(e)}"]}
