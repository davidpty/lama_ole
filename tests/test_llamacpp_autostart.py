"""Tests for the CLI-side llama.cpp autostart decisions.

Covers the ``_maybe_autostart_llamacpp`` gates (autostart flag, query modes,
remote-host exclusion), the effective-model resolution (CLI model vs resumed
session model), the option forwarding into ``ensure_server``, and the
missing-from-cache notice for targeted HF models. No real llama-server is ever
spawned; ``ensure_server`` is monkeypatched.
"""

import os
import sys
from types import SimpleNamespace

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)


def _cli():
    from lama_ole import lama_ole as cli_module
    return cli_module


def _args(**overrides):
    base = dict(
        llamacpp_autostart=True,
        list=False,
        ps=False,
        stop=None,
        transfer=None,
        model=None,
        resume=False,
        num_ctx=None,
        num_gpu=None,
        keep_alive=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _spy_ensure(cli, monkeypatch, result=None):
    calls = []

    def fake_ensure(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(cli.llamacpp_launcher, "ensure_server", fake_ensure)
    monkeypatch.setattr(cli.llamacpp_launcher, "_is_ready", lambda host, timeout=2.0: False)
    return calls


# -- _maybe_autostart_llamacpp gates -----------------------------------------


def test_autostart_skipped_when_disabled(monkeypatch):
    cli = _cli()
    calls = _spy_ensure(cli, monkeypatch)
    assert cli._maybe_autostart_llamacpp(
        _args(llamacpp_autostart=False), "http://localhost:5050"
    ) is None
    assert calls == []


def test_autostart_skipped_for_query_modes(monkeypatch):
    cli = _cli()
    for mode in ("list", "ps", "stop", "transfer"):
        calls = _spy_ensure(cli, monkeypatch)
        overrides = {mode: "x" if mode in ("stop", "transfer") else True}
        assert cli._maybe_autostart_llamacpp(
            _args(**overrides), "http://localhost:5050"
        ) is None
        assert calls == []


def test_autostart_skipped_for_remote_host(monkeypatch):
    cli = _cli()
    calls = _spy_ensure(cli, monkeypatch)
    assert cli._maybe_autostart_llamacpp(
        _args(), "http://192.168.1.50:8080"
    ) is None
    assert calls == []


def test_autostart_engages_for_local_host(monkeypatch):
    cli = _cli()
    calls = _spy_ensure(cli, monkeypatch, result=SimpleNamespace())
    assert cli._maybe_autostart_llamacpp(
        _args(), "http://localhost:5050"
    ) is not None
    assert calls
    kwargs = calls[0]
    assert kwargs["host"] == "http://localhost:5050"
    assert "model_id" not in kwargs
    assert kwargs["autostart"] is True


def test_autostart_forwards_options(monkeypatch):
    cli = _cli()
    calls = _spy_ensure(cli, monkeypatch, result=SimpleNamespace())
    args = _args(
        model="llamacpp:q.gguf",
        num_ctx=100000,
        num_gpu=99,
        keep_alive="10h",
    )
    launch_values = cli._maybe_autostart_llamacpp(args, "http://localhost:5050")
    assert launch_values is not None
    assert launch_values["num_ctx"] == 100000
    assert launch_values["num_gpu"] == 99
    assert launch_values["keep_alive"] == "10h"
    kwargs = calls[0]
    assert kwargs["options"] == {"num_ctx": 100000, "num_gpu": 99}
    assert kwargs["keep_alive"] == "10h"


def test_autostart_no_options_when_unset(monkeypatch):
    cli = _cli()
    calls = _spy_ensure(cli, monkeypatch, result=SimpleNamespace())
    assert cli._maybe_autostart_llamacpp(_args(), "http://localhost:5050") is not None
    assert calls[0]["options"] == {}


# -- not-served notice for HF models -----------------------------------------


def test_autostart_notice_when_hf_not_served(monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli.llamacpp_launcher, "server_has_model", lambda *a, **k: False)
    _spy_ensure(cli, monkeypatch, result=SimpleNamespace())
    args = _args(model="llamacpp:ggml-org/gpt-oss-20b-GGUF:MXFP4", resume=True)
    assert cli._maybe_autostart_llamacpp(args, "http://localhost:5050") is not None
    assert "is not served by the llama.cpp server" in capsys.readouterr().err


def test_autostart_no_notice_when_hf_served(monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli.llamacpp_launcher, "server_has_model", lambda *a, **k: True)
    _spy_ensure(cli, monkeypatch, result=SimpleNamespace())
    args = _args(model="llamacpp:ggml-org/gpt-oss-20b-GGUF:MXFP4", resume=True)
    cli._maybe_autostart_llamacpp(args, "http://localhost:5050")
    assert "is not served by the llama.cpp server" not in capsys.readouterr().err


def test_autostart_no_notice_when_server_unreachable(monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli.llamacpp_launcher, "server_has_model", lambda *a, **k: None)
    _spy_ensure(cli, monkeypatch, result=SimpleNamespace())
    args = _args(model="llamacpp:ggml-org/gpt-oss-20b-GGUF:MXFP4", resume=True)
    cli._maybe_autostart_llamacpp(args, "http://localhost:5050")
    assert "is not served by the llama.cpp server" not in capsys.readouterr().err


def test_autostart_no_notice_for_non_hf(monkeypatch, capsys):
    cli = _cli()
    _spy_ensure(cli, monkeypatch, result=SimpleNamespace())
    assert cli._maybe_autostart_llamacpp(
        _args(model="llamacpp:q.gguf"), "http://localhost:5050"
    ) is not None
    assert "is not served by the llama.cpp server" not in capsys.readouterr().err


# -- _effective_llamacpp_model -----------------------------------------------


def test_effective_model_cli_wins():
    cli = _cli()
    args = _args(model="llamacpp:foo", resume=True)
    assert cli._effective_llamacpp_model(args) == "foo"


def test_effective_model_uses_session(monkeypatch):
    cli = _cli()
    monkeypatch.setattr(
        cli, "find_recent_session", lambda sessions_dir, cwd: ("p", {"model": "llamacpp:bar"})
    )
    assert cli._effective_llamacpp_model(_args(model="ollama:x", resume=True)) == "bar"


def test_effective_model_session_not_llamacpp(monkeypatch):
    cli = _cli()
    monkeypatch.setattr(
        cli, "find_recent_session", lambda sessions_dir, cwd: ("p", {"model": "ollama:y"})
    )
    assert cli._effective_llamacpp_model(_args(model="ollama:x", resume=True)) is None


def test_effective_model_no_session(monkeypatch):
    cli = _cli()
    monkeypatch.setattr(cli, "find_recent_session", lambda sessions_dir, cwd: None)
    assert cli._effective_llamacpp_model(_args(model="ollama:x", resume=True)) is None


def test_effective_model_resume_off_ignores_session(monkeypatch):
    cli = _cli()
    monkeypatch.setattr(
        cli, "find_recent_session", lambda sessions_dir, cwd: ("p", {"model": "llamacpp:bar"})
    )
    assert cli._effective_llamacpp_model(_args(model="ollama:x", resume=False)) is None


def test_effective_model_none_model_none_resume():
    cli = _cli()
    assert cli._effective_llamacpp_model(_args(resume=False)) is None