"""LSP session manager: one client per language, document freshness, diagnostics.

``LspSessionManager`` is the layer the tool functions talk to. It keeps one
long-lived ``LspClient`` per language, re-syncs files whose on-disk content
changed since the last sync (the ``edit`` toolset modifies files directly), and
caches ``textDocument/publishDiagnostics`` notifications pushed asynchronously
by the server.
"""

import atexit
import os
import threading

from typing import Dict, List, Optional, Tuple

from .client import LspClient, LspClientCrashed, LspConfigError
from .registry import resolve_server, language_for_path

_LOG_LIMIT = 200


class LspSessionManager:
    """Holds the running language-server sessions and per-document state."""

    def __init__(
        self,
        root_dir: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> None:
        self._root_dir = root_dir or os.environ.get("LAMA_OLE_LSP_ROOT") or os.getcwd()
        try:
            self._request_timeout = float(
                request_timeout
                if request_timeout is not None
                else os.environ.get("LAMA_OLE_LSP_TIMEOUT", "15")
            )
        except ValueError:
            self._request_timeout = 15.0

        self._clients = {}  # type: Dict[str, LspClient]
        self._sync = {}  # type: Dict[str, Dict[str, tuple]]  language -> {path: state}
        self._restarts = {}  # type: Dict[str, int]  language -> consecutive restarts
        self._diagnostics = {}  # type: Dict[str, List[dict]]  uri -> [diagnostic, ...]
        self._log = []  # type: List[str]
        self._lock = threading.Lock()

        atexit.register(self.stop_all)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def get_client(self, language: str, root_dir: Optional[str] = None) -> LspClient:
        """Return the running client for *language*, starting it if needed.

        A session whose server crashed is restarted once automatically per
        explicit start; a second crash in a row surfaces a ``LspClientCrashed``
        telling the caller to run ``lsp_start`` again explicitly.
        """
        root_dir = root_dir or self._root_dir
        with self._lock:
            client = self._clients.get(language)
        if client is not None and client.is_running:
            return client
        if client is not None:
            restart_count = self._restarts.get(language, 0)
            if restart_count >= 1:
                raise LspClientCrashed(
                    "Language server for '%s' crashed again after auto-restart. "
                    "Run 'lsp_start' to start a fresh session." % language
                )
            # One free auto-restart per explicit start; do not reset the marker
            # so a second crash surfaces the error above instead of restarting
            # again indefinitely.
            with self._lock:
                self._restarts[language] = restart_count + 1
            client.start()
            return client
        return self._start_new(language, root_dir)

    def _start_new(self, language: str, root_dir: str) -> LspClient:
        command = resolve_server(language)  # raises LspConfigError
        client = LspClient(
            command,
            language=language,
            root_dir=root_dir,
            cwd=root_dir,
            env=os.environ.copy(),
            request_timeout=self._request_timeout,
            on_notification=self._handle_notification,
            on_log=self._handle_log,
        )
        client.start()
        with self._lock:
            self._clients[language] = client
            self._sync[language] = {}
            self._restarts[language] = 0
        return client

    def stop_session(self, language: str) -> bool:
        """Shutdown and exit the session for *language*. Returns False if none."""
        with self._lock:
            client = self._clients.get(language)
        if client is None:
            return False
        client.shutdown()
        with self._lock:
            self._clients.pop(language, None)
            self._sync.pop(language, None)
            self._restarts.pop(language, None)
        return True

    def stop_all(self) -> None:
        """Shutdown every session (idempotent; registered as atexit handler)."""
        with self._lock:
            clients = list(self._clients.values())
            languages = list(self._clients)
        for client in clients:
            try:
                client.shutdown()
            except Exception:
                try:
                    client.kill()
                except Exception:
                    pass
        with self._lock:
            for language in languages:
                self._clients.pop(language, None)
                self._sync.pop(language, None)
                self._restarts.pop(language, None)

    # ------------------------------------------------------------------
    # Document freshness
    # ------------------------------------------------------------------

    def sync_document(self, path: str, language: Optional[str] = None) -> str:
        """Ensure the server has the current on-disk content of *path*.

        Sends ``textDocument/didOpen`` on first sync, a full-text
        ``textDocument/didChange`` (version bump) when the file changed since the
        last sync, and nothing when it is unchanged. Returns the effective
        language id.
        """
        if language is None:
            language = language_for_path(path)
        if not language:
            raise LspConfigError(
                "Cannot infer a language for '%s'; pass 'language' explicitly."
                % path
            )
        client = self.get_client(language)
        abspath = os.path.abspath(path)
        uri = self.path_to_uri(abspath)
        with open(abspath, "r", encoding="utf-8") as f:
            text = f.read()
        stat = os.stat(abspath)
        mtime = stat.st_mtime
        size = stat.st_size

        with self._lock:
            state = self._sync.get(language, {})
            prev = state.get(abspath)
            if prev is None:
                version = 1
                client.notify(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": uri,
                            "languageId": language,
                            "version": version,
                            "text": text,
                        }
                    },
                )
            elif (prev[0], prev[1]) != (mtime, size):
                version = prev[2] + 1
                client.notify(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": uri, "version": version},
                        "contentChanges": [{"text": text}],
                    },
                )
            else:
                return language
            state[abspath] = (mtime, size, version, uri)
            self._sync[language] = state
        return language

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(self, path: str) -> List[dict]:
        """Return the cached ``publishDiagnostics`` list for *path* (or [])."""
        uri = self.path_to_uri(os.path.abspath(path))
        with self._lock:
            return list(self._diagnostics.get(uri, []))

    def get_active_client(self) -> Optional[LspClient]:
        """Return the first running client, or None if no session is active.

        Used for workspace-wide requests that have no file path to derive a
        language from (``workspace/symbol``).
        """
        with self._lock:
            for client in self._clients.values():
                if client.is_running:
                    return client
        return None

    @staticmethod
    def path_to_uri(path: str) -> str:
        """Convert a filesystem path to the ``file://`` URI used as document key."""
        return LspClient._path_to_uri(os.path.abspath(path))

    def _handle_notification(self, message: dict) -> None:
        method = message.get("method")
        if method == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = params.get("uri")
            if uri is not None:
                with self._lock:
                    self._diagnostics[uri] = list(params.get("diagnostics") or [])

    def _handle_log(self, line: str) -> None:
        with self._lock:
            self._log.append(line)
            if len(self._log) > _LOG_LIMIT:
                del self._log[: len(self._log) - _LOG_LIMIT]

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Snapshot of every session: process info, capabilities, log tail."""
        with self._lock:
            clients = dict(self._clients)
            log_tail = list(self._log)
            diag_counts = {
                uri: len(items) for uri, items in self._diagnostics.items()
            }
        result = {}
        for language, client in clients.items():
            result[language] = {
                "pid": client.pid,
                "running": client.is_running,
                "command": list(client.command),
                "capabilities": client.capabilities,
                "diagnostics_count": sum(
                    n for uri, n in diag_counts.items() if uri.startswith("file://")
                ),
                "log_tail": client.log[-10:],
            }
        return {
            "root_dir": self._root_dir,
            "request_timeout": self._request_timeout,
            "sessions": result,
            "log_tail": log_tail[-20:],
        }


# Module-level singleton used by the tool surface. Registered for atexit cleanup
# at import time so sessions die with the process regardless of load order.
_manager = LspSessionManager()


def get_manager() -> LspSessionManager:
    return _manager
