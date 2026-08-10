"""Tests for the plan/build mode switch (opencode-style) in the chat REPL.

Covers the plan-mode system prompt, the /plan and /build commands, full tool
advertisement across both modes, the prompt indicator, and session persistence
of the active mode.
"""

import contextlib
import io
import os
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402
from tool_base import compose_system_prompt, load_tools  # noqa: E402


def _make_state(**kwargs):
    kwargs.setdefault("client", None)
    kwargs.setdefault("model", "m")
    return chat.ChatState(**kwargs)


def _tool_names(state):
    return [t.function.name for t in state.ollama_tools]


def _state_with_tools(modules):
    st = _make_state()
    tools = []
    for mod in modules:
        tools.extend(load_tools(mod))
    st.loaded_tools = tools
    st.loaded_tool_modules = list(modules)
    st.refresh_ollama_tools()
    return st


def _system_content(state):
    for m in state.messages:
        if m.get("role") == "system":
            return m.get("content", "")
    return None


def test_default_mode_is_build():
    assert _make_state().mode == "build"


def test_compose_system_prompt_plan_contains_block():
    sp = compose_system_prompt(mode="plan")
    assert "[PLAN MODE BEGIN]" in sp
    assert "[PLAN MODE END]" in sp
    assert "PLAN MODE" in sp


def test_compose_system_prompt_build_has_no_block():
    assert "[PLAN MODE" not in compose_system_prompt()
    assert "[PLAN MODE" not in compose_system_prompt(mode="build")


def test_compose_system_prompt_plan_order():
    sp = compose_system_prompt(
        system_prompt="BASE", skill_text="SKILL", mode="plan"
    )
    assert sp.index("BASE") < sp.index("[SKILL BEGIN]") < sp.index("[PLAN MODE BEGIN]")


def test_handle_plan_switches_mode_and_prompt(capsys):
    st = _make_state()
    st.messages = [{"role": "user", "content": "hi"}]
    chat._handle_command("/plan", st)
    assert st.mode == "plan"
    assert "[PLAN MODE BEGIN]" in _system_content(st)
    assert "Switched to plan mode." in capsys.readouterr().out


def test_handle_build_removes_plan_block(capsys):
    st = _make_state()
    st.messages = [{"role": "user", "content": "hi"}]
    chat._handle_command("/plan", st)
    chat._handle_command("/build", st)
    assert st.mode == "build"
    assert "[PLAN MODE BEGIN]" not in _system_content(st)


def test_handle_plan_idempotent(capsys):
    st = _make_state()
    chat._handle_command("/plan", st)
    chat._handle_command("/plan", st)
    assert "Already in plan mode." in capsys.readouterr().out


def test_plan_keeps_all_tools_advertised():
    st = _state_with_tools(["tools.example_tools", "tools.edit"])
    assert set(_tool_names(st)) >= {"read_file", "edit"}
    chat._set_mode(st, "plan")
    names = _tool_names(st)
    assert "read_file" in names
    assert "edit" in names
    chat._set_mode(st, "build")
    assert "edit" in _tool_names(st)


def test_plan_mode_keeps_write_only_tools_advertised():
    st = _state_with_tools(["tools.edit"])
    chat._set_mode(st, "plan")
    assert st.ollama_tools is not None
    assert "edit" in _tool_names(st)
    chat._set_mode(st, "build")
    assert "edit" in _tool_names(st)


def test_mode_label():
    st = _make_state()
    assert chat._mode_label(st, use_color=False) == "[build] "
    st.mode = "plan"
    assert chat._mode_label(st, use_color=False) == "[plan] "
    assert chat._mode_label(st, use_color=True) != "[plan] "
    assert chat._mode_label(st, use_color=True) != "[build] "


def test_commands_include_plan_build():
    assert "/plan" in chat._COMMANDS
    assert "/build" in chat._COMMANDS


def test_completion_plan_build():
    assert chat._completion_candidates("/pl") == ["/plan"]
    assert chat._completion_candidates("/bu") == ["/build"]


def test_mode_serialized_when_plan():
    st = _make_state()
    st.mode = "plan"
    data = chat.serialize_session(st)
    assert data["mode"] == "plan"


def test_mode_not_serialized_when_build():
    st = _make_state()
    assert "mode" not in chat.serialize_session(st)


def test_apply_session_restores_mode():
    st2 = _make_state()
    chat.apply_session(st2, {"mode": "plan"})
    assert st2.mode == "plan"


def test_apply_session_ignores_invalid_mode():
    st = _make_state()
    chat.apply_session(st, {"mode": "bogus"})
    assert st.mode == "build"


def test_resume_plan_session_keeps_all_tools():
    st = _state_with_tools(["tools.example_tools", "tools.edit"])
    st.mode = "plan"
    st.messages = [{"role": "user", "content": "hi"}]
    st.apply_skill()
    data = chat.serialize_session(st)

    st2 = _make_state()
    chat.apply_session(st2, data)
    assert st2.mode == "plan"
    names = _tool_names(st2)
    assert "read_file" in names
    assert "edit" in names


def test_switch_round_trip_preserves_mode():
    st = _make_state()
    st.messages = [{"role": "user", "content": "hi"}]
    chat._handle_command("/plan", st)
    data = chat.serialize_session(st)

    st2 = _make_state()
    chat.apply_session(st2, data)
    chat._handle_command("/build", st2)
    assert st2.mode == "build"
    assert "[PLAN MODE BEGIN]" not in _system_content(st2)
