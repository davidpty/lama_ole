# LSP Toolset — Implementation Plan

Ordered, phased task list for building the toolset. Each phase is testable
before the next begins; everything must pass `python3 tests/run_all_tests.py`
at the end of each phase.

## Phase 1 — JSON-RPC codec (pure, no I/O)

**Files:** `lama_ole/lsp/__init__.py`, `lama_ole/lsp/jsonrpc.py`,
`lama_ole/tests/test_lsp_jsonrpc.py`

- [x] 1.1 Create the `lsp/` package (`__init__.py` re-exporting the public names).
- [x] 1.2 `JsonRpcCodec.encode(message) -> bytes` — `Content-Length` framing.
- [x] 1.3 `JsonRpcCodec.feed(data) -> List[dict]` — incremental streaming decoder
      (partial messages buffered; multiple messages per chunk).
- [x] 1.4 Header parsing: single `Content-Length` required, unknown headers
      ignored, 16 MiB buffer cap, `ValueError` on malformed framing.
- [x] 1.5 Unit tests: encode round-trip; body split across chunks; two messages
      in one chunk; empty body; non-dict JSON.

**Estimated time:** 45 min.

## Phase 2 — `LspClient` (process + reader thread)

**Files:** `lama_ole/lsp/client.py`, `lama_ole/tests/fakes/fake_lsp_server.py`,
`lama_ole/tests/test_lsp_client.py`

- [x] 2.1 `start()` — `Popen(stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True,
      encoding="utf-8")`, spawn reader thread + stderr drainer, send `initialize`
      (rootUri from `root_dir`, `processId`, capabilities query), wait for the
      response, then send the `initialized` notification. Store parsed
      capabilities.
- [x] 2.2 `request()` / `notify()` with pending-future table, id sequence,
      `Event`-based completion, timeout → `LspTimeout`, error response →
      `LspError`.
- [x] 2.3 Reader-thread dispatch: responses resolve futures; notifications go to
      `on_notification`; server-to-client *requests* get a `methodNotFound`
      error response; EOF → mark crashed, fail all pending futures.
- [x] 2.4 `shutdown()` (graceful: `shutdown` request → `exit` notification →
      wait → `terminate()`) and `kill()` (hard); both join threads.
- [x] 2.5 Write the **fake server fixture** (`fake_lsp_server.py`, see
      `004_testing_and_fake_server.md`) — a stdio JSON-RPC server whose behavior
      is driven by a "script" (env/argv): which notifications to push after how
      long, which requests to answer, whether to crash on a magic message.
- [x] 2.6 Tests: handshake; request/response correlation; timeout; error
      response; notification capture; crash detection (pending futures fail);
      shutdown/kill.

**Estimated time:** 2 h.

## Phase 3 — session manager + registry

**Files:** `lama_ole/lsp/registry.py`, `lama_ole/lsp/session.py`,
`lama_ole/tests/test_lsp_session.py`

- [x] 3.1 `registry.py`: `DEFAULT_LSP_SERVERS`, `resolve_server()`,
      `language_for_path()` (extension map), `known_languages()`;
      `LAMA_OLE_LSP_SERVERS` override (JSON, string or list commands).
- [x] 3.2 `session.py`: `LspSessionManager` — one client per language, lazy
      `get_client()`, `stop_session()`, `stop_all()` (atexit), status snapshot.
- [x] 3.3 `sync_document()` — path validation, mtime/size comparison, `didOpen`
      (first) vs full-text `didChange` (changed), version bumping.
- [x] 3.4 Diagnostics cache via `on_notification` (`textDocument/publishDiagnostics`),
      `window/logMessage` rolling log; `get_diagnostics(path)`.
- [x] 3.5 Crash auto-restart (one retry), then explicit `lsp_start` required.
- [x] 3.6 Tests (fake server): env override resolution; extension→language;
      freshness (mtime change → didChange sent); diagnostics cache update;
      auto-restart after crash; `stop_all` idempotent.

**Estimated time:** 1 h 45 min.

## Phase 4 — tool surface `tools/lsp_tools.py`

**Files:** `lama_ole/tools/lsp_tools.py`, `lama_ole/tests/test_lsp_tools.py`

- [x] 4.1 Module header: `__tool_readonly__ = True`, `__tool_env__`
      (`LAMA_OLE_LSP_SERVERS`, `LAMA_OLE_LSP_ROOT`, `LAMA_OLE_LSP_TIMEOUT`).
- [x] 4.2 Implement the 12 tools from `001_overview.md` (`lsp_start`,
      `lsp_open`, `lsp_hover`, `lsp_definition`, `lsp_references`,
      `lsp_completion`, `lsp_signature_help`, `lsp_document_symbols`,
      `lsp_workspace_symbols`, `lsp_diagnostics`, `lsp_status`, `lsp_stop`),
      each with `@tool(description=...)` and `{"status": ...}` returns.
- [x] 4.3 Result shaping: locations/items condensed to compact text/JSON
      (uri + line + snippet) so big result sets do not flood the context
      (respect the entropy-check conventions used by `dev_tools_readonly.py`).
- [x] 4.4 Error translation: `LspError`/`LspConfigError`/`LspTimeout`/
      `LspClientCrashed` → structured error dicts with actionable hints.
- [x] 4.5 Verify it loads: `--tool tools.lsp_tools` and `/tools load lsp_tools`
      (works through existing registry; `get_available_toolsets` picks it up).
- [x] 4.6 Tool tests (fake server): every tool's success and error path,
      path-validation rejections, not-started error, return-dict shape.

**Estimated time:** 2 h.

## Phase 5 — integration, docs, polish

**Files:** `lama_ole/tests/test_lsp_integration.py`, `README.md`, `AGENTS.md`

- [x] 5.1 Optional integration test: if a real server (pyright-langserver /
      pylsp / gopls) is found on PATH, run a smoke test (open, hover, definition,
      diagnostics on a known fixture); otherwise `skip` (never fail CI).
- [x] 5.2 Add an example + the toolset to the REPL/CLI docs and `AGENTS.md`
      layout table (`tools/lsp_tools.py`).
- [x] 5.3 `--help-tools` run shows the new tools and their env vars.
- [x] 5.4 Full suite green: `python3 tests/run_all_tests.py` (unittest + pytest).

**Estimated time:** 1 h 15 min.

## Dependency Order

```
Phase 1 (codec)
   ↓
Phase 2 (client)   ← needs Phase 1 + fake server
   ↓
Phase 3 (session)  ← needs Phase 2 + registry
   ↓
Phase 4 (tools)    ← needs Phase 3
   ↓
Phase 5 (docs/integration)
```

## Success Criteria

- [x] A session can be started, a file synced, and hover/definition/references/
      completion/signature/diagnostics answered against the fake server.
- [x] File edited externally (or by the `edit` toolset) is re-synced before the
      next query (mtime-based).
- [x] Server crash → pending calls fail cleanly, one auto-restart works, no
      leaked processes (atexit verified by tests).
- [x] No `lama_ole` engine changes (`run_with_tools`, `chat.py`, registry) — the
      toolset is a drop-in `tools/` module.
- [x] All code Python 3.9 compatible; full suite green.
- [x] Big outputs (references, workspace symbols) are truncated/condensed and
      pass the entropy-check conventions.

## Out of Scope (v2)

`lsp_rename`, `lsp_code_action`, `workspace/applyEdit`, semantic tokens,
multiple simultaneous workspaces, in-process `didChange` delta-only sync,
socket/pipe transport.
