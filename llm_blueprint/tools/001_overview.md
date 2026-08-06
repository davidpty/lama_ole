# Tool Management in the Chat REPL — Overview

## Purpose

The REPL supports **runtime tool management** — toolsets can be loaded and
unloaded without restarting the process:

| Requirement (task_004.txt) | Command |
|---|---|
| Loading of toolsets | `/tools load <toolsetname> [<toolsetname> ...]` |
| Unloading of toolsets | `/tools unload <toolsetname> [<toolsetname> ...]` |
| Listing of **loaded** tools (was bare `/tools`) | `/tools loaded` |
| Listing of **available** toolsets | `/tools available` |
| Listing of **all tools of one toolset** | `/tools show <toolsetname>` |
| Listing of **all tools of all toolsets** | `/tools all` |

The bare `/tools` command (which used to list loaded tools) now prints the
subcommand usage; the old listing is `/tools loaded`.

## Terminology

- **Tool** — a single callable, created by the `@tool` decorator
  (`Tool` in `tool_base/models.py`).
- **Toolset** — one tool module: a single `.py` module such as
  `dev_tools.py`, `web_tools.py`, or `example_tools.py`. Each exported
  `@tool`-decorated function is a tool of that toolset. Registered as
  `ToolModuleInfo(module_name, tools, env_vars)` in `tool_base/registry.py`.
- **Toolset name** — the user-supplied identifier for a toolset. The REPL
  accepts both the bare basename (`dev_tools`) and the dotted module path
  (`tools.dev_tools`). `_resolve_toolset_module()` maps a bare name to the
  `tools.` package first, falling back to a top-level module; dotted names are
  used as-is. This matches the CLI, where `--tool`/`load_tools()` use the
  fully-qualified module name (e.g. `--tool tools.example_tools`).

## Current Behavior / Motivation

### How tools are loaded at startup

`lama_ole.py` loads tools **once at startup**; the REPL can add or remove
toolsets afterwards via `/tools`:

```python
# lama_ole.py:568-578
loaded_tools = []
if args.tools:
    for module_name in args.tools:
        try:
            module_tools = load_tools(module_name)
            loaded_tools.extend(module_tools)
        except Exception as e:
            print(f"Error loading tool module '{module_name}': {e}", file=sys.stderr)
            sys.exit(1)
ollama_tools = to_ollama_tools(loaded_tools) if loaded_tools else None
```

The `--tool` list is the merged result of `LAMA_OLE_TOOL` (env/config) and CLI
values (`_merge_tool_lists`, `lama_ole.py:442`).

`load_tools()` (`tool_base/registry.py`) imports the module (once, into
`sys.modules`), collects every `Tool` instance from its namespace, and appends
a `ToolModuleInfo` to the **global** `_TOOL_MODULES` list. (It is now
idempotent: re-calling for the same module does not duplicate the entry.):

```python
def load_tools(module_name: str) -> List[Tool]:
    if module_name not in sys.modules:
        importlib.import_module(module_name)
    mod = sys.modules[module_name]
    tools = [obj for obj in vars(mod).values() if isinstance(obj, Tool)]
    env_vars = getattr(mod, "__tool_env__", {})
    _TOOL_MODULES.append(ToolModuleInfo(
        module_name=module_name, tools=list(tools), env_vars=dict(env_vars),
    ))
    return tools
```

`ChatState` then carries both the Python tool objects **and** the
already-converted Ollama representation:

```python
# chat.py:26-27
loaded_tools: list[Tool] = field(default_factory=list)
ollama_tools: object = None
```

Both are passed to `run_with_tools()` on every turn (`chat.py:139-140`).
`run_with_tools` sends `ollama_tools` to the model (`engine.py:227`) and
dispatches tool calls by looking up `loaded_tools` by name (`engine.py:321`).

### What was missing (all addressed by this feature)

1. Tool loading was **startup-only**; `ChatState.loaded_tools` and
   `ChatState.ollama_tools` were set once and never touched again.
2. `/tools` only listed loaded tools; there was no load/unload and no listing
   of the *available* library.
3. `ollama_tools` was a **stale snapshot**: mutating `loaded_tools` at runtime
   requires recomputing `to_ollama_tools(loaded_tools)`, otherwise the model
   keeps advertising tools that are no longer present (or never sees newly
   loaded ones).
4. The registry was **global and not idempotent**: calling `load_tools()`
   twice for the same module appended a duplicate `ToolModuleInfo` to
   `_TOOL_MODULES` (the `Tool` objects are the same, but the info list
   drifts).
5. There was no notion of "available toolsets" — `get_tool_modules_info()`
   only knew about modules that were explicitly loaded. The library is the
   `tools/` package directory.

## Design Principles

| Principle | Rationale |
|-----------|-----------|
| `loaded_tools` is the single source of truth | All runtime mutations edit `ChatState.loaded_tools`; `ollama_tools` is always derived from it. |
| Derive, never cache | After every load/unload, recompute `state.ollama_tools = to_ollama_tools(state.loaded_tools)` so the next turn advertises exactly the current set. |
| Atomic multi-operations | `/tools load a b c` validates **all** names first; if any fails, nothing changes (mirrors `_load_skill_texts`, `chat.py:404`). |
| Toolset name resolution | Bare names (`dev_tools`) resolve to `tools.dev_tools` first, then to a top-level module; dotted paths are used as-is. Consistent with `--tool`. |
| No partial state | Unload removes a toolset's tools only if the set is actually loaded; loading dedups so a toolset is never present twice. |
| Reuse the existing engine | `run_with_tools()` needs no changes; only `ChatState` and the REPL handler change. |
| Follow the `/skill` subcommand pattern | One `_cmd_tools()` dispatcher with subcommands, mirroring `_cmd_skill` (`chat.py:421`). |
| Python 3.9 compatible | No 3.10+ syntax (per `AGENTS.md`). |

## Command Design

Implemented; the subcommand vocabulary was approved in `task_004_001.txt`.

```
/tools                          → print usage (subcommand list)
/tools loaded                   → list loaded toolsets + each tool (renamed /tools)
/tools available                → list toolsets available to load (the tools/ library)
/tools show <toolset>           → list all tools of one toolset (name + description + signature)
/tools all                      → list all tools of all toolsets in the library
/tools load <t> [<t> ...]       → load one or more toolsets (dedup, atomic)
/tools unload <t> [<t> ...]     → unload one or more toolsets (atomic)
```

### Mapping to task_004.txt

| Task requirement | Command | Notes |
|---|---|---|
| unloading of tools | `/tools unload <toolsetname> [ ...]` | Removes the toolset from `loaded_tools` and recomputes `ollama_tools`. |
| loading of tools | `/tools load <toolsetname> [ ...]` | Imports via `load_tools()`, appends to `loaded_tools`, recomputes `ollama_tools`. |
| listing of loaded tools (was `/tools`) | `/tools loaded` | Preserves the old bare `/tools` behavior under a subcommand. |
| listing of available tools | `/tools available` | Lists the toolsets available in `tools/` (the loadable library). |
| listing of all tools of a toolset | `/tools show <toolset>` | Details of a single toolset's exported tools. |
| listing of all tools of all toolsets | `/tools all` | Union of every toolset in the library, each with its tools. |

## REPL Usage Examples

```text
>>> /tools available
Available toolsets:
  apply_patch
  example_tools
  web_tools
  ...

>>> /tools load example_tools web_tools
Loaded toolset(s): example_tools web_tools

>>> /tools loaded
Loaded toolset 'example_tools':
    get_weather(city: string) — Get the current weather for a city
    calculate(expression: string) — Calculate a mathematical expression
    read_file(path: string) — Read the contents of a file
Loaded toolset 'web_tools':
    web_fetch(url: string, [timeout: integer]) — Fetch a URL and return its content
    web_search(query: string, [timeout: integer]) — Search the web using a search engine

>>> /tools show edit
Toolset 'edit':
    edit(path: string, search: string, replace: string) — Replaces the 'search' string ...
    create_new_file(path: string, content: string) — Creates a new file ...

>>> /tools all                       # every toolset in the library, tool by tool

>>> /tools unload web_tools
Unloaded toolset(s): web_tools
```

The newly loaded/unloaded set takes effect on the **very next turn**: after a
load, the model sees the new tools; after an unload, the model no longer
advertises them (see `refresh_ollama_tools()` below).

## Architecture at a Glance

```
lama_ole/
├── chat.py                       ← _cmd_tools() dispatcher + helpers (_resolve_toolset_module,
│                                   refresh_ollama_tools(), _list_* / _tools_load / _tools_unload)
├── tool_base/
│   ├── registry.py               ← idempotent load_tools(), get_available_toolsets(),
│   │                                get_tools_of_module(), peek_tools_of_module()
│   └── models.py                 ← (unchanged) Tool, ToolModuleInfo
├── tools/                        ← the loadable library ("available toolsets")
└── documentation/tools/
    └── 001_overview.md           ← this file
```

## Implementation Notes (what was built)

> The sections below are the implementation notes; everything is implemented
> (see *Status*). Code snippets are representative of the final state in the
> referenced files.

### Registry (`tool_base/registry.py`)

`tool_base/registry.py`:

```python
def load_tools(module_name: str) -> List[Tool]:
    if module_name not in sys.modules:
        importlib.import_module(module_name)
    mod = sys.modules[module_name]
    tools = [obj for obj in vars(mod).values() if isinstance(obj, Tool)]
    env_vars = getattr(mod, "__tool_env__", {})
    # idempotent: do not duplicate ToolModuleInfo for an already-loaded module
    if not any(m.module_name == module_name for m in _TOOL_MODULES):
        _TOOL_MODULES.append(ToolModuleInfo(
            module_name=module_name, tools=list(tools), env_vars=dict(env_vars),
        ))
    return tools
```

Add a discovery helper that returns the **available** toolsets (every module
in `lama_ole/tools/`, excluding `__init__.py` and underscore-private files):

```python
TOOLS_PACKAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")

def get_available_toolsets(tools_dir: Optional[str] = None) -> List[str]:
    """Module names loadable via /tools load, i.e. *.py in tools/."""
    if tools_dir is None:
        tools_dir = TOOLS_PACKAGE_DIR
    names = []
    for f in sorted(os.listdir(tools_dir)):
        if f.startswith("_") or f == "__init__.py":
            continue
        if f.endswith(".py"):
            names.append(f[:-3])
    return names
```

> Note: modules without any `@tool` functions (e.g. `blob_server.py`,
> `testrunner.py`) show as "available" but load to zero tools; `/tools show`
> and `/tools all` handle that gracefully by reporting `(no tools)` (see
> *Resolved Questions*).

### `ChatState` (`chat.py`)

`chat.py`:

```python
@dataclass
class ChatState:
    ...
    loaded_tool_modules: list = field(default_factory=list)  # module names, in load order
    tools_dir: str = None            # overridable tools package dir (tests)

    def refresh_ollama_tools(self) -> None:
        """Recompute the Ollama tool list from loaded_tools."""
        self.ollama_tools = to_ollama_tools(self.loaded_tools) if self.loaded_tools else None
```

Import `to_ollama_tools` and `get_available_toolsets` from `tool_base` (add
`get_available_toolsets` to `tool_base/__init__.py` `__all__`).

`lama_ole.py` startup keeps its behavior but also records the module names so
`/tools loaded` and `/save`/`/load` know the source:

```python
state.loaded_tool_modules = list(args.tools)   # set alongside loaded_tools
```

### `_cmd_tools()` dispatcher (`chat.py`)

Mirror the `/skill` structure (`chat.py:421`). Bare `/tools` prints usage;
subcommands:

```python
def _cmd_tools(arg: str, state: ChatState):
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    sub_arg = parts[1] if len(parts) > 1 else ""

    if sub == "":
        _show_tools_usage()
    elif sub == "loaded":
        _list_loaded_tools(state)
    elif sub == "available":
        _list_available_toolsets(state)
    elif sub == "show":
        _show_toolset(sub_arg, state)
    elif sub == "all":
        _list_all_tools(state)
    elif sub == "load":
        _tools_load(sub_arg, state)
    elif sub == "unload":
        _tools_unload(sub_arg, state)
    else:
        print(f"Unknown /tools subcommand: {sub}")
        _show_tools_usage()
```

#### `loaded` (renamed `/tools`)

Lists loaded toolsets grouped by toolset (was the bare `/tools` output, now
under a subcommand):

```python
def _list_loaded_tools(state):
    if not state.loaded_tool_modules:
        print("No toolsets loaded.")
        return
    for mod in state.loaded_tool_modules:
        tools = [t for t in state.loaded_tools if t.name in _tool_names(mod)]
        ...
```

> Grouping by toolset requires knowing which `Tool` belongs to which module.
> The `Tool` dataclass has no module field; the simplest mapping is the
> `ToolModuleInfo.tools` list from `get_tool_modules_info()`. If a toolset was
> loaded at startup and again at runtime, dedup keeps the list correct.

#### `available`

```python
def _list_available_toolsets(state):
    names = get_available_toolsets()
    loaded = set(state.loaded_tool_modules)
    if not names:
        print("No toolsets available.")
        return
    for n in names:
        marker = " (loaded)" if n in loaded else ""
        print(f"  {n}{marker}")
```

#### `show <toolset>` — all tools of one toolset

Resolve the module (loaded or not) and print each tool's name, signature and
description. For an **unloaded** toolset this requires importing the module
just to introspect (see *Decision D1* below). Output mirrors `--help-tools`
(`lama_ole.py:596-618`):

```python
  tool_name(arg1: string, [arg2: integer]) — description
```

#### `all` — all tools of all toolsets

For every available toolset (and any loaded module outside `tools/`), print
the same one-line-per-tool listing grouped by toolset.

#### `load <toolset> [ ...]`

```python
def _tools_load(names, state):
    names = (names or "").split()
    if not names:
        print("Usage: /tools load <toolsetname> [<toolsetname> ...]")
        return
    already = set(state.loaded_tool_modules)
    new_names = []
    for n in names:
        if n in already:
            print(f"Toolset '{n}' is already loaded.")
        elif n not in get_available_toolsets():
            print(f"Error: unknown toolset '{n}'.")
        else:
            new_names.append(n)
    # atomic: only load if every name is valid
    if len(new_names) != len(names):
        return
    for n in new_names:
        try:
            tools = load_tools(n)
            state.loaded_tools.extend(tools)
            state.loaded_tool_modules.append(n)
        except Exception as e:
            print(f"Error loading toolset '{n}': {e}")
            # rollback already-applied names to stay atomic
            ...
            return
    state.refresh_ollama_tools()
    print(f"Loaded toolset(s): {' '.join(new_names)}")
```

#### `unload <toolset> [ ...]`

```python
def _tools_unload(names, state):
    names = (names or "").split()
    if not names:
        print("Usage: /tools unload <toolsetname> [<toolsetname> ...]")
        return
    # collect the Tool objects of each named toolset via ToolModuleInfo
    # validate all names are loaded BEFORE mutating (atomic)
    ...
    state.loaded_tool_modules = [m for m in state.loaded_tool_modules if m not in to_remove]
    state.loaded_tools = [t for t in state.loaded_tools if t not in removed_tools]
    state.refresh_ollama_tools()
    print(f"Unloaded toolset(s): {' '.join(names)}")
```

Because unload must map a toolset name to its `Tool` objects, the registry
provides a small helper:

```python
def get_tools_of_module(module_name: str) -> List[Tool]:
    for info in _TOOL_MODULES:
        if info.module_name == module_name:
            return list(info.tools)
    return []
```

### Help text + save/load persistence

- `_show_help()` (`chat.py:233`) replaces `/tools` with the new lines:

```
  /tools loaded              List loaded toolsets and their tools
  /tools available           List toolsets available to load
  /tools show <toolset>      List all tools of one toolset
  /tools all                 List all tools of all toolsets
  /tools load <toolset> [...]   Load one or more toolsets
  /tools unload <toolset> [...] Unload one or more toolsets
```

- `/save` (`chat.py:300`) adds `"loaded_tool_modules": state.loaded_tool_modules`
  (alongside `skill`/`skill_text`); `/load` (`chat.py:325`) restores it by
  re-loading each module via `load_tools()`. If a module no longer exists,
  warn and continue with the rest (messages still restore fine).

### Tests

New `lama_ole/tests/test_tools_repl.py` following the skills test pattern
(sys.path bootstrap, no `lama_ole.` imports; pytest-style). Cases:

- `load_tools()` is idempotent (second call does not duplicate `ToolModuleInfo`).
- `get_available_toolsets()` lists `example_tools`, `dev_tools`, ... and skips
  `__init__.py`/private files.
- `get_tools_of_module()` returns the right `Tool` objects; unknown module → `[]`.
- `_cmd_tools` dispatch: `loaded` lists loaded toolsets; `available` lists the
  library; `show <t>` prints one toolset's tools; `all` prints every toolset.
- `/tools load a b` loads both; duplicate load is rejected; unknown name fails
  **without** changing `loaded_tools` (atomic); `ollama_tools` recomputed.
- `/tools unload a b` removes both; unload of a not-loaded toolset errors and
  changes nothing; unloading everything sets `ollama_tools = None`.
- `/save` then `/load` restores `loaded_tool_modules`.
- Help output lists the new `/tools` subcommands.

Run with `python3 tests/run_all_tests.py` (documented in `AGENTS.md`).

## Decisions & Edge Cases (implemented)

| Situation | Decision |
|---|---|
| D1: `/tools show` / `/tools all` for an **unloaded** toolset | `_resolve_toolset_tools()` uses the registry (`get_tools_of_module`) first, else `peek_tools_of_module()` — imports the module to introspect its `Tool` objects **without** registering it. Listing never changes the loaded set; on import failure (missing dependency) it prints the error and skips the toolset. |
| Duplicate `load_tools()` calls | Registry dedups `_TOOL_MODULES`; `_cmd_tools` rejects already-loaded names. |
| Unknown toolset on load | Error printed; **no** partial load (all names validated before importing). |
| Unknown toolset on unload | Error printed; **no** partial unload. |
| Unloading everything | `loaded_tools` empty → `ollama_tools = None`; model gets no tools. |
| Modules in `tools/` without tools (e.g. `blob_server.py`) | Listed as available but `/tools show` reports "no tools"; `/tools load` succeeds but adds nothing (message clarifies). |
| `ollama_websearch` enabled | Unaffected — `run_with_tools` appends the web tool per-call (`engine.py:153`); recomputing `ollama_tools` is safe. |
| Safe-mode / dangerous tools | Unchanged; per-tool `safe` confirmation is checked at call time. |
| `/save`/`/load` with toolsets | Module names persisted; restore re-imports them. Modules in `sys.modules` stay imported after unload (harmless; re-load is cheap). |
| Re-loading after unload | Module is already in `sys.modules`; `load_tools()` re-collects the same `Tool` objects — no double registration (dedup). |
| Toolset loaded via absolute module path | Out of scope for v1: `/tools` only manages the `tools/` package library. `--tool` modules outside `tools/` still work at startup; they appear in `/tools loaded` if `loaded_tool_modules` is populated from `args.tools`. |

## Files Changed

| File | Change | Status |
|---|---|---|
| `lama_ole/tool_base/registry.py` | Idempotent `load_tools()`; add `get_available_toolsets()` and `get_tools_of_module()`. | Done |
| `lama_ole/tool_base/__init__.py` | Export the new registry helpers. | Done |
| `lama_ole/chat.py` | `loaded_tool_modules` + `tools_dir` fields, `refresh_ollama_tools()`, `_cmd_tools()` + helpers, help text, save/load persistence. | Done |
| `lama_ole/lama_ole.py` | Populate `loaded_tool_modules` at startup from `args.tools`. | Done |
| `lama_ole/tests/test_tools_repl.py` | Unit tests for dispatch, load/unload atomicity, registry helpers, persistence. | Done |
| `lama_ole/AGENTS.md` | Update `chat.py` description (slash-command list) if the help text grows. | Done |
| `documentation/tools/001_overview.md` | This overview; kept in sync with the implementation. | Done |

## Resolved Questions

1. **Subcommand naming.** `loaded` / `available` / `show` / `all` — **resolved:**
   approved in `task_004_001.txt`.
2. **`available` filtering.** List all `*.py` in `tools/` (cheap, predictable);
   modules without tools show as available and `/tools show` reports
   `(no tools)`. — **resolved.**
3. **Persistence scope.** `/save` persists `loaded_tool_modules` and `/load`
   restores them by re-importing. — **resolved: persist.**
4. **Tool→toolset mapping.** `ToolModuleInfo.tools` is the lookup
   (`get_tools_of_module()`); no `Tool` dataclass change. — **resolved.**

## Status

Implemented (2026-08-06):

- `/tools loaded` lists loaded toolsets and their tools (renamed from bare
  `/tools`, which now prints usage).
- `/tools available` lists the toolsets in the tools package library.
- `/tools show <toolset>` lists all tools of one toolset (introspects unloaded
  toolsets without registering them; `peek_tools_of_module()`).
- `/tools all` lists all tools of all toolsets.
- `/tools load <toolset> [ ...]` and `/tools unload <toolset> [ ...]` are
  atomic (validate all names first, roll back on import error), dedupe, and
  recompute `ollama_tools` via `ChatState.refresh_ollama_tools()`.
- Toolset names accept bare basenames (`dev_tools`) and resolve to the `tools.`
  package first, falling back to a top-level module (`_resolve_toolset_module`).
- `load_tools()` is idempotent (no duplicate `ToolModuleInfo`).
- `/save` / `/load` persist and restore `loaded_tool_modules`.
- Tests: `lama_ole/tests/test_tools_repl.py` (22 tests); full suite is green
  (`python3 tests/run_all_tests.py`).
