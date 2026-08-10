# LSP Toolset — Transport, Client & Session Design

This document details the three engine pieces that power the toolset:
the JSON-RPC codec (`jsonrpc.py`), the client (`client.py`) and the session
manager (`session.py`). It is the "class outline & API" companion to
`001_overview.md`. All snippets follow `AGENTS.md` (Python 3.9+, `typing.Optional`
/ `Union`, no 3.10-only syntax).

## 1. JSON-RPC 2.0 framing — `lsp/jsonrpc.py`

LSP is JSON-RPC 2.0 over stdio with `Content-Length` framing:

```
Content-Length: 123\r\n
\r\n
{"jsonrpc":"2.0","id":1,"method":"textDocument/hover","params":{...}}
```

Three message shapes:

```python
# request (has an id, expects a response)
{"jsonrpc": "2.0", "id": 1, "method": "...", "params": {...}}

# response (echoes the request id; result XOR error)
{"jsonrpc": "2.0", "id": 1, "result": {...}}
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "..."}}

# notification (no id, no response)
{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {...}}
```

### `JsonRpcCodec` (incremental streaming decoder)

```python
class JsonRpcCodec:
    def __init__(self) -> None: ...
    def feed(self, data: bytes) -> List[dict]:
        """Append raw bytes; return every complete JSON-RPC message parsed."""
    def encode(self, message: dict) -> bytes:
        """Serialize a message dict to Content-Length framed bytes."""
    def reset(self) -> None:
        """Clear internal buffer (used on process restart)."""
```

Implementation notes:

- `encode()`: `json.dumps` then `"Content-Length: %d\r\n\r\n" % n` prefix; return
  the concatenated `bytes`.
- `feed()`: maintain a `bytearray` buffer; loop: parse header block up to
  `\r\n\r\n`, read `Content-Length: N`, and if `N` bytes are buffered, slice the
  body, `json.loads` it, append to the result list, and continue. A partial
  message simply stays buffered until the next `feed()` call.
- Tolerate `Content-Type` header lines (ignore unknown headers), require exactly
  one `Content-Length` header (else `raise ValueError`).
- Hard cap on buffered size (e.g. 16 MiB) so a broken server cannot exhaust
  memory.

Pure and fully unit-testable — no I/O.

## 2. Client — `lsp/client.py`

`LspClient` owns exactly one server subprocess and the always-on reader thread.

### Lifecycle

```
new → starting → ready → shutting_down → stopped
            ↘ crashed (unexpected process exit)      ↗
                  ↖   (auto-restart handled by session)
```

### API

```python
class LspClient:
    def __init__(
        self,
        command: List[str],          # fixed argv, e.g. ["pyright-langserver", "--stdio"]
        *,
        language: str,               # e.g. "python"
        root_dir: str,               # workspace root passed to initialize
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        request_timeout: float = 15.0,
        on_notification: Optional[Callable[[dict], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None: ...

    def start(self) -> None:
        """Popen the server, spawn the reader thread, send initialize + wait for
        response, then send 'initialized' notification. Raises on failure."""

    def request(self, method: str, params: dict, timeout: Optional[float] = None):
        """Send a request, block until the response, return the 'result' field.
        Raises LspError on an error response, TimeoutError on timeout."""

    def notify(self, method: str, params: dict) -> None:
        """Fire-and-forget notification (didOpen / didChange / exit)."""

    def shutdown(self) -> None:
        """Send 'shutdown' request (best-effort), then 'exit' notification,
        wait briefly, terminate the process and join the reader thread."""

    def kill(self) -> None:
        """Hard-terminate the process and join the reader thread (no handshake)."""

    @property
    def is_running(self) -> bool: ...
    @property
    def pid(self) -> Optional[int]: ...
    @property
    def capabilities(self) -> dict:   # parsed initialize result
        ...
    @property
    def log(self) -> List[str]:       # recent window/logMessage + stderr lines
        ...
```

### Reader thread (the critical piece)

Started in `start()`, runs until the pipe closes. Loop:

1. `read(65536)` from `proc.stdout`; on `b""` (EOF) the server exited — set
   `crashed`, fail every pending future with `LspClientCrashed`, invoke
   `on_log`, and stop.
2. `codec.feed(chunk)`; for each message:
   - has `id` **and** `method` → server-to-client request (e.g. `workspace/applyEdit`,
     `client/registerCapability`) — for v1, respond `error: methodNotFound` unless
     we implement it; log others.
   - has `id` but no `method` → response: resolve the pending future, pass
     `result` or raise `LspError(code, message)` on `error`.
   - no `id` → notification: dispatch to `on_notification` (session uses this
     for `textDocument/publishDiagnostics`, `window/logMessage`, etc.).
3. Concurrently, a small thread drains `proc.stderr` into the rolling log.

The thread keeps the pipe drained at all times, which both captures async
diagnostics and prevents deadlock when a server floods stdout.

### Pending-request tracking

```python
self._pending: Dict[int, Tuple[str, "threading.Event", Any, list]] = {}
# id → (method, event, result_container, error_container)
```

- `request()` allocates `id = next(self._id_seq)`, registers an `Event`,
  writes the encoded request, then `event.wait(timeout)`. On timeout the entry
  is removed and `TimeoutError` raised.
- The reader thread sets the event and fills result/error exactly once.
- On crash, all pending events are set with `LspClientCrashed` so no caller
  hangs forever.

## 3. Session manager — `lsp/session.py`

`LspSessionManager` is the module-level singleton that tool functions use. It
keeps one `LspClient` per language plus per-document sync state.

### API

```python
class LspSessionManager:
    def __init__(self, registry) -> None: ...

    def get_client(self, language: str, root_dir: Optional[str] = None) -> LspClient:
        """Return the running client for 'language', starting it if needed."""

    def stop_session(self, language: str) -> bool:
        """Shutdown + exit the session; False if none was running."""

    def stop_all(self) -> None:
        """Shutdown every session (atexit handler)."""

    def sync_document(self, path: str, language: Optional[str] = None) -> str:
        """Ensure the server has the current on-disk content. Reads the file,
        compares mtime/size against the last sync, sends didOpen (first time) or
        a full-text didChange (if changed). Returns the effective language id."""

    def get_diagnostics(self, path: str) -> List[dict]:
        """Return the cached publishDiagnostics list for a file (or [])."""

    def status(self) -> dict:
        """language → {pid, running, capabilities, diagnostics_count, log_tail}."""
```

### State kept per client

```python
self._clients: Dict[str, LspClient]              # language → client
self._sync: Dict[str, Dict[str, tuple]]          # language → {path: (mtime, size, version)}
self._diagnostics: Dict[str, List[dict]]         # uri → [diagnostic, ...]
self._log: List[str]                             # rolling server log (window/logMessage + stderr)
```

- `sync_document` resolves the language (explicit arg, else extension→language
  map), obtains the client, reads the file with `validate_path` up front, and
  sends:
  - first sync → `textDocument/didOpen` with `{"textDocument": {"uri", "languageId",
    "version": 1, "text"}}`;
  - changed → `textDocument/didChange` with a single full-range change
    `{"range": {"start": {"line":0,"character":0},
                "end": {"line": BIG, "character": 0}},
      "text": <full content>}` and `version += 1`.
- Every query tool calls `sync_document` first, so D1 (freshness) is enforced
  centrally and no tool can forget it.
- Diagnostics notifications arrive via the client's `on_notification` hook:
  key is `params["uri"]` → store `params["diagnostics"]`.
- Crash auto-restart: `get_client` tracks "crashed since last explicit start";
  on first query after a crash it calls `client.start()` again (one retry), then
  re-syncs the requested document (version 1 in a fresh server).

## 4. Server registry — `lsp/registry.py`

```python
def resolve_server(language: str) -> List[str]:
    """Return the argv for 'language' from LAMA_OLE_LSP_SERVERS or the default
    table. Raises LspConfigError if unknown/not-installed."""

def language_for_path(path: str) -> Optional[str]:
    """Infer a language id from a file extension (.py, .ts, .rs, .c, .cpp, ...)."""

def known_languages() -> List[str]: ...
```

- `LAMA_OLE_LSP_SERVERS` is a JSON object `{"python": "pyright-langserver --stdio",
  "rust": ["rust-analyzer"]}`. String values are split with `shlex.split`;
  list values are used as-is. This keeps the model out of the argv-building path.
- Default resolution: look up the table, then `shutil.which(first_element)`;
  a missing binary raises `LspConfigError("language server for '<lang>' not
  found. Install it or set LAMA_OLE_LSP_SERVERS")`.

## 5. Error taxonomy

```python
class LspError(Exception):
    """Server returned a JSON-RPC error response (code, message)."""

class LspConfigError(Exception):
    """Unknown language, missing server binary, invalid LAMA_OLE_LSP_SERVERS."""

class LspClientCrashed(Exception):
    """Server process exited while requests were pending."""

class LspTimeout(Exception):
    """Request exceeded the timeout."""
```

Tool functions translate these into `{"status": "error", "message": [...],
"hint": "run lsp_start / check lsp_status"}` dicts, so the model always gets a
structured, actionable failure.

## 6. Thread-safety

- `LspClient.request/notify` are safe for one caller at a time; the engine loop
  already serializes tool calls, so no locking is required beyond the
  per-pending-future `Event`.
- `LspSessionManager` holds a `threading.Lock` around `_clients` / `_sync`
  mutations; the reader thread only touches `_diagnostics` / `_log`, guarded by
  the same lock or a dedicated one.
- `atexit` registration happens in `session.py` module scope so any load order
  cleans up; `stop_all` is idempotent.
