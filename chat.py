import importlib.util
import json
import os
import sys
import time
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
)

from color_util import C_PROMPT, color_mode_enabled, colored


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
        )
        for i, m in enumerate(self.messages):
            if m.get("role") == "system":
                self.messages[i]["content"] = new_content
                return
        self.messages.insert(0, {"role": "system", "content": new_content})

    def refresh_ollama_tools(self) -> None:
        """Recompute the Ollama tool list from ``loaded_tools``.

        Called after every runtime load/unload so the next turn advertises
        exactly the current set of tools.
        """
        self.ollama_tools = to_ollama_tools(self.loaded_tools) if self.loaded_tools else None

    def get_history_entries(self):
        """Returns a list of viewable history entries with numbering and type info."""
        M = len(self.messages)
        entries = []
        for i, msg in enumerate(self.messages):
            # Numbering: Index 0 -> M, Index M-1 -> 1
            num = M - i
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "user":
                entries.append({"num": num, "type": "user", "msg": msg})
            elif role == "system":
                # System messages are usually not part of the visible history in REPL
                continue
            elif role == "assistant":
                # Check for thinking process (Ollama format)
                if isinstance(content, dict) and "thinking" in content:
                    entries.append({"num": num, "type": "thinking", "msg": msg})
                elif isinstance(content, str) and "thought:" in content: # fallback if not structured
                     entries.append({"num": num, "type": "thinking", "msg": msg})
                else:
                    # Check for tool calls
                    if "tool_calls" in msg or (isinstance(content, dict) and "tool_calls" in content):
                        entries.append({"num": num, "type": "toolcall", "msg": msg})
                    else:
                        entries.append({"num": num, "type": "output", "msg": msg})
            elif role == "tool":
                entries.append({"num": num, "type": "tool_result", "msg": msg})

        return entries

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



_COMMANDS = [
    "/feed",
    "/clear",
    "/model",
    "/save",
    "/load",
    "/tools",
    "/skill",
    "/systemprompt",
    "/context",
    "/history",
    "/cut",
    "/help",
    "/exit",
    "/quit",
]

_COMMAND_SUBCOMMANDS = {
    "/tools": ["loaded", "available", "show", "all", "load", "unload"],
    "/skill": ["list", "load", "unload", "show"],
    "/systemprompt": ["show", "unset"],
}

_PATH_COMMANDS = {"/feed", "/save", "/load"}


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


def _completion_candidates(buffer: str) -> list:
    """Compute Tab-completion candidates for a raw input line.

    Pure function (no readline dependency) so it can be unit-tested:
    commands on the first word, subcommands on the first argument, and
    file paths for commands that take a path argument.
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

    if arg_index == 1 and head in _COMMAND_SUBCOMMANDS:
        subs = [s for s in _COMMAND_SUBCOMMANDS[head] if s.startswith(partial)]
        if head == "/systemprompt":
            for f in _complete_file_path(partial):
                if f not in subs:
                    subs.append(f)
        return subs

    if head in _PATH_COMMANDS and arg_index == 1:
        return _complete_file_path(partial)

    if head == "/skill" and arg_index == 2 and tokens[1].lower() == "load":
        return _complete_file_path(partial)

    if head == "/systemprompt" and arg_index >= 2:
        return _complete_file_path(partial)

    return []


def _complete(text: str, state: int):
    """readline completer callback, driven by the current line buffer."""
    if readline is None:
        return None
    matches = _completion_candidates(readline.get_line_buffer())
    return matches[state] if state < len(matches) else None


def _install_readline_completion() -> None:
    """Enable Tab completion for commands, subcommands and file paths."""
    if readline is None:
        return
    readline.set_completer(_complete)
    readline.set_completer_delims(" \t\n")
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


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
    _install_readline_completion()
    use_color = color_mode_enabled(state.color)
    prompt = colored(">>> ", C_PROMPT, use_color)

    while True:
        # Snapshot before reading input so an interrupt either at the prompt or
        # during the turn only rolls back what this iteration added.
        messages_before = len(state.messages)
        user_msg = None
        try:
            line = input(prompt)

            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("/"):
                if _handle_command(stripped, state):
                    break
                continue

            user_msg = {"role": "user", "content": stripped}
            state.stamp_message(user_msg)
            state.messages.append(user_msg)
            state.log_ndjson(user_msg)

            if state.chatinput_file_handle:
                from tool_base import _write_input
                _write_input(state.chatinput_file_handle, f"[chat input] {stripped}\n")
                state.chatinput_file_handle.flush()

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
            )
        except EOFError:
            print()
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

    if cmd in ("/exit", "/quit"):
        return True

    elif cmd == "/help":
        _show_help()

    elif cmd == "/clear":
        state.messages.clear()
        print("Conversation cleared.")

    elif cmd == "/feed":
        _cmd_feed(arg, state)

    elif cmd == "/model":
        if not arg:
            print(f"Current model: {state.model}")
        else:
            state.model = arg
            print(f"Switched to model: {arg}")

    elif cmd == "/save":
        _cmd_save(arg, state)

    elif cmd == "/load":
        _cmd_load(arg, state)

    elif cmd == "/tools":
        _cmd_tools(arg, state)

    elif cmd == "/skill":
        _cmd_skill(arg, state)

    elif cmd == "/systemprompt":
        _cmd_systemprompt(arg, state)

    elif cmd == "/context":
        total_chars = sum(len(m.get("content", "") or "") for m in state.messages)
        print(f"Messages: {len(state.messages)}, total characters: {total_chars}")

    elif cmd == "/history":
        _cmd_history(arg, state)

    elif cmd == "/cut":
        _cmd_cut(arg, state)

    else:
        print(f"Unknown command: {cmd}. Type /help for available commands.")

    return False


def _show_help():
    print()
    print("Commands:")
    print("  /feed <path>    Read a file and inject its content as a message")
    print("  /clear          Clear the conversation history")
    print("  /model <name>   Switch to a different model")
    print("  /save <path>    Save the conversation to a JSON file")
    print("  /load <path>    Load a conversation from a JSON file")
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
    print("  /context        Show conversation stats")
    print("  /history [-t] [N] [-N] [a..b ...]   Show conversation history entries")
    print("  /cut N | /cut a..b | /cut undo   Remove entries from the history")
    print("  /help           Show this help message")
    print("  /exit, /quit    Exit the chat")
    print()


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
        )
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
    """Human-readable summary of the tool calls in an assistant message.

    Mirrors the ``[data from <name>: <args>]`` marker that tool results are
    wrapped with, so the default /history shows which tool was invoked with
    which arguments without dumping the full result data.
    """
    calls = msg.get("tool_calls") or []
    parts = []
    for tc in calls:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        name = fn.get("name", "?")
        arguments = fn.get("arguments") or {}
        if isinstance(arguments, dict):
            args_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        else:
            args_str = str(arguments)
        parts.append(f"[data from {name}: {args_str}]")
    return ", ".join(parts)


def _format_history_entry(entry):
    import json
    num = entry['num']
    etype = entry['type'].upper()
    msg = entry['msg']
    role = msg.get('role')
    content = msg.get('content', '')
    ts = msg.get('timestamp')
    prefix = f"[{num}] [{ts}] " if ts else f"[{num}] "

    if role == 'user':
        return f"{prefix}USER: {content}"
    elif role == 'assistant':
        if etype == 'THINKING':
            if isinstance(content, dict) and 'thinking' in content:
                thought = content['thinking']
                return f"{prefix}ASSISTANT (THOUGHT): {thought}"
            else:
                return f"{prefix}ASSISTANT (THOUGHT): {content}"
        elif etype == 'TOOLCALL':
            summary = _toolcall_summary(msg)
            if summary:
                return f"{prefix}ASSISTANT (TOOLCALL) TOOL: {summary}"
            return f"{prefix}ASSISTANT (TOOLCALL)"
        else: # output
            if isinstance(content, dict):
                return f"{prefix}ASSISTANT: {json.dumps(content)}"
            return f"{prefix}ASSISTANT: {content}"
    elif role == 'tool':
        return f"{prefix}TOOL: {content}"
    else:
        return f"{prefix}{etype}: {msg}"


def _cmd_history(arg: str, state: ChatState):
    entries = state.get_history_entries()
    if not entries:
        print("No history available.")
        return

    M = len(state.messages)
    # -t includes tool responses; otherwise tool results are hidden
    # (only output, thinking, toolcalls and user messages are shown).
    include_tool_results = False
    target_ranges = []  # List of (start_num, end_num) where numbers are 1..M

    for token in arg.split():
        if token == "-t":
            include_tool_results = True
        elif ".." in token:
            try:
                a, b = map(int, token.split(".."))
                target_ranges.append((min(a, b), max(a, b)))
            except ValueError:
                pass
        else:
            try:
                val = int(token)
            except ValueError:
                continue
            if val < 0:
                n = abs(val)
                target_ranges.append((1, n))
            else:
                # "first N" means numbers M down to M-N+1
                target_ranges.append(("first", val))

    if include_tool_results:
        visible = entries
    else:
        visible = [e for e in entries if e["type"] != "tool_result"]

    if not target_ranges:
        # /history -> show all (respecting the -t filter)
        for e in visible:
            print(_format_history_entry(e))
        return

    # Collect all numbers to show
    numbers_to_show = set()
    for r in target_ranges:
        if isinstance(r[0], int):
            start, end = r
            for n in range(start, end + 1):
                numbers_to_show.add(n)
        elif r[0] == "first":
            n = r[1]
            # First N entries are numbers M, M-1, ..., M-N+1
            for j in range(M - n + 1, M + 1):
                numbers_to_show.add(j)

    if not numbers_to_show:
        print("No history matches the criteria.")
        return

    # Sort numbers descending (M down to 1)
    sorted_nums = sorted(numbers_to_show, reverse=True)

    found_any = False
    for n in sorted_nums:
        entry = next((e for e in visible if e["num"] == n), None)
        if entry:
            print(_format_history_entry(entry))
            found_any = True

    if not found_any:
        print("No history matches the criteria.")


def _cmd_cut(arg: str, state: ChatState):
    """Remove entries from the conversation history (/cut N, /cut a..b, /cut undo).

    History numbers count from M (oldest) down to 1 (newest), matching
    /history. /cut N removes entries numbered 1..N; /cut a..b removes the
    entries numbered a..b. System messages are never removed.
    """
    arg = arg.strip()
    if not arg:
        print("Usage: /cut N | /cut a..b | /cut undo")
        return

    if arg == "undo":
        if state.undo_cut():
            print("Cut undone.")
        else:
            print("Nothing to undo.")
        return

    M = len(state.messages)
    if M == 0:
        print("No history to cut.")
        return

    numbers = set()
    for token in arg.split():
        if ".." in token:
            try:
                a, b = map(int, token.split(".."))
            except ValueError:
                print(f"Invalid range: {token}")
                return
            lo, hi = min(a, b), max(a, b)
            numbers.update(range(lo, hi + 1))
        else:
            try:
                n = int(token)
            except ValueError:
                print(f"Invalid cut argument: {token}")
                return
            if n < 1:
                print(f"Invalid cut argument: {token}")
                return
            # /cut N removes the last N entries (numbers 1..N).
            numbers.update(range(1, n + 1))

    # Map history numbers (1..M) back to message indices (index = M - num).
    indices = sorted({M - n for n in numbers if 1 <= n <= M}, reverse=True)
    # Never remove system messages (keeps the conversation in a valid state).
    indices = [i for i in indices if state.messages[i].get("role") != "system"]

    if not indices:
        print("Nothing to cut.")
        return

    removed = [state.messages[i] for i in indices]
    state._last_cut_messages = removed
    state._last_cut_indices = indices

    for i in indices:
        del state.messages[i]

    print(f"Cut {len(removed)} message(s).")


def _cmd_save(path: str, state: ChatState):
    """Save the conversation to a JSON file."""
    if not path:
        print("Usage: /save <path>")
        return
    data = {
        "messages": state.messages,
        "model": state.model,
        "loaded_tool_modules": state.loaded_tool_modules,
    }
    if state.skill is not None:
        data["skill"] = state.skill
    if state.skill_text is not None:
        data["skill_text"] = state.skill_text
    if state.system_prompt is not None:
        data["system_prompt"] = state.system_prompt
    if state.loaded_tool_modules:
        data["loaded_tool_modules"] = list(state.loaded_tool_modules)
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
    state.messages = data.get("messages", [])
    if "model" in data:
        state.model = data["model"]
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
    print(f"Loaded conversation with {len(state.messages)} messages")


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


def _cmd_skill(arg: str, state: ChatState):
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    sub_arg = parts[1] if len(parts) > 1 else ""

    if sub == "list":
        files = _list_skill_files(state)
        if not files:
            print(f"No skills found in {_default_skills_dir(state)}")
            return
        print("Available skills:")
        for f in files:
            print(f"  {f}")

    elif sub == "load":
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
        print(f"Skill loaded: {' '.join(names)} ({len(combined)} characters)")

    elif sub == "unload":
        if not state.skill_text:
            print("No skill loaded.")
            return
        state.skill = None
        state.skill_text = None
        state.apply_skill()
        print("Skill unloaded.")

    elif sub == "show":
        if not state.skill_text:
            print("No skill loaded.")
            return
        print(f"Active skill: {state.skill or '(loaded via --skill)'}")
        print("---")
        print(state.skill_text)

    else:
        print("Skill commands:")
        print("  /skill list                              List available skills")
        print("  /skill load <name-or-path> [<name-or-path> ...]  Load skill(s) into the system role")
        print("  /skill unload                            Unload the active skill")
        print("  /skill show                              Show the active skill")


def _cmd_systemprompt(arg: str, state: ChatState):
    arg = arg.strip()

    if arg == "unset":
        if state.system_prompt is None:
            print("No system prompt set.")
            return
        state.system_prompt = None
        state.apply_skill()
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
    (``tools.dev_tools``), falling back to a top-level module. Dotted names are
    used as-is.
    """
    if "." in name:
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
        elif short not in available:
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
    print(f"Unloaded toolset(s): {short_names}")


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
