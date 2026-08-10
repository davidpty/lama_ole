# LSP Toolset — Testing Strategy & Fake Language Server

The obstacle to testing an LSP client is that real language servers
(pyright-langserver, pylsp, gopls, clangd, …) are heavy, slow to index, may not
be installed, and behave non-deterministically. The suite therefore tests
against a **deterministic fake server** that speaks real LSP framing over
stdio, plus optional smoke tests against a real server when one is present.

## Test layout

```
lama_ole/tests/
├── fakes/
│   └── fake_lsp_server.py     ← runnable stdio JSON-RPC server driven by a script
├── test_lsp_jsonrpc.py        ← pure codec tests (no subprocess)
├── test_lsp_client.py         ← client lifecycle vs fake server
├── test_lsp_session.py        ← session manager, registry, freshness
├── test_lsp_tools.py          ← @tool surface (return dicts, errors)
└── test_lsp_integration.py    ← optional real-server smoke test (skip if absent)
```

Tests follow the repo conventions: pytest-style, sys.path bootstrap, no
`lama_ole.` imports, and `tools_security.register_basepath()` for temp dirs.

## The fake server (`tests/fakes/fake_lsp_server.py`)

A small stdlib-only script, spawned by the client under test exactly like a real
server (`Popen(["python3", "fake_lsp_server.py", <scriptfile>])`). It reuses
`lama_ole/lsp/jsonrpc.py`'s codec for framing (imported by path), reads a JSON
"script" from `argv[1]` or `$FAKE_LSP_SCRIPT`, and then:

- Serves `initialize` → returns a fixed capabilities object
  (hoverProvider, definitionProvider, referencesProvider, completionProvider,
  signatureHelpProvider, documentSymbolProvider, workspaceSymbolProvider).
- Serves `textDocument/hover` / `definition` / `references` / `completion` /
  `signatureHelp` / `documentSymbol` / `workspace/symbol` from per-method answer
  tables keyed by uri + line.
- `textDocument/didOpen` / `didChange` → records the last-seen text and version
  (assertable via a special `__state` request).
- Pushes `textDocument/publishDiagnostics` **after a configurable delay** for
  any didOpen'd file (exercises async notifications and the reader thread).
- Supports scripted failure modes:
  - `"crash_on": "<substring>"` → exits(1) when a request/notification text
    contains it (crash detection, pending-future failure, auto-restart).
  - `"delay": <seconds>` on a request → tests `LspTimeout`.
  - `"error": {code, message}` on a method → tests `LspError` translation.
  - `"notify": [{"method": ..., "params": ..., "after_ms": ...}]` → arbitrary
    push (e.g. `window/logMessage`) for the rolling-log tests.

This keeps every test deterministic (no sleeps beyond the scripted delay) and
self-contained (no external binaries required to run the suite).

## Unit tests

### `test_lsp_jsonrpc.py` (Phase 1)

- `encode`/`feed` round-trip for a request, a response and a notification.
- Body split across chunk boundaries (feed one byte at a time).
- Two messages arriving in a single chunk are both returned.
- Unknown header lines (`Content-Type: application/vscode-jsonrpc; charset=utf-8`)
  are tolerated.
- Missing/duplicated `Content-Length` → `ValueError`.
- Buffer cap exceeded → `ValueError`; `reset()` clears state.

### `test_lsp_client.py` (Phase 2)

- `start()` performs the initialize/initialized handshake; `capabilities` populated.
- `request()` returns the result; two concurrent-style sequential requests get
  correctly correlated responses (id echo).
- Server responds with a JSON-RPC `error` → `LspError(code, message)`.
- `"delay"` script → `LspTimeout`; pending future is cleaned up.
- Notification push (diagnostics, logMessage) reaches `on_notification`.
- Server-to-client request (e.g. `workspace/applyEdit`) → answered with
  `methodNotFound` error (v1 policy).
- `"crash_on"` → `LspClientCrashed`, all pending futures fail, `is_running`
  False, EOF detected.
- `shutdown()` sends shutdown + exit; process exits; threads joined.
- `kill()` on a stuck server terminates promptly.

### `test_lsp_session.py` (Phase 3)

- `resolve_server()`: default table; `LAMA_OLE_LSP_SERVERS` override (string and
  list forms); unknown language → `LspConfigError`; missing binary → error.
- `language_for_path()`: `.py`→python, `.ts`→typescript, `.rs`→rust, unknown → None.
- `sync_document()`: first call sends didOpen; second call with unchanged file
  sends nothing; touch the file → didChange with full text, version bumped.
  (Verified via the fake server's `__state` request.)
- Diagnostics pushed asynchronously appear in `get_diagnostics(path)` (uri-keyed).
- Crash → next `get_client()` auto-restarts once; a second crash requires
  explicit `lsp_start`.
- `stop_all()` is idempotent and shuts down every session (no leftover
  processes — assert via `psutil`-free process polling / exit codes).

### `test_lsp_tools.py` (Phase 4)

- Every tool returns the `{"status": ...}` shape on success and on each error path.
- `lsp_hover`/`lsp_definition`/`lsp_references`/`lsp_completion`/`lsp_signature_help`/
  `lsp_document_symbols`/`lsp_workspace_symbols` map fake-server answers into the
  documented compact result format (uri + line + snippet).
- `lsp_diagnostics` returns cached diagnostics without a round-trip.
- `lsp_start` twice is idempotent (reuses the running session).
- `lsp_stop` on a non-running language → error dict, does not crash.
- Path validation: absolute path (no registered basepath) → error dict from
  `validate_path`; with `register_basepath(tmp)` → allowed.
- Missing server binary → error dict mentioning `LAMA_OLE_LSP_SERVERS`.
- Unknown language → error dict listing `known_languages()`.
- Module flag checks: `__tool_readonly__` True, `__tool_env__` keys present,
  `lsp_tools` appears in `get_available_toolsets()`.

### `test_lsp_integration.py` (Phase 5)

- `@pytest.mark.skipif` when no real server is on PATH (`shutil.which` for a
  configured server). When present: start, open a small fixture file, hover /
  definition on a known symbol, collect diagnostics, then `lsp_stop`. This is a
  best-effort smoke test and is excluded from the unittest discover step
  (`-p "test_*.py"` catches it, but it self-skips without a server).

## Determinism rules

- Never poll for conditions with fixed sleeps; use the scripted `after_ms`
  delays and `Event.wait(timeout)`.
- Randomize nothing; the fake server's answers are static tables.
- Clean up every subprocess in a `finally`/fixture teardown so a failing test
  cannot leak a server process (mirrors the atexit hygiene in `session.py`).

## Running

```bash
cd lama_ole
python3 tests/run_all_tests.py        # full suite incl. new LSP tests
python3 -m pytest tests/test_lsp_client.py tests/test_lsp_session.py tests/test_lsp_tools.py -q
```
