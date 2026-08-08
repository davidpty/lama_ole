"""Development tools for lama_ole — filesystem, code, and git operations."""

__tool_readonly__ = True

import base64
import os
import re
import glob as glob_mod
import subprocess
import py_compile
from pathlib import Path
from typing import Optional

from tool_base import tool
from tools_security.validate_path import validate_path as _validate_path


@tool(description="Read the contents of a file as base64")
def read_file_as_base64(path: str) -> str:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    with open(path, "rb") as f:
        return base64.b64encode(f.read())

# crashes
# @tool(description="Read the contents of a file binary")
# def read_file_binary(path: str) -> str:
#     with open(path, "rb") as f:
#         return f.read()

