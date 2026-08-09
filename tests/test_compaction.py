"""Tests for context-window compaction.

Covers the pure logic in ``tool_base/compaction.py`` (serialization, head/tail
selection, prompt building, message rewriting) plus the chat-layer wiring
(auto-compaction threshold, session round-trip, replay rendering).
"""

import os
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402
from tool_base.compaction import (  # noqa: E402
    COMPACTION_SYSTEM_PROMPT,
    DEFAULT_CTX_COMPACT_THRESHOLD,
    SUMMARY_TEMPLATE,
    apply_compaction,
    build_summary_prompt,
    default_preserve_budget,
    estimate_tokens,
    find_previous_summary,
    sanitize_ctx_threshold,
    select_head_tail,
    serialize_for_compaction,
)
from tool_base.compaction import _parse_tool_message  # noqa: E402


def _user(content):
    return {"role": "user", "content": content}


def _assistant(content):
    return {"role": "assistant", "content": content}


def _tool(content, tool_name="read_file", status=None, text=""):
    if content is None:
        nonce = "a" * 15
        status_word = {"success": "Success", "error": "Error", "result": "Result"}.get(
            status or "result", "Result"
        )
        content = (
            f"[data from {tool_name}: path=x]\n"
            f"---BEGIN DATA---\n"
            f"{nonce} {status_word} {nonce} {text} {nonce}\n"
            f"---END DATA---"
        )
    return {"role": "tool", "content": content, "tool_name": tool_name}


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2


def test_default_preserve_budget_clamps():
    assert default_preserve_budget(None) == 8000
    # tiny window: clamped to the minimum
    assert default_preserve_budget(4096) == 2000
    # 25% of usable lands in the middle of the clamp range
    # usable = 32768 - 8192 = 24576; 25% = 6144
    assert default_preserve_budget(32768) == 6144
    # huge window: clamped to the maximum
    assert default_preserve_budget(262144) == 8000


def test_serialize_roles():
    messages = [
        {"role": "system", "content": "safety prompt"},
        _user("hello world"),
        _assistant("hi there"),
        _user("what files?") ,
    ]
    text = serialize_for_compaction(messages)
    assert "[User]: hello world" in text
    assert "[Assistant]: hi there" in text
    assert "[User]: what files?" in text
    # system prompt is skipped (re-injected by the engine)
    assert "safety prompt" not in text


def test_serialize_thinking_and_tool_calls():
    messages = [
        _user("go"),
        {
            "role": "assistant",
            "content": "doing it",
            "thinking": "let me think",
            "tool_calls": [
                {
                    "function": {
                        "name": "calculate",
                        "arguments": {"a": 1, "b": 2},
                    }
                }
            ],
        },
    ]
    text = serialize_for_compaction(messages)
    assert "[Assistant tool call]: calculate({\"a\": 1, \"b\": 2})" in text
    assert "[Assistant reasoning]: let me think" in text
    assert "[Assistant]: doing it" not in text


def test_serialize_tool_result_strips_nonce_wrapper():
    wrapped = _tool(None, status="success", text="file contents here")
    text = serialize_for_compaction([_user("read"), wrapped])
    assert "[Tool result]: file contents here" in text
    # no nonce, wrapper or BEGIN/END markers leak into the summary input
    assert "aaaaaaaaaaaaaaa" not in text
    assert "---BEGIN DATA---" not in text
    assert "[data from" not in text


def test_serialize_tool_error():
    wrapped = _tool(None, status="error", text="permission denied")
    text = serialize_for_compaction([wrapped])
    assert "[Tool error]: permission denied" in text


def test_serialize_truncates_long_tool_result():
    long_text = "x" * 5000
    wrapped = _tool(None, status="success", text=long_text)
    text = serialize_for_compaction([wrapped])
    assert len(text) < 2500
    assert "[truncated]" in text


def test_parse_tool_message_fallback():
    status, text = _parse_tool_message({"role": "tool", "content": "plain"})
    assert status == "result"
    assert text == "plain"


def test_select_head_tail_keeps_recent_turns():
    messages = [
        _user("A" * 400),
        _assistant("resp1"),
        _user("B" * 400),
        _assistant("resp2"),
        _user("C"),
        _assistant("resp3"),
    ]
    head, tail = select_head_tail(messages, tail_turns=2)
    # everything fits in the default budget, so only the turn count matters
    assert head == messages[:2]
    assert tail == messages[2:]


def test_select_head_tail_no_user_messages():
    messages = [_assistant("stray"), {"role": "tool", "content": "t"}]
    head, tail = select_head_tail(messages)
    assert head == messages
    assert tail == []


def test_select_head_tail_budget_bound():
    # last two turns total ~1102 tokens; a 1050 budget keeps only turn 2
    messages = [
        _user("A" * 400),
        _assistant("resp1"),
        _user("B" * 4000),
        _assistant("resp2"),
    ]
    head, tail = select_head_tail(messages, tail_turns=2, budget=1050)
    assert head == messages[:2]
    assert tail == messages[2:]
    assert tail[0]["role"] == "user"


def test_select_head_tail_tiny_budget_keeps_one_turn():
    messages = [
        _user("A" * 4000),
        _assistant("resp1"),
        _user("B"),
        _assistant("resp2"),
    ]
    head, tail = select_head_tail(messages, tail_turns=2, budget=10)
    # atomic minimum: the final turn survives verbatim
    assert tail == messages[2:]
    assert tail[0]["role"] == "user"


def test_select_head_tail_excludes_system():
    messages = [
        {"role": "system", "content": "safety"},
        _user("one"),
        _assistant("r1"),
        _user("two"),
        _assistant("r2"),
    ]
    head, tail = select_head_tail(messages, tail_turns=1)
    assert head == messages[1:3]
    assert tail == messages[3:]


def test_find_previous_summary():
    messages = [
        _user("old"),
        {"role": "user", "content": "FIRST SUMMARY", "compacted": True, "summary_at": 1},
        _user("recent"),
    ]
    assert find_previous_summary(messages) == "FIRST SUMMARY"
    assert find_previous_summary([_user("plain")]) is None


def test_build_summary_prompt_one_shot():
    prompt = build_summary_prompt(None, "history text")
    assert prompt.startswith("Create a new anchored summary from the conversation history.")
    assert SUMMARY_TEMPLATE in prompt
    assert "The following is the conversation history:\nhistory text" in prompt
    assert "<previous-summary>" not in prompt


def test_build_summary_prompt_anchored():
    prompt = build_summary_prompt("OLD SUMMARY", "new history")
    assert "Update the anchored summary below" in prompt
    assert "<previous-summary>\nOLD SUMMARY\n</previous-summary>" in prompt
    assert "Create a new anchored summary" not in prompt


def test_apply_compaction_structure():
    system = [{"role": "system", "content": "safety"}]
    tail = [_user("recent")]
    result = apply_compaction(system, "THE SUMMARY", tail)
    assert result[0] == system[0]
    assert result[1]["role"] == "user"
    assert result[1]["content"] == "THE SUMMARY"
    assert result[1]["compacted"] is True
    assert result[2] == tail[0]


def test_apply_compaction_empty_summary_fallback():
    result = apply_compaction([], "   ", [])
    assert result[0]["content"] == "[compacted context]"


def _state(client=None, **kwargs):
    st = chat.ChatState(client=client, model=kwargs.pop("model", "test:model"))
    for key, value in kwargs.items():
        setattr(st, key, value)
    return st


def test_should_auto_compact_threshold():
    st = _state()
    st.ctx_compact = True
    st.ctx_max = 10000
    st.ctx_usage = {"prompt_eval_count": 8000, "eval_count": 500}
    assert chat._should_auto_compact(st) is True
    st.ctx_usage = {"prompt_eval_count": 6900, "eval_count": 500}
    assert chat._should_auto_compact(st) is False


def test_should_auto_compact_disabled_or_unknown():
    st = _state()
    st.ctx_compact = False
    st.ctx_max = 10000
    st.ctx_usage = {"prompt_eval_count": 9000, "eval_count": 500}
    assert chat._should_auto_compact(st) is False
    st.ctx_compact = True
    st.ctx_max = None
    assert chat._should_auto_compact(st) is False


def test_sanitize_ctx_threshold_accepts_valid():
    assert sanitize_ctx_threshold(0.01) == 0.01
    assert sanitize_ctx_threshold(1.0) == 1.0
    assert sanitize_ctx_threshold("0.5") == 0.5
    assert sanitize_ctx_threshold(0.75) == 0.75


def test_sanitize_ctx_threshold_rejects_invalid(capsys):
    assert sanitize_ctx_threshold(0) == DEFAULT_CTX_COMPACT_THRESHOLD
    assert sanitize_ctx_threshold(-0.5) == DEFAULT_CTX_COMPACT_THRESHOLD
    assert sanitize_ctx_threshold(1.5) == DEFAULT_CTX_COMPACT_THRESHOLD
    assert sanitize_ctx_threshold("abc") == DEFAULT_CTX_COMPACT_THRESHOLD
    assert sanitize_ctx_threshold(None) == DEFAULT_CTX_COMPACT_THRESHOLD
    err = capsys.readouterr().err
    assert err.count("Warning:") == 5


def test_sanitize_ctx_threshold_custom_default(capsys):
    assert sanitize_ctx_threshold(1.5, default=0.5) == 0.5
    assert sanitize_ctx_threshold(0, default=0.9) == 0.9
    assert sanitize_ctx_threshold(0.5, default=0.9) == 0.5


def test_cmd_compact_auto_toggle(capsys):
    st = _state()
    assert st.ctx_compact is False
    chat._handle_command("/compact auto on", st)
    assert st.ctx_compact is True
    assert "Auto-compaction enabled." in capsys.readouterr().out
    chat._handle_command("/compact auto", st)
    assert "Auto-compaction: enabled" in capsys.readouterr().out
    chat._handle_command("/compact auto off", st)
    assert st.ctx_compact is False
    assert "Auto-compaction disabled." in capsys.readouterr().out
    chat._handle_command("/compact auto off extra", st)
    assert "Usage: /compact auto [on|off]" in capsys.readouterr().out


def test_cmd_compact_bad_subcommand(capsys):
    st = _state()
    chat._handle_command("/compact wat", st)
    assert "Usage: /compact [auto on|off]" in capsys.readouterr().out


def test_session_round_trip_preserves_compaction():
    st = _state()
    st.ctx_compact = True
    st.ctx_compact_threshold = 0.8
    st.ctx_compact_model = "small:model"
    st.messages = [
        {"role": "system", "content": "safety"},
        {"role": "user", "content": "SUMMARY", "compacted": True, "summary_at": 1},
        {"role": "user", "content": "recent"},
    ]
    data = chat.serialize_session(st)
    assert data["ctx_compact"] is True
    assert data["ctx_compact_threshold"] == 0.8
    assert data["ctx_compact_model"] == "small:model"

    st2 = _state()
    chat.apply_session(st2, data, source="test")
    assert st2.ctx_compact is True
    assert st2.ctx_compact_threshold == 0.8
    assert st2.ctx_compact_model == "small:model"
    assert st2.messages[1]["compacted"] is True
    assert st2.messages[1]["content"] == "SUMMARY"


def test_replay_history_renders_compacted_label(capsys):
    st = _state(color="never")
    st.messages = [
        {"role": "system", "content": "safety"},
        {"role": "user", "content": "SUMMARY", "compacted": True, "summary_at": 1},
        {"role": "user", "content": "recent"},
    ]
    chat._replay_history(st, use_color=False)
    out = capsys.readouterr().out
    assert "[compacted context] SUMMARY" in out
    assert ">>> recent" in out


def test_compaction_system_prompt_present():
    assert "anchored context summarization assistant" in COMPACTION_SYSTEM_PROMPT
    assert "<previous-summary>" in COMPACTION_SYSTEM_PROMPT


def test_serialize_persists_authoritative_usage():
    st = _state(model="m")
    st.ctx_usage = {"prompt_eval_count": 5000, "eval_count": 200}
    st.ctx_usage_model = "m"
    data = chat.serialize_session(st)
    assert data["ctx_usage"] == {"prompt_eval_count": 5000, "eval_count": 200}
    assert data["ctx_usage_model"] == "m"


def test_serialize_skips_estimated_usage():
    st = _state(model="m")
    st.ctx_usage = {"prompt_eval_count": 5000, "eval_count": 200, "_estimated": True}
    st.ctx_usage_model = "m"
    data = chat.serialize_session(st)
    assert "ctx_usage" not in data
    assert "ctx_usage_model" not in data


def test_serialize_skips_usage_without_count():
    st = _state(model="m")
    st.ctx_usage = {}
    assert "ctx_usage" not in chat.serialize_session(st)


def test_apply_restores_usage_same_model():
    st = _state(model="m")
    data = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "ctx_usage": {"prompt_eval_count": 5000, "eval_count": 200},
        "ctx_usage_model": "m",
    }
    chat.apply_session(st, data, source="test")
    assert st.ctx_usage == {"prompt_eval_count": 5000, "eval_count": 200}
    assert st.ctx_usage_model == "m"
    assert not st.ctx_usage.get("_estimated")
    st.ctx_max = 8192
    gauge = chat._ctx_prompt_gauge(st, use_color=False)
    assert gauge.startswith("[ctx 5,200/8,192")
    assert "~" not in gauge


def test_apply_marks_estimated_different_model():
    st = _state(model="m")
    data = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "ctx_usage": {"prompt_eval_count": 5000, "eval_count": 200},
        "ctx_usage_model": "other",
    }
    chat.apply_session(st, data, source="test")
    assert st.ctx_usage["_estimated"] is True
    assert st.ctx_usage_model == "m"
    st.ctx_max = 8192
    assert chat._ctx_prompt_gauge(st, use_color=False).startswith("[ctx ~5,200/")


def test_apply_usage_none_without_stored():
    st = _state(model="m")
    st.ctx_usage = {"prompt_eval_count": 1, "eval_count": 1}
    chat.apply_session(st, {"model": "m", "messages": []}, source="test")
    assert st.ctx_usage is None
    assert st.ctx_usage_model is None


def test_should_auto_compact_skips_estimated():
    st = _state(model="m")
    st.ctx_compact = True
    st.ctx_max = 10000
    st.ctx_usage = {"prompt_eval_count": 8000, "eval_count": 500, "_estimated": True}
    assert chat._should_auto_compact(st) is False
    st.ctx_usage = {"prompt_eval_count": 8000, "eval_count": 500}
    assert chat._should_auto_compact(st) is True


def test_model_switch_marks_estimated(capsys):
    st = _state(model="m")
    st.ctx_usage = {"prompt_eval_count": 5000, "eval_count": 200}
    st.ctx_usage_model = "m"
    chat._handle_command("/model other", st)
    assert st.model == "other"
    assert st.ctx_usage["_estimated"] is True
    assert st.ctx_usage_model == "other"
    assert "~" in chat._ctx_prompt_gauge(st, use_color=False)


def test_model_switch_same_model_not_estimated(capsys):
    st = _state(model="m")
    st.ctx_usage = {"prompt_eval_count": 5000, "eval_count": 200}
    st.ctx_usage_model = "m"
    chat._handle_command("/model m", st)
    assert not st.ctx_usage.get("_estimated")
