"""Tests for the /history and /cut REPL commands.

Covers:
- get_history_entries() numbering (M down to 1, system messages hidden).
- /history selection: default (no tool results), -t, first/last N, ranges.
- /cut N, /cut a..b and /cut undo, including system-message protection.
"""

import os
import sys

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402

SAMPLE = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi there"},
    {"role": "user", "content": "what is 2+2?"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"function": {"name": "calculate", "arguments": {"expression": "2+2"}}}
        ],
    },
    {"role": "tool", "content": "[data from calculate: ...]", "tool_name": "calculate"},
    {"role": "assistant", "content": "The answer is 4"},
]


def _state(messages=SAMPLE):
    return chat.ChatState(client=None, model="test", messages=list(messages))


def _history_lines(arg, messages=SAMPLE):
    state = _state(messages)
    chat._cmd_history(arg, state)
    return state


def test_get_history_entries_numbering():
    entries = _state().get_history_entries()
    assert [e["num"] for e in entries] == [6, 5, 4, 3, 2, 1]
    assert [e["type"] for e in entries] == [
        "user",
        "output",
        "user",
        "toolcall",
        "tool_result",
        "output",
    ]


def test_history_default_hides_tool_results(capsys):
    _history_lines("")
    out = capsys.readouterr().out
    assert "[6] USER: hello" in out
    assert "[5] ASSISTANT: hi there" in out
    assert "[3] ASSISTANT (TOOLCALL)" in out
    assert "[1] ASSISTANT: The answer is 4" in out
    assert "[2] TOOL:" not in out


def test_history_t_shows_tool_results(capsys):
    _history_lines("-t")
    out = capsys.readouterr().out
    assert "[2] TOOL:" in out


def test_history_default_shows_toolcall_details(capsys):
    _history_lines("")
    out = capsys.readouterr().out
    assert "[3] ASSISTANT (TOOLCALL) TOOL: [data from calculate: expression='2+2']" in out


def test_history_toolcall_multiple_calls(capsys):
    msgs = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "a.txt"}}},
                {"function": {"name": "calculate", "arguments": {"expression": "1+1"}}},
            ],
        },
        {"role": "tool", "content": "[data from read_file: ...]", "tool_name": "read_file"},
        {"role": "tool", "content": "[data from calculate: ...]", "tool_name": "calculate"},
    ]
    _history_lines("", messages=msgs)
    out = capsys.readouterr().out
    assert (
        "[3] ASSISTANT (TOOLCALL) TOOL: [data from read_file: path='a.txt'], "
        "[data from calculate: expression='1+1']" in out
    )


def test_history_timestamp_shown(capsys):
    msgs = [
        {"role": "user", "content": "hi", "timestamp": "2026-01-01 10:00:00"},
        {"role": "assistant", "content": "hello", "timestamp": "2026-01-01 10:00:01"},
    ]
    _history_lines("", messages=msgs)
    out = capsys.readouterr().out
    assert "[2] [2026-01-01 10:00:00] USER: hi" in out
    assert "[1] [2026-01-01 10:00:01] ASSISTANT: hello" in out


def test_history_timestamp_on_toolcall(capsys):
    msgs = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": None,
            "timestamp": "2026-01-01 10:00:05",
            "tool_calls": [
                {"function": {"name": "calculate", "arguments": {"expression": "2+2"}}}
            ],
        },
        {"role": "tool", "content": "[data from calculate: ...]", "tool_name": "calculate"},
    ]
    _history_lines("", messages=msgs)
    out = capsys.readouterr().out
    assert (
        "[2] [2026-01-01 10:00:05] ASSISTANT (TOOLCALL) TOOL: "
        "[data from calculate: expression='2+2']" in out
    )


def test_history_no_timestamp_omits_prefix(capsys):
    _history_lines("")
    out = capsys.readouterr().out
    # Backward compatibility: messages without a timestamp render without [ts].
    assert "[6] USER: hello" in out
    assert "[]" not in out


def test_stamp_message_idempotent():
    state = _state([])
    msg = {"role": "user", "content": "x"}
    state.stamp_message(msg)
    first = msg["timestamp"]
    state.stamp_message(msg)
    assert msg["timestamp"] == first


def test_run_with_tools_stamps_messages():
    from types import SimpleNamespace

    from tool_base import run_with_tools

    class FakeClient:
        def __init__(self, stream):
            self._stream = stream

        def chat(self, **kwargs):
            return self._stream

    def chunk(name, args):
        return SimpleNamespace(
            message=SimpleNamespace(
                thinking=None,
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        function=SimpleNamespace(name=name, arguments=args)
                    )
                ],
            )
        )

    messages = [{"role": "user", "content": "hi"}]
    run_with_tools(
        client=FakeClient(iter([chunk("calculate", {"expression": "2+2"})])),
        model="m",
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
    assert [m["role"] for m in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    # The pre-existing user message is stamped by the caller (ChatState), the
    # messages created inside run_with_tools carry an event timestamp.
    assert messages[0].get("timestamp")
    assert messages[2].get("timestamp")
    assert messages[3].get("timestamp")
    assert messages[4].get("timestamp")



def test_history_first_n(capsys):
    _history_lines("3")
    out = capsys.readouterr().out
    # First 3 entries are numbers 7, 6, 5; number 7 is the hidden system message.
    assert "[6] USER: hello" in out
    assert "[5] ASSISTANT: hi there" in out
    assert "[4] USER: what is 2+2?" not in out


def test_history_last_n(capsys):
    _history_lines("-t -3")
    out = capsys.readouterr().out
    assert "[1] ASSISTANT: The answer is 4" in out
    assert "[2] TOOL:" in out
    assert "[3] ASSISTANT (TOOLCALL)" in out
    assert "[4] USER: what is 2+2?" not in out


def test_history_range(capsys):
    _history_lines("4..5")
    out = capsys.readouterr().out
    assert "[5] ASSISTANT: hi there" in out
    assert "[4] USER: what is 2+2?" in out
    assert "[1] ASSISTANT: The answer is 4" not in out


def test_history_multiple_ranges(capsys):
    _history_lines("3 -2")
    out = capsys.readouterr().out
    assert "[6] USER: hello" in out
    assert "[5] ASSISTANT: hi there" in out
    assert "[1] ASSISTANT: The answer is 4" in out
    assert "[4] USER: what is 2+2?" not in out


def test_cut_last_n():
    state = _state()
    chat._cmd_cut("2", state)
    assert [m["content"] for m in state.messages] == [
        "sys",
        "hello",
        "hi there",
        "what is 2+2?",
        None,  # toolcall assistant
    ]


def test_cut_range():
    state = _state()
    chat._cmd_cut("3..5", state)
    assert [m["content"] for m in state.messages] == [
        "sys",
        "hello",
        "[data from calculate: ...]",
        "The answer is 4",
    ]


def test_cut_undo_restores_range(capsys):
    state = _state()
    chat._cmd_cut("3..5", state)
    chat._cmd_cut("undo", state)
    assert state.messages == SAMPLE
    assert "Cut undone." in capsys.readouterr().out


def test_cut_undo_nothing(capsys):
    state = _state()
    chat._cmd_cut("undo", state)
    assert state.messages == SAMPLE
    assert "Nothing to undo." in capsys.readouterr().out


def test_cut_keeps_system_message():
    state = _state()
    chat._cmd_cut("7", state)
    assert len(state.messages) == 1
    assert state.messages[0]["role"] == "system"
    # Undo restores the full conversation.
    assert state.undo_cut()
    assert state.messages == SAMPLE


def test_cut_undo_only_last_cut():
    state = _state()
    chat._cmd_cut("2", state)
    chat._cmd_cut("1", state)
    assert [m["content"] for m in state.messages] == [
        "sys",
        "hello",
        "hi there",
        "what is 2+2?",
    ]
    state.undo_cut()
    assert [m["content"] for m in state.messages] == [
        "sys",
        "hello",
        "hi there",
        "what is 2+2?",
        None,  # only the second cut is restored
    ]


def test_cut_invalid_argument(capsys):
    state = _state()
    chat._cmd_cut("abc", state)
    assert state.messages == SAMPLE
    assert "Invalid cut argument: abc" in capsys.readouterr().out


def test_cut_empty_history(capsys):
    state = _state([])
    chat._cmd_cut("5", state)
    assert "No history to cut." in capsys.readouterr().out


def test_cut_empty_argument(capsys):
    state = _state()
    chat._cmd_cut("", state)
    assert state.messages == SAMPLE
    assert "Usage:" in capsys.readouterr().out


def test_cut_beyond_history_cuts_all(capsys):
    state = _state()
    chat._cmd_cut("999", state)
    assert len(state.messages) == 1
    assert state.messages[0]["role"] == "system"
    assert "Cut 6 message(s)." in capsys.readouterr().out
    assert state.undo_cut()
    assert state.messages == SAMPLE
