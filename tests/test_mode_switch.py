"""Tests for mid-turn mode switching (Shift+Tab during a working turn).

Covers:
- the pure ``EscapeSequenceParser`` (Shift+Tab byte detection),
- the ``TypeAheadBuffer`` (replayable mid-turn typing),
- the engine's execution-time write-tool gate in plan mode,
- the mid-turn tool-list stability (write tools stay advertised, gated at run time),
- and ``ModeHotkeyListener`` no-op behavior when there is no tty.
"""

import os
import sys
from types import SimpleNamespace

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402
from tool_base import (  # noqa: E402
    Tool,
    EscapeSequenceParser,
    TypeAheadBuffer,
    ModeHotkeyListener,
    load_tools,
    run_with_tools,
)


# ---------------------------------------------------------------------------
# EscapeSequenceParser
# ---------------------------------------------------------------------------


def _feed_bytes(parser, seq):
    return [i for i, byte in enumerate(seq) if parser.feed(byte)]


def test_parser_shift_tab_byte_by_byte():
    p = EscapeSequenceParser()
    assert p.feed(0x1B) is False
    assert p.feed(0x5B) is False
    assert p.feed(0x5A) is True


def test_parser_shift_tab_full_sequence():
    p = EscapeSequenceParser()
    assert _feed_bytes(p, b"\x1b[Z") == [2]


def test_parser_ignores_other_escapes():
    for seq in (b"\x1b[A", b"\x1b[1;5C", b"\x1b"):
        p = EscapeSequenceParser()
        assert _feed_bytes(p, seq) == []


def test_parser_garbage_resets_and_clean_match_still_works():
    p = EscapeSequenceParser()
    p.feed(0x1B)
    p.feed(0x61)  # 'a' aborts the pending ESC sequence
    p.feed(0x5B)  # stray '[' from idle -> ignored
    assert p.feed(0x5A) is False
    p.reset()
    assert _feed_bytes(p, b"\x1b[Z") == [2]


def test_parser_back_to_back_shift_tabs():
    p = EscapeSequenceParser()
    assert _feed_bytes(p, b"\x1b[Z\x1b[Z") == [2, 5]


# ---------------------------------------------------------------------------
# TypeAheadBuffer
# ---------------------------------------------------------------------------


def test_typeahead_collects_and_drains_once():
    buf = TypeAheadBuffer()
    buf.feed(b"hello")
    assert buf.drain() == "hello"
    assert buf.drain() == ""


def test_typeahead_backspace_deletes():
    buf = TypeAheadBuffer()
    buf.feed(b"hel\x7flo")
    buf.feed(b"!!\x08")
    assert buf.drain() == "helo!"


def test_typeahead_drops_escape_and_control():
    buf = TypeAheadBuffer()
    buf.feed(b"a\x1b[Zb\nc\r\x1b[A")
    assert buf.drain() == "abc"


def test_typeahead_split_utf8_byte_by_byte():
    data = "h\u00e9llo".encode("utf-8")
    buf = TypeAheadBuffer()
    for i in range(len(data)):
        buf.feed(data[i : i + 1])
    assert buf.drain() == "h\u00e9llo"


# ---------------------------------------------------------------------------
# Engine gate + mid-turn tool-list refresh
# ---------------------------------------------------------------------------


def _chunk(content=None, tool_calls=None):
    return SimpleNamespace(
        message=SimpleNamespace(thinking=None, content=content, tool_calls=tool_calls)
    )


def _tool_call(name, arguments):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))


class FakeClient:
    def __init__(self, chunks_by_call, on_chat=None):
        self.chunks_by_call = list(chunks_by_call)
        self.calls = []
        self.on_chat = on_chat or (lambda kwargs: None)

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        self.on_chat(kwargs)
        if self.chunks_by_call:
            return iter(self.chunks_by_call.pop(0))
        return iter([_chunk(content="done")])


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


class MutableModeState:
    def __init__(self, mode="build", ollama_tools=None):
        self.mode = mode
        self.ollama_tools = ollama_tools


def _tools(called):
    write = Tool(
        name="write_thing",
        description="",
        parameters={},
        fn=lambda: called.append("write"),
        readonly=False,
    )
    read = Tool(
        name="read_thing",
        description="",
        parameters={},
        fn=lambda: called.append("read"),
        readonly=True,
    )
    return [write, read]


def _blocked_tool_messages(messages):
    return [
        m.get("content", "")
        for m in messages
        if m.get("role") == "tool" and "plan mode" in m.get("content", "")
    ]


def test_write_tool_blocked_in_plan_mode():
    called = []
    client = FakeClient(
        [
            [_chunk(tool_calls=[_tool_call("write_thing", {})])],
            [_chunk(content="done")],
        ]
    )
    messages = [{"role": "user", "content": "hi"}]
    result = run_with_tools(
        **_run_kwargs(
            client,
            messages,
            loaded_tools=_tools(called),
            mode_state=MutableModeState(mode="plan"),
        )
    )
    assert called == []
    assert result == "done"
    assert _blocked_tool_messages(messages)


def test_readonly_tool_runs_in_plan_mode():
    called = []
    client = FakeClient(
        [
            [_chunk(tool_calls=[_tool_call("read_thing", {})])],
            [_chunk(content="done")],
        ]
    )
    messages = [{"role": "user", "content": "hi"}]
    result = run_with_tools(
        **_run_kwargs(
            client,
            messages,
            loaded_tools=_tools(called),
            mode_state=MutableModeState(mode="plan"),
        )
    )
    assert called == ["read"]
    assert result == "done"


def test_write_tool_runs_in_build_mode():
    called = []
    client = FakeClient(
        [
            [_chunk(tool_calls=[_tool_call("write_thing", {})])],
            [_chunk(content="done")],
        ]
    )
    messages = [{"role": "user", "content": "hi"}]
    result = run_with_tools(
        **_run_kwargs(
            client,
            messages,
            loaded_tools=_tools(called),
            mode_state=MutableModeState(mode="build"),
        )
    )
    assert called == ["write"]
    assert result == "done"


def test_mid_turn_flip_blocks_later_writes_without_changing_tools():
    state = MutableModeState(mode="build", ollama_tools=["tools-v1"])
    called = []

    def write_fn():
        called.append("write")
        # Mid-turn toggle: the user hits Shift+Tab while this turn is in flight.
        state.mode = "plan"
        return {"status": "success", "data": "ok"}

    def read_fn():
        called.append("read")
        return {"status": "success", "data": "ok"}

    tools = [
        Tool(name="write_thing", description="", parameters={}, fn=write_fn, readonly=False),
        Tool(name="read_thing", description="", parameters={}, fn=read_fn, readonly=True),
    ]
    client = FakeClient(
        [
            [_chunk(tool_calls=[_tool_call("write_thing", {})])],
            [_chunk(tool_calls=[_tool_call("read_thing", {})])],
            [_chunk(tool_calls=[_tool_call("write_thing", {})])],
            [_chunk(content="done")],
        ]
    )
    messages = [{"role": "user", "content": "hi"}]
    result = run_with_tools(
        **_run_kwargs(
            client,
            messages,
            loaded_tools=tools,
            ollama_tools=["tools-v1"],
            mode_state=state,
        )
    )
    # Round 1 ran the write tool (build); after the flip the read-only tool
    # still runs but the write tool is blocked.
    assert called == ["write", "read"]
    assert result == "done"
    assert _blocked_tool_messages(messages)
    # The advertised tool list is unchanged after the flip; write tools are
    # gated at execution time instead of being unloaded from the model.
    assert client.calls[1]["tools"] == ["tools-v1"]


# ---------------------------------------------------------------------------
# ModeHotkeyListener: no-op without a tty
# ---------------------------------------------------------------------------


def test_listener_noop_without_tty(monkeypatch):
    monkeypatch.setattr(
        ModeHotkeyListener, "_check_available", staticmethod(lambda: False)
    )
    fired = []
    listener = ModeHotkeyListener(lambda: fired.append(1))
    listener.start()
    listener.pause()
    listener.resume()
    assert listener.drain_typeahead() == ""
    listener.stop()
    assert fired == []
    assert listener._available is False


# ---------------------------------------------------------------------------
# Chat integration
# ---------------------------------------------------------------------------


def _system_content(state):
    for m in state.messages:
        if m.get("role") == "system":
            return m.get("content", "")
    return None


def test_toggle_mode_round_trip():
    st = chat.ChatState(client=None, model="m")
    st.messages = [{"role": "user", "content": "hi"}]
    st.toggle_mode()
    assert st.mode == "plan"
    assert "[PLAN MODE BEGIN]" in _system_content(st)
    st.toggle_mode()
    assert st.mode == "build"
    assert "[PLAN MODE BEGIN]" not in _system_content(st)


def test_hotkey_helpers_without_listener():
    st = chat.ChatState(client=None, model="m")
    st.hotkey_pause()
    st.hotkey_resume()
    assert st.hotkey_drain() == ""
    st.stop_hotkey_listener()


def test_load_tools_tags_readonly():
    for t in load_tools("tools.example_tools"):
        assert t.readonly is True
    for t in load_tools("tools.edit"):
        assert t.readonly is False
