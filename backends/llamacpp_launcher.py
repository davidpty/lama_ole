"""llama-server launcher: auto-start the llama.cpp server when it is not running.

lama_ole talks to an external ``llama-server`` over HTTP. When autostart is
enabled (the default) and no server answers at the configured host, this
module starts one itself so model listing and ``/model`` completion work out
of the box.

Two launch modes:

* **router mode** (default) — ``llama-server`` with no model. The server's
  router auto-discovers models from the llama.cpp cache (``$LLAMA_CACHE``,
  else ``~/.cache/llama.cpp``) or from ``LAMA_OLE_LLAMACPP_MODELS_DIR``,
  serves every model it finds and loads them on demand. This is what makes
  ``/model`` completion and mid-chat model switching work.
* **single-model mode** — when the user targeted an explicit ``llamacpp:``
  model that resolves to a local GGUF file (or an ``owner/name[:tag]``
  Hugging Face id), the server is started with that model directly.

Options that cannot be applied per request are honored at launch instead:
``num_ctx`` -> ``-c``, ``num_gpu`` -> ``-ngl``, ``keep_alive`` ->
``--sleep-idle-seconds``.

A launched server is left running (daemon-style) so later invocations reuse
it instantly; set ``LAMA_OLE_LLAMACPP_STOP_ON_EXIT=true`` to kill it when
lama_ole exits.
"""

import atexit
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# LaunchedServer instances we own, kept alive for the process lifetime.
_SPAWNED = []


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


def default_models_dir():
    """The llama.cpp model cache directory (used by router mode by default).

    Resolution mirrors llama.cpp: ``$LLAMA_CACHE``, then
    ``$XDG_CACHE_HOME/llama.cpp``, then ``~/.cache/llama.cpp``. Used only to
    decide whether autostart has anything to serve; the server resolves the
    same location itself.
    """
    value = os.environ.get("LLAMA_CACHE")
    if value:
        return value
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return os.path.join(xdg, "llama.cpp")
    return os.path.join(os.path.expanduser("~"), ".cache", "llama.cpp")


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


def resolve_model_args(model_id):
    """Return argv that serves ``model_id``, or None when unresolvable.

    * an existing file path -> ``["-m", <path>, "--alias", <basename>]``
    * an ``owner/name[:tag]`` Hugging Face id -> ``["--hf-repo", <id>]``
    * anything else -> None (the caller falls back to router mode)
    """
    if not model_id:
        return None
    expanded = os.path.expanduser(model_id)
    if os.path.isfile(expanded):
        return ["-m", expanded, "--alias", os.path.basename(expanded)]
    lower = model_id.lower()
    if (
        "/" in model_id
        and not lower.endswith(".gguf")
        and not model_id.startswith(("/", "."))
    ):
        return ["--hf-repo", model_id]
    return None


def _models_dir():
    """The explicit models directory from the environment, or None."""
    value = os.environ.get("LAMA_OLE_LLAMACPP_MODELS_DIR")
    if not value:
        return None
    return os.path.expanduser(value)


def can_autostart(model_id):
    """Whether there is anything worth serving (avoids pointless spawns).

    True when the targeted model resolves to a file/HF id, an explicit
    ``LAMA_OLE_LLAMACPP_MODELS_DIR`` exists, or the default llama.cpp cache
    directory exists.
    """
    if resolve_model_args(model_id) is not None:
        return True
    directory = _models_dir()
    if directory:
        return os.path.isdir(directory)
    return os.path.isdir(default_models_dir())


def build_command(host, model_id=None, options=None, keep_alive=None, api_key=None):
    """Assemble the llama-server argv.

    Returns ``(argv, mode)`` where mode is ``"router"`` or ``"single"``.
    Raises :class:`LauncherError` when no binary is available.
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

    model_args = resolve_model_args(model_id)
    if model_args is not None:
        argv += model_args
        mode = "single"
    else:
        directory = _models_dir()
        if directory:
            argv += ["--models-dir", directory]
        mode = "router"

    extra = os.environ.get("LAMA_OLE_LLAMACPP_ARGS")
    if extra:
        argv += shlex.split(extra)

    return argv, mode


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
    model_id=None,
    options=None,
    keep_alive=None,
    api_key=None,
    autostart=True,
    wait_timeout=120.0,
):
    """Start llama-server when needed; return a :class:`LaunchedServer` or None.

    Returns None (and prints nothing) when the host already answers, autostart
    is disabled, no binary is available, or there is nothing to serve. When a
    server is started, a one-line notice goes to stderr.
    """
    if not autostart or _is_ready(host) or not can_autostart(model_id):
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
            model_id=model_id,
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
    if _bool_env("LAMA_OLE_LLAMACPP_STOP_ON_EXIT", False):
        atexit.register(launched.stop)
    served = model_id if model_id else (
        "models from %s" % (_models_dir() or default_models_dir())
    )
    print(
        "[llamacpp] started llama-server (pid %d) on %s serving %s; log: %s"
        % (proc.pid, host, served, log_path),
        file=sys.stderr,
    )
    return launched