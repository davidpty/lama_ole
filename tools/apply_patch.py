"""Tools for applying patches to files using diff_match_patch."""

import os
from diff_match_patch import diff_match_patch
from tool_base import tool
from tools_security.validate_path import validate_path as _validate_path

@tool(description="""Apply a patch to a specific file using the character-based diff_match_patch format.

STRICT FORMAT RULES for patch_string: MUST start directly with '@@' (Do NOT include '---' or '+++' filename header lines).

CRITICAL EXAMPLE:
Correct patch_string format:
\"\"\"
@@ -1,11 +1,21 @@
 Hello%20
 +Brave%20New%20
  World
\"\"\"

"""

)

def apply_patch(path: str, patch_string: str) -> str:
    """
    Reads the content of the file at 'path', applies the provided 
    'patch_string' (unified diff format, via diff_match_patch), and overwrites the file.
    Don't use the filename in the head of a patch chunk.
    """
    # 1. Safety Check
    safety_error = _validate_path(path)
    if safety_error:
        return safety_error

    if not os.path.exists(path):
        return f"Error: File {path} does not exist."

    try:
        # 2. Read original content
        with open(path, "r", encoding="utf-8") as f:
            original_text = f.read()

        # 3. Initialize diff_match_patch and parse the patch
        dmp = diff_match_patch()
        patches = dmp.patch_fromText(patch_string)

        # 4. Apply patches
        patched_text, success_flags = dmp.patch_apply(patches, original_text)

        # 5. Write back to file if successful
        if all(success_flags):
            with open(path, "w", encoding="utf-8") as f:
                f.write(patched_text)
            return f"Successfully applied patch to {path}."
        else:
            # If some parts failed, we return a warning but don't overwrite 
            # the file to prevent corruption of partial states.
            return f"Warning: Some parts of the patch failed to apply to {path}."

    except Exception as e:
        return f"Error applying patch: {str(e)}"
