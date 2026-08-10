"""JSON-RPC 2.0 framing for the LSP toolset.

LSP transports JSON-RPC 2.0 messages over stdio using ``Content-Length``
framing::

    Content-Length: <n>\\r\\n
    \\r\\n
    <n bytes of JSON>

The codec in this module is pure (no I/O): it serializes outgoing messages and
incrementally decodes an incoming byte stream. It is fully unit-testable and
shared by ``lsp/client.py`` (the real client) and the fake server used by the
test suite.

Message shapes (JSON-RPC 2.0):

* request  -- ``{"jsonrpc": "2.0", "id": n, "method": "...", "params": {...}}``
* response -- ``{"jsonrpc": "2.0", "id": n, "result": ...}`` or ``{"error": ...}``
* notification -- ``{"jsonrpc": "2.0", "method": "...", "params": {...}}``
"""

import json

from typing import List, Optional


MAX_BUFFER_SIZE = 16 * 1024 * 1024  # 16 MiB hard cap on the decode buffer

_HEADER_TERMINATOR = b"\r\n\r\n"


class JsonRpcCodec:
    """Serialize and incrementally parse Content-Length framed JSON-RPC messages."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def reset(self) -> None:
        """Clear the internal decode buffer (used on server restart)."""
        self._buffer = bytearray()

    def encode(self, message: dict) -> bytes:
        """Serialize a message dict to Content-Length framed bytes."""
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        header = ("Content-Length: %d\r\n\r\n" % len(payload)).encode("ascii")
        return header + payload

    def feed(self, data: bytes) -> List[dict]:
        """Append raw bytes and return every complete message parsed from them.

        Partial messages stay buffered until the next ``feed()`` call. Raises
        ``ValueError`` on malformed framing or when the buffer cap is exceeded.
        """
        if not data:
            return []
        self._buffer.extend(data)
        if len(self._buffer) > MAX_BUFFER_SIZE:
            raise ValueError(
                "JSON-RPC buffer exceeded %d bytes (broken or malicious peer)"
                % MAX_BUFFER_SIZE
            )
        messages = []
        while True:
            body = self._extract_one()
            if body is None:
                break
            try:
                obj = json.loads(body)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("Invalid JSON-RPC payload: %s" % exc)
            if not isinstance(obj, dict):
                raise ValueError("JSON-RPC message must be a JSON object")
            messages.append(obj)
        return messages

    def _extract_one(self) -> Optional[bytes]:
        """Return the body bytes of the next complete message, or None."""
        terminator = self._buffer.find(_HEADER_TERMINATOR)
        if terminator < 0:
            return None
        header = bytes(self._buffer[:terminator])
        header_lines = header.split(b"\r\n")
        content_length = None
        for line in header_lines:
            if line.startswith(b"Content-Length:"):
                value = line[len(b"Content-Length:"):].strip()
                try:
                    parsed = int(value)
                except ValueError:
                    raise ValueError("Invalid Content-Length header: %r" % value)
                if content_length is not None:
                    raise ValueError("Duplicate Content-Length header")
                content_length = parsed
        if content_length is None:
            raise ValueError("Missing Content-Length header")
        if content_length < 0:
            raise ValueError("Negative Content-Length")
        body_start = terminator + len(_HEADER_TERMINATOR)
        body_end = body_start + content_length
        if len(self._buffer) < body_end:
            return None
        body = bytes(self._buffer[body_start:body_end])
        del self._buffer[:body_end]
        return body
