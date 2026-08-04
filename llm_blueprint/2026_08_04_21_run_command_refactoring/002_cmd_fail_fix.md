# Plan for fixing command failure reporting in `run_command`

## Issue
Currently, the `run_command` tool returns a `"status": "success"` even when the executed shell command fails (i.e., returns a non-zero exit code). The error message and exit code are included in the `data` field of the success response, which can be misleading for both users and LLMs who expect an `"error"` status for failed operations.

**Example of current behavior:**
```python
>>> r.run_command('/bin/lsp')
{'status': 'success', 'data': '/bin/sh: 1: /bin/lsp: not found\n\n(exit code: 127)'}
```

## Proposed Fix
Modify the `run_command` implementation to check the `returncode` of the subprocess. If the return code is non-zero, the tool should return a `"status": "error"` response containing the command's output (stdout and stderr) in the `message` field.

### Implementation Details

1.  **Check Return Code**: After running `subprocess.run`, check if `result.returncode != 0`.
2.  **Construct Error Response**: If it failed, capture the combined stdout/stderr and return:
    ```python
    {
        "status": "error",
        "message": [output_with_exit_code]
    }
    ```
3.  **Maintain Success Path**: For `returncode == 0`, continue returning `"status": "success"` with the output in the `data` field.

## Expected Behavior after Fix

**Successful command:**
```python
>>> r.run_command('/bin/echo 1')
{'status': 'success', 'data': '1\n'}
```

**Failed command:**
```python
>>> r.run_command('/bin/lsp')
{'status': 'error', 'message': ['/bin/sh: 1: /bin/lsp: not found\n\n(exit code: 127)']}
```

## Files Involved
- `lama_ole/tools/run_command.py` (Target for modification)
