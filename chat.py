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


def run_chat(state: ChatState):
    print("Chat mode. Type /help for commands.")
    use_color = color_mode_enabled(state.color)
    prompt = colored(">>> ", C_PROMPT, use_color)

    while True:
        try:
            line = input(prompt)
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print("\nInterrupted.")
            continue

        stripped = line.strip()
        if not stripped:
            continue

        # Track messages before this turn to allow rollback on error/interrupt
        messages_before = len(state.messages)

        try:
            if stripped.startswith("/"):
                if _handle_command(stripped, state):
                    break
                continue

            user_msg = {"role": "user", "content": stripped}
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
            )
        except KeyboardInterrupt:
            print("\nInterrupted.")
            state.state_manager.reset()
            # Rollback messages added during this turn (user message or assistant/tool messages)
            while len(state.messages) > messages_before:
                state.messages.pop()
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            state.state_manager.reset()
            # Rollback messages added during this turn
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

    elif cmd == "/context":
        total_chars = sum(len(m.get("content", "") or "") for m in state.messages)
        print(f"Messages: {len(state.messages)}, total characters: {total_chars}")

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
    print("  /context        Show conversation stats")
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
            max_tool_rounds=state.max_tool_rounds,
            max_tool_rounds_continuation=state.max_tool_rounds_continuation,
            ollama_websearch=state.ollama_websearch,
            color=state.color,
            ndjson_log_file_handle=state.ndjson_log_file_handle,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        state.messages.pop()


def _cmd_save(path: str, state: ChatState):
    if not path:
        print("Usage: /save <path>")
        return
    data = {
        "model": state.model,
        "messages": state.messages,
    }
    if state.skill is not None or state.skill_text is not None:
        data["skill"] = state.skill
        data["skill_text"] = state.skill_text
    if state.loaded_tool_modules:
        data["loaded_tool_modules"] = list(state.loaded_tool_modules)
    if os.path.exists(path):
        confirm = input(f"File '{path}' already exists. Overwrite? (y/n): ").lower()
        if confirm != 'y':
            print("Save aborted.")
            return

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Conversation saved to {path}")
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


def _read_skill_text(path: str) -> str:
    """Read a skill file as UTF-8 text after passing the entropy check.

    Returns None on missing file, read error, or entropy-check rejection.
    """
    if not os.path.exists(path):
        print(f"Error: skill file not found: {path}")
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:
        print(f"Error reading skill file: {e}")
        return None

    from security.entropychecker import EntropyChecker

    checker = EntropyChecker()
    result = checker.feed(raw)
    if result.is_suspicious:
        print(f"Error: skill file '{path}' rejected by entropy check: {result.reason}")
        return None
    return raw.decode("utf-8", errors="replace")


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
