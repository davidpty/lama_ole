"""Minimal Server-Sent-Events parser for llama.cpp's streaming responses.

llama-server emits ``data: {json}\n\n`` frames. We read lines lazily from the
already-open urllib response object, so chunks stream in as they arrive rather
than only after the request completes.
"""

import json
import sys


def iter_sse_events(stream):
    """Yield parsed JSON events from a line-based ``data:`` event stream.

    ``stream`` is an iterable of raw text lines (a urllib response object
    works directly). Comment lines (``: ...``), empty lines and the terminal
    ``data: [DONE]`` marker are skipped; malformed JSON lines are tolerated
    (a warning on stderr) instead of aborting the stream.
    """
    for raw in stream:
        if not raw:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not raw.lstrip().startswith("data:"):
            continue
        data = raw.partition("data:")[2].strip()
        if not data:
            continue
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError as e:
            print(
                f"[llamacpp] skipping malformed SSE frame: {e}",
                file=sys.stderr,
            )
