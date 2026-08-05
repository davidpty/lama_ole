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
)

from color_util import C_PROMPT, color_mode_enabled, colored


@dataclass
class ChatState:
    client: object
    model: str
    messages: list = field(default_factory=list)
    loaded_tools: list[Tool] = field(default_factory=list)
    ollama_tools: object = None
    options: dict = field(default_factory=dict)
    keep_alive: object = None
    show_thinking: bool = False
    no_safety_system_prompt: bool = False
    system_prompt: str = None
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
        if state.loaded_tools:
            print("Loaded tools:")
            for t in state.loaded_tools:
                print(f"  {t.name}: {t.description}")
        else:
            print("No tools loaded.")

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
    print("  /tools          List loaded tools")
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
    print(f"Loaded conversation with {len(state.messages)} messages")
