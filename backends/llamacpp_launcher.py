"""llama-server launcher: auto-start the llama.cpp server when it is not running.

lama_ole talks to an external ``llama-server`` over HTTP. When autostart is
enabled (the default) and no server answers at the configured host, this
module starts one itself so model listing and ``/model`` completion work out
of the box.

The server is always started in **router mode** — ``llama-server`` with no
model. The server's router auto-discovers models from the llama.cpp cache
(``$LLAMA_CACHE``, else ``~/.cache/llama.cpp``) or from
``LAMA_OLE_LLAMACPP_MODELS_DIR``, serves every model it finds and loads them
on demand. This is what makes ``/model`` completion and mid-chat model
switching work; a targeted ``owner/name[:tag]`` Hugging Face id is served only
once it is present in that cache (the caller is expected to warn when it is
not).

Options that cannot be applied per request are honored at launch instead:
``num_ctx`` -> ``-c``, ``num_gpu`` -> ``-ngl``, ``keep_alive`` ->
``--sleep-idle-seconds``.

A launched server is left running (daemon-style) so later invocations reuse
it instantly; set ``LAMA_OLE_LLAMACPP_STOP_ON_EXIT=true`` to kill it when
lama_ole exits.
"""

import atexit
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

# LaunchedServer instances we own, kept alive for the process lifetime.
_SPAWNED = []

# Loopback hostnames — the only hosts a locally-spawned server may use.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


class LauncherError(Exception):
    """The server cannot be launched (no binary, nothing to serve)."""


def _bool_env(name, default):
    value = os.environ.get(name)
    if not value:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _is_ready(host, timeout=2.0):
    """Return True when the server answers ``/health`` with HTTP 200."""
    url = host.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def resolve_binary():
    """Path to the llama-server binary, or None when not available.

    ``LAMA_OLE_LLAMACPP_BIN`` overrides the ``llama-server`` PATH lookup.
    """
    value = os.environ.get("LAMA_OLE_LLAMACPP_BIN")
    if value:
        return value
    return shutil.which("llama-server")


def _parse_keep_alive(value):
    """Parse an Ollama keep_alive value ('5m', '1h', '90', '0') into seconds.

    Returns an int >= 0, or None when unparsable.
    """
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    match = re.fullmatch(r"(\d+)([smhd]?)", value)
    if not match:
        return None
    number = int(match.group(1))
    unit = match.group(2) or "s"
    return number * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _host_port(host):
    """Split a host URL into ``(hostname, port)`` for ``--host``/``--port``."""
    host = (host or "http://localhost:8080").rstrip("/")
    parsed = urllib.parse.urlsplit(host)
    hostname = parsed.hostname or "localhost"
    port = parsed.port or 8080
    return hostname, port


def is_local_host(host):
    """True when ``host`` points at this machine (a loopback address).

    Autostart only makes sense for a local server: when the configured host is
    a remote machine, starting one here would just shadow the intended target.
    """
    hostname, _ = _host_port(host)
    return hostname in _LOCAL_HOSTS


def is_hf_id(model_id):
    """True when ``model_id`` is an ``owner/name[:quant]`` Hugging Face id."""
    if not model_id:
        return False
    lower = model_id.lower()
    return (
        "/" in model_id
        and not lower.endswith(".gguf")
        and not model_id.startswith(("/", "."))
    )


def server_has_model(host, model_id, api_key=None):
    """True when the server at ``host`` serves ``model_id``.

    Queries ``/v1/models`` — the same listing ``/model`` completion uses — so
    the check is authoritative. Returns True when the id is served, False when
    the server answers but does not list it, and None when the server is
    unreachable (so the caller can skip rather than guess).
    """
    url = host.rstrip("/") + "/v1/models"
    request = urllib.request.Request(url)
    if api_key:
        request.add_header("Authorization", "Bearer %s" % api_key)
    try:
        with urllib.request.urlopen(request, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    ids = [m.get("id") for m in data.get("data", [])]
    return model_id in ids


def _models_dir():
    """The explicit models directory from the environment, or None."""
    value = os.environ.get("LAMA_OLE_LLAMACPP_MODELS_DIR")
    if not value:
        return None
    return os.path.expanduser(value)


# State marker: records which llama.cpp hosts we autostarted and with which
# launch-time options, so later processes can recognize the daemon as ours and
# know what it was launched with.
_MARKER_PATH = os.path.join(
    tempfile.gettempdir(), "lama_ole-llamaserver-state.json"
)


def _read_marker():
    try:
        with open(_MARKER_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_marker(data):
    try:
        with open(_MARKER_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
    except OSError:
        pass


def _record_launch(host, options, keep_alive):
    """Persist the launch-time options honored by the server at ``host``."""
    values = {}
    for key in ("num_ctx", "num_gpu"):
        value = (options or {}).get(key)
        if value is not None:
            values[key] = int(value)
    seconds = _parse_keep_alive(keep_alive)
    if seconds is not None:
        values["keep_alive"] = seconds
    data = _read_marker()
    data[host] = values
    _write_marker(data)


def _forget_launch(host):
    data = _read_marker()
    if host in data:
        del data[host]
        _write_marker(data)


def launched_server_values(host):
    """The launch-time options of the autostarted server at ``host``, or None.

    Returns the recorded ``num_ctx``/``num_gpu``/``keep_alive`` (seconds) only
    when we autostarted a server there and it is still answering — otherwise a
    running server (if any) was not started by us and its options are unknown.
    """
    values = _read_marker().get(host)
    if not values or not _is_ready(host):
        return None
    return dict(values)


def build_command(host, options=None, keep_alive=None, api_key=None):
    """Assemble the llama-server argv (always router mode).

    Returns ``(argv, "router")``. Raises :class:`LauncherError` when no binary
    is available.
    """
    bin_path = resolve_binary()
    if not bin_path:
        raise LauncherError(
            "llama-server binary not found; set LAMA_OLE_LLAMACPP_BIN to enable autostart"
        )
    argv = [bin_path, "--jinja"]

    hostname, port = _host_port(host)
    argv += ["--host", hostname, "--port", str(port)]
    if api_key:
        argv += ["--api-key", api_key]

    options = options or {}
    num_ctx = options.get("num_ctx")
    if num_ctx is not None:
        argv += ["-c", str(int(num_ctx))]
    num_gpu = options.get("num_gpu")
    if num_gpu is not None:
        argv += ["-ngl", str(int(num_gpu))]
    idle = _parse_keep_alive(keep_alive)
    if idle is not None:
        argv += ["--sleep-idle-seconds", str(idle)]

    directory = _models_dir()
    if directory:
        argv += ["--models-dir", directory]

    extra = os.environ.get("LAMA_OLE_LLAMACPP_ARGS")
    if extra:
        argv += shlex.split(extra)

    return argv, "router"


class LaunchedServer:
    """A llama-server process started by lama_ole."""

    def __init__(self, proc, host, argv, log_path):
        self.proc = proc
        self.host = host
        self.argv = list(argv)
        self.log_path = log_path

    @property
    def pid(self):
        return self.proc.pid

    def stop(self):
        """Terminate the server process (used on explicit teardown)."""
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        _forget_launch(self.host)


def _report_failed_launch(proc, log_path, argv):
    print(
        "[llamacpp] llama-server exited during startup (log: %s): %s"
        % (log_path, " ".join(argv)),
        file=sys.stderr,
    )
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        for line in lines[-15:]:
            print("  " + line, file=sys.stderr)
    except OSError:
        pass


def ensure_server(
    host,
    options=None,
    keep_alive=None,
    api_key=None,
    autostart=True,
    wait_timeout=120.0,
):
    """Start llama-server (router mode) when needed; return a LaunchedServer or None.

    Returns None (and prints nothing) when the host already answers, autostart
    is disabled, no binary is available, or the launch fails. When a server is
    started, a one-line notice goes to stderr.
    """
    if not autostart or _is_ready(host):
        return None
    if not resolve_binary():
        print(
            "[llamacpp] llama-server binary not found; "
            "set LAMA_OLE_LLAMACPP_BIN to enable autostart",
            file=sys.stderr,
        )
        return None
    try:
        argv, mode = build_command(
            host,
            options=options,
            keep_alive=keep_alive,
            api_key=api_key,
        )
    except LauncherError as exc:
        print("[llamacpp] %s" % exc, file=sys.stderr)
        return None

    log_path = os.path.join(
        tempfile.gettempdir(),
        "lama_ole-llamaserver-%d-%d.log" % (os.getpid(), int(time.time())),
    )
    with open(log_path, "w", encoding="utf-8") as log_handle:
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=log_handle,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            print("[llamacpp] cannot start llama-server: %s" % exc, file=sys.stderr)
            return None

    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        if _is_ready(host):
            break
        if proc.poll() is not None:
            _report_failed_launch(proc, log_path, argv)
            return None
        time.sleep(0.5)
    else:
        print(
            "[llamacpp] llama-server did not become ready within %.0fs "
            "(log: %s)" % (wait_timeout, log_path),
            file=sys.stderr,
        )
        return None

    launched = LaunchedServer(proc, host, argv, log_path)
    _SPAWNED.append(launched)
    _record_launch(host, options, keep_alive)
    if _bool_env("LAMA_OLE_LLAMACPP_STOP_ON_EXIT", False):
        atexit.register(launched.stop)
    if _models_dir():
        served = "models from %s" % _models_dir()
    else:
        served = "models from the llama.cpp cache"
    print(
        "[llamacpp] started llama-server (pid %d) on %s serving %s; log: %s"
        % (proc.pid, host, served, log_path),
        file=sys.stderr,
    )
    return launched