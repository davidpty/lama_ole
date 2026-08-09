"""Tests for the chat-mode /stats command and session statistics.

Covers the formatting helpers, per-model session accumulation, the /stats
output (no-response and populated cases), reset on /new, and persistence of
averages + last-turn breakdown through serialize_session/apply_session.
"""

import os
import sys
from types import SimpleNamespace

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402


class _FakeClient:
    def ps(self):
        return SimpleNamespace(models=[])

    def show(self, model=None):
        raise RuntimeError("unexpected show() call")


def _state(model="test:model", **kwargs):
    st = chat.ChatState(client=_FakeClient(), model=model, color="never")
    for key, value in kwargs.items():
        setattr(st, key, value)
    return st


def _metrics():
    return {
        "prompt_eval_count": 4588,
        "eval_count": 436,
        "eval_duration_ns": 5_300_000_000,
        "prompt_eval_duration_ns": 2_200_000_000,
        "last_round_kind": "final answer",
        "rounds_model": "test:model",
        "rounds": [
            {
                "kind": "tool call",
                "eval_count": 642,
                "eval_duration_ns": 7_500_000_000,
                "prompt_eval_count": 3510,
                "prompt_eval_duration_ns": 1_500_000_000,
            },
            {
                "kind": "final answer",
                "eval_count": 436,
                "eval_duration_ns": 5_300_000_000,
                "prompt_eval_count": 4588,
                "prompt_eval_duration_ns": 2_200_000_000,
            },
        ],
        "turn_rounds": 2,
        "turn_eval_count": 1078,
        "turn_eval_duration_ns": 12_800_000_000,
        "turn_prompt_eval_count": 8098,
        "turn_prompt_eval_duration_ns": 3_700_000_000,
        "turn_elapsed_s": 17.1,
    }


class TestFormatting:
    def test_duration_seconds(self):
        assert chat._fmt_duration(3_400_000_000) == "3.4s"

    def test_duration_minutes(self):
        assert chat._fmt_duration(65_000_000_000) == "1m 05s"

    def test_duration_none(self):
        assert chat._fmt_duration(None) == "n/a"
        assert chat._fmt_duration(0) == "n/a"

    def test_speed_toks_per_sec(self):
        assert chat._fmt_speed(68) == "68 tok/s"

    def test_speed_thousands(self):
        assert chat._fmt_speed(2134) == "2.1K tok/s"

    def test_speed_zero(self):
        assert chat._fmt_speed(0) == "n/a"
        assert chat._fmt_speed(None) == "n/a"

    def test_count(self):
        assert chat._fmt_count(1847) == "1,847"

    def test_delta_pct(self):
        assert round(chat._delta_pct(82, 84), 2) == -2.38

    def test_toks_per_sec(self):
        assert round(chat._toks_per_sec(100, 1_000_000_000), 1) == 100.0
        assert chat._toks_per_sec(0, 1_000_000_000) == 0.0
        assert chat._toks_per_sec(100, None) == 0.0


class TestAccumulation:
    def test_stats_total_aggregates_across_models(self):
        st = _state(
            stats_by_model={
                "a": {"rounds": 8, "eval_count": 2500, "eval_duration_ns": 30_000_000_000,
                      "prompt_eval_count": 12000, "prompt_eval_duration_ns": 6_000_000_000},
                "b": {"rounds": 3, "eval_count": 900, "eval_duration_ns": 10_000_000_000,
                      "prompt_eval_count": 4000, "prompt_eval_duration_ns": 2_000_000_000},
            }
        )
        total = chat._stats_total(st)
        assert total["rounds"] == 11
        assert total["eval_count"] == 3400
        assert total["eval_duration_ns"] == 40_000_000_000

    def test_accumulate_folds_turn_into_per_model(self):
        st = _state()
        chat._accumulate_stats(st, _metrics())
        chat._accumulate_stats(st, _metrics())
        entry = st.stats_by_model["test:model"]
        assert entry["rounds"] == 4
        assert entry["eval_count"] == 2156
        assert entry["eval_duration_ns"] == 25_600_000_000
        assert entry["prompt_eval_count"] == 16196

    def test_accumulate_separates_models(self):
        st = _state(model="a")
        chat._accumulate_stats(st, _metrics())
        st.model = "b"
        chat._accumulate_stats(st, _metrics())
        assert st.stats_by_model["a"]["rounds"] == 2
        assert st.stats_by_model["b"]["rounds"] == 2

    def test_accumulate_noop_without_turn_data(self):
        st = _state()
        chat._accumulate_stats(st, {})
        chat._accumulate_stats(st, {"turn_rounds": 0})
        assert st.stats_by_model == {}


class TestCmdStats:
    def test_no_response_yet(self, capsys):
        st = _state()
        chat._cmd_stats(st)
        out = capsys.readouterr().out
        assert "test:model" in out
        assert "No model response yet this session." in out

    def test_populated_breakdown_and_averages(self, capsys):
        st = _state()
        st.ctx_usage = _metrics()
        st.ctx_usage_model = "test:model"
        chat._accumulate_stats(st, st.ctx_usage)
        chat._cmd_stats(st)
        out = capsys.readouterr().out
        assert "Last turn" in out
        assert "r1" in out
        assert "tool call" in out
        assert "r2" in out
        assert "final answer" in out
        assert "84 tok/s" in out
        assert "Session avg" in out
        assert "2 rounds" in out

    def test_single_round_turn(self, capsys):
        metrics = _metrics()
        metrics["rounds"] = [metrics["rounds"][1]]
        metrics["turn_rounds"] = 1
        metrics["turn_eval_count"] = 436
        metrics["turn_eval_duration_ns"] = 5_300_000_000
        st = _state()
        st.ctx_usage = metrics
        chat._accumulate_stats(st, metrics)
        chat._cmd_stats(st)
        out = capsys.readouterr().out
        assert "1 round" in out
        assert "r1" in out

    def test_per_model_rows(self, capsys):
        st = _state()
        st.ctx_usage = _metrics()
        st.ctx_usage_model = "test:model"
        chat._accumulate_stats(st, st.ctx_usage)
        st.model = "other:model"
        st.stats_by_model.setdefault(
            "other:model",
            {"rounds": 1, "eval_count": 100, "eval_duration_ns": 2_000_000_000,
             "prompt_eval_count": 500, "prompt_eval_duration_ns": 1_000_000_000},
        )
        chat._cmd_stats(st)
        out = capsys.readouterr().out
        assert "test:model" in out
        assert "other:model" in out


class TestPersistence:
    def test_round_trip_restores_stats_and_breakdown(self):
        st = _state()
        st.ctx_usage = _metrics()
        st.ctx_usage_model = "test:model"
        chat._accumulate_stats(st, st.ctx_usage)
        data = chat.serialize_session(st)

        st2 = _state()
        chat.apply_session(st2, data, source="test")
        assert st2.stats_by_model["test:model"]["rounds"] == 2
        assert st2.ctx_usage["rounds"] == st.ctx_usage["rounds"]
        assert st2.ctx_usage["rounds_model"] == "test:model"

    def test_model_mismatch_skips_stale_breakdown(self):
        st = _state()
        st.ctx_usage = _metrics()
        chat._accumulate_stats(st, st.ctx_usage)
        st.model = "different:model"
        data = chat.serialize_session(st)
        assert data["model"] == "different:model"
        assert data["stats"]["model"] == "test:model"

        st2 = _state()
        chat.apply_session(st2, data, source="test")
        assert st2.model == "different:model"
        assert st2.stats_by_model["test:model"]["rounds"] == 2
        assert "rounds" not in (st2.ctx_usage or {})

    def test_old_file_without_stats_key(self):
        st2 = _state()
        chat.apply_session(st2, {"model": "test:model", "messages": []}, source="test")
        assert st2.stats_by_model == {}
        assert st2.ctx_usage is None


class TestNewReset:
    def test_new_clears_stats(self, capsys):
        st = _state()
        st.stats_by_model = {"test:model": {"rounds": 5}}
        st.messages = []
        chat._handle_command("/new", st)
        assert st.stats_by_model == {}
