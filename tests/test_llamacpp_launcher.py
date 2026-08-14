"""Tests for the llama-server autostart launcher.

Covers keep_alive parsing, host splitting + local-host detection, HF id
detection, the ``server_has_model`` listing check, binary lookup, models-dir
handling, argv assembly (always router mode), and the ``ensure_server``
decision tree (already running, disabled, successful spawn + readiness wait).
No real llama-server is ever spawned.
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


# -- HF id detection ---------------------------------------------------------


def test_is_hf_id():
    assert L.is_hf_id("unsloth/Qwen3.5-0.8B-GGUF") is True
    assert L.is_hf_id("unsloth/Qwen:Q4_K_M") is True
    assert L.is_hf_id("ggml-org/gpt-oss-20b-GGUF:MXFP4") is True


def test_is_hf_id_false():
    assert L.is_hf_id(None) is False
    assert L.is_hf_id("") is False
    assert L.is_hf_id("qwen.gguf") is False
    assert L.is_hf_id("my-model") is False
    assert L.is_hf_id("sub/dir/q.gguf") is False  # relative gguf path
    assert L.is_hf_id("/abs/path/q.gguf") is False
    assert L.is_hf_id("./q.gguf") is False


# -- server_has_model --------------------------------------------------------


class _FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(monkeypatch, body=None, error=None):
    def fake_urlopen(request, timeout=None):
        if error is not None:
            raise error
        return _FakeResp(body)
    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)


def test_server_has_model_found(monkeypatch):
    _fake_urlopen(
        monkeypatch,
        body='{"data": [{"id": "meta-models/Muse-Glimmer-30B-GGUF:Q4_K_M"}, '
        '{"id": "ggml-org/gpt-oss-20b-GGUF:MXFP4"}]}',
    )
    assert L.server_has_model(
        "http://localhost:5050",
        "meta-models/Muse-Glimmer-30B-GGUF:Q4_K_M",
    ) is True


def test_server_has_model_absent(monkeypatch):
    _fake_urlopen(
        monkeypatch,
        body='{"data": [{"id": "ggml-org/gpt-oss-20b-GGUF:MXFP4"}]}',
    )
    assert L.server_has_model(
        "http://localhost:5050", "meta-models/Muse-Glimmer-30B-GGUF:Q4_K_M"
    ) is False


def test_server_has_model_unreachable(monkeypatch):
    _fake_urlopen(monkeypatch, error=OSError("connection refused"))
    assert L.server_has_model(
        "http://localhost:5050", "meta-models/Muse-Glimmer-30B-GGUF:Q4_K_M"
    ) is None


def test_server_has_model_sends_api_key(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["auth"] = request.get_header("Authorization")
        return _FakeResp('{"data": []}')
    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    L.server_has_model("http://localhost:5050", "x/y:z", api_key="secret")
    assert captured["auth"] == "Bearer secret"


# -- binary lookup -----------------------------------------------------------


def test_resolve_binary_env_wins(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/custom/llama-server")
    assert L.resolve_binary() == "/custom/llama-server"


def test_resolve_binary_path_lookup(monkeypatch):
    monkeypatch.delenv("LAMA_OLE_LLAMACPP_BIN", raising=False)
    found = L.resolve_binary()
    assert found is None or os.path.isabs(found)


# -- local-host detection ----------------------------------------------------


def test_is_local_host():
    assert L.is_local_host("http://localhost:5050") is True
    assert L.is_local_host("http://127.0.0.1:8080") is True
    assert L.is_local_host("http://[::1]:8080") is True
    assert L.is_local_host("http://0.0.0.0:8080") is True
    assert L.is_local_host(None) is True  # default host is localhost


def test_is_local_host_remote():
    assert L.is_local_host("http://192.168.1.50:8080") is False
    assert L.is_local_host("http://10.0.0.4:8080") is False
    assert L.is_local_host("http://my-server.local:8080") is False
    assert L.is_local_host("http://llm.example.com:8080") is False


# -- build_command -----------------------------------------------------------


def test_build_command_always_router(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_ARGS", "--threads 4 --no-mmap")
    gguf = tmp_path / "q.gguf"
    gguf.write_text("x")
    argv, mode = L.build_command(
        "http://127.0.0.1:9000",
        options={"num_ctx": 8192, "num_gpu": 33},
        keep_alive="5m",
        api_key="secret",
    )
    assert mode == "router"
    assert argv[0] == "/bin/llama-server"
    assert "-m" not in argv
    assert "--alias" not in argv
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
    argv, mode = L.build_command("http://localhost:8080")
    assert mode == "router"
    assert _flag(argv, "--models-dir") == str(tmp_path)
    assert "-c" not in argv
    assert "--sleep-idle-seconds" not in argv


def test_build_command_router_uses_server_cache(monkeypatch):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
    monkeypatch.delenv("LAMA_OLE_LLAMACPP_MODELS_DIR", raising=False)
    argv, mode = L.build_command("http://localhost:8080")
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


def test_ensure_server_launches_without_models_dir(monkeypatch, capsys):
    monkeypatch.delenv("LAMA_OLE_LLAMACPP_MODELS_DIR", raising=False)
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
    ready = {"n": 0}

    def fake_ready(host, timeout=2.0):
        ready["n"] += 1
        return ready["n"] > 2

    monkeypatch.setattr(L, "_is_ready", fake_ready)
    monkeypatch.setattr(
        L,
        "subprocess",
        SimpleNamespace(
            DEVNULL=os.devnull,
            Popen=lambda *a, **kw: SimpleNamespace(poll=lambda: None, pid=99),
        ),
    )
    launched = L.ensure_server(
        "http://localhost:8080", autostart=True, wait_timeout=5.0
    )
    assert launched is not None
    assert launched.pid == 99
    err = capsys.readouterr().err
    assert "serving models from the llama.cpp cache" in err


def test_ensure_server_starts_and_waits(monkeypatch, capsys):
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
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
        autostart=True,
        wait_timeout=1.0,
    ) is None
    err = capsys.readouterr().err
    assert "exited during startup" in err


# -- launch-state marker -----------------------------------------------------


def _marker_path(tmp_path, monkeypatch):
    marker = str(tmp_path / "state.json")
    monkeypatch.setattr(L, "_MARKER_PATH", marker)
    return marker


def test_record_launch_keeps_hosts_separate(tmp_path, monkeypatch):
    _marker_path(tmp_path, monkeypatch)
    L._record_launch("http://localhost:5050", {"num_ctx": 1}, None)
    L._record_launch("http://localhost:6060", {"num_ctx": 2}, "5m")
    data = L._read_marker()
    assert data["http://localhost:5050"] == {"num_ctx": 1}
    assert data["http://localhost:6060"] == {"num_ctx": 2, "keep_alive": 300}
    L._forget_launch("http://localhost:5050")
    assert "http://localhost:5050" not in L._read_marker()
    assert "http://localhost:6060" in L._read_marker()


def test_launched_server_values_when_ready(tmp_path, monkeypatch):
    _marker_path(tmp_path, monkeypatch)
    monkeypatch.setattr(L, "_is_ready", lambda host, timeout=2.0: True)
    L._record_launch(
        "http://localhost:5050", {"num_ctx": 100000, "num_gpu": 99}, "10h"
    )
    assert L.launched_server_values("http://localhost:5050") == {
        "num_ctx": 100000,
        "num_gpu": 99,
        "keep_alive": 36000,
    }


def test_launched_server_values_not_ready(tmp_path, monkeypatch):
    _marker_path(tmp_path, monkeypatch)
    monkeypatch.setattr(L, "_is_ready", lambda host, timeout=2.0: False)
    L._record_launch("http://localhost:5050", {"num_ctx": 100000}, None)
    assert L.launched_server_values("http://localhost:5050") is None


def test_launched_server_values_unknown_host(tmp_path, monkeypatch):
    _marker_path(tmp_path, monkeypatch)
    monkeypatch.setattr(L, "_is_ready", lambda host, timeout=2.0: True)
    assert L.launched_server_values("http://localhost:5050") is None


def test_ensure_server_writes_and_forgets_marker(tmp_path, monkeypatch, capsys):
    _marker_path(tmp_path, monkeypatch)
    monkeypatch.setenv("LAMA_OLE_LLAMACPP_BIN", "/bin/llama-server")
    ready = {"n": 0}

    def fake_ready(host, timeout=2.0):
        ready["n"] += 1
        return ready["n"] > 2

    monkeypatch.setattr(L, "_is_ready", fake_ready)
    proc = SimpleNamespace(poll=lambda: None, pid=5555)
    monkeypatch.setattr(
        L,
        "subprocess",
        SimpleNamespace(DEVNULL=os.devnull, Popen=lambda *a, **kw: proc),
    )
    launched = L.ensure_server(
        "http://localhost:5050",
        options={"num_ctx": 8192, "num_gpu": 33},
        keep_alive="5m",
        autostart=True,
        wait_timeout=5.0,
    )
    assert launched is not None
    assert L.launched_server_values("http://localhost:5050") == {
        "num_ctx": 8192,
        "num_gpu": 33,
        "keep_alive": 300,
    }
    launched.stop()
    assert L.launched_server_values("http://localhost:5050") is None