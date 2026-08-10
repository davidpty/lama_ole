"""Deterministic fake LSP server for the test suite.

Speaks real LSP framing over stdio (Content-Length JSON-RPC 2.0) but serves
answers from a static JSON "script" instead of analysing code. This makes the
LSP client/session/tool tests fast and deterministic without needing a real
language server on PATH.

Usage (spawned by the client under test)::

    python3 tests/fakes/fake_lsp_server.py <script.json>

Script schema::

    {
      "capabilities": {...},            // optional; default enables all providers
      "answers": {"<method>": <result>},// request -> result
      "errors": {"<method>": {code, message}},  // request -> JSON-RPC error
      "delay_ms": {"<method>": <int>},  // sleep before answering
      "crash_on": "<substring>",        // exit(1) if a message contains it
      "diagnostics": {"<uri>": [<diag>]},   // pushed after each didOpen
      "diagnostics_after_ms": <int>,    // push delay (default 100)
      "notify": [{"method":..., "params":..., "after_ms":...}]  // pushed at startup
    }

Special request ``__state`` returns the last received ``didOpen``/``didChange``
document state (text, version, languageId, uri) so tests can assert freshness.
"""

import json
import os
import sys
import threading
import time

_FID = os.path.abspath(__file__)
_LAMA_OLE_DIR = os.path.abspath(os.path.join(os.path.dirname(_FID), "..", ".."))
if _LAMA_OLE_DIR not in sys.path:
    sys.path.insert(0, _LAMA_OLE_DIR)

from lsp.jsonrpc import JsonRpcCodec  # noqa: E402

DEFAULT_CAPABILITIES = {
    "hoverProvider": True,
    "definitionProvider": True,
    "referencesProvider": True,
    "completionProvider": True,
    "signatureHelpProvider": True,
    "documentSymbolProvider": True,
    "workspaceSymbolProvider": True,
}

_print_lock = threading.Lock()
_codec = JsonRpcCodec()


def _write(message: dict) -> None:
    data = _codec.encode(message)
    with _print_lock:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def _respond(script: dict, msg_id, method, result=None, error=None) -> None:
    if error is not None:
        _write({"jsonrpc": "2.0", "id": msg_id, "error": error})
    else:
        _write({"jsonrpc": "2.0", "id": msg_id, "result": result})


def main() -> int:
    script_path = sys.argv[1] if len(sys.argv) > 1 else ""
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    capabilities = script.get("capabilities", DEFAULT_CAPABILITIES)
    answers = script.get("answers", {})
    errors = script.get("errors", {})
    delay_ms = script.get("delay_ms", {})
    crash_on = script.get("crash_on")
    diagnostics = script.get("diagnostics", {})
    diagnostics_after_ms = int(script.get("diagnostics_after_ms", 100))

    codec = JsonRpcCodec()
    document_state = {}  # type: dict
    received_responses = []  # type: list

    def schedule_push(message: dict, after_ms: int) -> None:
        def _push():
            time.sleep(after_ms / 1000.0)
            _write(message)

        threading.Thread(target=_push, daemon=True).start()

    for entry in script.get("notify", []):
        schedule_push(
            {
                "jsonrpc": "2.0",
                "method": entry["method"],
                "params": entry.get("params", {}),
            },
            int(entry.get("after_ms", 0)),
        )
    for entry in script.get("client_requests", []):
        schedule_push(
            {
                "jsonrpc": "2.0",
                "id": entry.get("id", 1000),
                "method": entry["method"],
                "params": entry.get("params", {}),
            },
            int(entry.get("after_ms", 0)),
        )

    while True:
        # read1 returns as soon as data is available; read(n) would block until
        # n bytes or EOF, deadlocking on small frames from a still-alive client.
        chunk = sys.stdin.buffer.read1(65536)
        if not chunk:
            break
        try:
            messages = codec.feed(chunk)
        except ValueError as exc:
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "parse error: %s" % exc},
                }
            )
            continue
        for message in messages:
            if "id" in message and "method" not in message:
                received_responses.append(message)  # response to our own request
                continue
            msg_id = message.get("id")  # None for notifications
            method = message["method"]
            params = message.get("params") or {}
            if crash_on and crash_on in json.dumps(message):
                os._exit(1)

            if method == "initialize":
                _respond(
                    script,
                    msg_id,
                    method,
                    result={
                        "capabilities": capabilities,
                        "serverInfo": {"name": "fake-lsp", "version": "0.0.1"},
                    },
                )
            elif method == "shutdown":
                _respond(script, msg_id, method, result=None)
            elif method == "exit":
                return 0
            elif method == "textDocument/didOpen":
                td = params.get("textDocument", {})
                document_state.update(
                    {
                        "text": td.get("text", ""),
                        "version": td.get("version", 1),
                        "languageId": td.get("languageId", ""),
                        "uri": td.get("uri", ""),
                    }
                )
                uri = td.get("uri")
                if uri in diagnostics:
                    schedule_push(
                        {
                            "jsonrpc": "2.0",
                            "method": "textDocument/publishDiagnostics",
                            "params": {
                                "uri": uri,
                                "diagnostics": diagnostics[uri],
                            },
                        },
                        diagnostics_after_ms,
                    )
            elif method == "textDocument/didChange":
                td = params.get("textDocument", {})
                document_state.update(
                    {
                        "text": (params.get("contentChanges") or [{}])[0].get(
                            "text", document_state.get("text", "")
                        ),
                        "version": td.get("version", 1),
                        "uri": td.get("uri", document_state.get("uri", "")),
                    }
                )
            elif method == "__state":
                _respond(script, msg_id, method, result=dict(document_state))
            elif method == "__received_responses":
                _respond(script, msg_id, method, result=list(received_responses))
            elif method in errors:
                _respond(
                    script,
                    msg_id,
                    method,
                    error=errors[method],
                )
            elif method in delay_ms:
                # Respond from a background thread so the read loop stays free
                # to serve later requests while this one is delayed.
                def _delayed():
                    time.sleep(delay_ms[method] / 1000.0)
                    _respond(script, msg_id, method, result=answers.get(method))

                threading.Thread(target=_delayed, daemon=True).start()
            elif method in answers:
                _respond(script, msg_id, method, result=answers[method])
            else:
                if msg_id is not None:  # only requests get a response
                    _respond(
                        script,
                        msg_id,
                        method,
                        error={"code": -32601, "message": "method not found: %s" % method},
                    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
