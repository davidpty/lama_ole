"""In-process fake llama-server for the llama.cpp backend tests.

Serves the OpenAI-compatible endpoints a real llama-server exposes, but
answers from a scripted config so tests are fast and deterministic:

  * GET  /v1/models   -> the configured model list
  * GET  /props       -> {"default_generation_settings": {"n_ctx": N}}
  * GET  /slots       -> the configured loaded slots
  * POST /v1/chat/completions -> one SSE stream per request, in call order

Every request is recorded (method, path, body) so tests can assert exactly
what the client sent. ``fail_next()`` makes the next chat request fail with
an HTTP 500 error body, for error-surfacing tests.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def content_chunk(text, finish_reason=None):
    """SSE event carrying a content delta."""
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish_reason}]}


def thinking_chunk(text):
    """SSE event carrying a reasoning delta (thinking)."""
    return {"choices": [{"delta": {"reasoning_content": text}, "finish_reason": None}]}


def tool_call_fragment(index, call_id, name, arguments):
    """SSE event carrying one fragment of a tool call."""
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call_id,
                            "function": {"name": name, "arguments": arguments},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }


def final_chunk(usage=None, timings=None, finish_reason="stop"):
    """SSE event signalling the end of a stream (optionally with metrics)."""
    event = {"choices": [{"delta": {}, "finish_reason": finish_reason}]}
    if usage:
        event["usage"] = usage
    if timings:
        event["timings"] = timings
    return event


def sse_body(*events):
    """Build an SSE response body from event dicts, ending with [DONE]."""
    parts = ["data: %s\n\n" % json.dumps(event) for event in events]
    parts.append("data: [DONE]\n\n")
    return "".join(parts)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, payload, code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, frames):
        body = "".join(frames).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {"raw": raw.decode("utf-8", "replace")}

    def do_GET(self):
        cfg = self.server.cfg
        if self.path.startswith("/v1/models"):
            self._send_json({"data": [{"id": m} for m in cfg["models"]]})
        elif self.path.startswith("/props"):
            self._send_json(
                {"default_generation_settings": {"n_ctx": cfg["n_ctx"]}}
            )
        elif self.path.startswith("/slots"):
            self._send_json(
                [{"model": m, "n_ctx": cfg["n_ctx"]} for m in cfg["slots"]]
            )
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        cfg = self.server.cfg
        body = self._read_body()
        cfg["requests"].append({"path": self.path, "body": body})

        if not self.path.startswith("/v1/chat/completions"):
            self._send_json({"error": "not found"}, 404)
            return

        with cfg["lock"]:
            if cfg.get("fail_next"):
                cfg["fail_next"] = False
                self._send_json({"error": "simulated failure"}, 500)
                return
            index = cfg["call_index"]
            cfg["call_index"] += 1

        streams = cfg["streams"]
        frames = (
            streams[index]
            if index < len(streams)
            else [sse_body(final_chunk())]
        )
        self._send_sse(frames)


class FakeLlamaServer:
    """Context manager: start the server and expose its base URL + config."""

    def __init__(self, models=None, n_ctx=4096, slots=None, streams=None):
        self.cfg = {
            "models": models or ["qwen2.5-7b-instruct-q4_k_m.gguf"],
            "n_ctx": n_ctx,
            "slots": slots if slots is not None else [],
            "streams": list(streams or []),
            "requests": [],
            "call_index": 0,
            "fail_next": False,
            "lock": threading.Lock(),
        }
        self._httpd = None
        self._thread = None

    def __enter__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.cfg = self.cfg
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
        return False

    @property
    def url(self):
        host, port = self._httpd.server_address
        return "http://%s:%d" % (host, port)

    @property
    def requests(self):
        return list(self.cfg["requests"])

    @property
    def calls(self):
        return self.cfg["call_index"]

    def fail_next(self):
        self.cfg["fail_next"] = True
