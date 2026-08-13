import importlib.util
import json
import hashlib
import math
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field

try:
    import readline
except ImportError:
    readline = None

from tool_base import (
    Tool,
    run_with_tools,
    StateManager,
    ExecutionState,
    ExecutionInterrupted,
    to_ollama_tools,
    load_tools,
    get_available_toolsets,
    get_tools_of_module,
    peek_tools_of_module,
    module_file_has_tools,
)
from tool_base.compaction import (
    COMPACTION_SYSTEM_PROMPT,
    DEFAULT_CTX_COMPACT_THRESHOLD,
    SUMMARY_OUTPUT_TOKENS,
    apply_compaction,
    build_summary_prompt,
    default_preserve_budget,
    estimate_tokens,
    find_previous_summary,
    sanitize_ctx_threshold,
    select_head_tail,
    serialize_for_compaction,
)
from tool_base.logging import StateLogger
from tool_base.engine import _print_diff_block

import color_util
import history as history_mod


@dataclass
class ChatState:
    client: object
    model: str
    messages: list = field(default_factory=list)
    loaded_tools: list[Tool] = field(default_factory=list)
    loaded_tool_modules: list = field(default_factory=list)
    ollama_tools: object = None
    options: dict = field(default_factory=dict)
    keep_alive: object = None
    show_thinking: bool = False
    no_safety_system_prompt: bool = False
    system_prompt: str = None
    skill_text: str = None
    skill: str = None
    skills_dir: str = None
    tools_dir: str = None
    verbose: int = 0
    safe: bool = False
    mode: str = "build"
    show_diff: bool = True
    thought_file_handle: object = None
    output_file_handle: object = None
    toolcall_file_handle: object = None
    chatinput_file_handle: object = None
    max_tool_rounds: int = None
    max_tool_rounds_continuation: str = "ask"
    ollama_websearch: bool = False
    ndjson_log_path: str = None
    ndjson_log_file_handle: object = None
    color: object = "auto"
    state_manager: StateManager = field(default_factory=StateManager)
    sessions_dir: str = None
    session_id: str = None
    session_created_at: float = None
    session_autosave: bool = True
    session_title: str = None
    ctx_meter: bool = True
    ctx_max: int = None
    ctx_usage: dict = None
    ctx_usage_model: str = None
    ctx_compact: bool = False
    ctx_compact_threshold: float = DEFAULT_CTX_COMPACT_THRESHOLD
    ctx_compact_model: str = None
    loaded_model: str = None
    model_backend: str = "ollama"
    stats_by_model: dict = field(default_factory=dict)
    _hotkey_listener: object = None
    _last_cut_messages: list = field(default_factory=list)
    _last_cut_indices: list = None  # message indices removed by the last /cut

    def __post_init__(self):
        if self.ndjson_log_path and self.ndjson_log_file_handle is None:
            self.ndjson_log_file_handle = open(
                self.ndjson_log_path, "w", encoding="utf-8"
            )

    def log_ndjson(self, message=None):
        if not self.ndjson_log_file_handle:
            return
        try:
            data = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "model": self.model,
                "message": message,
            }
            self.ndjson_log_file_handle.write(
                json.dumps(data, ensure_ascii=False) + "\n"
            )
            self.ndjson_log_file_handle.flush()
        except Exception as e:
            print(f"Error writing ndjson log: {e}", file=sys.stderr)

    @staticmethod
    def stamp_message(msg) -> None:
        """Attach the event time to a message dict (idempotent).

        Used by /history so each entry can show when the event happened.
        Existing timestamps (e.g. restored by /load) are left untouched.
        """
        if isinstance(msg, dict) and "timestamp" not in msg:
            msg["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def close(self):
        if self.ndjson_log_file_handle is not None:
            self.ndjson_log_file_handle.close()
            self.ndjson_log_file_handle = None

    def apply_skill(self) -> None:
        """Rewrite the system message in state.messages from skill_text.

        Mirrors tool_base/engine.py composition so the REPL and the engine stay
        in sync. If no system message exists yet (e.g. a skill was loaded before
        the first turn), compose and insert one.
        """
        from tool_base import compose_system_prompt

        new_content = compose_system_prompt(
            system_prompt=self.system_prompt,
            skill_text=self.skill_text,
            no_safety_system_prompt=self.no_safety_system_prompt,
            mode=self.mode,
        )
        for i, m in enumerate(self.messages):
            if m.get("role") == "system":
                self.messages[i]["content"] = new_content
                return
        self.messages.insert(0, {"role": "system", "content": new_content})

    def refresh_ollama_tools(self) -> None:
        """Recompute the backend tool list from ``loaded_tools``.

        Called after every runtime load/unload so the next turn advertises
        exactly the current set of tools. The full set is advertised in both
        build and plan mode; write tools are gated at execution time instead
        (see the engine's plan-mode gate), so the model always knows which
        tools exist and will work once build mode is active.
        """
        if not self.loaded_tools:
            self.ollama_tools = None
            return
        make_tools = getattr(self.client, "make_tools", None)
        if callable(make_tools):
            self.ollama_tools = make_tools(self.loaded_tools)
        else:
            self.ollama_tools = to_ollama_tools(self.loaded_tools)

    def get_history_entries(self):
        """Returns a list of viewable history entries with numbering and type info.

        Delegates to :mod:`history`, the shared model also used by /cut and
        session replay. Entries are numbered 1 (oldest) .. V (newest) over
        non-system messages, so the numbers shown by /history are exactly the
        ones /cut accepts.
        """
        return history_mod.history_entries(self.messages)

    def undo_cut(self):
        """Restores the messages removed by the last /cut command."""
        if self._last_cut_messages and self._last_cut_indices:
            for idx, msg in sorted(
                zip(self._last_cut_indices, self._last_cut_messages)
            ):
                self.messages.insert(idx, msg)
            self._last_cut_messages = []
            self._last_cut_indices = None
            return True
        return False

    # -- mid-turn mode switching ----------------------------------------------

    def toggle_mode(self) -> None:
        """Flip plan/build mode. Safe to call from the hotkey thread."""
        target = "plan" if self.mode == "build" else "build"
        _set_mode(self, target, autosave=False)

    def start_hotkey_listener(self) -> None:
        if self._hotkey_listener is None:
            from tool_base.mode_switch import ModeHotkeyListener
            self._hotkey_listener = ModeHotkeyListener(self.toggle_mode)
        self._hotkey_listener.start()

    def stop_hotkey_listener(self) -> None:
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()

    def hotkey_pause(self) -> None:
        """Park the listener + restore canonical stdin (engine prompts)."""
        if self._hotkey_listener is not None:
            self._hotkey_listener.pause()

    def hotkey_resume(self) -> None:
        if self._hotkey_listener is not None:
            self._hotkey_listener.resume()

    def hotkey_drain(self) -> str:
        """Text typed mid-turn, for replay at the next prompt."""
        if self._hotkey_listener is None:
            return ""
        return self._hotkey_listener.drain_typeahead()


# ---------------------------------------------------------------------------
# Plan / build mode
# ---------------------------------------------------------------------------

_MODES = ("build", "plan")


def _set_mode(state: ChatState, mode: str, autosave: bool = True) -> None:
    """Switch the chat agent between build and plan mode.

    Re-composes the system prompt (adds/removes the plan-mode block) and
    re-points the Shift+Tab toggle binding so the next keystroke returns to
    the other mode. The advertised tool set is unchanged: all loaded tools
    stay visible in plan mode, and write tools are refused at execution time
    by the engine's plan-mode gate.

    ``autosave=False`` is used for mid-turn toggles from the hotkey thread so
    the session file is never written concurrently with the main thread.
    """
    if mode not in _MODES:
        print("Mode must be one of: build, plan")
        return
    if state.mode == mode:
        print(f"Already in {mode} mode.")
        return
    state.mode = mode
    state.apply_skill()
    _bind_mode_toggle(state)
    if autosave:
        autosave_session(state)
    print(f"Switched to {mode} mode.")


def _mode_label(state: ChatState, use_color: bool) -> str:
    """Prompt prefix indicating the active mode ('[build] ' / '[plan] ')."""
    if state.mode == "plan":
        return color_util.colored("[plan] ", color_util.C_METER_MID, use_color)
    return color_util.colored("[build] ", color_util.C_METER_LOW, use_color)


def _bind_mode_toggle(state: ChatState) -> None:
    """Bind Shift+Tab to submit /plan or /build, whichever toggles the mode.

    Tab itself stays bound to completion, so the plan/build switch uses
    Shift+Tab (``ESC [ Z``). The macro first clears the line so a partially
    typed message is never mangled. The binding is re-pointed after every
    switch so the same keystroke keeps toggling.
    """
    if readline is None:
        return
    target = "/plan" if state.mode == "build" else "/build"
    try:
        if "libedit" in (readline.__doc__ or ""):
            readline.parse_and_bind(f'bind -s "^[[Z" "{target}"')
        else:
            readline.parse_and_bind(f'"\\e[Z": "\\C-a\\C-k{target}\\n"')
    except Exception:
        pass


_PATH_COMMANDS = {"/feed", "/save", "/load"}

# _COMMANDS and _COMMAND_SUBCOMMANDS are derived from the data-driven
# dispatch tables at the bottom of this module (single source of truth for
# both /handle_command dispatch and Tab completion).


def _complete_file_path(partial: str) -> list:
    """Completion candidates for a (possibly partial) file path.

    Directories are returned with a trailing separator so Tab keeps
    expanding into them.
    """
    partial = os.path.expanduser(partial)
    dirname, basename = os.path.split(partial)
    if dirname == "":
        dirname = "."
    try:
        entries = sorted(os.listdir(dirname))
    except OSError:
        return []
    matches = []
    for name in entries:
        if not name.startswith(basename):
            continue
        full = name if dirname in (".", "") else os.path.join(dirname, name)
        if os.path.isdir(full):
            full += os.sep
        matches.append(full)
    return matches


def _toolset_segment_dir(tools_dir: str, seg: str):
    """Resolve a leading dotted segment of a module path to a package directory.

    ``tools`` maps to the tools directory itself. Any other segment resolves to
    a subpackage inside ``tools/`` (``tools_dir/<seg>``) first, falling back to
    a sibling package at the same level as ``tools/`` (the parent of
    ``tools_dir``), so tools can live in packages like ``tools_security`` or
    ``mycompany``. Returns ``(abs_dir, module_prefix)`` or ``(None, None)``.
    """
    if seg == "tools":
        return tools_dir, "tools"
    if not tools_dir:
        return None, None
    sub = os.path.join(tools_dir, seg)
    if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "__init__.py")):
        return sub, f"tools.{seg}"
    parent = os.path.dirname(tools_dir)
    if parent:
        sib = os.path.join(parent, seg)
        if os.path.isdir(sib) and os.path.isfile(os.path.join(sib, "__init__.py")):
            return sib, seg
    return None, None


def _complete_toolset_module(partial: str, tools_dir: str = None) -> list:
    """Completion candidates for a /tools load argument.

    Bare names complete against the tools package (only modules that are real
    tool modules). Dotted names descend into package directories: the first
    segment resolves to a physical package dir (the tools dir, a subpackage of
    it, or a sibling package next to it) and each following segment walks into
    a subpackage. Leaf modules are offered only when they are tool modules
    (they import ``@tool`` from ``tool_base``); package dirs are always offered
    with a trailing dot so Tab keeps descending into them.
    """
    if tools_dir is None:
        from tool_base.registry import _TOOLS_PACKAGE_DIR
        tools_dir = _TOOLS_PACKAGE_DIR

    if "." not in partial:
        names = get_available_toolsets(tools_dir, only_tool_modules=True)
        return [n for n in names if n.startswith(partial)]

    segments = partial.split(".")
    pkg_dir, prefix = _toolset_segment_dir(tools_dir, segments[0])
    if pkg_dir is None:
        return []
    for seg in segments[1:-1]:
        pkg_dir = os.path.join(pkg_dir, seg)
        if not (
            os.path.isdir(pkg_dir)
            and os.path.isfile(os.path.join(pkg_dir, "__init__.py"))
        ):
            return []
        prefix = f"{prefix}.{seg}"
    last = segments[-1]
    try:
        entries = sorted(os.listdir(pkg_dir))
    except OSError:
        return []
    out = []
    for name in entries:
        if not name.startswith(last):
            continue
        full = os.path.join(pkg_dir, name)
        if (
            os.path.isfile(full)
            and name.endswith(".py")
            and not name.startswith("_")
            and name != "__init__.py"
        ):
            if module_file_has_tools(full):
                out.append(f"{prefix}.{name[:-3]}")
        elif os.path.isdir(full) and os.path.isfile(
            os.path.join(full, "__init__.py")
        ):
            out.append(f"{prefix}.{name}.")
    return out


_RESUME_ANNOTATION_MARKER = "→"


def _session_completion_candidates(sessions_dir: str, partial: str) -> list:
    """Completion candidates for /resume: session titles and 8-char ids.

    Current-cwd sessions come first as plain titles; sessions from other
    projects follow, annotated with their recorded project path so the two
    groups stay visually distinct in the match list. _cmd_resume strips the
    annotation marker before matching, so selecting an annotated candidate
    still resolves.
    """
    if not sessions_dir:
        return []
    current = os.path.normpath(os.getcwd())
    current_hits = []
    others = {}
    for path, data in _list_session_files(sessions_dir):
        title = (data.get("title") or "").strip()
        sid = (data.get("session_id") or "").strip()
        parts = [title] if title else []
        if sid:
            parts.append(sid[:8])
        hits = [p for p in parts if p.lower().startswith(partial.lower())]
        if not hits:
            continue
        cwd = os.path.normpath(data.get("cwd") or "")
        if cwd == current:
            current_hits.extend(hits)
        else:
            others.setdefault(cwd, []).extend(hits)
    out = []
    seen = set()
    for cand in sorted(set(current_hits)):
        seen.add(cand)
        out.append(cand)
    for cwd in sorted(others):
        for cand in sorted(set(others[cwd])):
            annotated = f"{cand}  {_RESUME_ANNOTATION_MARKER}  {cwd}"
            if annotated not in seen and cand not in seen:
                seen.add(annotated)
                out.append(annotated)
    return out


_MODEL_LIST_TTL = 60.0


def _make_model_lister(state: ChatState) -> callable:
    """Return a model-listing callable for the completion closure.

    ``client.list()`` is a network round-trip to Ollama, so the result is
    cached for a short TTL. The cache is only marked fresh on success: a
    transient failure returns the stale list (or ``[]``) and retries on the
    next Tab.
    """
    cache = {"ts": 0.0, "loaded": False, "names": []}
    client = getattr(state, "client", None)

    def lister():
        if client is None:
            return []
        now = time.time()
        if cache["loaded"] and now - cache["ts"] < _MODEL_LIST_TTL:
            return cache["names"]
        try:
            resp = client.list()
            names = sorted(
                {
                    getattr(m, "model", None) or getattr(m, "name", None)
                    for m in getattr(resp, "models", []) or []
                }
            )
            names = [n for n in names if n]
        except Exception:
            return cache["names"]
        cache["ts"] = now
        cache["loaded"] = True
        cache["names"] = names
        return names

    return lister


def _model_completion_candidates(list_models: callable, partial: str) -> list:
    """Completion candidates for /model: installed model names, prefix-sorted."""
    if list_models is None:
        return []
    try:
        names = sorted(set(list_models()))
    except Exception:
        return []
    return [n for n in names if n.startswith(partial)]


_SKILL_FILE_EXTENSIONS = (".md", ".txt")


def _is_skill_path_candidate(candidate: str) -> bool:
    """Keep directories and ``.md``/``.txt`` skill files from path completion.

    Directories (trailing separator) are always kept so Tab keeps descending;
    files are kept only when they are skill files and not private (``_``-prefixed).
    """
    if candidate.endswith(os.sep):
        return True
    base = os.path.basename(candidate)
    return base.endswith(_SKILL_FILE_EXTENSIONS) and not base.startswith("_")


def _skill_load_completion_candidates(skills_dir: str, partial: str) -> list:
    """Completion candidates for /skill load: skills-dir names plus file paths.

    Skill files are offered by their stem (``code-reviewer.md`` →
    ``code-reviewer``) because ``_resolve_skill_path`` accepts the bare name.
    Bare names resolve against the skills directory; slash-form names are plain
    file paths resolved against the working directory, so file-path candidates
    are merged in and filtered to directories and ``.md``/``.txt`` skill files
    (``dirname/`` + Tab shows the skills inside that directory).
    """
    out = []
    if skills_dir is None:
        skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
    if os.path.isdir(skills_dir):
        for name in sorted(os.listdir(skills_dir)):
            if not name.endswith(_SKILL_FILE_EXTENSIONS) or name.startswith("_"):
                continue
            stem = os.path.splitext(name)[0]
            if stem.startswith(partial):
                out.append(stem)
    for f in _complete_file_path(partial):
        if _is_skill_path_candidate(f) and f not in out:
            out.append(f)
    return out


def _sessions_rm_completion_candidates(sessions_dir: str, partial: str) -> list:
    """Completion candidates for /sessions rm: 'all' and valid session numbers.

    Numbers follow the /sessions numbering (1 = oldest across all projects),
    matching what /sessions rm resolves against.
    """
    if not sessions_dir:
        return []
    count = len(_list_session_files(sessions_dir))
    candidates = ["all"] + [str(i) for i in range(1, count + 1)]
    return [c for c in candidates if c.startswith(partial)]


def _session_id_prefix_completion_candidates(sessions_dir: str, partial: str) -> list:
    """Completion candidates for the /rename <id-prefix> form.

    Returns 8-char session-id prefixes matching the typed prefix, mirroring the
    id lookup in _cmd_rename.
    """
    if not sessions_dir:
        return []
    out = set()
    for _path, data in _list_session_files(sessions_dir):
        sid = (data.get("session_id") or "").strip()
        if sid and sid[:8].lower().startswith(partial.lower()):
            out.add(sid[:8])
    return sorted(out)


def _completion_candidates(
    buffer: str,
    tools_dir: str = None,
    loaded_modules: list = None,
    sessions_dir: str = None,
    list_models: callable = None,
    skills_dir: str = None,
) -> list:
    """Compute Tab-completion candidates for a raw input line.

    Pure function (no readline dependency) so it can be unit-tested:
    commands on the first word, subcommands on the first argument, file
    paths for path arguments, toolset names for the /tools subcommands
    (scanned from the tools directory / the loaded-tool module names),
    saved-session titles/ids for /resume, installed model names for
    /model, skill names for /skill load, session selectors for /sessions
    rm, session id prefixes for /rename, and on/off/undo values for
    /context, /compact auto and /cut.
    """
    stripped = buffer.lstrip()
    if not stripped:
        return []
    trailing_space = stripped[-1] in " \t"
    tokens = stripped.split()
    head = tokens[0].lower()
    partial = "" if trailing_space else tokens[-1]

    if len(tokens) == 1 and not trailing_space:
        if head.startswith("/"):
            return [c for c in _COMMANDS if c.startswith(head)]
        return []

    arg_index = len(tokens) if trailing_space else len(tokens) - 1

    if arg_index == 1 and head in _SUBCOMMAND_HANDLERS:
        subs = [s for s in _SUBCOMMAND_HANDLERS[head] if s.startswith(partial)]
        if head == "/systemprompt":
            for f in _complete_file_path(partial):
                if f not in subs:
                    subs.append(f)
        return subs

    if head in _PATH_COMMANDS and arg_index == 1:
        return _complete_file_path(partial)

    if head == "/tools" and arg_index >= 2:
        sub = tokens[1].lower()
        if sub in _TOOLS_SUBCOMMANDS:
            kind = _TOOLS_SUBCOMMANDS[sub][1]
            if kind == "toolset":
                return _complete_toolset_module(partial, tools_dir)
            if kind == "loaded_toolset":
                names = []
                for m in (loaded_modules or []):
                    parts = m.split(".")
                    names.append(parts[-1] if len(parts) == 2 else m)
                out = []
                for s in names:
                    if s.startswith(partial) or s.rsplit(".", 1)[-1].startswith(partial):
                        out.append(s)
                return out

    if head == "/skill" and arg_index == 2:
        sub = tokens[1].lower()
        if sub in _SKILL_SUBCOMMANDS and _SKILL_SUBCOMMANDS[sub][1] == "path":
            return _skill_load_completion_candidates(skills_dir, partial)

    if head == "/systemprompt" and arg_index >= 2:
        return _complete_file_path(partial)

    if head == "/resume" and arg_index >= 1:
        return _session_completion_candidates(sessions_dir, partial)

    if head == "/model" and arg_index == 1:
        return _model_completion_candidates(list_models, partial)

    if head == "/sessions" and arg_index >= 2 and tokens[1].lower() == "rm":
        return _sessions_rm_completion_candidates(sessions_dir, partial)

    if head == "/rename" and arg_index == 1:
        return _session_id_prefix_completion_candidates(sessions_dir, partial)

    if head == "/context" and arg_index == 1:
        return [v for v in ("on", "off") if v.startswith(partial)]

    if head == "/compact" and arg_index >= 2 and tokens[1].lower() == "auto":
        return [v for v in ("on", "off") if v.startswith(partial)]

    if head == "/cut" and arg_index == 1:
        return [v for v in ("undo",) if v.startswith(partial)]

    return []


def _maybe_append_completion_space(buffer: str, matches: list) -> list:
    """Append a trailing space to the sole completion of a command word,
    subcommand word, or toolset-name argument so the next Tab reaches the
    next completion level.

    Pure and readline-free. Because space is a readline delimiter, GNU
    readline never appends a trailing space itself, so a completed command
    would otherwise stall on the same branch. Ambiguous matches (len != 1)
    and file-path completions are returned unchanged: appending a space to
    every match of a shared prefix would silently commit readline to the
    common prefix (e.g. ``load`` for ``load``/``loaded``).
    """
    if len(matches) != 1:
        return matches
    stripped = buffer.lstrip()
    if not stripped:
        return matches
    trailing_space = stripped[-1] in " \t"
    tokens = stripped.split()
    head = tokens[0].lower()
    arg_index = len(tokens) if trailing_space else len(tokens) - 1
    match = matches[0]

    if len(tokens) == 1 and not trailing_space and head.startswith("/"):
        return [match + " "]

    if arg_index == 1 and head in _SUBCOMMAND_HANDLERS:
        if head == "/systemprompt" and match not in _SUBCOMMAND_HANDLERS[head]:
            return matches
        return [match + " "]

    if (
        head == "/tools"
        and arg_index >= 2
        and tokens[1].lower() in _TOOLS_SUBCOMMANDS
    ):
        kind = _TOOLS_SUBCOMMANDS[tokens[1].lower()][1]
        if kind in ("toolset", "loaded_toolset"):
            if match.endswith("."):
                return matches
            return [match + " "]

    if head == "/resume" and arg_index >= 1:
        return [match + " "]

    if head == "/model" and arg_index == 1:
        return [match + " "]

    if head == "/context" and arg_index == 1:
        return [match + " "]

    if head == "/cut" and arg_index == 1:
        return [match + " "]

    if head == "/rename" and arg_index == 1:
        return [match + " "]

    if head == "/compact" and arg_index >= 2 and tokens[1].lower() == "auto":
        return [match + " "]

    if head == "/sessions" and arg_index >= 2 and tokens[1].lower() == "rm":
        return [match + " "]

    if head == "/skill" and arg_index == 2 and tokens[1].lower() == "load":
        if match.endswith("/"):
            return matches
        return [match + " "]

    return matches


def _install_readline_completion(state=None) -> None:
    """Enable Tab completion for commands, subcommands, toolsets and file paths.

    The completer is a closure over the live ChatState so ``/tools load``
    completion sees newly created tool modules and newly loaded toolsets
    without re-installing the binding.
    """
    if readline is None:
        return

    model_lister = _make_model_lister(state)

    def _complete(text: str, index: int):
        line = readline.get_line_buffer()
        matches = _completion_candidates(
            line,
            getattr(state, "tools_dir", None),
            getattr(state, "loaded_tool_modules", []),
            getattr(state, "sessions_dir", None),
            list_models=model_lister,
            skills_dir=getattr(state, "skills_dir", None),
        )
        matches = _maybe_append_completion_space(line, matches)
        return matches[index] if index < len(matches) else None

    readline.set_completer(_complete)
    readline.set_completer_delims(" \t\n")
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def _install_bracketed_paste() -> None:
    """Keep multi-line paste content in the line buffer instead of submitting on newlines.

    GNU readline 8.1+ supports bracketed paste: terminals that emit the
    bracketed-paste sequences (most modern ones) let the whole pasted block be
    inserted literally, with embedded newlines, so ``input()`` returns it as a
    single user message rather than firing on the first newline. Non-bracketed
    terminals and libedit degrade to the default behavior.
    """
    if readline is None:
        return
    if "libedit" in (readline.__doc__ or ""):
        return
    try:
        readline.parse_and_bind("set enable-bracketed-paste on")
    except Exception:
        pass


def _install_typeahead_replay(state: ChatState) -> None:
    """Replay mid-turn typed text into the next prompt's line buffer.

    Text collected by the mode hotkey listener while the model was working is
    inserted into the next readline line (once, when the hook next fires; the
    buffer is empty otherwise, so the hook is a harmless no-op between turns).
    GNU readline only -- libedit / non-tty environments skip the replay and
    degrade to the swallow behavior.
    """
    if readline is None:
        return
    if "libedit" in (readline.__doc__ or ""):
        return
    if not hasattr(readline, "set_pre_input_hook") or not hasattr(readline, "insert_text"):
        return

    def _replay():
        text = state.hotkey_drain()
        if text:
            readline.insert_text(text)

    try:
        readline.set_pre_input_hook(_replay)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Context-window usage meter
# ---------------------------------------------------------------------------

_CTX_WARN_PCT = 70
_CTX_DANGER_PCT = 90
_BAR_WIDTH = 10

_METER_COLOR_GLOBALS = {
    "low": "C_METER_LOW",
    "mid": "C_METER_MID",
    "high": "C_METER_HIGH",
}


def _ensure_ctx_max(state: ChatState) -> None:
    """Resolve the effective context window for ``state.model`` once.

    Order: explicit --num_ctx option -> LAMA_OLE_CTX_SIZE override -> the
    running model's allocated context (client.ps) -> the model's num_ctx
    parameter -> the model's declared capacity (client.show). Returns None
    when nothing is known, in which case the meter shows token counts without
    a percentage.
    """
    if state.ctx_max is not None:
        return
    state.ctx_max = _resolve_ctx_max(state)


def _resolve_ctx_max(state: ChatState):
    num_ctx = (state.options or {}).get("num_ctx")
    if num_ctx:
        return int(num_ctx)

    override = os.environ.get("LAMA_OLE_CTX_SIZE")
    if override:
        try:
            return int(override)
        except ValueError:
            pass

    try:
        resp = state.client.ps()
        for m in getattr(resp, "models", []) or []:
            name = getattr(m, "model", None) or getattr(m, "name", None)
            cl = getattr(m, "context_length", None)
            if name == state.model and cl:
                return int(cl)
    except Exception:
        pass

    try:
        r = state.client.show(model=state.model)
        params = getattr(r, "parameters", None)
        if isinstance(params, str):
            m = re.search(r"\bnum_ctx\s+(\d+)", params)
            if m:
                return int(m.group(1))
        elif isinstance(params, dict):
            nc = params.get("num_ctx")
            if nc:
                return int(nc)
        for key, val in (getattr(r, "modelinfo", None) or {}).items():
            if key.endswith(".context_length") and val:
                return int(val)
    except Exception:
        pass

    return None


def _meter_state(percent: int):
    """Color state for a usage percentage: 'low', 'mid', 'high' or None."""
    if percent is None:
        return None
    if percent >= _CTX_DANGER_PCT:
        return "high"
    if percent >= _CTX_WARN_PCT:
        return "mid"
    return "low"


def _meter_colored(text: str, percent, use_color: bool) -> str:
    if not use_color:
        return text
    state = _meter_state(percent) or "low"
    code = getattr(color_util, _METER_COLOR_GLOBALS[state])
    return color_util.colored(text, code, True)


def _ctx_usage_total(usage: dict):
    """Total context tokens (input + output of the last round), opencode-style.

    The last request's prompt_eval_count is the exact context consumed so far;
    adding eval_count projects the context that will be used once this turn's
    response is appended.
    """
    if not usage:
        return None
    used = usage.get("prompt_eval_count")
    if not used:
        return None
    return used + (usage.get("eval_count") or 0)


def _ctx_prompt_gauge(state: ChatState, use_color: bool) -> str:
    """Compact gauge for the input prompt, e.g. '[ctx 12,345/32,768 ████░░░░░░ 37%] '.

    An approximate count (restored from a session whose model differs, or
    invalidated by a mid-session /model switch) is shown with a tilde:
    '[ctx ~12,345/32,768 ████░░░░░░ ~37%] '.
    """
    total = _ctx_usage_total(state.ctx_usage)
    percent = None
    if total is None:
        label = "[ctx --]"
    else:
        _ensure_ctx_max(state)
        tilde = "~" if (state.ctx_usage or {}).get("_estimated") else ""
        if state.ctx_max:
            percent = round(total / state.ctx_max * 100)
            filled = min(percent // 10, _BAR_WIDTH)
            bar = "█" * filled + "░" * (_BAR_WIDTH - filled)
            label = f"[ctx {tilde}{total:,}/{state.ctx_max:,} {bar} {tilde}{percent}%]"
        else:
            label = f"[ctx {tilde}{total:,} tokens]"
    return _meter_colored(label, percent, use_color) + " "


# ---------------------------------------------------------------------------
# Per-turn / session statistics (/stats)
# ---------------------------------------------------------------------------


def _stats_total(state: ChatState) -> dict:
    """Aggregate session-wide stats across all models seen this session."""
    total = {
        "rounds": 0,
        "eval_count": 0,
        "eval_duration_ns": 0,
        "prompt_eval_count": 0,
        "prompt_eval_duration_ns": 0,
    }
    for m in (state.stats_by_model or {}).values():
        total["rounds"] += m.get("rounds") or 0
        total["eval_count"] += m.get("eval_count") or 0
        total["eval_duration_ns"] += m.get("eval_duration_ns") or 0
        total["prompt_eval_count"] += m.get("prompt_eval_count") or 0
        total["prompt_eval_duration_ns"] += m.get("prompt_eval_duration_ns") or 0
    return total


def _accumulate_stats(state: ChatState, metrics: dict) -> None:
    """Fold the last turn's round totals into the per-model session stats."""
    if not metrics or not metrics.get("turn_rounds"):
        return
    entry = state.stats_by_model.setdefault(
        state.model,
        {
            "rounds": 0,
            "eval_count": 0,
            "eval_duration_ns": 0,
            "prompt_eval_count": 0,
            "prompt_eval_duration_ns": 0,
        },
    )
    entry["rounds"] += metrics.get("turn_rounds") or 0
    entry["eval_count"] += metrics.get("turn_eval_count") or 0
    entry["eval_duration_ns"] += metrics.get("turn_eval_duration_ns") or 0
    entry["prompt_eval_count"] += metrics.get("turn_prompt_eval_count") or 0
    entry["prompt_eval_duration_ns"] += metrics.get("turn_prompt_eval_duration_ns") or 0


def _fmt_duration(ns) -> str:
    if not ns:
        return "n/a"
    secs = ns / 1e9
    if secs < 60:
        return f"{secs:.1f}s"
    m = int(secs // 60)
    s = int(secs % 60)
    return f"{m}m {s:02d}s"


def _fmt_speed(tok_per_s) -> str:
    if not tok_per_s or tok_per_s <= 0:
        return "n/a"
    if tok_per_s >= 1000:
        return f"{tok_per_s / 1000:.1f}K tok/s"
    return f"{tok_per_s:.0f} tok/s"


def _fmt_count(n) -> str:
    if n is None:
        return "n/a"
    return f"{int(n):,}"


def _toks_per_sec(tokens, duration_ns) -> float:
    if not tokens or not duration_ns:
        return 0.0
    return tokens / (duration_ns / 1e9)


def _delta_pct(last, avg) -> float:
    if not avg or avg <= 0 or not last:
        return 0.0
    return (last - avg) / avg * 100.0


def _fmt_delta(pct: float, use_color: bool) -> str:
    if pct > 0.5:
        return color_util.colored(
            f"   (vs avg ▲ {pct:.0f}% faster)", color_util.C_METER_LOW, use_color
        )
    if pct < -0.5:
        return color_util.colored(
            f"   (vs avg ▼ {abs(pct):.0f}% slower)", color_util.C_METER_HIGH, use_color
        )
    return color_util.colored("   (vs avg ≈)", color_util.C_METER_MID, use_color)


def _cmd_stats(state: ChatState) -> None:
    """Show the current model, last turn's per-round breakdown, and session averages."""
    use_color = color_util.color_mode_enabled(state.color)
    dim = lambda s: color_util.colored(s, color_util.C_THINK, use_color)
    cyan = lambda s: color_util.colored(s, color_util.C_INPUT, use_color)

    usage = state.ctx_usage

    model_line = f"{dim('Model:'):<14} {cyan(state.model)}"
    if usage and usage.get("prompt_eval_count"):
        _ensure_ctx_max(state)
        used = _ctx_usage_total(usage)
        if state.ctx_max and used:
            pct = round(used / state.ctx_max * 100)
            model_line += f"   (ctx {used:,} / {state.ctx_max:,} · {pct}%)"
        elif used:
            model_line += f"   (ctx {used:,} tokens)"
    print(model_line)

    if not usage:
        print("No model response yet this session.")
        return

    total = _stats_total(state)
    avg_speed = _toks_per_sec(total["eval_count"], total["eval_duration_ns"])

    rounds = usage.get("rounds") or []
    rounds_model = usage.get("rounds_model") or state.model
    if rounds:
        turn_tokens = sum(r.get("eval_count") or 0 for r in rounds)
        turn_time = sum(r.get("eval_duration_ns") or 0 for r in rounds)
        turn_speed = _toks_per_sec(turn_tokens, turn_time)
        turn_label = "1 round" if len(rounds) == 1 else f"{len(rounds)} rounds"
        model_tag = "" if rounds_model == state.model else f" ({rounds_model})"
        print()
        print(
            f"{dim('Last turn'):<10}{model_tag}: {dim(turn_label)}"
            f" · {_fmt_duration(turn_time)} · {_fmt_speed(turn_speed)}"
            f"{_fmt_delta(_delta_pct(turn_speed, avg_speed), use_color)}"
        )
        for i, r in enumerate(rounds, 1):
            speed = _toks_per_sec(r.get("eval_count"), r.get("eval_duration_ns"))
            prompt_speed = _toks_per_sec(
                r.get("prompt_eval_count"), r.get("prompt_eval_duration_ns")
            )
            print(
                f"  r{i} · {r.get('kind') or 'final answer'} · "
                f"{_fmt_count(r.get('eval_count'))} tok · {_fmt_duration(r.get('eval_duration_ns'))}"
                f" · {_fmt_speed(speed)}"
                f"   (prompt {_fmt_count(r.get('prompt_eval_count'))} tok @ {_fmt_speed(prompt_speed)})"
            )

    if total["rounds"]:
        print()
        print(
            f"{dim('Session avg:'):<12} {_fmt_speed(avg_speed)}"
            f" · {_fmt_count(total['eval_count'])} tok"
            f" · {_fmt_duration(total['eval_duration_ns'])}"
            f" · {total['rounds']} rounds"
        )
        per_model = sorted(
            state.stats_by_model.items(),
            key=lambda kv: _toks_per_sec(kv[1].get("eval_count"), kv[1].get("eval_duration_ns")),
            reverse=True,
        )
        for name, m in per_model:
            speed = _toks_per_sec(m.get("eval_count"), m.get("eval_duration_ns"))
            row = f"  {name}   {_fmt_speed(speed)} · {m.get('rounds', 0)} rounds"
            if name == state.model:
                row = color_util.colored(row, color_util.C_INPUT, use_color)
            print(row)


def _estimate_context_tokens(state: ChatState) -> int:
    """Estimate the current context tokens from message characters (chars/4).

    Falls back on this when no authoritative token count exists yet (first
    turn): it includes the just-appended user message and the composed system
    prompt if one is not present in ``state.messages``.
    """
    from tool_base import compose_system_prompt

    total_chars = 0
    has_system = False
    for m in state.messages:
        total_chars += len(m.get("content", "") or "")
        if m.get("role") == "system":
            has_system = True
    if not has_system:
        sp = compose_system_prompt(
            system_prompt=state.system_prompt,
            skill_text=state.skill_text,
            no_safety_system_prompt=state.no_safety_system_prompt,
            mode=state.mode,
        )
        total_chars += len(sp)
    return max(1, total_chars // 4)


def _warn_ctx_overflow(state: ChatState, text: str) -> None:
    """Warn on stderr when the typed message is predicted to fill the window.

    Uses the last round's exact input token count when available; otherwise
    estimates the current context (first turn) via chars/4. The new message's
    tokens are estimated as chars/4 (matching the context breakdown heuristic).
    """
    if not state.ctx_meter:
        return
    used = (state.ctx_usage or {}).get("prompt_eval_count")
    _ensure_ctx_max(state)
    if not state.ctx_max:
        return
    if used:
        predicted = used + max(1, len(text) // 4)
    else:
        predicted = _estimate_context_tokens(state)
    if predicted > state.ctx_max:
        pct = round(predicted / state.ctx_max * 100)
        print(
            f"[WARNING] Estimated context after this message: {predicted:,} / "
            f"{state.ctx_max:,} tokens ({pct}%) — exceeds the context window.",
            file=sys.stderr,
        )
    elif predicted / state.ctx_max >= _CTX_DANGER_PCT / 100:
        pct = round(predicted / state.ctx_max * 100)
        print(
            f"[WARNING] Estimated context after this message: {predicted:,} / "
            f"{state.ctx_max:,} tokens ({pct}%).",
            file=sys.stderr,
        )


def _estimate_context_breakdown(messages: list, input_tokens: int) -> list:
    """Estimate per-category context usage (system/user/assistant/tool).

    Categories are estimated via chars/4 and then scaled so their total
    matches the authoritative input token count from the last request
    (the same approach opencode uses for its context breakdown).
    Returns a list of (key, tokens, percent) tuples for non-empty categories.
    """
    counts = {"system": 0, "user": 0, "assistant": 0, "tool": 0}
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role in counts:
            counts[role] += len(content)

    est = {k: math.ceil(v / 4) for k, v in counts.items()}
    estimated = sum(est.values())
    if not estimated or not input_tokens:
        return []

    if estimated <= input_tokens:
        est["other"] = input_tokens - estimated
    else:
        scale = input_tokens / estimated
        est = {k: math.floor(v * scale) for k, v in est.items()}
        est["other"] = max(0, input_tokens - sum(est.values()))

    result = []
    for key, tokens in est.items():
        if tokens <= 0:
            continue
        percent = round(tokens / input_tokens * 100, 1)
        result.append((key, tokens, percent))
    return result


def _should_auto_compact(state: ChatState) -> bool:
    """True when the context window crossed the auto-compaction threshold.

    Requires auto-compaction to be enabled, a known window size, an
    authoritative token count from the last turn, and that count to not be an
    estimate (restored after a model change or invalidated by /model), so we
    never auto-compact on approximate numbers.
    """
    if not state.ctx_compact:
        return False
    if (state.ctx_usage or {}).get("_estimated"):
        return False
    total = _ctx_usage_total(state.ctx_usage)
    if not total:
        return False
    _ensure_ctx_max(state)
    if not state.ctx_max:
        return False
    return total >= state.ctx_compact_threshold * state.ctx_max


def run_compaction(state: ChatState, confirm: bool = True) -> bool:
    """Compact the conversation: summarize the head, keep the tail verbatim.

    The head (older turns) is serialized into labeled text and handed to the
    summarizer model (``ctx_compact_model``, defaulting to the chat model),
    which streams a structured anchored summary. On success the head is
    replaced by a ``compacted`` user message and the recent tail stays
    verbatim. Returns True when compaction ran.

    The hotkey listener is parked for the whole call (confirmation prompt and
    streaming) so typed-ahead text never races stdin.
    """
    state.hotkey_pause()
    try:
        use_color = color_util.color_mode_enabled(state.color)
        _ensure_ctx_max(state)
        system_messages = [m for m in state.messages if m.get("role") == "system"]
        non_sys = [m for m in state.messages if m.get("role") != "system"]
        head, tail = select_head_tail(
            non_sys,
            budget=default_preserve_budget(state.ctx_max),
        )
        head_text = serialize_for_compaction(head)
        if not head or not head_text.strip():
            print("No older context to compact.", file=sys.stderr)
            return False

        previous_summary = find_previous_summary(head)
        prompt = build_summary_prompt(previous_summary, head_text)
        est = estimate_tokens(COMPACTION_SYSTEM_PROMPT) + estimate_tokens(prompt)
        if state.ctx_max and est + SUMMARY_OUTPUT_TOKENS > state.ctx_max:
            print(
                f"[ERROR] Conversation too large to compact: {est:,} tokens for the "
                f"request plus {SUMMARY_OUTPUT_TOKENS:,} for the summary exceeds the "
                f"{state.ctx_max:,} token window.",
                file=sys.stderr,
            )
            return False

        if confirm:
            head_tokens = estimate_tokens(head_text)
            tail_tokens = estimate_tokens(serialize_for_compaction(tail))
            print(
                f"Compacting {len(head)} messages (~{head_tokens:,} tokens) into a "
                f"summary; keeping {len(tail)} recent messages (~{tail_tokens:,} "
                f"tokens) verbatim.",
                file=sys.stderr,
            )
            try:
                answer = input("Compact context? (y/N): ").strip().lower()
            except EOFError:
                answer = "n"
            except KeyboardInterrupt:
                print("\nCompaction cancelled.", file=sys.stderr)
                return False
            if answer != "y":
                print("Compaction cancelled.", file=sys.stderr)
                return False

        model = state.ctx_compact_model or state.model
        output_logger = (
            StateLogger(handle=state.output_file_handle)
            if state.output_file_handle
            else None
        )
        summary_parts = []
        try:
            stream = state.client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": COMPACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                stream=True,
                options=state.options,
                keep_alive=state.keep_alive,
            )
            try:
                for chunk in stream:
                    content = chunk.message.content
                    if not content:
                        continue
                    summary_parts.append(content)
                    print(
                        color_util.colored(content, color_util.C_METER_MID, use_color),
                        end="",
                        flush=True,
                    )
                    if output_logger:
                        output_logger.write_output(content)
            finally:
                if hasattr(stream, "close"):
                    stream.close()
            print()
        except KeyboardInterrupt:
            print("\nCompaction interrupted.", file=sys.stderr)
            return False
        except Exception as e:
            print(f"\nError during compaction: {e}", file=sys.stderr)
            return False

        summary = "".join(summary_parts).strip()
        if not summary:
            print("[ERROR] Summarizer produced no output.", file=sys.stderr)
            return False

        state.messages = apply_compaction(system_messages, summary, tail)
        state.ctx_usage = None
        print(f"Context compacted: {len(head)} messages summarized.", file=sys.stderr)
        autosave_session(state)
        return True
    finally:
        state.hotkey_resume()


def maybe_auto_compact(state: ChatState) -> None:
    """Run compaction automatically when the threshold was crossed this turn."""
    if not _should_auto_compact(state):
        return
    total = _ctx_usage_total(state.ctx_usage)
    print(
        f"Context usage reached {total:,} / {state.ctx_max:,} tokens "
        f"({state.ctx_compact_threshold:.0%}).",
        file=sys.stderr,
    )
    run_compaction(state, confirm=True)


def _drop_incomplete_trailing_messages(state, user_msg):
    """Remove trailing messages that belong to an incomplete tool round.

    An assistant tool-call message must be followed by one tool result per
    announced call. If the round was interrupted mid-execution (or its results
    were never appended), the partial results and the tool-call message are
    dropped so the history never contains an orphaned tool call. Completed
    rounds (tool call + tool results) are preserved. Stops at ``user_msg`` so
    the turn's user message and everything before it is never touched.
    """
    msgs = state.messages
    # Walk back past any tool results to the assistant message that announced
    # the current round.
    idx = len(msgs) - 1
    while idx >= 0 and msgs[idx] is not user_msg and msgs[idx].get("role") == "tool":
        idx -= 1
    if (
        idx >= 0
        and msgs[idx] is not user_msg
        and msgs[idx].get("role") == "assistant"
        and msgs[idx].get("tool_calls")
    ):
        n_calls = len(msgs[idx].get("tool_calls") or [])
        n_results = len(msgs) - 1 - idx
        if n_results < n_calls:
            # The round is incomplete: drop the partial results and the
            # assistant tool-call message that announced them.
            del msgs[idx:]


def _rollback_interrupted_turn(state, e, user_msg, messages_before):
    """Report an interrupt and roll back the incomplete part of the turn.

    Keeps ``user_msg`` — the user message appended this turn — together with
    everything that came before it (including the system prompt that
    ``run_with_tools`` prepends when none existed yet) and every *completed*
    tool round (assistant tool call + tool results). Only the incomplete part
    is dropped: an interrupted model response was never written to the history,
    and a tool round whose results were not all appended is removed so no
    orphaned tool call stays visible in ``/history``.

    If no user message was added this turn, everything appended since
    ``messages_before`` is removed instead.
    """
    if isinstance(e, ExecutionInterrupted) and e.state not in (None, ExecutionState.IDLE):
        print(
            f"\nInterrupted during {e.state.name.lower()}. Returning to prompt.",
            file=sys.stderr,
        )
    else:
        print("\nInterrupted.")
    state.state_manager.reset()
    if user_msg is None:
        while len(state.messages) > messages_before:
            state.messages.pop()
        return
    _drop_incomplete_trailing_messages(state, user_msg)


def run_chat(state: ChatState):
    print("Chat mode. Type /help for commands.")
    _install_readline_completion(state)
    _install_bracketed_paste()
    _bind_mode_toggle(state)
    _install_typeahead_replay(state)
    use_color = color_util.color_mode_enabled(state.color)
    if state.ctx_meter:
        _ensure_ctx_max(state)
    base_prompt = color_util.colored(">>> ", color_util.C_PROMPT, use_color)
    if use_color:
        base_prompt += color_util.C_INPUT

    while True:
        prompt = _mode_label(state, use_color)
        if state.ctx_meter:
            prompt += _ctx_prompt_gauge(state, use_color) + base_prompt
        else:
            prompt += base_prompt
        # Snapshot before reading input so an interrupt either at the prompt or
        # during the turn only rolls back what this iteration added.
        messages_before = len(state.messages)
        user_msg = None
        try:
            try:
                line = input(prompt)
            finally:
                if use_color:
                    sys.stdout.write(color_util.C_RESET)
                    sys.stdout.flush()

            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("/"):
                if _handle_command(stripped, state):
                    autosave_session(state)
                    break
                continue

            user_msg = {"role": "user", "content": stripped}
            state.stamp_message(user_msg)
            state.messages.append(user_msg)
            state.log_ndjson(user_msg)
            _warn_ctx_overflow(state, stripped)

            if state.chatinput_file_handle:
                from tool_base import _write_input
                _write_input(state.chatinput_file_handle, f"[chat input] {stripped}\n")
                state.chatinput_file_handle.flush()

            metrics = {}
            state.start_hotkey_listener()
            try:
                run_with_tools(
                    client=state.client,
                    model=state.model,
                    messages=state.messages,
                    loaded_tools=state.loaded_tools,
                    ollama_tools=state.ollama_tools,
                    options=state.options,
                    keep_alive=state.keep_alive,
                    show_thinking=state.show_thinking,
                    no_safety_system_prompt=state.no_safety_system_prompt,
                    system_prompt = state.system_prompt,
                    skill_text = state.skill_text,
                    verbose=state.verbose,
                    safe=state.safe,
                    thought_file_handle=state.thought_file_handle,
                    output_file_handle=state.output_file_handle,
                    toolcall_file_handle=state.toolcall_file_handle,
                    chatinput_file_handle=state.chatinput_file_handle,
                    max_tool_rounds=state.max_tool_rounds,
                    max_tool_rounds_continuation=state.max_tool_rounds_continuation,
                    ollama_websearch=state.ollama_websearch,
                    color=state.color,
                    ndjson_log_file_handle=state.ndjson_log_file_handle,
                    state_manager=state.state_manager,
                    metrics=metrics,
                    mode_state=state,
                    show_diff=state.show_diff,
                )
            finally:
                state.stop_hotkey_listener()
            stamp_turn_messages(state, messages_before)
            state.ctx_usage = metrics
            state.ctx_usage_model = state.model
            _accumulate_stats(state, metrics)
            autosave_session(state)
            maybe_auto_compact(state)
        except EOFError:
            print()
            autosave_session(state)
            break
        except KeyboardInterrupt as e:
            # A second Ctrl-C while we clean up must not kill the REPL.
            try:
                _rollback_interrupted_turn(state, e, user_msg, messages_before)
            except KeyboardInterrupt:
                pass

        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            state.state_manager.reset()
            # Keep the user message (and any system prompt prepended for this
            # turn) together with all completed tool rounds; only drop a
            # trailing tool call that never got its result.
            if user_msg is not None:
                _drop_incomplete_trailing_messages(state, user_msg)
            else:
                while len(state.messages) > messages_before:
                    state.messages.pop()


def _handle_command(line: str, state: ChatState) -> bool:
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    handler = _COMMAND_HANDLERS.get(cmd)
    if handler is None:
        print(f"Unknown command: {cmd}. Type /help for available commands.")
        return False
    return bool(handler(arg, state))


def _cmd_new(arg: str, state: ChatState):
    if _has_conversation(state):
        autosave_session(state)
    state.messages.clear()
    state.ctx_usage = None
    state.session_id = new_session_id()
    state.session_created_at = time.time()
    state.stats_by_model.clear()
    print("New session started. Previous session preserved; use /resume to restore it.")


def _cmd_model(arg: str, state: ChatState):
    if not arg:
        print(f"Current model: {state.model}")
    else:
        canonicalize = getattr(state.client, "canonicalize", None)
        model = canonicalize(arg) if callable(canonicalize) else arg
        state.model = model
        state.loaded_model = model
        if state.ctx_usage and state.ctx_usage_model and state.ctx_usage_model != model:
            state.ctx_usage = dict(state.ctx_usage)
            state.ctx_usage["_estimated"] = True
            state.ctx_usage_model = model
        autosave_session(state)
        print(f"Switched to model: {model}")


def _cmd_compact(arg: str, state: ChatState):
    sub = arg.strip().lower()
    if not sub:
        run_compaction(state, confirm=True)
    elif sub == "auto" or sub.startswith("auto "):
        _cmd_compact_auto(sub.split(maxsplit=1)[1] if sub != "auto" else "", state)
    else:
        print("Usage: /compact [auto on|off]")
        print("  /compact          Compact the context now (ask for confirmation)")
        print("  /compact auto     Show whether auto-compaction is enabled")
        print("  /compact auto on  Enable auto-compaction on threshold crossing")
        print("  /compact auto off Disable auto-compaction")


def _show_help():
    print()
    print("Commands:")
    print("  /feed <path>    Read a file and inject its content as a message")
    print("  /new            Start a new session (previous session is preserved)")
    print("  /compact [auto on|off]  Compact now, or toggle/show auto-compaction")
    print("  /model <name>   Switch to a different model")
    print("  /plan           Switch to plan mode (write tools blocked until /build)")
    print("  /build          Switch to build mode (full tools, changes allowed)")
    print("  /save <path>    Save the conversation to a JSON file")
    print("  /load <path>    Load a conversation from a JSON file")
    print("  /resume [id|title]  Resume a saved session (with no arg: picker; with a match: direct)")
    print("  /sessions [all]  List saved sessions in this directory (1 = oldest, newest shown first);")
    print("                  'all' includes every project; /sessions rm deletes (N | -N | a..b | all)")
    print("  /stats          Show model, last turn's per-round breakdown, and session averages")
    print("  /rename <new title>  Rename the current session")
    print("  /rename <id-prefix> <new title>  Rename a stored session")
    print("  /tools loaded                    List loaded toolsets and their tools")
    print("  /tools available                 List toolsets available to load")
    print("  /tools show <toolset>            List all tools of one toolset")
    print("  /tools all                       List all tools of all toolsets")
    print("  /tools load <toolset> [<toolset> ...]   Load one or more toolsets")
    print("  /tools unload <toolset> [<toolset> ...] Unload one or more toolsets")
    print("  /skill list     List available skills")
    print("  /skill load <name-or-path> [<name-or-path> ...]  Load skill(s) into the system role")
    print("  /skill unload   Unload the active skill")
    print("  /skill show     Show the active skill")
    print("  /systemprompt [show]  Show the current system prompt")
    print("  /systemprompt <file>  Load a system prompt from a file")
    print("  /systemprompt unset   Unset the system prompt")
    print("  /context [on|off]  Show context usage stats, or toggle the context meter")
    print("  /history [-t] [N] [-N] [a..b ...]   Show history entries (1 = oldest, -N = last N)")
    print("  /cut N | /cut -N | /cut a..b | /cut undo   Trim history to a selection (keeps what you name)")
    print("  /help           Show this help message")
    print("  /exit, /quit    Exit the chat")
    print()


def _cmd_context(arg: str, state: ChatState):
    arg = arg.strip().lower()
    if arg in ("on", "off"):
        state.ctx_meter = arg == "on"
        print(f"Context meter {'enabled' if state.ctx_meter else 'disabled'}.")
        return
    if arg:
        print("Usage: /context [on|off]")
        return
    _ensure_ctx_max(state)
    total_chars = sum(len(m.get("content", "") or "") for m in state.messages)
    print(f"Messages: {len(state.messages)}, total characters: {total_chars}")
    used = (state.ctx_usage or {}).get("prompt_eval_count")
    if not used:
        print("Context usage: no model response yet this session.")
        return
    eval_count = state.ctx_usage.get("eval_count") or 0
    total = used + eval_count
    note = " (estimate)" if (state.ctx_usage or {}).get("_estimated") else ""
    if state.ctx_max:
        pct = round(total / state.ctx_max * 100)
        print(f"Context usage: {total:,} / {state.ctx_max:,} tokens ({pct}%){note}")
    else:
        print(f"Context usage: {total:,} tokens (window size unknown){note}")
    breakdown = _estimate_context_breakdown(state.messages, used)
    for key, tokens, percent in breakdown:
        print(f"  {key}: {tokens:,} tokens ({percent}%)")
    if state.ctx_compact and state.ctx_max:
        print(
            f"Auto-compaction: enabled (threshold {state.ctx_compact_threshold:.0%}, "
            f"model {state.ctx_compact_model or state.model})"
        )


def _cmd_compact_auto(arg: str, state: ChatState):
    arg = arg.strip().lower()
    if arg == "on":
        state.ctx_compact = True
        print("Auto-compaction enabled.")
    elif arg == "off":
        state.ctx_compact = False
        print("Auto-compaction disabled.")
    elif not arg:
        print(f"Auto-compaction: {'enabled' if state.ctx_compact else 'disabled'} "
              f"(threshold {state.ctx_compact_threshold:.0%}, "
              f"model {state.ctx_compact_model or state.model})")
    else:
        print("Usage: /compact auto [on|off]")


def _cmd_feed(path: str, state: ChatState):
    if not path:
        print("Usage: /feed <path>")
        return
    if not os.path.exists(path):
        print(f"Error: file not found: {path}")
        return
    try:
        with open(path, "rb") as f:
            raw_content = f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    # Entropy check: reject binary / random content before it enters the conversation
    from security.entropychecker import EntropyChecker

    checker = EntropyChecker()
    result = checker.feed(raw_content)
    if result.is_suspicious:
        print(f"Error: {path} rejected by entropy check: {result.reason}")
        return

    content = raw_content.decode("utf-8", errors="replace")
    user_msg = {"role": "user", "content": content}
    state.stamp_message(user_msg)
    messages_before = len(state.messages)
    state.messages.append(user_msg)
    state.log_ndjson(user_msg)
    print(f"Loaded {len(content)} characters from {path}")
    try:
        metrics = {}
        run_with_tools(
            client=state.client,
            model=state.model,
            messages=state.messages,
            loaded_tools=state.loaded_tools,
            ollama_tools=state.ollama_tools,
            options=state.options,
            keep_alive=state.keep_alive,
            show_thinking=state.show_thinking,
            no_safety_system_prompt=state.no_safety_system_prompt,
            system_prompt = state.system_prompt,
            skill_text = state.skill_text,
            verbose=state.verbose,
            safe=state.safe,
            thought_file_handle=state.thought_file_handle,
            output_file_handle=state.output_file_handle,
            toolcall_file_handle=state.toolcall_file_handle,
            chatinput_file_handle=state.chatinput_file_handle,
            max_tool_rounds=state.max_tool_rounds,
            max_tool_rounds_continuation=state.max_tool_rounds_continuation,
            ollama_websearch=state.ollama_websearch,
            color=state.color,
            ndjson_log_file_handle=state.ndjson_log_file_handle,
            state_manager=state.state_manager,
            metrics=metrics,
            show_diff=state.show_diff,
        )
        state.ctx_usage = metrics
        state.ctx_usage_model = state.model
        _accumulate_stats(state, metrics)
        autosave_session(state)
        maybe_auto_compact(state)
    except KeyboardInterrupt as e:
        try:
            _rollback_interrupted_turn(state, e, user_msg, messages_before)
        except KeyboardInterrupt:
            pass
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        state.state_manager.reset()
        _drop_incomplete_trailing_messages(state, user_msg)


def _toolcall_summary(msg):
    """Human-readable summary of the tool calls in an assistant message."""
    return history_mod.toolcall_summary(msg)


def _cmd_history(arg: str, state: ChatState):
    entries = state.get_history_entries()
    if not entries:
        print("No history available.")
        return

    # -t forces tool results on even when the template hides them.
    include_tool_results = False
    tokens = []
    for token in arg.split():
        if token == "-t":
            include_tool_results = True
        else:
            tokens.append(token)

    selectors = history_mod.parse_selectors(" ".join(tokens))
    formats = history_mod.parse_line_formats("history")
    if include_tool_results:
        formats = history_mod.with_tool_results(formats)
    use_color = color_util.color_mode_enabled(state.color)

    if not selectors:
        # /history -> show all entries that have a template, oldest first.
        for e in entries:
            line = history_mod.format_list_entry(e, use_color, formats=formats)
            if line:
                print(line)
        return

    numbers = history_mod.select_entry_numbers(entries, selectors)
    if not numbers:
        print("No history matches the criteria.")
        return

    found_any = False
    for e in entries:
        if e["num"] in numbers:
            line = history_mod.format_list_entry(e, use_color, formats=formats)
            if line:
                print(line)
                found_any = True
    if not found_any:
        print("No history matches the criteria.")


def stamp_turn_messages(state, start):
    """Stamp every message a turn appended (assistant / tool / thinking).

    Only the user's message is stamped at input time (see :meth:`stamp_message`),
    so /history would otherwise render ``{ts}`` for user entries alone. This
    pass runs after the turn completes so all new entries carry a timestamp,
    matching session replay where restored messages are stamped on load.
    """
    for m in state.messages[start:]:
        state.stamp_message(m)


def _cmd_cut(arg: str, state: ChatState):
    """Trim the conversation history down to a selection (/cut N, /cut -N, /cut a..b, /cut undo).

    ``/cut`` keeps exactly the entries it names and removes the rest: entries
    are numbered 1 (oldest) .. V (newest), so ``/cut N`` keeps entries N..V
    (dropping the older 1..N-1), ``/cut -N`` keeps the last N, and
    ``/cut a..b`` keeps only that inclusive range (negative bounds count from
    the end). ``/cut 1`` keeps everything and is a no-op. System messages are
    never removed. The result is persisted immediately so the on-disk session
    stays in sync with the live history.
    """
    arg = arg.strip()
    if not arg:
        print("Usage: /cut N | /cut -N | /cut a..b | /cut undo")
        return

    if arg == "undo":
        if state.undo_cut():
            print("Cut undone.")
            autosave_session(state)
        else:
            print("Nothing to undo.")
        return

    entries = state.get_history_entries()
    if not entries:
        print("No history to cut.")
        return

    selectors = history_mod.parse_cut_selectors(arg)
    if not selectors:
        print(f"Invalid cut argument: {arg}")
        return

    # System messages are never removed (keeps the conversation valid).
    kept = history_mod.select_message_indices(entries, selectors)
    if not kept:
        print("Nothing to cut.")
        return

    removed = [
        i for i, msg in enumerate(state.messages)
        if msg.get("role") != "system" and i not in set(kept)
    ]
    if not removed:
        print("Nothing to cut.")
        return

    state._last_cut_messages = [state.messages[i] for i in removed]
    state._last_cut_indices = removed

    for i in reversed(removed):
        del state.messages[i]

    print(f"Cut {len(removed)} message(s), kept {len(kept)}.")
    autosave_session(state)


def serialize_session(
    state: ChatState,
    session_id: str = None,
    cwd: str = None,
    created_at: float = None,
) -> dict:
    """Serialize the resumable conversation state into a JSON-able dict.

    Shared by ``/save`` (explicit snapshot) and session auto-save. Only
    optional fields that are actually set are included so old files keep
    loading and the output stays small.
    """
    data = {
        "model": state.model,
        "messages": state.messages,
        "updated_at": time.time(),
    }
    title = state.session_title or _session_title(state)
    if title:
        data["title"] = title
    if state.mode != "build":
        data["mode"] = state.mode
    if session_id:
        data["session_id"] = session_id
    if cwd:
        data["cwd"] = cwd
    if created_at:
        data["created_at"] = created_at
    if state.skill is not None or state.skill_text is not None:
        data["skill"] = state.skill
        data["skill_text"] = state.skill_text
    if state.system_prompt is not None:
        data["system_prompt"] = state.system_prompt
    if state.loaded_tool_modules:
        data["loaded_tool_modules"] = list(state.loaded_tool_modules)
    if state.ctx_compact:
        data["ctx_compact"] = True
        data["ctx_compact_threshold"] = state.ctx_compact_threshold
    if state.ctx_compact_model is not None:
        data["ctx_compact_model"] = state.ctx_compact_model
    if state.ctx_usage and not state.ctx_usage.get("_estimated"):
        if state.ctx_usage.get("prompt_eval_count"):
            data["ctx_usage"] = {
                "prompt_eval_count": state.ctx_usage["prompt_eval_count"],
                "eval_count": state.ctx_usage.get("eval_count") or 0,
            }
            data["ctx_usage_model"] = state.ctx_usage_model or state.model
    if state.stats_by_model:
        stats = {
            "by_model": state.stats_by_model,
            "model": (state.ctx_usage or {}).get("rounds_model") or state.model,
        }
        rounds = (state.ctx_usage or {}).get("rounds")
        if rounds:
            stats["rounds"] = rounds
        data["stats"] = stats
    return data


def apply_session(state: ChatState, data: dict, source: str = "session") -> None:
    """Restore conversation state from a serialized session dict.

    Shared by ``/load`` and session resume. Unknown or absent keys are
    ignored so old-format files and forward compatibility both work.

    Restored messages that lack a timestamp are stamped so /history renders
    uniformly after a load/resume (and the next autosave persists the stamps).
    """
    state.messages = data.get("messages", [])
    for m in state.messages:
        if isinstance(m, dict) and "timestamp" not in m:
            m["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if data.get("session_id"):
        state.session_id = data["session_id"]
    if data.get("created_at"):
        state.session_created_at = data["created_at"]
    if data.get("title"):
        state.session_title = data["title"]
    if "model" in data:
        state.model = data["model"]
    stored_usage = data.get("ctx_usage")
    stored_usage_model = data.get("ctx_usage_model")
    if stored_usage and stored_usage.get("prompt_eval_count"):
        if stored_usage_model and stored_usage_model == state.model:
            state.ctx_usage = dict(stored_usage)
            state.ctx_usage_model = stored_usage_model
        else:
            state.ctx_usage = dict(stored_usage)
            state.ctx_usage["_estimated"] = True
            state.ctx_usage_model = state.model
    else:
        state.ctx_usage = None
        state.ctx_usage_model = None
    stored_stats = data.get("stats")
    if stored_stats and isinstance(stored_stats, dict):
        by_model = stored_stats.get("by_model")
        if isinstance(by_model, dict):
            state.stats_by_model = dict(by_model)
        else:
            state.stats_by_model = {}
        if state.ctx_usage is not None and stored_stats.get("model") == state.model:
            rounds = stored_stats.get("rounds")
            if isinstance(rounds, list) and rounds:
                state.ctx_usage["rounds"] = rounds
                state.ctx_usage["rounds_model"] = stored_stats.get("model") or state.model
    else:
        state.stats_by_model = {}
    # Mode must be restored before tool reload so refresh_ollama_tools()
    # runs with the resumed mode in effect (tools are advertised in full in
    # both modes; write tools are gated at execution time).
    if "mode" in data and data.get("mode") in _MODES:
        state.mode = data["mode"]
    if "loaded_tool_modules" in data:
        module_names = data["loaded_tool_modules"]
        state.loaded_tools = []
        state.loaded_tool_modules = []
        for module_name in module_names:
            try:
                tools = load_tools(module_name)
                state.loaded_tools.extend(tools)
                state.loaded_tool_modules.append(module_name)
            except Exception as e:
                print(f"Warning: could not reload toolset '{module_name}': {e}")
        state.refresh_ollama_tools()
    if "skill" in data or "skill_text" in data:
        state.skill = data.get("skill")
        state.skill_text = data.get("skill_text")
    if "system_prompt" in data:
        state.system_prompt = data["system_prompt"]
    if "ctx_compact" in data:
        state.ctx_compact = bool(data["ctx_compact"])
    if "ctx_compact_threshold" in data:
        state.ctx_compact_threshold = sanitize_ctx_threshold(
            data["ctx_compact_threshold"], state.ctx_compact_threshold
        )
    if "ctx_compact_model" in data:
        state.ctx_compact_model = data["ctx_compact_model"]
    _bind_mode_toggle(state)


def _cmd_save(path: str, state: ChatState):
    if not path:
        print("Usage: /save <path>")
        return
    data = serialize_session(state)
    if os.path.exists(path):
        confirm = input(f"File '{path}' already exists. Overwrite? (y/n): ").lower()
        if confirm != 'y':
            print("Save aborted.")
            return

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Saved conversation with {len(state.messages)} messages to {path}")
    except Exception as e:
        print(f"Error saving conversation: {e}")


def _cmd_load(path: str, state: ChatState):
    if not path:
        print("Usage: /load <path>")
        return
    if not os.path.exists(path):
        print(f"Error: file not found: {path}")
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading conversation: {e}")
        return
    if _has_conversation(state) and autosave_session(state):
        print("Previous conversation archived; use /resume to restore it.")
    apply_session(state, data, source=path)
    print(f"Loaded conversation with {len(state.messages)} messages")
    _replay_history(state, color_util.color_mode_enabled(state.color))


# ---------------------------------------------------------------------------
# Session auto-save / resume
# ---------------------------------------------------------------------------


def _session_title(state: ChatState, limit: int = 60) -> str:
    """Derive a short human title from the first user message."""
    for m in state.messages:
        if m.get("role") == "user":
            text = (m.get("content") or "").strip().replace("\n", " ")
            if text:
                return text[:limit]
    return ""


def _has_conversation(state: ChatState) -> bool:
    return any(m.get("role") != "system" for m in state.messages)


def _encode_cwd(path: str) -> str:
    """Encode an absolute path into a filesystem-safe, collision-free slug.

    Every run of non-alphanumeric characters becomes a '-', so
    /home/nx/proj -> home-nx-proj, then a short SHA-1 digest of the real
    path is appended. The digest guarantees distinct project paths (e.g.
    lama_ole vs lama-ole, or unicode names) never share a directory. The
    real path is always stored inside the session file as ground truth.
    """
    abs_path = os.path.abspath(path)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", abs_path).strip("-")
    digest = hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def session_dir_for(cwd: str, sessions_dir: str) -> str:
    return os.path.join(sessions_dir, _encode_cwd(cwd))


def new_session_id() -> str:
    return uuid.uuid4().hex


def _write_session_file(path: str, data: dict) -> None:
    """Atomically write a session file with 0600 permissions."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def autosave_session(state: ChatState) -> bool:
    """Persist the current session to disk (best-effort).

    No-op unless sessions are configured, a session id exists, auto-save is
    enabled, and the conversation has at least one non-system message.
    Returns True when a session file was actually written.
    """
    if not state.sessions_dir or not state.session_id:
        return False
    if not state.session_autosave:
        return False
    if not _has_conversation(state):
        return False
    dirpath = session_dir_for(os.getcwd(), state.sessions_dir)
    try:
        os.makedirs(dirpath, exist_ok=True)
    except OSError as e:
        print(f"Error creating sessions directory: {e}", file=sys.stderr)
        return False
    data = serialize_session(
        state,
        session_id=state.session_id,
        cwd=os.getcwd(),
        created_at=state.session_created_at or time.time(),
    )
    path = os.path.join(dirpath, state.session_id + ".json")
    try:
        _write_session_file(path, data)
    except OSError as e:
        print(f"Error saving session: {e}", file=sys.stderr)
        return False
    return True


def find_recent_session(sessions_dir: str, cwd: str):
    """Return (path, data) for the newest session recorded in ``cwd``.

    Returns None when the directory has no session files. The recorded cwd
    (ground truth inside the file) must match the given directory, so a
    renamed project simply does not match and starts fresh.
    """
    base = session_dir_for(cwd, sessions_dir)
    if not os.path.isdir(base):
        return None
    norm = os.path.normpath(cwd)
    best = None
    for name in os.listdir(base):
        if not name.endswith(".json"):
            continue
        path = os.path.join(base, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if os.path.normpath(data.get("cwd") or "") != norm:
            continue
        updated = data.get("updated_at") or data.get("created_at") or 0
        if best is None or updated > (best[1].get("updated_at") or best[1].get("created_at") or 0):
            best = (path, data)
    return best


def _list_session_files(sessions_dir: str) -> list:
    """Return [(path, data)] for every session file across all projects."""
    results = []
    if not os.path.isdir(sessions_dir):
        return results
    for entry in sorted(os.listdir(sessions_dir)):
        full = os.path.join(sessions_dir, entry)
        if not os.path.isdir(full):
            continue
        for name in sorted(os.listdir(full)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(full, name)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            results.append((path, data))
    return results


def _session_age(data: dict) -> float:
    """Recency timestamp for a session dict (updated_at preferred)."""
    return data.get("updated_at") or data.get("created_at") or 0


def _collect_session_groups(state: ChatState, all_projects: bool = False) -> list:
    """Return [(label, [(num, path, data), ...]), ...] for the sessions listing.

    Sessions are numbered 1 (oldest) .. V (newest) within each group, mirroring
    /history. The current-cwd group comes first so its numbers are identical in
    the bare and the 'all' listing; other projects follow, grouped by their
    recorded cwd. Callers display each group newest-first (numbers travel with
    their entries).
    """
    current = os.path.normpath(os.getcwd())
    sessions = _list_session_files(state.sessions_dir)
    cur = [
        pd for pd in sessions
        if os.path.normpath(pd[1].get("cwd") or "") == current
    ]
    groups = [("current", cur)]
    if all_projects:
        others = [
            pd for pd in sessions
            if os.path.normpath(pd[1].get("cwd") or "") != current
        ]
        by_cwd = {}
        for pd in others:
            key = os.path.normpath(pd[1].get("cwd") or "?")
            by_cwd.setdefault(key, []).append(pd)
        for cwd in sorted(by_cwd):
            groups.append((cwd, by_cwd[cwd]))
    num = 0
    result = []
    for label, items in groups:
        numbered = []
        for path, data in sorted(items, key=lambda pd: _session_age(pd[1])):
            num += 1
            numbered.append((num, path, data))
        result.append((label, numbered))
    return result


def _print_session(num: int, data: dict, current_sid: str = None) -> None:
    sid = (data.get("session_id") or "?")[:8]
    title = data.get("title") or "(untitled)"
    model = data.get("model") or "?"
    updated = _session_age(data)
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
    n = sum(1 for m in data.get("messages", []) if m.get("role") != "system")
    marker = "*" if current_sid and data.get("session_id") == current_sid else " "
    print(f"{marker} {num}. [{ts}] {title}  ({model}, {n} msgs, {sid})")


def _display_session_groups(groups: list, current_sid: str = None) -> None:
    """Print session groups newest-first, with project headers."""
    first_other = True
    for label, items in groups:
        if not items:
            continue
        if label == "current":
            print(f"Sessions in {os.getcwd()} (newest first):")
        else:
            if first_other:
                print()
                print("Other projects:")
                first_other = False
            print(f"  {label}:")
        for num, path, data in reversed(items):
            _print_session(num, data, current_sid)


def _print_sessions_footer(groups: list, state: ChatState) -> None:
    """Print the count summary and the active session's file path."""
    total = sum(len(items) for _label, items in groups)
    if not total:
        return
    print()
    print(f"{total} session(s) stored in {state.sessions_dir}")
    if state.session_id:
        for _label, items in groups:
            for _num, path, data in items:
                if data.get("session_id") == state.session_id:
                    print("current session file: " + os.path.relpath(path, state.sessions_dir))
                    return


def _replay_history(state: ChatState, use_color: bool) -> None:
    """Replay a resumed session as a numbered listing, identical to /history.

    Rendering delegates to the shared history model (``format_list_entry``)
    with the replay view's line templates (``LAMA_OLE_FORMAT_REPLAY`` on top
    of ``LAMA_OLE_FORMAT``), so a resumed session matches /history unless
    configured independently. Stored edit diffs are printed after tool
    entries when ``show_diff`` is on, mirroring live edit display.
    """
    formats = history_mod.parse_line_formats("replay")
    for entry in state.get_history_entries():
        text = history_mod.format_list_entry(entry, use_color, formats=formats)
        if text:
            print(text)
            # Stored edit diffs belong to the tool entry; keep them hidden
            # when the entry itself is hidden by a template.
            msg = entry["msg"]
            if msg.get("role") == "tool" and state.show_diff and msg.get("diff"):
                _print_diff_block(
                    msg.get("file") or msg.get("tool_name") or "?",
                    msg.get("diff") or "",
                    use_color,
                )


def _resume_into_state(state: ChatState, path: str, data: dict) -> None:
    """Load a session into the REPL, re-associating it if it moved."""
    old_cwd = data.get("cwd")
    apply_session(state, data, source=path)
    if not state.session_id:
        state.session_id = new_session_id()
        state.session_created_at = time.time()
    if old_cwd and os.path.normpath(old_cwd) != os.path.normpath(os.getcwd()):
        data["cwd"] = os.getcwd()
        new_path = os.path.join(
            session_dir_for(os.getcwd(), state.sessions_dir),
            state.session_id + ".json",
        )
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            _write_session_file(new_path, data)
            if os.path.abspath(path) != os.path.abspath(new_path):
                os.remove(path)
        except OSError as e:
            print(f"Error re-associating session: {e}", file=sys.stderr)
        print(f"Re-associated session from {old_cwd} to {os.getcwd()}.")
    title = data.get("title") or "(untitled)"
    print(f"Resumed session: {title} ({len(state.messages)} messages)")
    _replay_history(state, color_util.color_mode_enabled(state.color))


def _cmd_resume(arg: str, state: ChatState):
    if not state.sessions_dir:
        print("Sessions directory is not configured.")
        return

    if arg:
        needle = arg.strip().lower()
        if _RESUME_ANNOTATION_MARKER in needle:
            needle = needle.split(_RESUME_ANNOTATION_MARKER)[0].strip()
        sessions = _list_session_files(state.sessions_dir)
        cands = [
            pd for pd in sessions
            if needle in (pd[1].get("session_id") or "").lower()
            or needle in (pd[1].get("title") or "").lower()
        ]
        if not cands:
            print(f"No session matching '{arg}'.")
            return
        cands.sort(key=lambda pd: pd[1].get("updated_at") or 0, reverse=True)
        _resume_into_state(state, cands[0][0], cands[0][1])
        return

    groups = _collect_session_groups(state, all_projects=True)
    ordered = []
    for _label, items in groups:
        ordered.extend(items)
    if not ordered:
        print("No saved sessions to resume.")
        return
    _display_session_groups(groups, current_sid=state.session_id)
    choice = input(f"Select session (1-{len(ordered)}) or Enter to cancel: ").strip()
    if not choice:
        print("Cancelled.")
        return
    if not choice.isdigit():
        print("Invalid selection.")
        return
    n = int(choice)
    if not (1 <= n <= len(ordered)):
        print("Invalid selection.")
        return
    _resume_into_state(state, ordered[n - 1][1], ordered[n - 1][2])


def _cmd_sessions(arg: str, state: ChatState):
    if not state.sessions_dir:
        print("Sessions directory is not configured.")
        return
    arg = arg.strip().lower()
    if arg == "rm" or arg.startswith("rm "):
        _cmd_sessions_rm(arg[2:].strip(), state)
        return
    if arg and arg not in ("all", "-a", "--all"):
        print("Usage: /sessions [all]  |  /sessions rm <N | -N | a..b | all>")
        return
    groups = _collect_session_groups(state, all_projects=bool(arg))
    if not any(items for _label, items in groups):
        if arg:
            print(f"No saved sessions in {state.sessions_dir}.")
            return
        total = len(_list_session_files(state.sessions_dir))
        if total == 0:
            print(f"No saved sessions in {state.sessions_dir}.")
        else:
            print(
                f"No sessions saved in {os.getcwd()}. "
                f"{total} session(s) found in other projects - "
                "use /sessions all to list them."
            )
        return
    _display_session_groups(groups, current_sid=state.session_id)
    _print_sessions_footer(groups, state)


def _parse_rm_selectors(arg: str) -> list:
    """Parse /sessions rm selectors into (start, end) inclusive spans.

    Mirrors the /cut grammar: ``N`` (exactly session N), ``-N`` (the N most
    recent sessions), ``a..b`` (inclusive range; negative bounds count from the
    newest), any space-separated combination. Invalid tokens are skipped.
    """
    ranges = []
    for token in arg.split():
        if ".." in token:
            parts = token.split("..")
            if len(parts) != 2:
                continue
            try:
                a, b = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if a == 0 or b == 0:
                continue
            ranges.append((a, b))
        else:
            try:
                val = int(token)
            except ValueError:
                continue
            if val == 0:
                continue
            if val < 0:
                ranges.append((-abs(val), -1))
            else:
                ranges.append((val, val))
    return ranges


def _resolve_rm_selectors(selectors: list, count: int) -> list:
    """Resolve rm selector spans against the session count (1 = oldest).

    Negative bounds count from the newest session (-1 = newest). Returns the
    sorted, de-duplicated, in-range session numbers to delete.
    """
    chosen = set()
    for a, b in selectors:
        if a < 0:
            a = count + a + 1
        if b < 0:
            b = count + b + 1
        lo, hi = sorted((a, b))
        for n in range(lo, hi + 1):
            if 1 <= n <= count:
                chosen.add(n)
    return sorted(chosen)


def _cmd_sessions_rm(arg: str, state: ChatState):
    if not state.sessions_dir:
        print("Sessions directory is not configured.")
        return
    groups = _collect_session_groups(state, all_projects=True)
    ordered = []
    for _label, items in groups:
        ordered.extend(items)
    if not ordered:
        print(f"No saved sessions in {state.sessions_dir}.")
        return
    if not arg:
        _display_session_groups(groups, current_sid=state.session_id)
        print()
        choice = input(
            "Delete which session(s)? (e.g. 2, 4..6, all, or Enter to cancel): "
        ).strip()
        if not choice:
            print("Cancelled.")
            return
        arg = choice
    if arg.strip().lower() == "all":
        chosen = list(range(1, len(ordered) + 1))
    else:
        selectors = _parse_rm_selectors(arg)
        if not selectors:
            print("Usage: /sessions rm <N | -N | a..b | all>  (space-separated selectors allowed)")
            return
        chosen = _resolve_rm_selectors(selectors, len(ordered))
    if not chosen:
        print("No sessions match that selection.")
        return
    targets = [(num, path, data) for num, path, data in ordered if num in chosen]
    print("Delete these session file(s)?")
    for num, path, _data in targets:
        print(f"  {num}. {os.path.relpath(path, state.sessions_dir)}")
    confirm = input("Type 'y' to delete, anything else to cancel: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return
    removed = 0
    for _num, path, _data in targets:
        try:
            os.remove(path)
            print(f"removed {os.path.relpath(path, state.sessions_dir)}")
            removed += 1
        except OSError as e:
            print(f"Error removing {path}: {e}", file=sys.stderr)
    print(f"Deleted {removed} session(s).")


def _cmd_rename(arg: str, state: ChatState):
    """Rename the current session or a stored one.

    ``/rename <new title>`` renames the live session (persisted immediately).
    ``/rename <id-prefix> <new title>`` renames any stored session whose
    session id starts with the given prefix; the title may contain spaces.
    """
    if not state.sessions_dir:
        print("Sessions directory is not configured.")
        return
    arg = arg.strip()
    if not arg:
        print("Usage: /rename <new title>  or  /rename <id-prefix> <new title>")
        return

    parts = arg.split(maxsplit=1)
    id_cands = [
        pd for pd in _list_session_files(state.sessions_dir)
        if (pd[1].get("session_id") or "").lower().startswith(parts[0].lower())
    ]
    if len(parts) > 1 and id_cands:
        if len(id_cands) > 1:
            ids = ", ".join(sorted(pd[1]["session_id"][:8] for pd in id_cands))
            print(f"Ambiguous id prefix '{parts[0]}' matches: {ids}")
            return
        path, data = id_cands[0]
        old = data.get("title") or "(untitled)"
        new_title = parts[1].strip()
        if not new_title:
            print("Usage: /rename <id-prefix> <new title>")
            return
        data["title"] = new_title
        data["updated_at"] = time.time()
        try:
            _write_session_file(path, data)
        except OSError as e:
            print(f"Error renaming session: {e}", file=sys.stderr)
            return
        print(f"Renamed '{old}' to '{new_title}'.")
        return

    if id_cands and len(parts) == 1:
        print(
            f"Session '{parts[0]}' matches a stored session; use "
            f"/rename <id> <new title> to rename it, or give a longer title "
            f"for the current session."
        )
        return

    state.session_title = arg
    autosave_session(state)
    print(f"Renamed current session to '{arg}'.")


def _default_skills_dir(state: ChatState) -> str:
    if state.skills_dir:
        return state.skills_dir
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")


def _list_skill_files(state: ChatState) -> list:
    skills_dir = _default_skills_dir(state)
    if not os.path.isdir(skills_dir):
        return []
    return sorted(
        f for f in os.listdir(skills_dir)
        if os.path.isfile(os.path.join(skills_dir, f))
    )


def _resolve_skill_path(name: str, state: ChatState) -> str:
    """Resolve a skill name to a file path.

    Absolute/relative paths that exist are used as-is; otherwise the name is
    looked up in the skills directory (trying <name>.md, <name>.txt, <name>).
    """
    if os.path.exists(name):
        return name
    skills_dir = _default_skills_dir(state)
    for candidate in (f"{name}.md", f"{name}.txt", name):
        path = os.path.join(skills_dir, candidate)
        if os.path.exists(path):
            return path
    return name


def _read_text_file(path: str, label: str = "file") -> str:
    """Read a text file as UTF-8 after passing the entropy check.

    Returns None on missing file, read error, or entropy-check rejection.
    """
    if not os.path.exists(path):
        print(f"Error: {label} not found: {path}")
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:
        print(f"Error reading {label}: {e}")
        return None

    from security.entropychecker import EntropyChecker

    checker = EntropyChecker()
    result = checker.feed(raw)
    if result.is_suspicious:
        print(f"Error: {label} '{path}' rejected by entropy check: {result.reason}")
        return None
    return raw.decode("utf-8", errors="replace")


def _read_skill_text(path: str) -> str:
    """Read a skill file as UTF-8 text after passing the entropy check.

    Returns None on missing file, read error, or entropy-check rejection.
    """
    return _read_text_file(path, label="skill file")


def _load_skill_texts(names: list, state: ChatState):
    """Resolve and read one or more skill files, each entropy-checked.

    Returns a list of text parts in the given order, or None if any file is
    missing, unreadable, or rejected. Reading is done for all files before
    returning so a partial load never leaves the state half-applied.
    """
    parts = []
    for name in names:
        path = _resolve_skill_path(name, state)
        text = _read_skill_text(path)
        if text is None:
            return None
        parts.append(text)
    return parts


def _cmd_skill_list(state: ChatState):
    files = _list_skill_files(state)
    if not files:
        print(f"No skills found in {_default_skills_dir(state)}")
        return
    print("Available skills:")
    for f in files:
        print(f"  {f}")


def _cmd_skill_load(sub_arg: str, state: ChatState):
    if not sub_arg:
        print("Usage: /skill load <name-or-path> [<name-or-path> ...]")
        return
    names = sub_arg.strip().split()
    texts = _load_skill_texts(names, state)
    if texts is None:
        return
    combined = "\n\n".join(texts)
    state.skill = " ".join(names)
    state.skill_text = combined
    state.apply_skill()
    autosave_session(state)
    print(f"Skill loaded: {' '.join(names)} ({len(combined)} characters)")


def _cmd_skill_unload(state: ChatState):
    if not state.skill_text:
        print("No skill loaded.")
        return
    state.skill = None
    state.skill_text = None
    state.apply_skill()
    autosave_session(state)
    print("Skill unloaded.")


def _cmd_skill_show(state: ChatState):
    if not state.skill_text:
        print("No skill loaded.")
        return
    print(f"Active skill: {state.skill or '(loaded via --skill)'}")
    print("---")
    print(state.skill_text)


def _show_skill_usage():
    print("Skill commands:")
    print("  /skill list                              List available skills")
    print("  /skill load <name-or-path> [<name-or-path> ...]  Load skill(s) into the system role")
    print("  /skill unload                            Unload the active skill")
    print("  /skill show                              Show the active skill")


def _cmd_skill(arg: str, state: ChatState):
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    sub_arg = parts[1] if len(parts) > 1 else ""

    entry = _SKILL_SUBCOMMANDS.get(sub)
    if entry is None:
        _show_skill_usage()
        return
    entry[0](sub_arg, state)


def _cmd_systemprompt(arg: str, state: ChatState):
    arg = arg.strip()

    if arg == "unset":
        if state.system_prompt is None:
            print("No system prompt set.")
            return
        state.system_prompt = None
        state.apply_skill()
        autosave_session(state)
        print("System prompt unset.")
        return

    if not arg or arg == "show":
        if state.system_prompt:
            print(state.system_prompt)
        else:
            print("No system prompt set.")
        return

    text = _read_text_file(arg, label="system prompt file")
    if text is None:
        return
    state.system_prompt = text
    state.apply_skill()
    autosave_session(state)
    print(f"System prompt loaded ({len(text)} characters)")


def _show_tools_usage():
    print("Tools commands:")
    print("  /tools loaded                    List loaded toolsets and their tools")
    print("  /tools available                 List toolsets available to load")
    print("  /tools show <toolset>            List all tools of one toolset")
    print("  /tools all                       List all tools of all toolsets")
    print("  /tools load <toolset> [<toolset> ...]   Load one or more toolsets")
    print("  /tools unload <toolset> [<toolset> ...] Unload one or more toolsets")


def _resolve_toolset_module(name: str) -> str:
    """Map a user-supplied toolset name to an importable module name.

    Bare names (e.g. ``dev_tools``) resolve to the tools package first
    (``tools.dev_tools``), falling back to a top-level module. Dotted names
    resolve inside the tools package first (``tools.<name>``, so subpackages
    of ``tools/`` work), falling back to the dotted name as-is (sibling
    packages like ``tools_security``).
    """
    if "." in name:
        for c in (f"tools.{name}", name):
            try:
                if importlib.util.find_spec(c) is not None:
                    return c
            except (ImportError, ModuleNotFoundError, AttributeError):
                continue
        return name
    candidates = [f"tools.{name}", name]
    for c in candidates:
        try:
            if importlib.util.find_spec(c) is not None:
                return c
        except (ImportError, ModuleNotFoundError, AttributeError):
            continue
    return candidates[0]


def _print_tool(t: Tool) -> None:
    props = t.parameters.get("properties", {})
    required = t.parameters.get("required", [])
    sig_parts = []
    for pname, pinfo in props.items():
        ptype = pinfo.get("type", "string")
        if pname in required:
            sig_parts.append(f"{pname}: {ptype}")
        else:
            sig_parts.append(f"[{pname}: {ptype}]")
    sig = ", ".join(sig_parts)
    print(f"    {t.name}({sig}) — {t.description}")


def _resolve_toolset_tools(state: ChatState, module_name: str):
    """Tool objects for a module: registry first, else import without
    registering. Returns None on import error."""
    tools = get_tools_of_module(module_name)
    if tools:
        return tools
    try:
        return peek_tools_of_module(module_name)
    except Exception as e:
        print(f"Error importing toolset '{module_name}': {e}")
        return None


def _list_loaded_tools(state: ChatState):
    if not state.loaded_tool_modules:
        print("No toolsets loaded.")
        return
    for mod in state.loaded_tool_modules:
        short = mod.rsplit(".", 1)[-1]
        tools = get_tools_of_module(mod)
        print(f"Loaded toolset '{short}':")
        if not tools:
            print("    (no tools)")
        for t in tools:
            _print_tool(t)


def _list_available_toolsets(state: ChatState):
    names = get_available_toolsets(state.tools_dir)
    if not names:
        print("No toolsets available.")
        return
    loaded = set(state.loaded_tool_modules)
    print("Available toolsets:")
    for n in names:
        fq = _resolve_toolset_module(n)
        marker = "  (loaded)" if fq in loaded else ""
        print(f"  {n}{marker}")


def _show_toolset(name: str, state: ChatState):
    if not name:
        print("Usage: /tools show <toolsetname>")
        return
    module_name = _resolve_toolset_module(name)
    short = module_name.rsplit(".", 1)[-1]
    tools = _resolve_toolset_tools(state, module_name)
    if tools is None:
        return
    if not tools:
        print(f"Toolset '{short}' has no tools.")
        return
    print(f"Toolset '{short}':")
    for t in tools:
        _print_tool(t)


def _list_all_tools(state: ChatState):
    names = get_available_toolsets(state.tools_dir)
    if not names:
        print("No toolsets available.")
        return
    loaded = set(state.loaded_tool_modules)
    for n in names:
        module_name = _resolve_toolset_module(n)
        tools = _resolve_toolset_tools(state, module_name)
        if tools is None:
            tools = []
        marker = " (loaded)" if module_name in loaded else ""
        print(f"Toolset '{n}'{marker}:")
        if not tools:
            print("    (no tools)")
        for t in tools:
            _print_tool(t)


def _tools_load(names: str, state: ChatState):
    names = (names or "").split()
    if not names:
        print("Usage: /tools load <toolsetname> [<toolsetname> ...]")
        return
    available = set(get_available_toolsets(state.tools_dir))
    already = set(state.loaded_tool_modules)
    to_load = []
    ok = True
    for name in names:
        module_name = _resolve_toolset_module(name)
        short = module_name.rsplit(".", 1)[-1]
        if module_name in already:
            print(f"Toolset '{short}' is already loaded.")
            ok = False
        elif "." not in name and short not in available:
            print(f"Error: unknown toolset '{name}'.")
            ok = False
        else:
            to_load.append(module_name)
    if not ok:
        return
    tools_before = list(state.loaded_tools)
    modules_before = list(state.loaded_tool_modules)
    loaded_any = []
    for module_name in to_load:
        try:
            tools = load_tools(module_name)
            state.loaded_tools.extend(tools)
            state.loaded_tool_modules.append(module_name)
            loaded_any.append(module_name)
        except Exception as e:
            print(f"Error loading toolset '{module_name}': {e}")
            state.loaded_tools = tools_before
            state.loaded_tool_modules = modules_before
            state.refresh_ollama_tools()
            return
    state.refresh_ollama_tools()
    short_names = " ".join(m.rsplit(".", 1)[-1] for m in loaded_any)
    autosave_session(state)
    print(f"Loaded toolset(s): {short_names}")


def _tools_unload(names: str, state: ChatState):
    names = (names or "").split()
    if not names:
        print("Usage: /tools unload <toolsetname> [<toolsetname> ...]")
        return
    loaded = set(state.loaded_tool_modules)
    to_remove = []
    ok = True
    for name in names:
        module_name = _resolve_toolset_module(name)
        if module_name not in loaded:
            print(f"Error: toolset '{name}' is not loaded.")
            ok = False
        else:
            to_remove.append(module_name)
    if not ok:
        return
    remove_tools = []
    for module_name in to_remove:
        remove_tools.extend(get_tools_of_module(module_name))
    state.loaded_tool_modules = [
        m for m in state.loaded_tool_modules if m not in set(to_remove)
    ]
    state.loaded_tools = [t for t in state.loaded_tools if t not in remove_tools]
    state.refresh_ollama_tools()
    short_names = " ".join(m.rsplit(".", 1)[-1] for m in to_remove)
    autosave_session(state)
    print(f"Unloaded toolset(s): {short_names}")


def _cmd_tools(arg: str, state: ChatState):
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    sub_arg = parts[1] if len(parts) > 1 else ""

    if not sub:
        _show_tools_usage()
        return
    entry = _TOOLS_SUBCOMMANDS.get(sub)
    if entry is None:
        print(f"Unknown /tools subcommand: {sub}")
        _show_tools_usage()
        return
    entry[0](sub_arg, state)


# ---------------------------------------------------------------------------
# Data-driven dispatch + completion registry
#
# _handle_command, _cmd_tools and _cmd_skill dispatch through the tables below,
# and Tab completion (/_completion_candidates, /_maybe_append_completion_space)
# reads the SAME tables — a command or subcommand is registered exactly once.
# Keep insertion order: _COMMANDS order == the completion order.
# ---------------------------------------------------------------------------


_COMMAND_HANDLERS = {
    "/feed": _cmd_feed,
    "/new": _cmd_new,
    "/compact": _cmd_compact,
    "/model": _cmd_model,
    "/plan": lambda _a, s: _set_mode(s, "plan"),
    "/build": lambda _a, s: _set_mode(s, "build"),
    "/save": _cmd_save,
    "/load": _cmd_load,
    "/resume": _cmd_resume,
    "/sessions": _cmd_sessions,
    "/stats": lambda _a, s: _cmd_stats(s),
    "/rename": _cmd_rename,
    "/tools": _cmd_tools,
    "/skill": _cmd_skill,
    "/systemprompt": _cmd_systemprompt,
    "/context": _cmd_context,
    "/history": _cmd_history,
    "/cut": _cmd_cut,
    "/help": lambda _a, _s: _show_help(),
    "/exit": lambda _a, _s: True,
    "/quit": lambda _a, _s: True,
}

_COMMANDS = list(_COMMAND_HANDLERS)


_TOOLS_SUBCOMMANDS = {
    "loaded":    (lambda _a, s: _list_loaded_tools(s), None),
    "available": (lambda _a, s: _list_available_toolsets(s), None),
    "show":      (_show_toolset, "toolset"),
    "all":       (lambda _a, s: _list_all_tools(s), None),
    "load":      (_tools_load, "toolset"),
    "unload":    (_tools_unload, "loaded_toolset"),
}

_SKILL_SUBCOMMANDS = {
    "list":   (lambda _a, s: _cmd_skill_list(s), None),
    "load":   (_cmd_skill_load, "path"),
    "unload": (lambda _a, s: _cmd_skill_unload(s), None),
    "show":   (lambda _a, s: _cmd_skill_show(s), None),
}

# /compact, /systemprompt and /sessions keep bespoke dispatch (bare /compact
# runs a compaction, /systemprompt <file> loads a prompt file, /sessions
# handles rm/all/-a/--all itself) — the tables below are completion metadata
# only, documented exceptions to the table-driven rule.
_COMPACT_SUBCOMMANDS = {
    "auto": (_cmd_compact_auto, None),
}
_SYSTEMPROMPT_SUBCOMMANDS = {
    "show":  (lambda _a, s: _cmd_systemprompt("show", s), None),
    "unset": (lambda _a, s: _cmd_systemprompt("unset", s), None),
}
_SESSIONS_SUBCOMMANDS = {
    "all": (lambda _a, s: _cmd_sessions("all", s), None),
    "rm":  (_cmd_sessions_rm, None),
}

_SUBCOMMAND_HANDLERS = {
    "/compact": _COMPACT_SUBCOMMANDS,
    "/tools": _TOOLS_SUBCOMMANDS,
    "/skill": _SKILL_SUBCOMMANDS,
    "/systemprompt": _SYSTEMPROMPT_SUBCOMMANDS,
    "/sessions": _SESSIONS_SUBCOMMANDS,
}

_COMMAND_SUBCOMMANDS = {head: list(t) for head, t in _SUBCOMMAND_HANDLERS.items()}
