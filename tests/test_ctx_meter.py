"""Tests for the chat-mode context-window usage meter.

Covers the usage-total computation, prompt gauge formatting, the
opencode-style per-category breakdown, overflow prediction warnings, and the
context-window resolution chain (num_ctx -> env -> ps -> show -> None).
"""

import os
import sys
from types import SimpleNamespace

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402
import color_util  # noqa: E402


class _FakePsModel:
    def __init__(self, name, context_length):
        self.model = name
        self.name = name
        self.context_length = context_length


class _FakeClient:
    def __init__(self, ps_models=None, show=None):
        self._ps_models = ps_models or []
        self._show = show

    def ps(self):
        return SimpleNamespace(models=list(self._ps_models))

    def show(self, model=None):
        if self._show is None:
            raise RuntimeError("unexpected show() call")
        return self._show


def _state(client=None, **kwargs):
    opts = kwargs.pop("options", {})
    st = chat.ChatState(client=client, model=kwargs.pop("model", "test:model"), options=opts)
    for key, value in kwargs.items():
        setattr(st, key, value)
    return st


def test_usage_total_input_only():
    st = _state(client=_FakeClient())
    st.ctx_usage = {"prompt_eval_count": 1000}
    assert chat._ctx_usage_total(st.ctx_usage) == 1000


def test_usage_total_input_plus_output():
    st = _state(client=_FakeClient())
    st.ctx_usage = {"prompt_eval_count": 1000, "eval_count": 234}
    assert chat._ctx_usage_total(st.ctx_usage) == 1234


def test_usage_total_empty():
    st = _state(client=_FakeClient())
    assert chat._ctx_usage_total(None) is None
    assert chat._ctx_usage_total({}) is None
    assert chat._ctx_usage_total({"prompt_eval_count": 0}) is None


def test_prompt_gauge_no_usage():
    st = _state(client=_FakeClient())
    assert chat._ctx_prompt_gauge(st, use_color=False) == "[ctx --] "


def test_prompt_gauge_with_window():
    st = _state(client=_FakeClient())
    st.ctx_max = 8192
    st.ctx_usage = {"prompt_eval_count": 1000, "eval_count": 234}
    label = chat._ctx_prompt_gauge(st, use_color=False)
    assert label == "[ctx 1,234/8,192 █░░░░░░░░░ 15%] "


def test_prompt_gauge_without_window():
    st = _state(client=_FakeClient())
    st.ctx_max = None
    st.ctx_usage = {"prompt_eval_count": 1000, "eval_count": 234}
    assert chat._ctx_prompt_gauge(st, use_color=False) == "[ctx 1,234 tokens] "


def test_prompt_gauge_estimated_shows_tilde():
    st = _state(client=_FakeClient())
    st.ctx_max = 8192
    st.ctx_usage = {"prompt_eval_count": 1000, "eval_count": 234, "_estimated": True}
    assert chat._ctx_prompt_gauge(st, use_color=False) == "[ctx ~1,234/8,192 █░░░░░░░░░ ~15%] "


def test_prompt_gauge_estimated_without_window():
    st = _state(client=_FakeClient())
    st.ctx_max = None
    st.ctx_usage = {"prompt_eval_count": 1000, "eval_count": 234, "_estimated": True}
    assert chat._ctx_prompt_gauge(st, use_color=False) == "[ctx ~1,234 tokens] "


def test_prompt_gauge_colored():
    st = _state(client=_FakeClient())
    st.ctx_max = 100
    st.ctx_usage = {"prompt_eval_count": 100, "eval_count": 100}
    assert "\x01\033[" in chat._ctx_prompt_gauge(st, use_color=True)


def test_meter_state_thresholds():
    assert chat._meter_state(None) is None
    assert chat._meter_state(0) == "low"
    assert chat._meter_state(50) == "low"
    assert chat._meter_state(70) == "mid"
    assert chat._meter_state(89) == "mid"
    assert chat._meter_state(90) == "high"
    assert chat._meter_state(120) == "high"


def test_meter_colored_plain_when_disabled():
    assert chat._meter_colored("x", 95, False) == "x"


def test_meter_colored_uses_configured_color(monkeypatch):
    monkeypatch.setattr(color_util, "C_METER_HIGH", "\x01\033[35m\x02")
    result = chat._meter_colored("x", 95, True)
    assert "\x01\033[35m\x02" in result
    assert color_util.C_RESET in result


def test_breakdown_undershoot_adds_other():
    messages = [
        {"role": "system", "content": "s" * 4000},
        {"role": "user", "content": "u" * 400},
        {"role": "assistant", "content": "a" * 200},
        {"role": "tool", "content": "t" * 100},
    ]
    result = chat._estimate_context_breakdown(messages, input_tokens=1500)
    keys = [k for k, _, _ in result]
    assert "system" in keys and "other" in keys
    total = sum(tokens for _, tokens, _ in result)
    assert total == 1500
    assert all(tokens > 0 for _, tokens, _ in result)


def test_breakdown_overshoot_scales_down():
    messages = [
        {"role": "system", "content": "s" * 10000},
        {"role": "user", "content": "u" * 8000},
    ]
    result = chat._estimate_context_breakdown(messages, input_tokens=500)
    total = sum(tokens for _, tokens, _ in result)
    assert total == 500
    assert all(tokens > 0 for _, tokens, _ in result)
    # The bigger category should still dominate.
    by_key = dict((k, t) for k, t, _ in result)
    assert by_key["system"] > by_key["user"]


def test_breakdown_empty():
    assert chat._estimate_context_breakdown([], 100) == []
    assert chat._estimate_context_breakdown([{"role": "user", "content": "x"}], 0) == []


def test_overflow_warning_exceeds(capsys):
    st = _state(client=_FakeClient())
    st.ctx_max = 4096
    st.ctx_usage = {"prompt_eval_count": 4000}
    chat._warn_ctx_overflow(st, "x" * 400)
    out = capsys.readouterr().err
    assert "exceeds the context window" in out
    assert "4,100 / 4,096" in out


def test_overflow_warning_near_limit(capsys):
    st = _state(client=_FakeClient())
    st.ctx_max = 4096
    st.ctx_usage = {"prompt_eval_count": 3700}
    chat._warn_ctx_overflow(st, "x" * 100)
    out = capsys.readouterr().err
    assert "WARNING" in out
    assert "exceeds" not in out


def test_overflow_warning_silent_below_threshold(capsys):
    st = _state(client=_FakeClient())
    st.ctx_max = 4096
    st.ctx_usage = {"prompt_eval_count": 500}
    chat._warn_ctx_overflow(st, "hi")
    assert capsys.readouterr().err == ""


def test_overflow_warning_silent_when_meter_off(capsys):
    st = _state(client=_FakeClient(), ctx_meter=False)
    st.ctx_max = 4096
    st.ctx_usage = {"prompt_eval_count": 5000}
    chat._warn_ctx_overflow(st, "x" * 5000)
    assert capsys.readouterr().err == ""


def test_estimate_context_tokens_no_prior_usage(capsys):
    st = _state(client=_FakeClient(), system_prompt="s" * 1000)
    st.ctx_max = 2048
    st.messages = [{"role": "user", "content": "u" * 8000}]
    chat._warn_ctx_overflow(st, "u" * 8000)
    out = capsys.readouterr().err
    assert "WARNING" in out
    assert "exceeds the context window" in out


def test_estimate_context_tokens_includes_system_prompt():
    st = _state(client=_FakeClient(), system_prompt="s" * 4000)
    st.messages = [{"role": "user", "content": "u" * 4000}]
    assert chat._estimate_context_tokens(st) >= 2000
    st2 = _state(client=_FakeClient(), system_prompt="s" * 4000)
    st2.messages = [{"role": "system", "content": "s" * 8000}, {"role": "user", "content": "u" * 4000}]
    assert chat._estimate_context_tokens(st2) >= 3000


def test_resolve_num_ctx_option_wins():
    client = _FakeClient(ps_models=[_FakePsModel("test:model", 100000)])
    st = _state(client=client, options={"num_ctx": 16384})
    assert chat._resolve_ctx_max(st) == 16384


def test_resolve_env_override(monkeypatch):
    client = _FakeClient(ps_models=[_FakePsModel("test:model", 100000)])
    st = _state(client=client)
    monkeypatch.setenv("LAMA_OLE_CTX_SIZE", "8192")
    assert chat._resolve_ctx_max(st) == 8192


def test_resolve_ps_running_model():
    client = _FakeClient(ps_models=[_FakePsModel("test:model", 100000)])
    st = _state(client=client)
    assert chat._resolve_ctx_max(st) == 100000


def test_resolve_show_num_ctx_parameter():
    show = SimpleNamespace(parameters="num_ctx            4096\n", modelinfo={})
    st = _state(client=_FakeClient(show=show))
    assert chat._resolve_ctx_max(st) == 4096


def test_resolve_show_modelinfo_context_length():
    show = SimpleNamespace(parameters="", modelinfo={"qwen35moe.context_length": 262144})
    st = _state(client=_FakeClient(show=show))
    assert chat._resolve_ctx_max(st) == 262144


def test_resolve_none_when_unavailable():
    st = _state(client=_FakeClient())
    assert chat._resolve_ctx_max(st) is None


def test_ensure_ctx_max_caches():
    client = _FakeClient(ps_models=[_FakePsModel("test:model", 100000)])
    st = _state(client=client)
    chat._ensure_ctx_max(st)
    assert st.ctx_max == 100000
    client._ps_models = []
    chat._ensure_ctx_max(st)
    assert st.ctx_max == 100000
