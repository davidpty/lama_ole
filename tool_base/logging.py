import time
import json
from typing import Optional, Any


def _log_ndjson_message(handle, model: str, message: Any) -> None:
    """Write a single conversation message as one NDJSON line."""
    data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": model,
        "message": message,
    }
    handle.write(json.dumps(data, ensure_ascii=False) + "\n")
    handle.flush()


class StateLogger:
    """Centralized logger writing a timestamp before every logical state slice.

    A "state slice" is one logical event block (user input, model thinking,
    model output, tool call or tool result). The logger tracks whether a
    timestamp was already written for the current slice so that consecutive
    writes belonging to the same block (e.g. streamed thinking chunks) only
    produce a single timestamp line.
    """

    def __init__(self, handle=None, path: Optional[str] = None):
        self._handle = handle
        self._owns_handle = handle is None and path is not None
        if self._owns_handle:
            self._handle = open(path, "w", encoding="utf-8")
        self._slice_written = False

    @property
    def handle(self):
        return self._handle

    def new_slice(self) -> None:
        """Start a new logical block; the next write gets a fresh timestamp."""
        self._slice_written = False

    def write(self, text: str, marker: Optional[str] = None) -> None:
        """Write text to the log, preceded by a timestamp line on a new slice."""
        if not self._handle:
            return
        if not self._slice_written:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            prefix = f"\nTIME: {ts}\n"
            if marker:
                prefix += f"[{marker}] "
            self._handle.write(prefix)
            self._slice_written = True
        self._handle.write(text)
        self._handle.flush()

    def write_input(self, text: str) -> None:
        self.write(text, marker="INPUT")

    def write_thought(self, text: str) -> None:
        self.write(text, marker="THOUGHT")

    def write_output(self, text: str) -> None:
        self.write(text, marker="OUTPUT")

    def write_tool_call(self, text: str) -> None:
        self.write(text, marker="TOOL_CALL")

    def write_tool_result(self, text: str) -> None:
        self.write(text, marker="TOOL_RESULT")

    def close(self) -> None:
        """Close the handle if this logger opened it."""
        if self._owns_handle and self._handle is not None:
            self._handle.close()
            self._handle = None


def _state_ts(handle) -> None:
    """Write one timestamp line at the start of a state. Bounded by newlines."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    handle.write(f"\nTIME: {ts}\n")


def _write_input(handle, text: str) -> None:
    """Write user input with a timestamp line before it. Bounded by newlines."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    handle.write(f"\nTIME: {ts}\n{text}")


def _log_messages_payload(messages, file):
    preview = []
    for m in messages:
        entry = {"role": m["role"]}
        if m.get("content"):
            entry["content"] = m["content"][:2000]
        if m.get("tool_calls"):
            entry["tool_calls"] = m["tool_calls"]
        if m.get("tool_name"):
            entry["tool_name"] = m["tool_name"]
        preview.append(entry)
    print("[messages sent to API]", file=file)
    print(json.dumps(preview, indent=2, ensure_ascii=False)[:10000], file=file)
    print("[/messages]", file=file, flush=True)


def _log_chunk(msg, file):
    parts = []
    if msg.content:
        parts.append(f"content={msg.content!r}")
    if msg.tool_calls:
        calls = ", ".join(
            f"{tc.function.name}({dict(tc.function.arguments)})"
            for tc in msg.tool_calls
        )
        parts.append(f"tool_calls=[{calls}]")
    print(f"[chunk: {', '.join(parts)}]", file=file, flush=True)
