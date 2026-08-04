# Safe Switch Overview (`--safe`)

The `--safe` flag in `lama_ole` enables a confirmation mechanism for tools identified as "dangerous." When this flag is active, the system will prompt the user for manual approval before executing any tool that matches the predefined list of dangerous operations.

## Affected Tools

The following tools are currently classified as **DANGEROUS** and will require explicit user confirmation when `--safe` is enabled:

| Tool Name | Description / Risk |
|-----------|-------------------|
| `run_command` | Executes arbitrary shell commands on the host system. |
| `write_file` | Creates or overwrites files on the filesystem. |
| `append_file` | Appends data to existing files on the filesystem. |
| `replace_in_file` | Modifies content within an existing file. |
| `delete_file` | Removes files from the filesystem. |

## Behavior

When a tool call is intercepted:
1. The system identifies if the tool name is in the `DANGEROUS_TOOLS` registry.
2. If `--safe` is active, it prints a warning message to `stderr`:
   `[DANGER] Tool '<tool_name>' called with: <arguments>`
3. It prompts the user: `Proceed? (y/N): `.
4. **If 'y'**: The tool executes normally.
5. **If anything else (e.g., 'n', Enter, Ctrl+C)**: Execution is cancelled, and an error message is returned to the model: 
   `Execution of '<tool_name>' cancelled by user (safe mode).`

## Implementation Details

The logic is implemented in `lama_ole/tool_base.py` within the `run_with_tools` function loop. The list of dangerous tools is maintained in the `DANGEROUS_TOOLS` set.
