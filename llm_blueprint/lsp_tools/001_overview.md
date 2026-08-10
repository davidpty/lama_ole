# LSP Toolset — Overview

## Purpose

This blueprint plans a **toolset** that lets an LLM talk to a real
**Language Server** over the Language Server Protocol (LSP). Today `lama_ole`
can read files, grep them, run tests and edit them, but the model has no
*native understanding* of the codebase semantics: no hover, no go-to-definition,
no find-references, no symbol outline, no completion, and no compiler-grade
diagnostics.

The toolset gives the model the same power a human IDE user has:

| Capability | What the LLM can now do |
|---|---|
| **Hover** | Ask "what is this identifier, what type is it?" at a precise position. |
| **Definition** | Jump from a call site to where the symbol is defined. |
| **References** | Find every place a symbol is used. |
| **Completion** | Ask the language server what members/methods are valid at a point. |
| **Signature help** | Ask what arguments a callable expects, with parameter docs. |
| **Document symbols** | Get the outline (functions/classes/variables) of a file. |
| **Workspace symbols** | Search symbols by name across the whole workspace. |
| **Diagnostics** | Pull compiler/linter findings (type errors, warnings) for a file. |

Everything is **read-only**: the toolset queries the language server but never
modifies files itself. It complements the existing `edit` toolset (write) and
the `dev_tools_readonly` tools (read) — the model can now edit, then *verify*
its edits against the language server's diagnostics and definitions in the
same turn.

## Task Mapping

Requirement from `task_007.txt`:

> Work out a plan for a toolset how to implement a tool which allows an LLM to
> use a LSP.

This document set (`llm_blueprint/lsp_tools/`) *is* that plan:

| File | Content |
|---|---|
| `001_overview.md` | Purpose, design principles, architecture, tool surface, decisions & edge cases (this file). |
| `002_transport_and_session.md` | JSON-RPC framing, `LspClient`, `LspSessionManager` — classes & APIs. |
| `003_implementation_plan.md` | Ordered, phased task list with file targets and success criteria. |
| `004_testing_and_fake_server.md` | Testing strategy incl. a deterministic fake language server. |

> Note: `task_007.txt` spells the overview file `001_oberview.md`; this repo's
> blueprint convention is `001_overview.md` (see `security/entropychecker/`,
> `skillz/`), so the correct spelling is used here.

## Design Principles

| Principle | Rationale |
|-----------|-----------|
| **Stdlib only — no `pygls`/`pylsp` dependency** | The repo targets Python 3.9+ with no mandatory third-party runtime deps. A minimal JSON-RPC 2.0 client over stdio is ~200 lines and fully controllable. |
| **One long-lived session per language** | LSP servers are expensive to start (indexing). Sessions persist across tool calls; `lsp_start` is lazy and explicit stop is available. |
| **Always-on reader thread** | The server pushes notifications (`publishDiagnostics`, `window/logMessage`) asynchronously. A dedicated thread drains stdout continuously, which both captures those notifications and prevents pipe-buffer deadlock. |
| **Document freshness** | The `edit` toolset changes files on disk; the server's in-memory copy goes stale. Before any query, the session resyncs the file (full-text `didChange`) if its mtime changed since the last sync. |
| **Server commands come from an allowlist mapping** | Language → command is resolved from a built-in table overridable via `LAMA_OLE_LSP_SERVERS`. The LLM never passes a raw shell command; a fixed argv list is used. |
| **Positions are 0-based** | LSP uses 0-based line/character. This matches the existing zero-based line tools (`read_lines_patch_lines_zero_based.py`) and avoids off-by-one surprises. Character offset = UTF-16 code units, per the LSP spec. |
| **Read-only v1, write ops deferred** | Hover/definition/references/completion/signature/diagnostics are pure queries. Renames, code actions and `workspace/applyEdit` are explicitly **v2** (they would collide with the `edit` toolset's file ownership). |
| **Reuse existing infrastructure** | `@tool` decorator + auto-inferred JSON schema, `{"status": ...}` return shape, `tools_security.validate_path`, `__tool_env__` env-var declaration, `__tool_readonly__ = True`, `atexit` cleanup — no engine changes. |
| **Python 3.9 compatible** | No PEP 604 unions at runtime, no `match`/`case` (per `AGENTS.md`). Use `typing.Optional`/`Union`. |

## Architecture at a Glance

```
lama_ole/
├── lsp/                          ← NEW engine package (not a toolset itself)
│   ├── __init__.py
│   ├── jsonrpc.py                ← JSON-RPC 2.0 framing + message codec (pure, testable)
│   ├── client.py                 ← LspClient: Popen, reader thread, request/response routing
│   ├── session.py                ← LspSessionManager: language→client map, freshness, diagnostics cache
│   └── registry.py               ← language → server command resolution (defaults + env override)
├── tools/
│   └── lsp_tools.py              ← NEW loadable toolset (the @tool surface, __tool_env__)
├── tests/
│   ├── fakes/
│   │   └── fake_lsp_server.py    ← deterministic fake LSP server for tests
│   ├── test_lsp_jsonrpc.py
│   ├── test_lsp_client.py
│   ├── test_lsp_session.py
│   └── test_lsp_tools.py
└── tools_security/validate_path.py  ← reused unchanged
```

This mirrors the existing split: `security/` and `tools_security/` hold engine
logic at top level, while `tools/*.py` are the loadable toolsets discovered by
`get_available_toolsets()` / `/tools load` / `--tool tools.lsp_tools`.

## Tool Surface (v1 — read-only)

All functions decorated with `@tool(description=...)`, module marked
`__tool_readonly__ = True`. Every query returns `{"status": "success",
"data": ...}` or `{"status": "error", "message": [...]}`.

| Tool | Purpose | Key params |
|---|---|---|
| `lsp_start` | Start a session for a language (lazy init handshake). | `language: str`, `root_dir: Optional[str]` |
| `lsp_open` | Sync a file into the server (didOpen). | `path: str`, `language: Optional[str]` |
| `lsp_hover` | Hover documentation/type at a position. | `path: str`, `line: int`, `character: int` |
| `lsp_definition` | Go-to-definition locations. | `path: str`, `line: int`, `character: int` |
| `lsp_references` | All references of the symbol at a position. | `path: str`, `line: int`, `character: int`, `include_declaration: bool` |
| `lsp_completion` | Completion items at a position. | `path: str`, `line: int`, `character: int`, `limit: int` |
| `lsp_signature_help` | Callable signature + parameter docs. | `path: str`, `line: int`, `character: int` |
| `lsp_document_symbols` | Outline of a file (name, kind, range). | `path: str` |
| `lsp_workspace_symbols` | Workspace-wide symbol search by name. | `query: str`, `limit: int` |
| `lsp_diagnostics` | Cached `publishDiagnostics` for a file. | `path: str` |
| `lsp_status` | Active sessions, processes, diagnostics counts, recent server log. | — |
| `lsp_stop` | Shutdown + exit a session. | `language: str` |

### Env vars (`__tool_env__`)

```python
__tool_env__ = {
    "LAMA_OLE_LSP_SERVERS": "JSON dict mapping language id → server command (string or list), overrides defaults",
    "LAMA_OLE_LSP_ROOT": "Default workspace root for sessions (default: current working directory)",
    "LAMA_OLE_LSP_TIMEOUT": "Default request timeout in seconds (default: 15)",
}
```

### Default server mapping (`lsp/registry.py`)

```python
DEFAULT_LSP_SERVERS = {
    "python":     ["pyright-langserver", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "rust":       ["rust-analyzer"],
    "cpp":        ["clangd"],
    "c":          ["clangd"],
    "go":         ["gopls"],
    "json":       ["vscode-json-language-server", "--stdio"],
}
```

The mapping is resolved via `shutil.which()`; an unresolvable server yields a
clear error ("language server not found — install it or set
`LAMA_OLE_LSP_SERVERS`"). Unknown languages error with the list of known ones.

## Usage Example (REPL)

```text
>>> /tools load lsp_tools
Loaded toolset(s): lsp_tools

user: what type is `Client` at line 12 in tool_base/models.py?

assistant: [tool: lsp_start(language='python')]
[tool result: success]
[tool: lsp_open(path='tool_base/models.py')]
[tool: lsp_hover(path='tool_base/models.py', line=11, character=4)]
result: Tool(name='Client', ...) — dataclass field ...

user: now find every place Client is used
assistant: [tool: lsp_references(path='tool_base/models.py', line=11, character=4)]
```

## Decisions & Edge Cases (planned)

| Situation | Decision |
|---|---|
| D1: file edited by the `edit` toolset since last sync | Session compares stored mtime/size before every query; on change it sends a full-text `didChange` (version bump) first. The model never sees stale server state. |
| D2: server binary missing | Error dict with install hint + pointer to `LAMA_OLE_LSP_SERVERS`. No silent fallback to a different server. |
| D3: server process crashes mid-session | Pending requests fail with "server crashed"; reader thread EOF marks the session crashed; the next query attempts **one** auto-restart, then surfaces a "run `lsp_start` again" error. |
| D4: server pushes diagnostics at arbitrary times | Reader thread writes them into a per-session `uri → [diagnostic]` cache; `lsp_diagnostics(path)` reads the cache without a request round-trip. |
| D5: slow operations (first indexing, workspace symbols) | Default request timeout 15 s, overridable per call; `lsp_status`/`lsp_diagnostics` can be polled while indexing continues. |
| D6: server stderr noise | Captured into the session log (shown by `lsp_status`); never fed to the model raw. |
| D7: files from different languages | One session per language id, each with its own process. `lsp_open` accepts an optional `language`; else it is inferred from the file extension. |
| D8: position encoding | 0-based line and character; character in UTF-16 code units (LSP spec). Documented in every tool description. |
| D9: process hygiene | `atexit` handler shuts down all sessions (graceful `shutdown` + `exit`, then `terminate()`); `lsp_stop` is explicit. |
| D10: server command injection | The model never supplies a command string. Commands come only from `registry.py`/env. |
| D11: absolute/relative paths | Reuse `tools_security.validate_path` for every `path` argument; tests register a basepath for temp dirs. |
| D12: no engine changes | `run_with_tools`, `ChatState`, registry, and `to_ollama_tools` are untouched; `lsp_tools` is a normal loadable toolset. |
| D13: write features | `lsp_rename`, `lsp_code_action`, `workspace/applyEdit` are **v2** (they would race with the `edit` toolset). |

## Status

**Implemented.** The LSP toolset is live: `lsp/` (jsonrpc/client/registry/session),
`tools/lsp_tools.py`, the fake LSP server in `tests/fakes/fake_lsp_server.py`, and
the full test suite in `tests/test_lsp_*.py` (jsonrpc, client, session, tools,
plus a real-server integration smoke test that self-skips when no server is on
PATH). One deviation from this design doc: a session whose server crashes is
auto-restarted **once per explicit start**; a second crash in a row surfaces a
"run `lsp_start` again" error (see `004_testing_and_fake_server.md` § session
tests). Run the suite with `python3 tests/run_all_tests.py` from `lama_ole/`.
