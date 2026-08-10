"""LSP tools for lama_ole — code intelligence via a Language Server.

The model queries a real language server (hover, definition, references,
completion, signature help, document/workspace symbols, diagnostics) the same
way an IDE would. All tools are read-only; files are edited with the ``edit``
toolset and re-synced into the server automatically before every query.

Positions are **0-based** (line and character), matching the LSP spec and the
zero-based line tools in this repository. Character offsets are UTF-16 code
units, per the LSP specification.

Load as a toolset: ``--tool tools.lsp_tools`` or ``/tools load lsp_tools``.
"""

__tool_readonly__ = True

import os
from typing import Optional

from tool_base import tool
from tools_security.validate_path import validate_path as _validate_path

from lsp import (
    get_manager,
    LspError,
    LspConfigError,
    LspClientCrashed,
    LspTimeout,
)

__tool_env__ = {
    "LAMA_OLE_LSP_SERVERS": "JSON dict mapping language id to server command (string or list); overrides the built-in defaults",
    "LAMA_OLE_LSP_ROOT": "Default workspace root for sessions (default: current working directory)",
    "LAMA_OLE_LSP_TIMEOUT": "Default request timeout in seconds (default: 15)",
}

_manager = get_manager()

_DEFAULT_LIMIT = 50


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _validate_path_arg(path: str) -> Optional[str]:
    """Return an error string if *path* is unsafe, else None."""
    safety_error = _validate_path(path)
    if safety_error:
        return safety_error
    if not os.path.exists(path):
        return "File %s does not exist." % path
    return None


def _translate_error(exc: Exception) -> dict:
    if isinstance(exc, LspConfigError):
        return {"status": "error", "message": [str(exc)]}
    if isinstance(exc, LspError):
        return {
            "status": "error",
            "message": ["LSP error %d: %s" % (exc.code, exc.message)],
        }
    if isinstance(exc, LspTimeout):
        return {"status": "error", "message": [str(exc)]}
    if isinstance(exc, LspClientCrashed):
        return {"status": "error", "message": [str(exc)]}
    return {"status": "error", "message": [str(exc)]}


def _query_positional(method, path, line, character):
    """Sync the file and run a textDocument/* request at a position."""
    error = _validate_path_arg(path)
    if error:
        return {"status": "error", "message": [error]}
    try:
        language = _manager.sync_document(path)
        client = _manager.get_client(language)
        uri = _manager.path_to_uri(path)
        result = client.request(
            method,
            {
                "textDocument": {"uri": uri},
                "position": {"line": int(line), "character": int(character)},
            },
        )
    except Exception as exc:  # noqa: BLE001 - tools must never crash the loop
        return _translate_error(exc)
    return {"status": "success", "data": result}


def _query_document(method, path):
    """Sync the file and run a textDocument/* request without a position."""
    error = _validate_path_arg(path)
    if error:
        return {"status": "error", "message": [error]}
    try:
        language = _manager.sync_document(path)
        client = _manager.get_client(language)
        uri = _manager.path_to_uri(path)
        result = client.request(method, {"textDocument": {"uri": uri}})
    except Exception as exc:  # noqa: BLE001
        return _translate_error(exc)
    return {"status": "success", "data": result}


def _cap_items(items, limit):
    """Truncate a list of returned items, noting truncation."""
    total = len(items)
    if total > limit:
        return items[:limit], "%d of %d items returned" % (limit, total)
    return items, None


# ---------------------------------------------------------------------------
# Result formatters (raw LSP shapes -> compact, model-friendly data)
# ---------------------------------------------------------------------------


def _format_hover(result) -> dict:
    if not result:
        return {"contents": None}
    contents = result.get("contents")
    if isinstance(contents, list):
        parts = []
        for part in contents:
            if isinstance(part, dict):
                parts.append(part.get("value") or "")
            else:
                parts.append(str(part))
        text = "\n".join(p for p in parts if p)
    elif isinstance(contents, dict):
        text = contents.get("value") or ""
    else:
        text = str(contents)
    return {"contents": text, "range": result.get("range")}


def _format_locations(result, limit):
    locations = result
    if isinstance(locations, dict):  # single Location
        locations = [locations]
    if not isinstance(locations, list):
        return {"locations": [], "note": "unexpected response shape"}
    items = []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        items.append(
            {
                "uri": loc.get("uri"),
                "range": loc.get("range"),
            }
        )
    items, note = _cap_items(items, limit)
    return {"locations": items, "note": note}


def _format_completions(result, limit):
    if isinstance(result, dict):  # CompletionList
        items = result.get("items") or []
        is_incomplete = result.get("isIncomplete", False)
    else:
        items = result or []
        is_incomplete = False
    condensed = []
    for item in items:
        if not isinstance(item, dict):
            continue
        condensed.append(
            {
                "label": item.get("label"),
                "kind": item.get("kind"),
                "detail": item.get("detail"),
                "insertText": item.get("insertText"),
            }
        )
    condensed, note = _cap_items(condensed, limit)
    return {"items": condensed, "is_incomplete": is_incomplete, "note": note}


def _format_signature_help(result) -> dict:
    if not isinstance(result, dict):
        return {"signatures": [], "active": None}
    sigs = []
    for sig in result.get("signatures") or []:
        params = [
            {
                "label": p.get("label") if isinstance(p, dict) else p,
                "documentation": p.get("documentation") if isinstance(p, dict) else None,
            }
            for p in (sig.get("parameters") or [])
        ]
        sigs.append(
            {
                "label": sig.get("label"),
                "documentation": sig.get("documentation"),
                "parameters": params,
            }
        )
    return {
        "signatures": sigs,
        "active_signature": result.get("activeSignature"),
        "active_parameter": result.get("activeParameter"),
    }


def _flatten_symbols(symbols, indent=0):
    """Flatten nested DocumentSymbol trees into an indented outline."""
    out = []
    for sym in symbols or []:
        if not isinstance(sym, dict):
            continue
        out.append(
            {
                "indent": indent,
                "name": sym.get("name"),
                "kind": sym.get("kind"),
                "detail": sym.get("detail"),
                "range": sym.get("range"),
            }
        )
        out.extend(_flatten_symbols(sym.get("children"), indent + 1))
    return out


def _format_document_symbols(result, limit):
    symbols = result if isinstance(result, list) else []
    flattened = _flatten_symbols(symbols)
    flattened, note = _cap_items(flattened, limit)
    return {"symbols": flattened, "note": note}


def _format_workspace_symbols(result, limit):
    items = []
    for sym in result or []:
        if not isinstance(sym, dict):
            continue
        items.append(
            {
                "name": sym.get("name"),
                "kind": sym.get("kind"),
                "containerName": sym.get("containerName"),
                "location": sym.get("location"),
            }
        )
    items, note = _cap_items(items, limit)
    return {"symbols": items, "note": note}


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


@tool(
    description="Starts a language server session for 'language' (e.g. 'python', 'typescript', 'rust', 'go'). Sessions persist across calls; queries auto-start sessions, so this is optional unless you want to control the workspace root."
)
def lsp_start(language: str, root_dir: str = ""):
    root = root_dir or None
    try:
        client = _manager.get_client(language, root_dir=root)
    except Exception as exc:  # noqa: BLE001
        return _translate_error(exc)
    return {
        "status": "success",
        "data": "Language server session '%s' is running (pid %s)."
        % (language, client.pid),
        "language": language,
        "pid": client.pid,
        "capabilities": list(client.capabilities),
    }


@tool(
    description="Synchronizes a file into the language server so later queries see its current content. Automatic before every query; call explicitly after heavy edits to force a re-read."
)
def lsp_open(path: str, language: str = ""):
    error = _validate_path_arg(path)
    if error:
        return {"status": "error", "message": [error]}
    try:
        effective = _manager.sync_document(path, language or None)
    except Exception as exc:  # noqa: BLE001
        return _translate_error(exc)
    return {
        "status": "success",
        "data": "Synced %s (language %s)." % (path, effective),
        "language": effective,
    }


@tool(
    description="Returns hover information (type, documentation) for the symbol at the given 0-based 'line' and 'character' position in 'path'."
)
def lsp_hover(path: str, line: int, character: int):
    result = _query_positional("textDocument/hover", path, line, character)
    if result.get("status") == "success":
        result["data"] = _format_hover(result["data"])
    return result


@tool(
    description="Returns the definition location(s) of the symbol at the given 0-based 'line'/'character' position in 'path'. Each location has 'uri' and 'range'."
)
def lsp_definition(path: str, line: int, character: int):
    result = _query_positional("textDocument/definition", path, line, character)
    if result.get("status") == "success":
        result["data"] = _format_locations(result["data"], _DEFAULT_LIMIT)
    return result


@tool(
    description="Returns every reference of the symbol at the given 0-based 'line'/'character' position in 'path'."
)
def lsp_references(
    path: str, line: int, character: int, include_declaration: bool = False
):
    result = _query_positional("textDocument/references", path, line, character)
    if result.get("status") == "success":
        result["data"] = _format_locations(result["data"], _DEFAULT_LIMIT)
    return result


@tool(
    description="Returns completion items (members, keywords, snippets) available at the given 0-based 'line'/'character' position in 'path'. 'limit' caps how many items are returned."
)
def lsp_completion(path: str, line: int, character: int, limit: int = _DEFAULT_LIMIT):
    result = _query_positional("textDocument/completion", path, line, character)
    if result.get("status") == "success":
        result["data"] = _format_completions(result["data"], int(limit))
    return result


@tool(
    description="Returns signature help (function signatures and parameter documentation) for a callable at the given 0-based 'line'/'character' position in 'path'."
)
def lsp_signature_help(path: str, line: int, character: int):
    result = _query_positional("textDocument/signatureHelp", path, line, character)
    if result.get("status") == "success":
        result["data"] = _format_signature_help(result["data"])
    return result


@tool(
    description="Returns the symbol outline of 'path' (functions, classes, variables, with nesting levels) as reported by the language server."
)
def lsp_document_symbols(path: str, limit: int = _DEFAULT_LIMIT):
    result = _query_document("textDocument/documentSymbol", path)
    if result.get("status") == "success":
        result["data"] = _format_document_symbols(result["data"], int(limit))
    return result


@tool(
    description="Searches the whole workspace for symbols whose name contains 'query'. 'limit' caps how many are returned."
)
def lsp_workspace_symbols(query: str, limit: int = _DEFAULT_LIMIT):
    client = _manager.get_active_client()
    if client is None:
        return {
            "status": "error",
            "message": [
                "No language server session is running; call lsp_start first."
            ],
        }
    try:
        result = client.request("workspace/symbol", {"query": query})
    except Exception as exc:  # noqa: BLE001
        return _translate_error(exc)
    return {
        "status": "success",
        "data": _format_workspace_symbols(result, int(limit)),
    }


@tool(
    description="Returns the diagnostics (errors, warnings) the language server reported for 'path'. Values are cached from server push notifications; run after edits to see fresh findings."
)
def lsp_diagnostics(path: str):
    error = _validate_path_arg(path)
    if error:
        return {"status": "error", "message": [error]}
    try:
        _manager.sync_document(path)
        diagnostics = _manager.get_diagnostics(path)
    except Exception as exc:  # noqa: BLE001
        return _translate_error(exc)
    items = []
    for diag in diagnostics:
        if not isinstance(diag, dict):
            continue
        items.append(
            {
                "severity": diag.get("severity"),
                "message": diag.get("message"),
                "range": diag.get("range"),
                "source": diag.get("source"),
            }
        )
    return {
        "status": "success",
        "data": {"diagnostics": items, "count": len(items)},
    }


@tool(
    description="Reports the current state of all language server sessions: process ids, running status, capabilities, and recent server log lines."
)
def lsp_status():
    return {"status": "success", "data": _manager.status()}


@tool(
    description="Shuts down the language server session for 'language' (e.g. 'python'). Subsequent queries restart it automatically."
)
def lsp_stop(language: str):
    if not _manager.stop_session(language):
        return {
            "status": "error",
            "message": [
                "No language server session '%s' is running." % language
            ],
        }
    return {
        "status": "success",
        "data": "Language server session '%s' stopped." % language,
    }
