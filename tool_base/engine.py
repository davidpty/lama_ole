import json
import os
import sys
import time
from typing import Any, Optional, List

from ollama import Tool as OllamaTool

import color_util

from .models import Tool
from .constants import DANGEROUS_TOOLS
from .utils import create_uuid_15

_MISSING = object()


def _stamp_message(msg) -> None:
    """Attach the event time to a message dict (idempotent).

    /history uses this timestamp to show when each entry happened. Existing
    timestamps (e.g. from /load) are left untouched.
    """
    if isinstance(msg, dict) and "timestamp" not in msg:
        msg["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")


def _entropy_check_tool_result(result, tool_name) -> None:
    """Defensive entropy check on a tool result dict (opt-in, see below).

    Warns on stderr and truncates the data when the content looks binary or
    random. Enabled only when LAMA_OLE_ENTROPY_CHECK is set or verbose >= 2,
    so normal operation is not slowed down.
    """
    if not isinstance(result, dict) or result.get("status") != "success":
        return
    content = result.get("data", "")
    if isinstance(content, bytes):
        data_bytes = content
    elif isinstance(content, str):
        data_bytes = content.encode("utf-8", errors="replace")
    else:
        try:
            data_bytes = json.dumps(content).encode("utf-8")
        except Exception:
            return

    from security.entropychecker import EntropyChecker

    check = EntropyChecker().feed(data_bytes)
    if check.is_suspicious:
        print(
            f"[WARNING] Tool '{tool_name}' result failed entropy check: "
            f"{check.reason}",
            file=sys.stderr,
        )
        if isinstance(content, bytes):
            result["data"] = content[:1000] + b"... [TRUNCATED BY ENTROPY CHECK]"
        else:
            result["data"] = str(content)[:1000] + "... [TRUNCATED BY ENTROPY CHECK]"


_MAX_DIFF_LINES = 200


def _print_diff_block(file, diff, use_color) -> None:
    """Print a colored unified diff block to stdout (mirrors opencode's edit card)."""
    if not diff:
        return
    lines = diff.split("\n")
    additions = 0
    deletions = 0
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    if len(lines) > _MAX_DIFF_LINES + 2:
        shown = lines[:_MAX_DIFF_LINES]
        truncated = True
    else:
        shown = lines
        truncated = False

    header = f"[edit: {file}] +{additions} -{deletions}"
    print(color_util.colored(header, color_util.C_PROMPT, use_color))
    for line in shown:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            color = color_util.C_METER_MID
        elif line.startswith("+"):
            color = color_util.C_METER_LOW
        elif line.startswith("-"):
            color = color_util.C_METER_HIGH
        else:
            color = color_util.C_THINK
        print(color_util.colored(line, color, use_color))
    if truncated:
        print(color_util.colored(f"... diff truncated ({len(lines) - _MAX_DIFF_LINES} lines omitted)", color_util.C_THINK, use_color))


def compose_system_prompt(
    system_prompt: Optional[str] = None,
    skill_text: Optional[str] = None,
    no_safety_system_prompt: bool = False,
    mode: Optional[str] = None,
) -> str:
    """Build the system message content from its ordered parts.

    Order: base system prompt -> optional skill block -> optional plan-mode
    block -> safety prompts. The skill block is delimited so it can be
    identified and stripped on unload, and so it stays visually distinct from
    the base prompt.
    """
    sp = ""
    if system_prompt is not None:
        sp += system_prompt
        sp += "\n"
    if skill_text:
        sp += "[SKILL BEGIN]\n"
        sp += skill_text
        sp += "\n[SKILL END]\n"
    if mode == "plan":
        from .constants import PLAN_MODE_SYSTEM_PROMPT

        sp += "[PLAN MODE BEGIN]\n"
        sp += PLAN_MODE_SYSTEM_PROMPT
        sp += "\n[PLAN MODE END]\n"
    if not no_safety_system_prompt:
        from .constants import SAFETY_SYSTEM_PROMPT, JSON_RETURN_PROMPT

        sp += SAFETY_SYSTEM_PROMPT
        sp += JSON_RETURN_PROMPT
    return sp


def run_with_tools(
    client,
    model,
    messages: List[dict],
    loaded_tools: List[Tool],
    ollama_tools: Optional[List[OllamaTool]],
    options: dict,
    keep_alive: Any,
    show_thinking: bool,
    no_safety_system_prompt: bool,
    system_prompt: Optional[str] = None,
    skill_text: Optional[str] = None,
    mode: Optional[str] = None,
    verbose: int = 0,
    safe: bool = False,
    thought_file_handle=None,
    output_file_handle=None,
    toolcall_file_handle=None,
    chatinput_file_handle=None,
    max_tool_rounds: Optional[int] = None,
    max_tool_rounds_continuation: str = "ask",
    ollama_websearch: bool = False,
    ndjson_log_file_handle=None,
    color: str = "auto",
    state_manager=None,
    metrics: Optional[dict] = None,
    mode_state=None,
    show_diff: bool = True,
):
    from .loop_states import ExecutionState, StateManager, ExecutionInterrupted
    from .logging import StateLogger

    if state_manager is None:
        state_manager = StateManager()
    use_color = color_util.color_mode_enabled(color)
    tool_rounds = 0
    think_state = False
    final_response = ""
    last_prompt_eval_count = None
    last_eval_count = None
    last_eval_duration_ns = None
    last_prompt_eval_duration_ns = None
    turn_rounds = []
    turn_elapsed_started = None

    def _current_mode() -> str:
        """Effective mode right now (may change mid-turn via the hotkey)."""
        if mode_state is not None:
            current = getattr(mode_state, "mode", None)
            if current is not None:
                return current
        return mode or "build"

    def _refresh_tools_for_request():
        """Adopt mode_state's advertised tool list after a mid-turn toggle."""
        if mode_state is None:
            return tools_for_request
        new_tools = getattr(mode_state, "ollama_tools", _MISSING)
        if new_tools is not _MISSING and new_tools is not tools_for_request:
            return new_tools
        return tools_for_request

    tools_for_request = ollama_tools

    from contextlib import contextmanager

    @contextmanager
    def _hotkey_suspended():
        """Park the mid-turn hotkey listener around a blocking stdin prompt."""
        pause = getattr(mode_state, "hotkey_pause", None)
        resume = getattr(mode_state, "hotkey_resume", None)
        if pause is not None:
            pause()
        try:
            yield
        finally:
            if resume is not None:
                resume()

    thought_logger = (
        StateLogger(handle=thought_file_handle) if thought_file_handle else None
    )
    output_logger = (
        StateLogger(handle=output_file_handle) if output_file_handle else None
    )
    toolcall_logger = (
        StateLogger(handle=toolcall_file_handle) if toolcall_file_handle else None
    )

    has_system = any(m.get("role") == "system" for m in messages)
    if not has_system:
        sp = compose_system_prompt(
            system_prompt=system_prompt,
            skill_text=skill_text,
            no_safety_system_prompt=no_safety_system_prompt,
            mode=mode,
        )

        system_msg = {"role": "system", "content": sp}
        _stamp_message(system_msg)
        messages.insert(0, system_msg)
        if ndjson_log_file_handle:
            from .logging import _log_ndjson_message
            _log_ndjson_message(ndjson_log_file_handle, model, system_msg)

    if ollama_websearch:
        web_tool = OllamaTool(
            type="function",
            function=OllamaTool.Function(
                name="web_search",
                description="Search the web for current information",
                parameters=OllamaTool.Function.Parameters(
                    type="object",
                    properties={
                        "query": OllamaTool.Function.Parameters.Property(
                            type="string",
                            description="The search query",
                        ),
                    },
                    required=["query"],
                ),
            ),
        )
        if ollama_tools:
            ollama_tools.append(web_tool)
        else:
            ollama_tools = [web_tool]

    if verbose >= 2:
        from .logging import _log_messages_payload
        _log_messages_payload(messages, file=sys.stderr)

    while True:
        if max_tool_rounds is not None and tool_rounds >= max_tool_rounds:
            if max_tool_rounds_continuation == "fallback":
                print(
                    "Reached maximum number of tool-calling rounds.",
                    file=sys.stderr,
                )
                state_manager.reset()
                break
            elif max_tool_rounds_continuation == "ask":
                print(
                    f"Maximum tool rounds ({max_tool_rounds}) reached.",
                    file=sys.stderr,
                )
                print("Options:", file=sys.stderr)
                print("  1. Set a new max round limit", file=sys.stderr)
                print("  2. Set unlimited (continue indefinitely)", file=sys.stderr)
                print("  3. Fallback (current mode default)", file=sys.stderr)
                print("  4. Quit", file=sys.stderr)
                print("Enter choice (1-4): ", file=sys.stderr, end='', flush=True)
                try:
                    with _hotkey_suspended():
                        choice = sys.stdin.readline().strip()
                except EOFError:
                    choice = "3"
                except KeyboardInterrupt:
                    state_manager.reset()
                    print("\nInterrupted.", file=sys.stderr)
                    break
                if choice == "1":
                    print(
                        "Enter new max round limit: ",
                        file=sys.stderr, end='', flush=True,
                    )
                    try:
                        with _hotkey_suspended():
                            new_val = sys.stdin.readline().strip()
                        max_tool_rounds = int(new_val)
                        print(
                            f"New limit set to {max_tool_rounds}.",
                            file=sys.stderr,
                        )
                    except (ValueError, EOFError):
                        print("Invalid input. Falling back.", file=sys.stderr)
                        state_manager.reset()
                        break
                    except KeyboardInterrupt:
                        state_manager.reset()
                        print("\nInterrupted.", file=sys.stderr)
                        break
                elif choice == "2":
                    max_tool_rounds = None
                    print("Unlimited rounds set.", file=sys.stderr)
                elif choice == "4":
                    state_manager.reset()
                    print("Exiting.", file=sys.stderr)
                    return final_response
                else:
                    state_manager.reset()
                    break
                continue

        if verbose >= 2:
            from .logging import _log_messages_payload
            _log_messages_payload(messages, file=sys.stderr)

        # A fresh model generation round starts a new slice for thought/output logs.
        if thought_logger:
            thought_logger.new_slice()
        if output_logger:
            output_logger.new_slice()

        response_content = ""
        response_tool_calls = None
        think_text = ""
        round_prompt_eval_count = None
        round_eval_count = None
        round_eval_duration_ns = None
        round_prompt_eval_duration_ns = None
        round_started = time.monotonic()
        if turn_elapsed_started is None:
            turn_elapsed_started = round_started

        tools_for_request = _refresh_tools_for_request()

        try:
            stream = client.chat(
                model=model,
                messages=[{k: v for k, v in m.items() if k != "thinking"} for m in messages],
                tools=tools_for_request,
                stream=True,
                options=options,
                keep_alive=keep_alive,
            )
            try:
                for chunk in stream:
                    msg = chunk.message

                    if getattr(chunk, "prompt_eval_count", None) is not None:
                        last_prompt_eval_count = chunk.prompt_eval_count
                    if getattr(chunk, "eval_count", None) is not None:
                        last_eval_count = chunk.eval_count
                    if getattr(chunk, "eval_duration", None) is not None:
                        last_eval_duration_ns = chunk.eval_duration
                    if getattr(chunk, "prompt_eval_duration", None) is not None:
                        last_prompt_eval_duration_ns = chunk.prompt_eval_duration

                    if verbose >= 3:
                        from .logging import _log_chunk
                        _log_chunk(msg, file=sys.stderr)

                    if msg.thinking:
                        think_text += msg.thinking
                        if not think_state:
                            think_state = True
                            state_manager.transition_to(ExecutionState.THINKING)
                            if thought_logger:
                                thought_logger.new_slice()
                            if show_thinking:
                                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                                print(color_util.colored(f"[{ts}] Thinking starts", color_util.C_THINK, use_color))
                        if show_thinking:
                            print(color_util.colored(msg.thinking, color_util.C_THINK, use_color), end='', flush=True)
                        if thought_logger:
                            thought_logger.write_thought(msg.thinking)

                    if msg.content:
                        if think_state:
                            think_state = False
                            state_manager.transition_to(ExecutionState.OUTPUTTING)
                            if output_logger:
                                output_logger.new_slice()
                            if show_thinking:
                                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                                print()
                                print(color_util.colored(f"[{ts}] Thinking ends", color_util.C_THINK, use_color))
                                print()
                        elif state_manager.current_state != ExecutionState.OUTPUTTING:
                            state_manager.transition_to(ExecutionState.OUTPUTTING)
                        response_content += msg.content
                        print(color_util.colored(msg.content, color_util.C_OUTPUT, use_color), end='', flush=True)
                        if output_logger:
                            output_logger.write_output(msg.content)

                    if msg.tool_calls:
                        response_tool_calls = msg.tool_calls
            finally:
                if stream is not None and hasattr(stream, "close"):
                    stream.close()
        except KeyboardInterrupt:
            interrupted_state = state_manager.current_state
            state_manager.reset()
            think_state = False
            print("\nInterrupted during model response. Returning to prompt.", file=sys.stderr)
            raise ExecutionInterrupted(interrupted_state)

        if think_state:
            think_state = False
            state_manager.transition_to(ExecutionState.IDLE)
            if show_thinking:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                print()
                print(color_util.colored(f"[{ts}] Thinking ends", color_util.C_THINK, use_color))
                print()

        print()

        if metrics is not None:
            metrics["prompt_eval_count"] = last_prompt_eval_count
            metrics["eval_count"] = last_eval_count
            metrics["eval_duration_ns"] = last_eval_duration_ns
            metrics["prompt_eval_duration_ns"] = last_prompt_eval_duration_ns
            metrics["last_round_kind"] = "tool call" if response_tool_calls else "final answer"
            metrics["rounds_model"] = model

        if metrics is not None:
            turn_rounds.append(
                {
                    "kind": "tool call" if response_tool_calls else "final answer",
                    "eval_count": last_eval_count,
                    "eval_duration_ns": last_eval_duration_ns,
                    "prompt_eval_count": last_prompt_eval_count,
                    "prompt_eval_duration_ns": last_prompt_eval_duration_ns,
                }
            )

        if response_tool_calls:
            assistant_msg = {
                "role": "assistant",
                "content": response_content or None,
                "tool_calls": [
                    {
                        "function": {
                            "name": tc.function.name,
                            "arguments": dict(tc.function.arguments),
                        }
                    }
                    for tc in response_tool_calls
                ],
            }
            if show_thinking and think_text.strip():
                assistant_msg["thinking"] = think_text
            _stamp_message(assistant_msg)
            messages.append(assistant_msg)
            if ndjson_log_file_handle:
                from .logging import _log_ndjson_message
                _log_ndjson_message(ndjson_log_file_handle, model, assistant_msg)

            for tc in response_tool_calls:
                tool_name = tc.function.name
                arguments = dict(tc.function.arguments) if tc.function.arguments else {}
                args_str = ", ".join(
                    f"{k}={v!r}" for k, v in arguments.items()
                )

                tool_obj = next(
                    (t for t in loaded_tools if t.name == tool_name),
                    None,
                )

                if verbose >= 1:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    print(
                        f"[{ts}] [tool: {tool_name}({args_str})]",
                        file=sys.stderr,
                        flush=True,
                    )

                state_manager.transition_to(ExecutionState.TOOLCALLING)

                if toolcall_logger:
                    toolcall_logger.new_slice()
                    toolcall_logger.write_tool_call(f"[tool: {tool_name}({args_str})]\n")

                try:
                    if tool_obj:
                        if _current_mode() == "plan" and not tool_obj.readonly:
                            # Mid-turn safety net: a write tool proposed before
                            # the user toggled to plan mode must not run. The
                            # model should learn the tool still exists and will
                            # work once build mode is activated.
                            result = {
                                "status": "error",
                                "message": (
                                    f"Execution of '{tool_name}' blocked: plan mode is "
                                    "currently enforced. This tool is still available and "
                                    "will run once build mode is activated (/build). Do not "
                                    "retry it; continue planning with read-only tools."
                                ),
                            }
                        else:
                            should_run = True
                            if safe and tool_name in DANGEROUS_TOOLS:
                                print(
                                    f"\n[DANGER] Tool '{tool_name}' called with: {args_str}",
                                    file=sys.stderr,
                                )
                                print(
                                    "Proceed? (y/N): ",
                                    file=sys.stderr, end='', flush=True,
                                )
                                try:
                                    with _hotkey_suspended():
                                        answer = sys.stdin.readline().strip().lower()
                                except EOFError:
                                    answer = 'n'
                                except KeyboardInterrupt:
                                    answer = 'n'
                                should_run = answer == 'y'

                            if should_run:
                                try:
                                    raw_result = tool_obj.fn(**arguments)
                                    if isinstance(raw_result, dict):
                                        result = raw_result
                                    else:
                                        result = {"status": "success", "data": raw_result}
                                except Exception as e:
                                    result = {"status": "error", "message": str(e)}
                            else:
                                result = {"status": "error", "message": f"Execution of '{tool_name}' cancelled by user (safe mode)."}
                    else:
                        result = {"status": "error", "message": f"unknown tool '{tool_name}'"}
                except KeyboardInterrupt:
                    interrupted_state = state_manager.current_state
                    state_manager.reset()
                    print("\nInterrupted during tool execution. Returning to prompt.", file=sys.stderr)
                    raise ExecutionInterrupted(interrupted_state)

                # Defensive entropy check (opt-in): catches future tools that
                # bypass the per-tool integration.
                if verbose >= 2 or os.environ.get("LAMA_OLE_ENTROPY_CHECK"):
                    _entropy_check_tool_result(result, tool_name)

                if verbose >= 1:
                    display = json.dumps(result, indent=2) if isinstance(result, dict) else str(result)
                    if verbose < 2 and len(display) > 500:
                        display = display[:500] + "..."
                    print(
                        f"[tool result: {display}]",
                        file=sys.stderr,
                        flush=True,
                    )

                if show_diff and isinstance(result, dict):
                    _print_diff_block(
                        result.get("file") or tool_name,
                        result.get("diff") or "",
                        use_color,
                    )

                # --- NEW NONCE LOGIC START ---
                nonce = create_uuid_15()
                if isinstance(result, dict) and result.get("status") == "success":
                    status_str = "Success"
                    content_str = result.get("data", "")
                elif isinstance(result, dict) and result.get("status") == "error":
                    status_str = "Error"
                    content_str = result.get("message", "")
                else:
                    status_str = "Result"
                    content_str = result

                if not isinstance(content_str, str):
                    content_str = json.dumps(content_str)

                nonce_wrapped_content = f"{nonce} {status_str} {nonce} {content_str} {nonce}"

                wrapped = (
                    f"[data from {tool_name}: {args_str}]\n"
                    f"---BEGIN DATA---\n"
                    f"{nonce_wrapped_content}\n"
                    f"---END DATA---"
                )
                # --- NEW NONCE LOGIC END ---

                if toolcall_logger:
                    toolcall_logger.new_slice()
                    toolcall_logger.write_tool_result(f"[result: {nonce_wrapped_content}]\n")

                tool_msg = {
                    "role": "tool",
                    "content": wrapped,
                    "tool_name": tool_name,
                    "diff": result.get("diff") if isinstance(result, dict) else None,
                    "file": result.get("file") if isinstance(result, dict) else None,
                }
                _stamp_message(tool_msg)
                messages.append(tool_msg)
                if ndjson_log_file_handle:
                    from .logging import _log_ndjson_message
                    _log_ndjson_message(ndjson_log_file_handle, model, tool_msg)

            if verbose >= 2:
                total_chars = sum(
                    len(m.get("content", "") or "") for m in messages
                )
                print(
                    f"[round {tool_rounds + 1} complete: "
                    f"{len(messages)} messages, {total_chars} chars]",
                    file=sys.stderr,
                    flush=True,
                )

            tool_rounds += 1
        else:
            assistant_msg = {"role": "assistant", "content": response_content}
            if show_thinking and think_text.strip():
                assistant_msg["thinking"] = think_text
            _stamp_message(assistant_msg)
            messages.append(assistant_msg)
            if ndjson_log_file_handle:
                from .logging import _log_ndjson_message
                _log_ndjson_message(ndjson_log_file_handle, model, assistant_msg)
            final_response = response_content
            state_manager.transition_to(ExecutionState.IDLE)
            break

    if metrics is not None:
        metrics["rounds"] = list(turn_rounds)
        metrics["turn_rounds"] = len(turn_rounds)
        metrics["turn_eval_count"] = sum(r.get("eval_count") or 0 for r in turn_rounds)
        metrics["turn_eval_duration_ns"] = sum(r.get("eval_duration_ns") or 0 for r in turn_rounds)
        metrics["turn_prompt_eval_count"] = sum(r.get("prompt_eval_count") or 0 for r in turn_rounds)
        metrics["turn_prompt_eval_duration_ns"] = sum(r.get("prompt_eval_duration_ns") or 0 for r in turn_rounds)
        if turn_elapsed_started is not None:
            metrics["turn_elapsed_s"] = time.monotonic() - turn_elapsed_started

    return final_response


def to_ollama_tools(tools: List[Tool]) -> List[OllamaTool]:
    result = []
    for t in tools:
        params = t.parameters
        properties: dict[str, Any] = {}
        required = params.get("required", [])

        for pname, pinfo in params.get("properties", {}).items():
            prop = OllamaTool.Function.Parameters.Property(
                type=pinfo.get("type", "string"),
                description=pinfo.get("description", ""),
            )
            if "enum" in pinfo:
                prop.enum = pinfo["enum"]
            properties[pname] = prop
        ot = OllamaTool(
            type="function",
            function=OllamaTool.Function(
                name=t.name,
                description=t.description,
                parameters=OllamaTool.Function.Parameters(
                    type="object",
                    properties=properties,
                    required=required if required else None,
                ),
            ),
        )
        result.append(ot)
    return result
