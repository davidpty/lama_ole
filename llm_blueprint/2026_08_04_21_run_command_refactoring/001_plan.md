# Plan for refactoring run_command tool

## Overview
The `run_command` tool is currently located in the outdated and insecure file `lama_ole/tools_insecure_outdated_deprecated/dev_tools.py`. It needs to be extracted into its own dedicated module in `lama_ole/tools/run_command.py` for better organization and maintenance.

## Steps

1.  **Identify the target function**: Locate the `run_command` function in `lama_ole/tools_insecure_outdated_deprecated/dev_tools.py`.
2.  **Create the new file**: Create a new file at `lama_ole/tools/run_command.py`.
3.  **Extract dependencies and security logic**: Identify necessary imports (`subprocess`, `re`) and include the `_DANGEROUS_PATTERNS` and `_validate_command` function to maintain safety measures.
4.  **Implement standard return format**: Refactor `run_command` to use the `@tool` decorator and return a dictionary with `"status": "success"` or `"status": "error"`, as per project standards defined in `AGENTS.md`.
5.  **Verify**: Ensure the new module is syntactically correct, follows Python 3.9+ compatibility rules (e.g., using `Optional` from `typing`), and adheres to the tool implementation standards in `AGENTS.md`.

## Files Involved
- `lama_ole/tools_insecure_outdated_deprecated/dev_tools.py` (Source)
- `lama_ole/tools/run_command.py` (Destination)

