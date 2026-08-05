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
├── chat.py                  # ChatState + REPL with slash commands (/feed, /clear, /model, etc.)
├── README.md                # Full documentation
│
└── tools/                   # Loadable tool modules (each is a Python module)
    ├── __init__.py
    ├── example_tools.py     # get_weather, calculate, read_file
    ├── media_understanding_tools.py  # image/video/audio via vision models + Whisper + OCR
    ├── dev_tools.py         # run_command, read/write/glob files, git_status, etc.
    ├── dev_tools_safer*.py  # Restricted subsets of dev tools
    ├── web_tools.py         # web_fetch, web_search
    ├── image_tools.py       # Image format conversion/resizing
    ├── video_tools.py       # Video format conversion/trimming
    ├── audio_tools.py       # Audio format conversion
    └── blob_server.py       # HTTP server for remote model transfer
```

---

### Key Files & Responsibilities

| File | Role |
|------|------|
| **`lama_ole.py`** | CLI entry point. Parses args, creates Ollama `Client`, handles model listing/transfer/blob-server as standalone modes, then delegates to either `run_with_tools()` (one-shot) or `ChatState` + `run_chat()` (REPL). Contains the full model transfer logic (`FilesystemBlobSource`, `HttpBlobSource`). |
| **`tool_base/`** | Core engine package. Defines `@tool` decorator that auto-infers JSON Schema from type annotations, registers tools in a registry (`registry.py`). `run_with_tools()` (`engine.py`) is the main loop: prepends safety system prompt, streams chat responses, handles tool calls (invoke → wrap result with `[data from ...]` markers → feed back to model), supports safe-mode confirmation for dangerous tools. `loop_states.py` provides `ExecutionState`/`StateManager` for state tracking and robust Ctrl-C handling. `logging.py` provides the centralized `StateLogger` used for granular timestamps. |
| **`chat.py`** | Interactive REPL (`ChatState`). Manages multi-turn conversation history, slash commands (`/feed`, `/clear`, `/model`, `/save`, `/load`, `/tools`, `/context`, `/help`, `/exit`), and delegates each turn to `run_with_tools()`. |
| **`tools/*.py`** | Tool modules. Each exports functions decorated with `@tool`. Tools can declare env vars via module-level `__tool_env__` dict (shown by `--help-tools`). |

---

### Key Patterns

- **Tool calling:** Python functions → JSON Schema inference → Ollama tool format conversion (`to_ollama_tools()`) → stream-based execution loop.
- **Thinking process:** Ollama's `msg.thinking` field is printed/flushed in real-time when `-t` or `--thoughtlog` is set.
- **Safety system prompt:** Hardcoded in `tool_base.py`; injected automatically unless `--no_safety_system_prompt` is given.
- **Model transfer:** Reads Ollama's local manifest/blobs, uploads via HTTP API to destination, rewrites Modelfile paths. Supports local→remote and remote→local (via blob server).

---

### Quick Navigation Tips

- To understand how tools work: read `tool_base/` → `registry.py` (`@tool`), `utils.py` (`_infer_params`), `engine.py` (`run_with_tools`).
- To understand the CLI flow: read `lama_ole.py` top-to-bottom.
- To add a new tool: create a module in `tools/`, decorate functions with `@tool`, load via `--tool mymodule`.
- Chat REPL logic is isolated in `chat.py` — `ChatState` holds messages, tools, options; `run_chat()` drives the loop.

---

### Tool Implementation Standards (Mandatory)

All new tools and refactored existing tools **must** follow the pattern used in `lama_ole/tools/edit.py`:

1.  **Decorator**: Use `@tool(description="...")` for all tool functions to ensure proper metadata extraction.
2.  **Return Format**: Functions must return a dictionary with the following structure:
    -   **Success**: `{"status": "success", "data": <content>}` (where `<content>` can be a string or JSON-serializable object).
    -   **Error**: `{"status": "error", "message": [<list_of_strings_or_string>]}`.
3.  **Safety Checks**: Perform validation (e.g., path traversal checks, permission checks) at the beginning of the function and return an error dictionary if validation fails.

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
