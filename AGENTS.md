## Project Overview — `lama_ole`

**What it is:** A CLI wrapper around [Ollama](https://ollama.com) for interacting with local LLMs, supporting streaming chat, tool calling, thinking-process display, and media understanding (image/video/audio).

---

### Layout

```
lama_ole/
├── __init__.py              # empty package marker
├── lama_ole.py              # CLI entry point — argparse setup, orchestration, model transfer
├── tool_base/               # Core engine package: @tool decorator, Tool registry, run_with_tools loop, safety prompt
│   ├── __init__.py          # Public re-exports (run_with_tools, tool, StateManager, StateLogger, ...)
│   ├── constants.py         # SAFETY_SYSTEM_PROMPT, JSON_RETURN_PROMPT, DANGEROUS_TOOLS
│   ├── config.py            # Vision models + Ollama host configuration
│   ├── engine.py            # run_with_tools loop + to_ollama_tools conversion
│   ├── logging.py           # StateLogger + granular timestamp helpers
│   ├── loop_states.py       # ExecutionState enum + StateManager
│   ├── models.py            # Tool, ToolModuleInfo dataclasses
│   ├── registry.py          # @tool decorator, tool loading
│   └── utils.py             # _infer_params, create_uuid_15
├── chat.py                  # ChatState + REPL with slash commands (/feed, /new, /model, /skill, /stats, etc.)
├── lsp/                     # Language Server integration engine (JSON-RPC over stdio)
│   ├── __init__.py          # Public re-exports (JsonRpcCodec, LspClient, LspSessionManager, resolve_server, ...)
│   ├── jsonrpc.py           # JsonRpcCodec: Content-Length framing, incremental parser
│   ├── client.py            # LspClient: server subprocess + reader thread, requests/notifications, crash detection
│   ├── registry.py          # DEFAULT_LSP_SERVERS + LAMA_OLE_LSP_SERVERS override, language_for_path
│   └── session.py           # LspSessionManager: one session per language, document freshness, diagnostics cache
├── README.md                # Full documentation
├── skills/                  # Skill library loaded into the system role (/skill, --skill)
│   ├── code-reviewer.md
│   └── german-assistant.md
├── tests/                   # pytest-style + unittest-style tests
│   ├── fakes/fake_lsp_server.py  # deterministic scripted LSP server for the LSP tests
│   ├── lsp_fixtures.py           # fake-server script helpers (standard_script, diag_script, ...)
│   ├── test_lsp_*.py             # jsonrpc / client / session / tools / integration tests
│   └── run_all_tests.py     # Runs both frameworks (see "Running the Tests")
└── tools/                   # Loadable tool modules (each is a Python module)
    ├── __init__.py
    ├── example_tools.py     # get_weather, calculate, read_file
    ├── media_understanding_tools.py  # image/video/audio via vision models + Whisper + OCR
    ├── dev_tools_readonly.py  # Restricted subsets of dev tools
    ├── web_tools.py         # web_fetch, web_search
    ├── image_tools.py       # Image format conversion/resizing
    ├── video_tools.py       # Video format conversion/trimming
    ├── audio_tools.py       # Audio format conversion
    ├── lsp_tools.py         # LSP code-intelligence toolset (hover, definition, diagnostics, ...)
    └── blob_server.py       # HTTP server for remote model transfer
```

---

### Key Files & Responsibilities

| File | Role |
|------|------|
| **`lama_ole.py`** | CLI entry point. Parses args, creates Ollama `Client`, handles model listing/transfer/blob-server as standalone modes, then delegates to either `run_with_tools()` (one-shot) or `ChatState` + `run_chat()` (REPL). Contains the full model transfer logic (`FilesystemBlobSource`, `HttpBlobSource`). |
| **`tool_base/`** | Core engine package. Defines `@tool` decorator that auto-infers JSON Schema from type annotations, registers tools in a registry (`registry.py`). `run_with_tools()` (`engine.py`) is the main loop: prepends safety system prompt, streams chat responses, handles tool calls (invoke → wrap result with `[data from ...]` markers → feed back to model), supports safe-mode confirmation for dangerous tools. `loop_states.py` provides `ExecutionState`/`StateManager` for state tracking and robust Ctrl-C handling. `logging.py` provides the centralized `StateLogger` used for granular timestamps. |
| **`chat.py`** | Interactive REPL (`ChatState`). Manages multi-turn conversation history, slash commands (`/feed`, `/new`, `/model`, `/save`, `/load`, `/tools` (load/unload/show toolsets), `/skill`, `/stats`, `/systemprompt`, `/context`, `/help`, `/exit`), and delegates each turn to `run_with_tools()`. |
| **`lsp/`** | LSP integration engine. `jsonrpc.py` (`JsonRpcCodec`) does `Content-Length` framing with an incremental parser; `client.py` (`LspClient`) owns one server subprocess per session with an always-on `read1` reader thread, JSON-RPC requests/notifications, and crash detection; `registry.py` maps language ids to server commands (built-in table + `LAMA_OLE_LSP_SERVERS` env override — the LLM never supplies commands); `session.py` (`LspSessionManager`) keeps one client per language, re-syncs changed files (mtime/size) with didOpen/didChange, caches push diagnostics, auto-restarts a crashed server once per explicit start, and registers an `atexit` `stop_all`. |
| **`tools/*.py`** | Tool modules. Each exports functions decorated with `@tool`. Tools can declare env vars via module-level `__tool_env__` dict (shown by `--help-tools`). |

---

### Key Patterns

- **Tool calling:** Python functions → JSON Schema inference → Ollama tool format conversion (`to_ollama_tools()`) → stream-based execution loop.
- **Thinking process:** Ollama's `msg.thinking` field is printed/flushed in real-time when `-t` or `--thoughtlog` is set.
- **Safety system prompt:** Built by `compose_system_prompt()` in `tool_base/engine.py`; injected automatically unless `--no_safety_system_prompt` is given.
- **Model transfer:** Reads Ollama's local manifest/blobs, uploads via HTTP API to destination, rewrites Modelfile paths. Supports local→remote and remote→local (via blob server).

---

### Quick Navigation Tips

- To understand how tools work: read `tool_base/` → `registry.py` (`@tool`), `utils.py` (`_infer_params`), `engine.py` (`run_with_tools`).
- To understand the CLI flow: read `lama_ole.py` top-to-bottom.
- To add a new tool: create a module in `tools/`, decorate functions with `@tool`, load via `--tool mymodule`.
- Chat REPL logic is isolated in `chat.py` — `ChatState` holds messages, tools, options; `run_chat()` drives the loop.

---

### Running the Tests

The suite in `tests/` mixes **pytest-style** and **unittest-style** files (see
`llm_blueprint/testing/001_guidelines.md`). The single entry point runs both:

```bash
cd lama_ole
python3 tests/run_all_tests.py        # runs everything
python3 tests/run_all_tests.py -v     # verbose unittest output
```

The helper executes, in order, and exits non-zero if any run fails:

1. `python3 -m unittest discover -s tests -p "test_*.py"` — the unittest-style
   files (`test_edit_tools.py`, `test_true.py`).
2. `python3 -m pytest tests/ -q` — the full suite (pytest also collects the
   unittest classes, so no test is skipped).

Equivalent one-liners when only one framework is needed:

```bash
python3 -m pytest tests/ -q
python3 -m unittest discover -s tests -p "test_*.py" -v
```

---

### Tool Implementation Standards (Mandatory)

All new tools and refactored existing tools **must** follow the pattern used in `lama_ole/tools/edit.py`:

1.  **Decorator**: Use `@tool(description="...")` for all tool functions to ensure proper metadata extraction.
2.  **Return Format**: Functions must return a dictionary with the following structure:
    -   **Success**: `{"status": "success", "data": <content>}` (where `<content>` can be a string or JSON-serializable object).
    -   **Error**: `{"status": "error", "message": [<list_of_strings_or_string>]}`.
3.  **Safety Checks**: Perform validation (e.g., path traversal checks, permission checks) at the beginning of the function and return an error dictionary if validation fails.

### CLI Help Defaults (Mandatory)

`build_parser()` in `lama_ole.py` uses `argparse.ArgumentDefaultsHelpFormatter`,
which appends `(default: <value>)` to each option's help from its `default=`
argument automatically. Therefore **help strings must never hardcode a default
value** — writing `(default: ...)` (or "Default is ...") inside `help=` produces
doubled/duplicated output and lets the help text drift from the code. Use the
actual `default=` as the single source of truth; this is enforced by
`tests/test_help_defaults.py`.

---

### Python 3.9+ Compatibility (Mandatory)

The project targets **Python 3.9 and newer** (the repo ships `cpython-39` byte-code
caches). Code must run unchanged on 3.9, so avoid Python 3.10+ syntax and stdlib:

- **No PEP 604 unions at runtime** — `bytes | str`, `str | None`, etc. raise
  `TypeError` on 3.9. Prefer `typing.Optional` / `typing.Union`.
- **If you must use `|` unions**, add `from __future__ import annotations` as the first
  statement after the module docstring. Annotations then become lazily-evaluated strings
  and are never resolved at import time (see `security/entropychecker.py`).
- **No other 3.10+ syntax**: `match`/`case`, parenthesized context managers.
- **No 3.10+ stdlib**: `tomllib`, `zoneinfo`, `str.removeprefix`/`removesuffix`,
  `itertools.pairwise`, etc.
- PEP 585 generics (`list[str]`, `dict[str, int]`) are fine in 3.9, and are safe in
  annotations with `from __future__ import annotations`.
