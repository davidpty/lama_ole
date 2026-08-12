"""Tools for editing files."""


import os
import difflib
from tool_base import tool
import re
import glob as glob_mod
from tools_security.validate_path import validate_path as _validate_path

read_lines_tuple3 = None


def _unified_diff(old: str, new: str, path: str) -> str:
    """Return a compact unified diff between two file contents (or '' if none)."""
    if old == new:
        return ""
    diff_lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )
    return _trim_diff("".join(diff_lines).rstrip())


def _trim_diff(diff: str) -> str:
    """Strip common indentation from changed lines so the patch stays compact."""
    if not diff:
        return diff
    lines = diff.split("\n")
    content_lines = [
        line
        for line in lines
        if line and line[0] in "+- " and not line.startswith("---") and not line.startswith("+++")
    ]
    if not content_lines:
        return diff

    min_indent = None
    for line in content_lines:
        content = line[1:]
        if content.strip():
            indent = len(content) - len(content.lstrip(" "))
            min_indent = indent if min_indent is None else min(min_indent, indent)
    if not min_indent:
        return diff

    out = []
    for line in lines:
        if line and line[0] in "+- " and not line.startswith("---") and not line.startswith("+++"):
            out.append(line[0] + line[1:][min_indent:])
        else:
            out.append(line)
    return "\n".join(out)

@tool(description="Returns a description of this module.")
def edit_help():
    return """Tools for editing files."""
    

@tool(description="""Replaces the 'search' string with the 'replace' string in the file at 'path'. The 'search' string must match exactly once in the file to ensure it is unambiguous.""")
def edit( path: str, search: str, replace: str) -> str:
    # 1. Safety Check

    if not os.path.exists(path):
        return {"status": "error", "message": ["File", path, "does not exist."]}

    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    # 2. Read original content
    with open(path, "r", encoding="utf-8") as f:
        original_text = f.read()

    match_count = original_text.count( search)

    if match_count != 1:
        return {"status": "error", "message": ["Error: search string matches not exactly 1 time :", match_count]}

    edited_text = original_text.replace( search, replace)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write( edited_text)
        return {
            "status": "success",
            "data": f"Successfully applied patch to {path}.",
            "file": path,
            "diff": _unified_diff(original_text, edited_text, path),
        }

    except Exception as e:
        return {"status": "error", "message": ["Error applying patch:", str(e)]}

@tool(description="""Replaces the contiguous range of text from 'search_from' to 'search_to' (both inclusive) with the 'replace' string in the file at 'path'. Both 'search_from' and 'search_to' must each match exactly once in the file, must not overlap, and 'search_from' must come before 'search_to'.""")
def edit_range_based( path: str, search_from: str, search_to: str, replace: str) -> str:
    # 1. Safety Check

    if not os.path.exists(path):
        return {"status": "error", "message": ["File", path, "does not exist."]}

    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    # 2. Read original content
    with open(path, "r", encoding="utf-8") as f:
        original_text = f.read()

    from_count = original_text.count(search_from)
    if from_count != 1:
        return {"status": "error", "message": ["Error: search_from string matches not exactly 1 time :", from_count]}

    to_count = original_text.count(search_to)
    if to_count != 1:
        return {"status": "error", "message": ["Error: search_to string matches not exactly 1 time :", to_count]}

    from_start = original_text.index(search_from)
    from_end = from_start + len(search_from)
    to_start = original_text.index(search_to)
    to_end = to_start + len(search_to)

    if from_end > to_start:
        return {"status": "error", "message": ["Error: search_from and search_to overlap, or search_from does not come before search_to."]}

    edited_text = original_text[:from_start] + replace + original_text[to_end:]

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write( edited_text)
        return {
            "status": "success",
            "data": f"Successfully applied patch to {path}.",
            "file": path,
            "diff": _unified_diff(original_text, edited_text, path),
        }

    except Exception as e:
        return {"status": "error", "message": ["Error applying patch:", str(e)]}

@tool(description="""Replaces the contiguous range of text from 'pe_search_from' to 'pe_search_to' (both inclusive) in the file at 'path2edit' with the contiguous range of text from 'pg_search_from' to 'pg_search_to' (both inclusive) read from the file at 'path2grep'. All search strings are fixed strings and must each match exactly once. If a search string is None or not provided, the start of the corresponding file is meant for the *_from parameters and the end of the corresponding file for the *_to parameters.""")
def edit_range_based_file( path2edit: str, path2grep: str, pe_search_from: str = None, pe_search_to: str = None, pg_search_from: str = None, pg_search_to: str = None ) -> str:
    # 1. Safety Checks

    if not os.path.exists(path2edit):
        return {"status": "error", "message": ["File", path2edit, "does not exist."]}

    safety_error = _validate_path(path2edit)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    if path2grep is None:
        return {"status": "error", "message": ["Error: path2grep must be provided."]}

    if not os.path.exists(path2grep):
        return {"status": "error", "message": ["File", path2grep, "does not exist."]}

    safety_error = _validate_path(path2grep)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    # 2. Read original content of both files
    with open(path2edit, "r", encoding="utf-8") as f:
        original_text = f.read()

    with open(path2grep, "r", encoding="utf-8") as f:
        grep_text = f.read()

    # 3. Locate the range in path2edit (fixed string search, exactly one match each)
    if pe_search_from is None:
        pe_from_start = 0
    else:
        pe_from_count = original_text.count(pe_search_from)
        if pe_from_count != 1:
            return {"status": "error", "message": ["Error: pe_search_from string matches not exactly 1 time :", pe_from_count]}
        pe_from_start = original_text.index(pe_search_from)

    if pe_search_to is None:
        pe_to_end = len(original_text)
    else:
        pe_to_count = original_text.count(pe_search_to)
        if pe_to_count != 1:
            return {"status": "error", "message": ["Error: pe_search_to string matches not exactly 1 time :", pe_to_count]}
        pe_to_start = original_text.index(pe_search_to)
        pe_to_end = pe_to_start + len(pe_search_to)

    if pe_search_from is not None and pe_search_to is not None:
        pe_from_end = pe_from_start + len(pe_search_from)
        if pe_from_end > pe_to_start:
            return {"status": "error", "message": ["Error: pe_search_from and pe_search_to overlap, or pe_search_from does not come before pe_search_to."]}

    # 4. Locate the range in path2grep (fixed string search, exactly one match each)
    if pg_search_from is None:
        pg_from_start = 0
    else:
        pg_from_count = grep_text.count(pg_search_from)
        if pg_from_count != 1:
            return {"status": "error", "message": ["Error: pg_search_from string matches not exactly 1 time :", pg_from_count]}
        pg_from_start = grep_text.index(pg_search_from)

    if pg_search_to is None:
        pg_to_end = len(grep_text)
    else:
        pg_to_count = grep_text.count(pg_search_to)
        if pg_to_count != 1:
            return {"status": "error", "message": ["Error: pg_search_to string matches not exactly 1 time :", pg_to_count]}
        pg_to_start = grep_text.index(pg_search_to)
        pg_to_end = pg_to_start + len(pg_search_to)

    if pg_search_from is not None and pg_search_to is not None:
        pg_from_end = pg_from_start + len(pg_search_from)
        if pg_from_end > pg_to_start:
            return {"status": "error", "message": ["Error: pg_search_from and pg_search_to overlap, or pg_search_from does not come before pg_search_to."]}

    # 5. Replace the edit range (inclusive of the boundary patterns) with the grep range
    replace = grep_text[pg_from_start:pg_to_end]

    edited_text = original_text[:pe_from_start] + replace + original_text[pe_to_end:]

    try:
        with open(path2edit, "w", encoding="utf-8") as f:
            f.write( edited_text)
        return {
            "status": "success",
            "data": f"Successfully applied patch to {path2edit}.",
            "file": path2edit,
            "diff": _unified_diff(original_text, edited_text, path2edit),
        }

    except Exception as e:
        return {"status": "error", "message": ["Error applying patch:", str(e)]}

def _resolve_split_search_param(text: str, x1: str, x2: str, name: str):
    """Resolve a split search parameter (name1 + name2) to a single match.

    Returns None when both x1 and x2 are None (meaning start/end of file).
    Otherwise returns (start, search_len, boundary), where boundary is the
    split point between x1 and x2. Raises ValueError when the concatenated
    string does not match exactly once in text.
    """
    if x1 is None and x2 is None:
        return None
    x1 = x1 or ""
    x2 = x2 or ""
    search = x1 + x2
    count = text.count(search)
    if count != 1:
        raise ValueError("%s1+%s2 string matches not exactly 1 time : %d" % (name, name, count))
    start = text.index(search)
    return (start, len(search), start + len(x1))

@tool(description="""Second form of edit_range_based_file where each search parameter is split into two parts. The concatenation of the X1 and X2 parts represents the search string X, and the split point between X1 and X2 marks the start (for the *_from parameters) or the end (for the *_to parameters) of the range. Replaces the range of text in 'path2edit' starting at the split point of 'pe_search_from1'+'pe_search_from2' and ending at the split point of 'pe_search_to1'+'pe_search_to2' with the corresponding range read from 'path2grep' (from the split point of 'pg_search_from1'+'pg_search_from2' to the split point of 'pg_search_to1'+'pg_search_to2'). All search strings are fixed strings and must each match exactly once, and the from/to strings must not overlap. If both parts of a parameter are None or not provided, the start of the corresponding file is meant for the *_from parameters and the end of the corresponding file for the *_to parameters; if only one part is None it is treated as an empty string.""")
def edit_range_based_file2( path2edit: str, path2grep: str, pe_search_from1: str = None, pe_search_from2: str = None, pe_search_to1: str = None, pe_search_to2: str = None, pg_search_from1: str = None, pg_search_from2: str = None, pg_search_to1: str = None, pg_search_to2: str = None ) -> str:
    # 1. Safety Checks

    if not os.path.exists(path2edit):
        return {"status": "error", "message": ["File", path2edit, "does not exist."]}

    safety_error = _validate_path(path2edit)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    if path2grep is None:
        return {"status": "error", "message": ["Error: path2grep must be provided."]}

    if not os.path.exists(path2grep):
        return {"status": "error", "message": ["File", path2grep, "does not exist."]}

    safety_error = _validate_path(path2grep)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    # 2. Read original content of both files
    with open(path2edit, "r", encoding="utf-8") as f:
        original_text = f.read()

    with open(path2grep, "r", encoding="utf-8") as f:
        grep_text = f.read()

    # 3. Locate the split search strings (each concatenation must match exactly once)
    try:
        pe_from = _resolve_split_search_param(original_text, pe_search_from1, pe_search_from2, "pe_search_from")
        pe_to = _resolve_split_search_param(original_text, pe_search_to1, pe_search_to2, "pe_search_to")
        pg_from = _resolve_split_search_param(grep_text, pg_search_from1, pg_search_from2, "pg_search_from")
        pg_to = _resolve_split_search_param(grep_text, pg_search_to1, pg_search_to2, "pg_search_to")
    except ValueError as e:
        return {"status": "error", "message": ["Error:", str(e)]}

    # 4. Search strings must not overlap
    if pe_from is not None and pe_to is not None:
        if pe_from[0] + pe_from[1] > pe_to[0]:
            return {"status": "error", "message": ["Error: pe_search_from and pe_search_to overlap, or pe_search_from does not come before pe_search_to."]}

    if pg_from is not None and pg_to is not None:
        if pg_from[0] + pg_from[1] > pg_to[0]:
            return {"status": "error", "message": ["Error: pg_search_from and pg_search_to overlap, or pg_search_from does not come before pg_search_to."]}

    # 5. Compute the range boundaries (the split points between the X1 and X2 parts)
    pe_from_boundary = 0 if pe_from is None else pe_from[2]
    pe_to_boundary = len(original_text) if pe_to is None else pe_to[2]
    pg_from_boundary = 0 if pg_from is None else pg_from[2]
    pg_to_boundary = len(grep_text) if pg_to is None else pg_to[2]

    # 6. Replace the edit range (pe_search_from2..pe_search_to1) with the grep range (pg_search_from2..pg_search_to1)
    replace = grep_text[pg_from_boundary:pg_to_boundary]

    edited_text = original_text[:pe_from_boundary] + replace + original_text[pe_to_boundary:]

    try:
        with open(path2edit, "w", encoding="utf-8") as f:
            f.write( edited_text)
        return {
            "status": "success",
            "data": f"Successfully applied patch to {path2edit}.",
            "file": path2edit,
            "diff": _unified_diff(original_text, edited_text, path2edit),
        }

    except Exception as e:
        return {"status": "error", "message": ["Error applying patch:", str(e)]}

# /home/ox/aitasks/2026_08_12_12_tool_explanation/code.py

@tool(description="""provides documentation for the tool edit_range_based_file3""")
def edit_range_based_file3_help() : 
    return { "status": "success", "data" :
    """
    Wrapper around edit_range_based_file2 that uses a separator to mark the split point.

    Instead of providing two separate parts (X1 and X2) for each search parameter,
    you provide a single string with a separator (default '|') that marks where the
    range starts or ends. The full search string is the concatenation of the parts
    around the separator.

    For example:
        pe_search_from="ab|c" means the full search string is "abc" and the split
        point is between "ab" and "c". The range starts at the position of "c".

        pe_search_to="XYZ|after" means the full search string is "XYZafter" and the
        split point is between "XYZ" and "after". The range ends at the position of "after".

    If no separator is present, the entire string is used as X1 and X2 is treated as None
    (empty string), meaning the split point is at the end of the string.

    Parameters:
        path2edit: Path to the file to edit.
        path2grep: Path to the file to read replacement content from.
        pe_search_from: Marker in path2edit where the range starts, with optional separator.
            e.g., "abc" (split at end) or "ab|c" (split between "ab" and "c").
        pe_search_to: Marker in path2edit where the range ends, with optional separator.
            e.g., "xyz" (split at end) or "xy|z" (split between "xy" and "z").
        pg_search_from: Marker in path2grep where the source range starts, with optional separator.
        pg_search_to: Marker in path2grep where the source range ends, with optional separator.
        sep: The separator character that marks the split point. Default is '|'.

    Returns:
        Result string from edit_range_based_file2.

    Examples:
        # Replace content between [START] and [END] markers
        edit_range_based_file3(
            path2edit="target.txt",
            path2grep="source.txt",
            pe_search_from="[START]",
            pe_search_to="[END]",
            pg_search_from="[SRC_START]",
            pg_search_to="[SRC_END]"
        )

        # Replace content from after "ab" to after "XYZ"
        edit_range_based_file3(
            path2edit="target.txt",
            path2grep="source.txt",
            pe_search_from="ab|c",
            pe_search_to="XYZ|after",
            pg_search_from="de|f",
            pg_search_to="GHI|after"
        )
    """ }

@tool( """Wrapper around edit_range_based_file2 that uses a separator to mark the split point.
If no separator is present, the split point is at the end of the string.
 edit_range_based_file3_help() provides the documentation.""")
def edit_range_based_file3(
    path2edit: str,
    path2grep: str,
    pe_search_from: str = None,
    pe_search_to: str = None,
    pg_search_from: str = None,
    pg_search_to: str = None,
    sep: str = '|'
) -> str:

    """
    Wrapper around edit_range_based_file2 that uses a separator to mark the split point.

    Instead of providing two separate parts (X1 and X2) for each search parameter,
    you provide a single string with a separator (default '|') that marks where the
    range starts or ends. The full search string is the concatenation of the parts
    around the separator.
    """

    if not isinstance( sep, str):
        return {"status": "error", "message": ["sep has to be a string"]}

    if len( sep) == 0 :
        return {"status": "error", "message": ["sep must be at least 1 character long"]}

    for paramname in "pe_search_from,pe_search_to,pg_search_from,pg_search_to".split( ','):
        if not eval( paramname).count( sep) in [0,1] :
            return {"status": "error", "message": [f"parameter sep {sep} must match exactly 0 or 1 times in parameter {paramname}"]}


    def split_param(param, sep):
        """Split a parameter into two parts based on the separator."""
        if param is None:
            return None, None
        if sep in param:
            idx = param.index(sep)
            return param[:idx], param[idx + 1:]
        else:
            # No separator: entire string is X1, X2 is None (treated as empty)
            return param, None

    pe_from1, pe_from2 = split_param(pe_search_from, sep)
    pe_to1, pe_to2 = split_param(pe_search_to, sep)
    pg_from1, pg_from2 = split_param(pg_search_from, sep)
    pg_to1, pg_to2 = split_param(pg_search_to, sep)

    ### TODO : the error messages do not really match the parameters in this function
    return edit_range_based_file2(
        path2edit=path2edit,
        path2grep=path2grep,
        pe_search_from1=pe_from1,
        pe_search_from2=pe_from2,
        pe_search_to1=pe_to1,
        pe_search_to2=pe_to2,
        pg_search_from1=pg_from1,
        pg_search_from2=pg_from2,
        pg_search_to1=pg_to1,
        pg_search_to2=pg_to2
    )

@tool(description="Creates a new file with the specified content at the given path. Fails if the file already exists.")
def create_new_file(path: str, content: str):
    # 1. Safety Check
    if os.path.exists(path):
        return {"status": "error", "message": [f"File {path} already exists."]}

    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {
            "status": "success",
            "data": f"Successfully created file {path}.",
            "file": path,
            "diff": _unified_diff("", content, path),
        }
    except Exception as e:
        return {"status": "error", "message": [f"Error creating file: {str(e)}"]}

@tool(description="Appends the specified content to the end of the file at the given path.")
def append_to_file(path: str, content: str):
    # 1. Safety Check
    if not os.path.exists(path):
        return {"status": "error", "message": [f"File {path} does not exist."]}

    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        with open(path, "r", encoding="utf-8") as f:
            original_text = f.read()
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return {
            "status": "success",
            "data": f"Successfully appended to {path}.",
            "file": path,
            "diff": _unified_diff(original_text, original_text + content, path),
        }
    except Exception as e:
        return {"status": "error", "message": [f"Error appending to file: {str(e)}"]}

@tool(description="Creates a directory and all necessary parent directories. Refuses absolute paths or paths containing '..' for safety.")
def makedirs(path: str):
    # 1. Safety Check
    if not path or not path.strip():
        return {"status": "error", "message": ["Refused: Path must not be empty."]}

    if os.path.isabs(path):
        return {"status": "error", "message": [f"Refused: Path must be relative, not absolute: {path}"]}

    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    try:
        os.makedirs(path, exist_ok=True)
        return {"status": "success", "data": f"Successfully created directory chain: {path}"}
    except Exception as e:
        return {"status": "error", "message": [f"Error creating directories: {str(e)}"]}

