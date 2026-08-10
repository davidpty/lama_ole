"""Shared helpers for the LSP test files (not a test module itself).

Provides paths to the fake LSP server, helpers to build fake-server scripts,
and the standard answer script used by most client/session/tool tests.
"""

import json
import os
import sys
import tempfile

_THIS_DIR = os.path.abspath(os.path.dirname(__file__))
LAMA_OLE_DIR = os.path.abspath(os.path.join(_THIS_DIR, ".."))
if LAMA_OLE_DIR not in sys.path:
    sys.path.insert(0, LAMA_OLE_DIR)

FAKE_SERVER = os.path.join(_THIS_DIR, "fakes", "fake_lsp_server.py")


def fake_server_command(script: dict):
    """Write *script* to a temp file and return (argv, script_path)."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="fake_lsp_script_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(script, f)
    return [sys.executable, FAKE_SERVER, path], path


def env_override(language: str, command):
    """Build a ``LAMA_OLE_LSP_SERVERS`` value for *language* -> *command*."""
    return json.dumps({language: command})


def standard_script():
    """Script that answers every toolset query with a static response."""
    return {
        "answers": {
            "textDocument/hover": {
                "contents": "hover-info: int",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 5},
                },
            },
            "textDocument/definition": [
                {
                    "uri": "file:///def.py",
                    "range": {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 1, "character": 5},
                    },
                }
            ],
            "textDocument/references": [
                {
                    "uri": "file:///ref.py",
                    "range": {
                        "start": {"line": 2, "character": 3},
                        "end": {"line": 2, "character": 8},
                    },
                }
            ],
            "textDocument/completion": {
                "isIncomplete": False,
                "items": [
                    {"label": "foo", "kind": 3, "detail": "int", "insertText": "foo"}
                ],
            },
            "textDocument/signatureHelp": {
                "signatures": [
                    {
                        "label": "fn(x: int)",
                        "parameters": [
                            {"label": "x", "documentation": "the x param"}
                        ],
                    }
                ],
                "activeSignature": 0,
                "activeParameter": 0,
            },
            "textDocument/documentSymbol": [
                {
                    "name": "main",
                    "kind": 12,
                    "detail": "def main",
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 5, "character": 0},
                    },
                    "children": [],
                }
            ],
            "workspace/symbol": [
                {
                    "name": "main",
                    "kind": 12,
                    "containerName": "mod",
                    "location": {
                        "uri": "file:///main.py",
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 4},
                        },
                    },
                }
            ],
        }
    }


def diag_script(uri, diagnostics=None, after_ms=100):
    """Script that pushes *diagnostics* for *uri* after *after_ms*."""
    script = standard_script()
    script["diagnostics"] = {uri: diagnostics or []}
    script["diagnostics_after_ms"] = after_ms
    return script
