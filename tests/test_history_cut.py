"""Tests for the /history and /cut REPL commands and the shared history model.

Covers:
- history_entries() numbering (1 = oldest, V = newest, system messages hidden).
- shared selector parsing: N, -N, a..b (incl. negative bounds), reversed/clamped.
- /history selection: default (no tool results), -t, first/last N, ranges, mixed.
- /cut N, /cut -N, /cut a..b and /cut undo: /cut trims to the named entries
  (N = from N to the end, -N = last N, a..b = only that span), with
  system-message protection and safe no-ops for out-of-range selections.
- LAMA_OLE_FORMAT line templates (bare / per-type / empty = hidden / view
  overrides) and /history + session replay rendering.
"""

import os
import sys

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402
import history as history_mod  # noqa: E402

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


@pytest.fixture(autouse=True)
def _clean_format_env(monkeypatch):
    # Keep LAMA_OLE_FORMAT* from the caller's environment out of every test.
    for name in ("LAMA_OLE_FORMAT", "LAMA_OLE_FORMAT_HISTORY", "LAMA_OLE_FORMAT_REPLAY"):
        monkeypatch.delenv(name, raising=False)


def _state(messages=SAMPLE, **kwargs):
    kwargs.setdefault("client", None)
    kwargs.setdefault("model", "test")
    kwargs.setdefault("messages", list(messages))
    return chat.ChatState(**kwargs)


def _history_lines(arg, state=None):
    state = state or _state()
    chat._cmd_history(arg, state)
    return state


def test_get_history_entries_numbering():
    entries = _state().get_history_entries()
    assert [e["num"] for e in entries] == [1, 2, 3, 4, 5, 6]
    assert [e["type"] for e in entries] == [
        "user",
        "output",
        "user",
        "toolcall",
        "tool_result",
        "output",
    ]
    # System message is excluded from the numbering entirely.
    assert all(e["msg"]["role"] != "system" for e in entries)


def test_history_default_hides_tool_results(capsys):
    _history_lines("")
    out = capsys.readouterr().out
    assert "[1] USER: hello" in out
    assert "[2] ASSISTANT: hi there" in out
    assert "[4] ASSISTANT (TOOLCALL)" in out
    assert "[6] ASSISTANT: The answer is 4" in out
    assert "[5] TOOL:" not in out


def test_history_t_shows_tool_results(capsys):
    _history_lines("-t")
    out = capsys.readouterr().out
    assert "[5] TOOL:" in out


def test_history_default_shows_toolcall_details(capsys):
    _history_lines("")
    out = capsys.readouterr().out
    assert "[4] ASSISTANT (TOOLCALL) TOOL: [data from calculate: expression='2+2']" in out


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
    _history_lines("", state=_state(msgs))
    out = capsys.readouterr().out
    assert (
        "[2] ASSISTANT (TOOLCALL) TOOL: [data from read_file: path='a.txt'], "
        "[data from calculate: expression='1+1']" in out
    )


def test_history_timestamp_shown(capsys):
    msgs = [
        {"role": "user", "content": "hi", "timestamp": "2026-01-01 10:00:00"},
        {"role": "assistant", "content": "hello", "timestamp": "2026-01-01 10:00:01"},
    ]
    _history_lines("", state=_state(msgs))
    out = capsys.readouterr().out
    assert "[1] [2026-01-01 10:00:00] USER: hi" in out
    assert "[2] [2026-01-01 10:00:01] ASSISTANT: hello" in out


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
    _history_lines("", state=_state(msgs))
    out = capsys.readouterr().out
    assert (
        "[2] [2026-01-01 10:00:05] ASSISTANT (TOOLCALL) TOOL: "
        "[data from calculate: expression='2+2']" in out
    )


def test_history_no_timestamp_omits_prefix(capsys):
    _history_lines("")
    out = capsys.readouterr().out
    # Backward compatibility: messages without a timestamp render without [ts].
    assert "[1] USER: hello" in out
    assert "[]" not in out


def test_stamp_message_idempotent():
    state = _state([])
    msg = {"role": "user", "content": "x"}
    state.stamp_message(msg)
    first = msg["timestamp"]
    state.stamp_message(msg)
    assert msg["timestamp"] == first


def test_history_first_n(capsys):
    _history_lines("3")
    out = capsys.readouterr().out
    # First 3 entries are numbers 1, 2, 3.
    assert "[1] USER: hello" in out
    assert "[2] ASSISTANT: hi there" in out
    assert "[3] USER: what is 2+2?" in out
    assert "[4] ASSISTANT (TOOLCALL)" not in out


def test_history_last_n(capsys):
    _history_lines("-t -3")
    out = capsys.readouterr().out
    # Last 3 entries are numbers 4, 5, 6.
    assert "[4] ASSISTANT (TOOLCALL)" in out
    assert "[5] TOOL:" in out
    assert "[6] ASSISTANT: The answer is 4" in out
    assert "[3] USER: what is 2+2?" not in out


def test_history_range(capsys):
    _history_lines("2..3")
    out = capsys.readouterr().out
    assert "[2] ASSISTANT: hi there" in out
    assert "[3] USER: what is 2+2?" in out
    assert "[6] ASSISTANT: The answer is 4" not in out


def test_history_range_negative_bounds(capsys):
    _history_lines("2..-2")
    out = capsys.readouterr().out
    # Entries 2 through second-to-last (5): tool result hidden by default.
    assert "[2] ASSISTANT: hi there" in out
    assert "[4] ASSISTANT (TOOLCALL)" in out
    assert "[1] USER: hello" not in out
    assert "[6] ASSISTANT: The answer is 4" not in out


def test_history_multiple_ranges(capsys):
    _history_lines("3 -2")
    out = capsys.readouterr().out
    # First 3 (1,2,3) plus last 2 (5,6); tool result 5 hidden by default.
    assert "[1] USER: hello" in out
    assert "[3] USER: what is 2+2?" in out
    assert "[6] ASSISTANT: The answer is 4" in out
    assert "[4] ASSISTANT (TOOLCALL)" not in out


def test_history_reversed_range_normalized(capsys):
    _history_lines("5..2")
    out = capsys.readouterr().out
    assert "[2] ASSISTANT: hi there" in out
    assert "[5] TOOL:" not in out  # hidden by default, but entry 5 is selected


def test_history_invalid_selectors_show_all(capsys):
    # Non-numeric tokens are ignored (legacy behavior): no valid selectors
    # means the full listing, respecting the -t filter.
    _history_lines("abc")
    out = capsys.readouterr().out
    assert "[1] USER: hello" in out
    assert "[6] ASSISTANT: The answer is 4" in out


# ---------------------------------------------------------------------------
# LAMA_OLE_FORMAT line templates (shared by /history + replay, per-view overrides)
# ---------------------------------------------------------------------------


def test_parse_line_formats_defaults():
    formats = history_mod.parse_line_formats()
    for t in ("user", "output", "thinking", "toolcall", "compacted"):
        assert formats[t] == "[{num}] {ts}{role}: {text}"
    # Tool results are hidden unless a template names them.
    assert formats["tool_result"] == ""


def test_parse_line_formats_bare_applies_to_visible_types(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "[{num}] {text}")
    formats = history_mod.parse_line_formats()
    for t in ("user", "output", "thinking", "toolcall", "compacted"):
        assert formats[t] == "[{num}] {text}"
    # A bare value does not unhide tool results.
    assert formats["tool_result"] == ""


def test_parse_line_formats_pairs_and_empty_hides(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "user=[{num}] You: {text};thinking=;bogus=ignored")
    formats = history_mod.parse_line_formats()
    assert formats["user"] == "[{num}] You: {text}"
    assert formats["thinking"] == ""
    assert formats["output"] == "[{num}] {ts}{role}: {text}"
    assert formats["compacted"] == "[{num}] {ts}{role}: {text}"


def test_parse_line_formats_whitespace_spec(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "  ")
    assert history_mod.parse_line_formats() == history_mod.parse_line_formats()
    for t in ("user", "output", "thinking", "toolcall", "compacted"):
        assert history_mod.parse_line_formats()[t] == "[{num}] {ts}{role}: {text}"


def test_parse_line_formats_view_override_merges(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "user=[{num}] Base: {text}")
    monkeypatch.setenv("LAMA_OLE_FORMAT_REPLAY", "user=[{num}] Replay: {text}")
    base = history_mod.parse_line_formats()
    replay = history_mod.parse_line_formats("replay")
    # The replay override only changes the types it names.
    assert base["user"] == "[{num}] Base: {text}"
    assert replay["user"] == "[{num}] Replay: {text}"
    assert base["output"] == replay["output"] == "[{num}] {ts}{role}: {text}"
    # /history stays on the base when only the replay var is set.
    assert history_mod.parse_line_formats("history")["user"] == "[{num}] Base: {text}"


def test_with_tool_results_unhides_only_tool_result(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "[{num}] {text}")
    formats = history_mod.with_tool_results(history_mod.parse_line_formats())
    # A bare shared template is inherited, so -t keeps tool results in style.
    assert formats["tool_result"] == "[{num}] {text}"
    assert formats["user"] == "[{num}] {text}"
    # Already-visible tool results are left alone.
    formats = history_mod.with_tool_results(history_mod.parse_line_formats())
    assert history_mod.with_tool_results(formats)["tool_result"] == "[{num}] {text}"


def test_with_tool_results_default_style_without_bare(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "tool_result=")
    formats = history_mod.with_tool_results(history_mod.parse_line_formats())
    # No bare value -> hidden tool results fall back to the default template.
    assert formats["tool_result"] == "[{num}] {ts}{role}: {text}"


def test_history_template_hides_types(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "thinking=;toolcall=")
    chat._cmd_history("", _state())
    out = capsys.readouterr().out
    assert "[1] USER: hello" in out
    assert "[2] ASSISTANT: hi there" in out
    assert "[6] ASSISTANT: The answer is 4" in out
    assert "(TOOLCALL)" not in out
    assert "TOOL:" not in out


def test_history_template_without_timestamp_token(monkeypatch, capsys):
    msgs = [
        {"role": "user", "content": "hi", "timestamp": "2026-01-01 10:00:00"},
        {"role": "assistant", "content": "hello"},
    ]
    monkeypatch.setenv("LAMA_OLE_FORMAT", "[{num}] {role}: {text}")
    chat._cmd_history("", _state(messages=msgs))
    out = capsys.readouterr().out
    assert "USER: hi" in out
    assert "[2026-01-01 10:00:00]" not in out
    assert "[]" not in out


def test_history_t_forces_tool_results_over_explicit_hide(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "tool_result=")
    chat._cmd_history("-t", _state())
    out = capsys.readouterr().out
    assert "[5] TOOL: [data from calculate: ...]" in out


def test_history_template_custom_name(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "user=[{num}] You: {text};assistant=[{num}] Bot: {text}")
    chat._cmd_history("", _state())
    out = capsys.readouterr().out
    assert "[1] You: hello" in out
    assert "[2] Bot: hi there" in out
    assert "USER:" not in out
    assert "ASSISTANT:" not in out


def test_history_name_spec_custom_role_names(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "name.user=You;name.assistant=Bot")
    chat._cmd_history("", _state())
    out = capsys.readouterr().out
    assert "[1] You: hello" in out
    assert "[2] Bot: hi there" in out
    assert "[6] Bot: The answer is 4" in out
    assert "USER:" not in out
    assert "ASSISTANT:" not in out


def test_history_name_spec_composes_toolcall_and_tool(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "name.toolcall=Agent;name.tool=HANDLER")
    chat._cmd_history("", _state())
    out = capsys.readouterr().out
    assert "[4] Agent HANDLER: [data from calculate: expression='2+2']" in out


def test_history_name_spec_compacted(monkeypatch, capsys):
    state = _state(messages=[
        {"role": "user", "content": "SUMMARY", "compacted": True},
        {"role": "user", "content": "recent"},
    ])
    monkeypatch.setenv("LAMA_OLE_FORMAT", "name.compacted=SUMMARY")
    chat._cmd_history("", state)
    out = capsys.readouterr().out
    assert "[1] SUMMARY: SUMMARY" in out
    assert "[2] USER: recent" in out


def test_replay_name_spec_view_override(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "name.user=You")
    monkeypatch.setenv("LAMA_OLE_FORMAT_REPLAY", "name.user=Du")
    state = _state(messages=[{"role": "user", "content": "hi"}])
    chat._cmd_history("", state)
    assert "[1] You: hi" in capsys.readouterr().out
    chat._replay_history(state, use_color=False)
    assert "[1] Du: hi" in capsys.readouterr().out


def test_history_template_tool_tokens(monkeypatch, capsys):
    monkeypatch.setenv(
        "LAMA_OLE_FORMAT",
        "toolcall=[{num}] [tool: {tool}({args})];tool_result=[{num}] [tool result: {tool}]",
    )
    chat._cmd_history("-t", _state())
    out = capsys.readouterr().out
    assert "[4] [tool: calculate(expression='2+2')]" in out
    assert "[5] [tool result: calculate]" in out


def test_history_invalid_template_falls_back(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "user={bogus}")
    chat._cmd_history("", _state())
    out, err = capsys.readouterr()
    # Invalid user template falls back to the default rendering.
    assert "[1] USER: hello" in out
    assert "Warning: invalid history template" in err


def test_history_invalid_template_attribute_path_falls_back(monkeypatch, capsys):
    # Attribute paths raise AttributeError in format_map, not KeyError.
    monkeypatch.setenv("LAMA_OLE_FORMAT", "user={text.__nonexistent}")
    chat._cmd_history("", _state())
    out, err = capsys.readouterr()
    assert "[1] USER: hello" in out
    assert "Warning: invalid history template" in err


def test_history_empty_user_template_skips(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "user=")
    chat._cmd_history("", _state())
    out = capsys.readouterr().out
    assert "[1] USER: hello" not in out
    assert "[2] ASSISTANT: hi there" in out


def test_history_old_style_look(monkeypatch, capsys):
    monkeypatch.setenv(
        "LAMA_OLE_FORMAT",
        "user=[{num}] >>> {text};output=[{num}] {text};"
        "toolcall=[{num}] [tool: {tool}({args})];tool_result=[{num}] [tool result: {tool}]",
    )
    chat._cmd_history("-t", _state())
    out = capsys.readouterr().out
    assert "[1] >>> hello" in out
    assert "[2] hi there" in out
    assert "[4] [tool: calculate(expression='2+2')]" in out
    assert "[5] [tool result: calculate]" in out
    assert "USER:" not in out


def test_replay_view_independent_of_history(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT_REPLAY", "user=[{num}] >>> {text}")
    state = _state()
    chat._cmd_history("", state)
    history_out = capsys.readouterr().out
    chat._replay_history(state, use_color=False)
    replay_out = capsys.readouterr().out
    assert "[1] USER: hello" in history_out
    assert "[1] >>> hello" in replay_out
    assert "[2] ASSISTANT: hi there" in replay_out  # inherits the base


# ---------------------------------------------------------------------------
# Shared selector parsing
# ---------------------------------------------------------------------------


def test_parse_selectors_forms():
    assert history_mod.parse_selectors("") == []
    assert history_mod.parse_selectors("3") == [(1, 3)]
    assert history_mod.parse_selectors("-3") == [(-3, None)]
    assert history_mod.parse_selectors("2..-2") == [(2, -2)]
    assert history_mod.parse_selectors("1 4..5 -2") == [(1, 1), (4, 5), (-2, None)]
    assert history_mod.parse_selectors("abc 0 5..") == []


def test_parse_cut_selectors_forms():
    parse_cut = history_mod.parse_cut_selectors
    # /cut keeps what it names: bare N means "from N to the end".
    assert parse_cut("") == []
    assert parse_cut("5") == [(5, None)]
    assert parse_cut("-5") == [(-5, None)]
    assert parse_cut("2..-2") == [(2, -2)]
    assert parse_cut("1 4..5 -2") == [(1, None), (4, 5), (-2, None)]
    assert parse_cut("abc 0 5..") == []


def test_resolve_selectors():
    resolve = history_mod.resolve_selectors
    assert resolve([(1, 3)], 6) == [(1, 3)]
    assert resolve([(-3, None)], 6) == [(4, 6)]
    assert resolve([(2, -2)], 6) == [(2, 5)]
    assert resolve([(5, 2)], 6) == [(2, 5)]
    assert resolve([(1, -1)], 6) == [(1, 6)]
    assert resolve([(1, 999)], 6) == [(1, 6)]
    assert resolve([(-999, None)], 6) == [(1, 6)]
    assert resolve([(3, 3)], 2) == [(2, 2)]  # clamped
    # /cut's "from N to the end" form.
    assert resolve([(5, None)], 6) == [(5, 6)]
    assert resolve([(1, None)], 6) == [(1, 6)]
    assert resolve([(9, None)], 6) == []  # past the end -> empty (safe no-op)


def test_select_message_indices():
    entries = _state().get_history_entries()
    assert history_mod.select_message_indices(entries, history_mod.parse_selectors("2..-2")) == [2, 3, 4, 5]
    assert history_mod.select_message_indices(entries, history_mod.parse_selectors("-1")) == [6]


# ---------------------------------------------------------------------------
# /cut
# ---------------------------------------------------------------------------


def _cut_contents(arg, state=None):
    state = state or _state()
    chat._cmd_cut(arg, state)
    return [m["content"] for m in state.messages]


def test_cut_keep_from_n():
    # Entries 2..6 are kept; entry 1 ("hello") is removed.
    assert _cut_contents("2") == [
        "sys",
        "hi there",
        "what is 2+2?",
        None,  # toolcall assistant
        "[data from calculate: ...]",
        "The answer is 4",
    ]


def test_cut_keep_last_n():
    # Only the newest entry survives; the older 1..5 are removed.
    assert _cut_contents("-1") == ["sys", "The answer is 4"]


def test_cut_range_mixed_bounds():
    # Entries 3..5 are kept (user "what is 2+2?", toolcall, tool result).
    assert _cut_contents("3..-2") == [
        "sys",
        "what is 2+2?",
        None,
        "[data from calculate: ...]",
    ]


def test_cut_reversed_range_normalized():
    # 5..2 normalizes to 2..5: entries 2..5 kept, 1 and 6 removed.
    assert _cut_contents("5..2") == [
        "sys",
        "hi there",
        "what is 2+2?",
        None,
        "[data from calculate: ...]",
    ]


def test_cut_one_keeps_everything(capsys):
    # /cut 1 means "from entry 1 to the end" = keep all -> no-op.
    state = _state()
    chat._cmd_cut("1", state)
    assert state.messages == SAMPLE
    assert "Nothing to cut." in capsys.readouterr().out


def test_cut_single_entry_range():
    # /cut 6..6 keeps only the newest entry.
    assert _cut_contents("6..6") == ["sys", "The answer is 4"]


def test_cut_out_of_range_is_noop(capsys):
    # Keeping from entry 999 onward selects nothing -> safe no-op.
    state = _state()
    chat._cmd_cut("999", state)
    assert state.messages == SAMPLE
    assert "Nothing to cut." in capsys.readouterr().out


def test_cut_undo_restores_range(capsys):
    state = _state()
    chat._cmd_cut("3..-2", state)
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
    chat._cmd_cut("2", state)
    assert state.messages[0]["role"] == "system"
    assert [m["content"] for m in state.messages] == [
        "sys",
        "hi there",
        "what is 2+2?",
        None,
        "[data from calculate: ...]",
        "The answer is 4",
    ]
    # Undo restores the full conversation.
    assert state.undo_cut()
    assert state.messages == SAMPLE


def test_cut_undo_only_last_cut():
    state = _state()
    chat._cmd_cut("3", state)
    chat._cmd_cut("2", state)
    assert [m["content"] for m in state.messages] == [
        "sys",
        None,
        "[data from calculate: ...]",
        "The answer is 4",
    ]
    state.undo_cut()
    # Only the second cut is restored.
    assert [m["content"] for m in state.messages] == [
        "sys",
        "what is 2+2?",
        None,
        "[data from calculate: ...]",
        "The answer is 4",
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


# ---------------------------------------------------------------------------
# rendering: colors, replay parity, per-view independence
# ---------------------------------------------------------------------------


def test_history_labels_and_color_when_enabled(capsys):
    state = _state(color="always")
    chat._cmd_history("", state)
    out = capsys.readouterr().out
    assert "[1] USER: hello" in out
    assert "\x01\x1b[" in out


def test_history_no_color_when_disabled(capsys):
    state = _state(color="never")
    chat._cmd_history("", state)
    out = capsys.readouterr().out
    assert "[1] USER: hello" in out
    assert "\x1b[" not in out and "\x01\x1b[" not in out


def test_history_template_without_role_has_no_labels(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "[{num}] {ts}{text}")
    state = _state(color="always")
    chat._cmd_history("", state)
    out = capsys.readouterr().out
    assert "[1] hello" in out
    assert "USER:" not in out
    assert "ASSISTANT:" not in out
    assert "\x01\x1b[" in out


def test_replay_labels_by_default(capsys):
    state = _state(messages=[
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ])
    chat._replay_history(state, use_color=False)
    out = capsys.readouterr().out
    assert "[1] USER: hi" in out
    assert "[2] ASSISTANT: hello there" in out
    assert ">>>" not in out


def test_replay_matches_history(capsys):
    state = _state(messages=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ])
    chat._replay_history(state, use_color=False)
    out = capsys.readouterr().out
    assert "[1] USER: hi" in out
    assert "[2] ASSISTANT: hello there" in out
    assert ">>>" not in out


def test_replay_identical_to_history(capsys):
    # Replay is a /history listing: same numbered, labeled, filtered output.
    state = _state()
    chat._cmd_history("", state)
    history_out = capsys.readouterr().out
    chat._replay_history(state, use_color=False)
    replay_out = capsys.readouterr().out
    assert replay_out == history_out
    assert "[1] USER: hello" in replay_out


def test_replay_respects_shared_template(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_FORMAT", "thinking=;toolcall=")
    state = _state()
    chat._replay_history(state, use_color=False)
    out = capsys.readouterr().out
    assert "hi there" in out
    assert "The answer is 4" in out
    assert "TOOLCALL" not in out
    assert "[data from calculate" not in out


def test_replay_custom_names_via_template(monkeypatch, capsys):
    monkeypatch.setenv(
        "LAMA_OLE_FORMAT", "user=[{num}] You: {text};assistant=[{num}] Bot: {text}"
    )
    state = _state(messages=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    chat._replay_history(state, use_color=False)
    out = capsys.readouterr().out
    assert "[1] You: hi" in out
    assert "[2] Bot: hello" in out


def test_stamp_turn_messages_stamps_all_new_messages():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    state = _state(messages=msgs)
    for m in state.messages:
        assert "timestamp" not in m
    chat.stamp_turn_messages(state, 0)
    for m in state.messages:
        assert "timestamp" in m


def test_stamp_turn_messages_respects_start():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    state = _state(messages=msgs)
    chat.stamp_turn_messages(state, 1)
    assert "timestamp" not in state.messages[0]
    assert "timestamp" in state.messages[1]


def test_replay_hides_diff_when_tool_result_hidden(monkeypatch, capsys):
    state = _state(show_diff=True, messages=[
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "[data from edit_file]", "tool_name": "edit_file", "diff": "--- a\n+++ b\n+hi"},
    ])
    monkeypatch.setenv("LAMA_OLE_FORMAT", "tool_result=")
    chat._replay_history(state, use_color=False)
    out = capsys.readouterr().out
    assert "TOOL:" not in out
    assert "--- a" not in out


def test_replay_shows_diff_when_tool_result_visible(monkeypatch, capsys):
    state = _state(show_diff=True, messages=[
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "[data from edit_file]", "tool_name": "edit_file", "diff": "--- a\n+++ b\n+hi"},
    ])
    monkeypatch.setenv("LAMA_OLE_FORMAT", "tool_result=[{num}] {role}: {text}")
    chat._replay_history(state, use_color=False)
    out = capsys.readouterr().out
    assert "TOOL: [data from edit_file]" in out
    assert "--- a" in out
