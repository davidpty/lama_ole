"""Tests for the llama-server autostart launcher.

Covers keep_alive parsing, host splitting, model resolution (file/HF id vs
router mode), binary lookup, models-dir handling, argv assembly, and the
``ensure_server`` decision tree (already running, disabled, nothing to serve,
successful spawn + readiness wait). No real llama-server is ever spawned.
"""

import os
import sys
from types import SimpleNamespace

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

from backends import llamacpp_launcher as L  # noqa: E402


def _flag(argv, flag):
    assert flag in argv, "%s not in %r" % (flag, argv)
    return argv[argv.index(flag) + 1]


# -- keep_alive parsing ------------------------------------------------------


def test_parse_keep_alive():
    assert L._parse_keep_alive(None) is None
    assert L._parse_keep_alive("") is None
    assert L._parse_keep_alive("90") == 90
    assert L._parse_keep_alive("0") == 0
    assert L._parse_keep_alive("5m") == 300
    assert L._parse_keep_alive("1h") == 3600
    assert L._parse_keep_alive("2d") == 172800
    assert L._parse_keep_alive("1.5h") is None
    assert L._parse_keep_alive("bogus") is None
    assert L._parse_keep_alive("5 h") is None


# -- host splitting ----------------------------------------------------------


def test_host_port():
    assert L._host_port("http://localhost:8080") == ("localhost", 8080)
    assert L._host_port("http://127.0.0.1:9000") == ("127.0.0.1", 9000)
    assert L._host_port("http://example.com") == ("example.com", 8080)
    assert L._host_port(None) == ("localhost", 8080)


# -- models dir resolution ---------------------------------------------------


def test_default_models_dir_llama_cache(monkeypatch):
    monkeypatch.setenv("LLAMA_CACHE", "/cache/llama")
    assert L.default_models_dir() == "/cache/llama"


def test_default_models_dir_xdg(monkeypatch):
    monkeypatch.delenv("LLAMA_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", "/xdg")
    assert L.default_models_dir() == "/xdg/llama.cpp"


def test_default_models_dir_home(monkeypatch):
    monkeypatch.delenv("LLAMA_CACHE", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/tester")
    assert L.default_models_dir() == "/home/tester/.cache/llama.cpp"


# -- model resolution --------------------------------------------------------


def test_resolve_model_args_existing_file(tmp_path):
    gguf = tmp_path / "q.gguf"
    gguf.write_text("x")
    argv = L.resolve_model_args(str(gguf))
    assert argv == ["-m", str(gguf), "--alias", "q.gguf"]


def test_resolve_model_args_expands_user(tmp_path, monkeypatch):
    gguf = tmp_path / "q.gguf"
    gguf.write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path))
    argv = L.resolve_model_args("~/q.gguf")
    assert argv[1] == str(gguf)


def test_resolve_model_args_hf_repo():
    assert L.resolve_model_args("unsloth/Qwen3.5-0.8B-GGUF") == [
        "--hf-repo",
        "unsloth/Qwen3.5-0.8B-GGUF",
    ]
    assert L.resolve_model_args("unsloth/Qwen:Q4_K_M") == [
        "--hf-repo",
        "unsloth/Qwen:Q4_K_M",
    ]


def test_resolve_model_args_unresolvable_is_none():
    assert L.resolve_model_args(None) is None
    assert L.resolve_model_args("") is None
    assert L.resolve_model_args("qwen.gguf") is None
    assert L.resolve_model_args("my-model") is None
    assert L.resolve_model_args("sub/dir/q.gguf") is None  # relative gguf path


# -- binary lookup -----------------------------------------------------------


def test_resolve_binary_env_wins(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/custom/llama-server")
    assert L.resolve_binary() == "/custom/llama-server"


def test_resolve_binary_path_lookup(monkeypatch):
    monkeypatch.delenv("LAMA_OLE_LLAMACPP_BIN", raising=False)
    found = L.resolve_binary()
    assert found is None or os.path.isabs(found)


# -- can_autostart -----------------------------------------------------------


def test_can_autostart_single_model(tmp_path):
    gguf = tmp_path / "q.gguf"
    gguf.write_text("x")
    assert L.can_autostart(str(gguf)) is True


def test_can_autostart_models_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_MODELS_DIR", str(tmp_path))
    assert L.can_autostart(None) is True


def test_can_autostart_nothing(monkeypatch):
    monkeypatch.delenv("LAMA_OLE_LLAMACPP_MODELS_DIR", raising=False)
    monkeypatch.delenv("LLAMA_CACHE", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/nonexistent-home-xyz")
    assert L.can_autostart(None) is False


# -- build_command -----------------------------------------------------------


def test_build_command_single_model(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_ARGS", "--threads 4 --no-mmap")
    gguf = tmp_path / "q.gguf"
    gguf.write_text("x")
    argv, mode = L.build_command(
        "http://127.0.0.1:9000",
        model_id=str(gguf),
        options={"num_ctx": 8192, "num_gpu": 33},
        keep_alive="5m",
        api_key="secret",
    )
    assert mode == "single"
    assert argv[0] == "/bin/llama-server"
    assert _flag(argv, "-m") == str(gguf)
    assert _flag(argv, "--alias") == "q.gguf"
    assert _flag(argv, "-c") == "8192"
    assert _flag(argv, "-ngl") == "33"
    assert _flag(argv, "--sleep-idle-seconds") == "300"
    assert _flag(argv, "--host") == "127.0.0.1"
    assert _flag(argv, "--port") == "9000"
    assert _flag(argv, "--api-key") == "secret"
    assert "--threads" in argv and "4" in argv
    assert "--models-dir" not in argv


def test_build_command_router_with_models_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_MODELS_DIR", str(tmp_path))
    argv, mode = L.build_command("http://localhost:8080", model_id=None)
    assert mode == "router"
    assert _flag(argv, "--models-dir") == str(tmp_path)
    assert "-c" not in argv
    assert "--sleep-idle-seconds" not in argv


def test_build_command_router_uses_server_cache(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
    monkeypatch.delenv("LAMA_OLE_LLAMACPP_MODELS_DIR", raising=False)
    argv, mode = L.build_command("http://localhost:8080", model_id=None)
    assert mode == "router"
    assert "--models-dir" not in argv


def test_build_command_raises_without_binary(monkeypatch):
    monkeypatch.setattr(L, "resolve_binary", lambda: None)
    with pytest.raises(L.LauncherError):
        L.build_command("http://localhost:8080")


# -- ensure_server -----------------------------------------------------------


def test_ensure_server_already_running(monkeypatch):
    monkeypatch.setattr(L, "_is_ready", lambda host, timeout=2.0: True)
    assert L.ensure_server("http://localhost:8080", autostart=True) is None


def test_ensure_server_disabled(monkeypatch):
    monkeypatch.setattr(L, "_is_ready", lambda host, timeout=2.0: False)
    assert L.ensure_server("http://localhost:8080", autostart=False) is None


def test_ensure_server_nothing_to_serve(monkeypatch):
    monkeypatch.setattr(L, "_is_ready", lambda host, timeout=2.0: False)
    monkeypatch.setattr(L, "can_autostart", lambda model_id: False)
    assert L.ensure_server("http://localhost:8080", autostart=True) is None


def test_ensure_server_starts_and_waits(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
    monkeypatch.setattr(L, "can_autostart", lambda model_id: True)
    ready = {"n": 0}

    def fake_ready(host, timeout=2.0):
        ready["n"] += 1
        return ready["n"] > 2

    monkeypatch.setattr(L, "_is_ready", fake_ready)
    proc = SimpleNamespace(poll=lambda: None, pid=4242)
    monkeypatch.setattr(
        L,
        "subprocess",
        SimpleNamespace(
            DEVNULL=os.devnull,
            Popen=lambda *a, **kw: proc,
        ),
    )
    launched = L.ensure_server(
        "http://localhost:8080",
        model_id="q.gguf",
        autostart=True,
        wait_timeout=5.0,
    )
    assert launched is not None
    assert launched.pid == 4242
    assert launched.argv[0] == "/bin/llama-server"
    err = capsys.readouterr().err
    assert "started llama-server" in err
    assert "4242" in err


def test_ensure_server_failed_launch_reports_log(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
    monkeypatch.setattr(L, "_is_ready", lambda host, timeout=2.0: False)
    monkeypatch.setattr(L, "can_autostart", lambda model_id: True)
    monkeypatch.setattr(
        L,
        "subprocess",
        SimpleNamespace(
            DEVNULL=os.devnull,
            Popen=lambda *a, **kw: SimpleNamespace(poll=lambda: 1),
        ),
    )
    assert L.ensure_server(
        "http://localhost:8080",
        model_id="q.gguf",
        autostart=True,
        wait_timeout=1.0,
    ) is None
    err = capsys.readouterr().err
    assert "exited during startup" in err