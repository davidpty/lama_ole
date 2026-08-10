"""LSP language server client: process lifecycle, reader thread, JSON-RPC routing.

``LspClient`` owns exactly one server subprocess (communicating over stdio with
``Content-Length`` framing) plus an always-on reader thread. The reader thread
drains stdout continuously, which both captures asynchronous notifications
(``textDocument/publishDiagnostics``, ``window/logMessage``, ...) and prevents
pipe-buffer deadlock when a server floods stdout.

Lifecycle::

    new -> starting -> ready -> shutting_down -> stopped
                            \\-> crashed (unexpected process exit)
"""

import itertools
import os
import subprocess
import threading
import urllib.parse

from typing import Callable, List, Optional

from .jsonrpc import JsonRpcCodec


class LspError(Exception):
    """Server returned a JSON-RPC error response."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__("%d: %s" % (code, message))
        self.code = code
        self.message = message


class LspConfigError(Exception):
    """Unknown language, missing server binary or invalid configuration."""


class LspClientCrashed(Exception):
    """Server process exited while requests were pending (or not running)."""


class LspTimeout(Exception):
    """Request exceeded its timeout."""


# JSON-RPC 2.0 error codes (subset used by this client)
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INTERNAL = -32603

_LOG_LIMIT = 200


class LspClient:
    """One language server process speaking JSON-RPC 2.0 over stdio."""

    def __init__(
        self,
        command: List[str],
        *,
        language: str,
        root_dir: str,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        request_timeout: float = 15.0,
        on_notification: Optional[Callable[[dict], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.command = list(command)
        self.language = language
        self.root_dir = root_dir
        self._cwd = cwd
        self._env = env
        self.request_timeout = request_timeout
        self._on_notification = on_notification
        self._on_log = on_log

        self._codec = JsonRpcCodec()
        self._proc = None  # type: Optional[subprocess.Popen]
        self._reader_thread = None  # type: Optional[threading.Thread]
        self._stderr_thread = None  # type: Optional[threading.Thread]
        self._pending = {}  # id -> [method, threading.Event, result_box, error_box]
        self._id_seq = itertools.count(1)
        self._write_lock = threading.Lock()
        self._capabilities = {}  # type: dict
        self._log = []  # type: List[str]
        self._log_lock = threading.Lock()
        self._shutting_down = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> Optional[int]:
        if self._proc is not None:
            return self._proc.pid
        return None

    @property
    def capabilities(self) -> dict:
        return dict(self._capabilities)

    @property
    def log(self) -> List[str]:
        with self._log_lock:
            return list(self._log)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the server process and complete the initialize handshake.

        Spawns the subprocess, starts the reader and stderr threads, sends the
        ``initialize`` request, stores the returned capabilities, then sends the
        ``initialized`` notification. Raises ``LspError`` / ``LspTimeout`` /
        ``LspConfigError`` on handshake failure and cleans up the process in
        that case.
        """
        if self.is_running:
            return
        self._shutting_down = False
        self._capabilities = {}
        env = dict(self._env) if self._env else None
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._cwd,
                env=env,
            )
        except OSError as exc:
            self._proc = None
            raise LspConfigError(
                "Failed to start language server %r: %s" % (self.command, exc)
            )

        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="lsp-reader-%s" % self.language, daemon=True
        )
        self._reader_thread.start()
        if self._proc.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=self._stderr_loop,
                name="lsp-stderr-%s" % self.language,
                daemon=True,
            )
            self._stderr_thread.start()

        try:
            result = self.request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": self._path_to_uri(self.root_dir),
                    "capabilities": {},
                },
                timeout=30.0,
            )
            if not isinstance(result, dict):
                result = {}
            self._capabilities = result.get("capabilities") or {}
            self.notify("initialized", {})
        except Exception:
            self.kill()
            raise

    def shutdown(self) -> None:
        """Graceful shutdown: ``shutdown`` request, ``exit`` notification, cleanup."""
        if not self.is_running:
            self._join_threads()
            return
        self._shutting_down = True
        try:
            self.request("shutdown", {}, timeout=5.0)
        except Exception:
            pass
        try:
            self.notify("exit", {})
        except Exception:
            pass
        if self._proc is not None:
            try:
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        self._join_threads()

    def kill(self) -> None:
        """Hard-terminate the process and join the helper threads."""
        self._shutting_down = True
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                pass
        self._join_threads()

    def _join_threads(self) -> None:
        for thread in (self._reader_thread, self._stderr_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=3.0)

    # ------------------------------------------------------------------
    # Requests / notifications
    # ------------------------------------------------------------------

    def request(self, method: str, params: dict, timeout: Optional[float] = None):
        """Send a request and block until the response arrives.

        Returns the ``result`` field. Raises ``LspError`` on a JSON-RPC error
        response, ``LspTimeout`` when the timeout elapses, and
        ``LspClientCrashed`` if the server dies meanwhile.
        """
        if not self.is_running:
            raise LspClientCrashed("Language server is not running")
        if timeout is None:
            timeout = self.request_timeout
        msg_id = next(self._id_seq)
        event = threading.Event()
        result_box = []
        error_box = []
        self._pending[msg_id] = [method, event, result_box, error_box]
        try:
            self._write(
                {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
            )
            if not event.wait(timeout):
                raise LspTimeout("Request '%s' timed out after %.1fs" % (method, timeout))
        finally:
            self._pending.pop(msg_id, None)
        if error_box:
            raise error_box[0]
        return result_box[0]

    def notify(self, method: str, params: dict) -> None:
        """Send a fire-and-forget notification."""
        if not self.is_running:
            raise LspClientCrashed("Language server is not running")
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, message: dict) -> None:
        data = self._codec.encode(message)
        with self._write_lock:
            if self._proc is None or self._proc.stdin is None:
                raise LspClientCrashed("Language server is not running")
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise LspClientCrashed("Write to language server failed: %s" % exc)

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        try:
            while True:
                if self._proc is None or self._proc.stdout is None:
                    break
                # read1 returns as soon as any bytes are available; read(n)
                # would block until n bytes (or EOF), deadlocking on small
                # responses from a still-alive server.
                chunk = self._proc.stdout.read1(65536)
                if not chunk:
                    break
                try:
                    for message in self._codec.feed(chunk):
                        self._handle_message(message)
                except ValueError as exc:
                    self._log_line("framing error: %s" % exc)
                    break
        finally:
            self._on_eof()

    def _handle_message(self, message: dict) -> None:
        has_id = "id" in message
        has_method = "method" in message
        if has_method and has_id:
            # Server-to-client request. v1 policy: refuse with methodNotFound
            # (callers use the session-level on_notification hook for writes).
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {
                        "code": ERROR_METHOD_NOT_FOUND,
                        "message": "server-to-client request '%s' is not supported"
                        % message.get("method"),
                    },
                }
            )
            return
        if has_id:
            # Response to one of our requests.
            pending = self._pending.get(message.get("id"))
            if pending is None:
                return
            _method, event, result_box, error_box = pending
            if "error" in message and message["error"] is not None:
                err = message["error"]
                error_box.append(
                    LspError(
                        err.get("code", ERROR_INTERNAL),
                        str(err.get("message", "")),
                    )
                )
            else:
                result_box.append(message.get("result"))
            event.set()
            return
        # Notification (no id).
        if self._on_notification is not None:
            try:
                self._on_notification(message)
            except Exception as exc:
                self._log_line("notification handler error: %s" % exc)
        if message.get("method") in ("window/logMessage", "window/showMessage"):
            params = message.get("params") or {}
            self._log_line(
                "[%s] %s" % (message.get("method"), params.get("message", ""))
            )

    def _stderr_loop(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            for raw in self._proc.stderr:
                self._log_line(raw.decode("utf-8", errors="replace").rstrip())
        except Exception:
            pass

    def _on_eof(self) -> None:
        crashed = not self._shutting_down and not self.is_running
        for _method, event, _result_box, error_box in self._pending.values():
            error_box.append(
                LspClientCrashed(
                    "Language server exited unexpectedly"
                    if crashed
                    else "Language server stopped"
                )
            )
            event.set()
        if crashed:
            self._log_line("server process exited unexpectedly")

    def _log_line(self, line: str) -> None:
        if not line:
            return
        with self._log_lock:
            self._log.append(line)
            if len(self._log) > _LOG_LIMIT:
                del self._log[: len(self._log) - _LOG_LIMIT]
        if self._on_log is not None:
            try:
                self._on_log(line)
            except Exception:
                pass

    @staticmethod
    def _path_to_uri(path: str) -> str:
        """Convert a filesystem path to a ``file://`` URI (best effort)."""
        abspath = os.path.abspath(path)
        return urllib.parse.urljoin("file://", urllib.parse.quote(abspath))
