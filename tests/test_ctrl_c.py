"""Tests for Ctrl-C / KeyboardInterrupt handling.

Covers:
- run_with_tools() re-raises ExecutionInterrupted carrying the busy state.
- run_with_tools() uses the caller's StateManager when one is passed.
- run_chat() survives an interrupt during a turn and rolls back messages.
- main() chat mode with initial content falls back to the REPL on interrupt.
"""

import importlib.util
import os
import sys
from types import SimpleNamespace

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402
from tool_base import StateManager, run_with_tools  # noqa: E402
from tool_base.loop_states import (  # noqa: E402
    ExecutionInterrupted,
    ExecutionState,
)

# Load the CLI module by path (same pattern as test_skills.py).
_spec = importlib.util.spec_from_file_location(
    "lama_ole_cli", os.path.join(lama_ole_dir, "lama_ole.py")
)
lama_ole_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lama_ole_cli)


def _chunk(content=None, thinking=None, tool_calls=None):
    return SimpleNamespace(
        message=SimpleNamespace(
            thinking=thinking, content=content, tool_calls=tool_calls
        )
    )


def _normal_stream(content="reply"):
    return iter([_chunk(content=content)])


def _interrupting_stream():
    yield _chunk(content="partial")
    raise KeyboardInterrupt()


class FakeClient:
    def __init__(self, stream=None, content="reply"):
        self.calls = []
        self._stream = stream if stream is not None else _normal_stream(content)

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self._stream


def _run_kwargs(client, messages, **extra):
    kwargs = dict(
        client=client,
        model="test",
        messages=messages,
        loaded_tools=[],
        ollama_tools=None,
        options={},
        keep_alive=None,
        show_thinking=False,
        no_safety_system_prompt=True,
        system_prompt=None,
        skill_text=None,
        color="never",
    )
    kwargs.update(extra)
    return kwargs


class RecordingStateManager(StateManager):
    def __init__(self):
        super().__init__()
        self.seen = []

    def transition_to(self, new_state):
        self.seen.append(new_state)
        super().transition_to(new_state)


def test_run_with_tools_reraises_execution_interrupted():
    messages = [{"role": "user", "content": "hi"}]
    client = FakeClient(stream=_interrupting_stream())
    with pytest.raises(ExecutionInterrupted) as ei:
        run_with_tools(**_run_kwargs(client, messages))
    assert ei.value.state == ExecutionState.OUTPUTTING


def test_run_with_tools_interrupt_is_keyboard_interrupt():
    messages = [{"role": "user", "content": "hi"}]
    client = FakeClient(stream=_interrupting_stream())
    with pytest.raises(KeyboardInterrupt):
        run_with_tools(**_run_kwargs(client, messages))


def test_run_with_tools_uses_caller_state_manager():
    sm = RecordingStateManager()
    messages = [{"role": "user", "content": "hi"}]
    result = run_with_tools(
        **_run_kwargs(FakeClient(), messages, state_manager=sm)
    )
    assert result == "reply"
    assert ExecutionState.OUTPUTTING in sm.seen
    assert sm.current_state == ExecutionState.IDLE


def test_thinking_stored_when_show_thinking():
    chunks = [
        _chunk(thinking="First part "),
        _chunk(thinking="second part"),
        _chunk(content="final answer"),
    ]
    client = FakeClient(stream=iter(chunks))
    messages = [{"role": "user", "content": "hi"}]
    run_with_tools(**_run_kwargs(client, messages, show_thinking=True))
    assistant = [m for m in messages if m.get("role") == "assistant"][-1]
    assert assistant["thinking"] == "First part second part"
    sent = client.calls[0]["messages"]
    assert all("thinking" not in m for m in sent)


def test_thinking_not_stored_when_hidden():
    chunks = [_chunk(thinking="secret"), _chunk(content="answer")]
    client = FakeClient(stream=iter(chunks))
    messages = [{"role": "user", "content": "hi"}]
    run_with_tools(**_run_kwargs(client, messages, show_thinking=False))
    assistant = [m for m in messages if m.get("role") == "assistant"][-1]
    assert "thinking" not in assistant


def test_run_chat_interrupt_during_turn_rolls_back_and_continues(
    monkeypatch, capsys
):
    state = chat.ChatState(
        client=FakeClient(stream=_interrupting_stream()), model="test", color="never"
    )
    sequence = ["hello", KeyboardInterrupt(), EOFError()]

    def fake_input(prompt):
        item = sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("builtins.input", fake_input)
    chat.run_chat(state)

    assert state.messages == []
    err = capsys.readouterr().err
    assert "outputting" in err


def test_main_initial_content_interrupt_falls_back_to_repl(monkeypatch, capsys):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def chat(self, **kwargs):
            raise KeyboardInterrupt()

    calls = {"run_chat": 0}
    seen = {"state": None}

    def fake_run_chat(state):
        calls["run_chat"] += 1
        seen["state"] = state

    monkeypatch.setattr(lama_ole_cli, "Client", FakeClient)
    monkeypatch.setattr(lama_ole_cli, "run_chat", fake_run_chat)
    monkeypatch.setattr(lama_ole_cli, "load_env_files", lambda: None)
    for key in (
        "LAMA_OLE_TOOL",
        "LAMA_OLE_SKILL",
        "LAMA_OLE_VISION_MODEL",
        "LAMA_OLE_MODEL",
        "LAMA_OLE_COLOR",
        "LAMA_OLE_CHAT",
        "LAMA_OLE_THINKING",
        "LAMA_OLE_MAX_TOOL_ROUNDS",
        "LAMA_OLE_MAX_TOOL_ROUNDS_CONTINUATION",
        "LAMA_OLE_VERBOSE",
        "LAMA_OLE_TEMPERATURE",
        "LAMA_OLE_NUM_CTX",
        "LAMA_OLE_NUM_GPU",
        "LAMA_OLE_KEEP_ALIVE",
        "LAMA_OLE_SAFE",
        "LAMA_OLE_OLLAMA_WEBSRCH",
        "LAMA_OLE_SYSTEM_PROMPT",
        "LAMA_OLE_SYSTEM_PROMPT_FILE",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lama_ole.py",
            "--chat",
            "--no-resume",
            "-m",
            "testmodel",
            "-i",
            "hello",
            "--color",
            "never",
            "--no_safety_system_prompt",
        ],
    )

    lama_ole_cli.main()

    assert calls["run_chat"] == 1
    assert seen["state"].messages == []
    assert "Entering chat mode" in capsys.readouterr().err
