"""llama.cpp backend client.

Talks to a running ``llama-server`` over its OpenAI-compatible HTTP API
(``/v1/chat/completions``, ``/v1/models``, ``/props``, ``/slots``) using only
the standard library. Streamed chunks are parsed with the tiny SSE reader in
:mod:`.sse`.

Metrics mapping (Ollama attribute names kept for the ctx meter):

    prompt_eval_count        <- usage.prompt_tokens
    eval_count               <- usage.completion_tokens
    prompt_eval_duration     <- timings.prompt_ms (converted to ns)
    eval_duration            <- timings.predicted_ms (converted to ns)

When llama-server does not include ``timings`` we fall back to the wall-clock
time spent receiving the chunk.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Optional, List

from .base import ListResponse, ModelClient, ModelEntry, StreamChunk, StreamMessage
from .sse import iter_sse_events


class _Function(SimpleNamespace):
    pass


class _ToolCall(SimpleNamespace):
    function: _Function = None


class LlamaCppClient(ModelClient):
    """llama.cpp backend client (llama-server OpenAI-compatible API)."""

    name = "llamacpp"

    def __init__(self, host=None, api_key=None, launched=False):
        self.host = (host or "http://localhost:8080").rstrip("/")
        self.api_key = api_key
        self.launched = launched
        self._warned = set()
        self._tool_acc = []
        self._server_ctx = None
        self._server_ctx_fetched = False

    # -- HTTP plumbing -------------------------------------------------------

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key
        return headers

    def _request(self, method, path, payload=None, timeout=120.0):
        url = self.host + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url, data=data, headers=self._headers(), method=method
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = b""
            if e.fp is not None:
                try:
                    body = e.fp.read()
                except Exception:
                    body = b""
            detail = body.decode("utf-8", errors="replace").strip()
            if detail:
                try:
                    parsed = json.loads(detail)
                    detail = parsed.get("error", detail) if isinstance(parsed, dict) else detail
                except Exception:
                    pass
            raise RuntimeError(
                "llama.cpp server (%s) returned HTTP %s: %s"
                % (self.host, e.code, detail or e.reason)
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                "cannot reach llama.cpp server at %s: %s" % (self.host, e.reason)
            ) from e

    def _get_json(self, path, timeout=5.0):
        with self._request("GET", path, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # -- message normalization ----------------------------------------------

    @staticmethod
    def _normalize_message(msg, pending_tool_ids):
        """Convert an internal message dict to the OpenAI chat format.

        Internal-only keys (``timestamp``, ``thinking``, ``diff``, ``file``,
        ``tool_name``) are dropped; tool call ids are assigned by sequence so
        assistant tool calls and the following ``role: tool`` results pair up
        without relying on the model echoing an id.
        """
        role = msg.get("role", "user")
        content = msg.get("content")
        if content is not None and not isinstance(content, str):
            content = str(content)

        out = {"role": role, "content": content}

        if role == "tool":
            if pending_tool_ids:
                out["tool_call_id"] = pending_tool_ids.pop(0)
            return out

        if role == "assistant":
            tcs = msg.get("tool_calls")
            if tcs:
                out["tool_calls"] = []
                for i, tc in enumerate(tcs):
                    fn = tc.get("function", tc) if isinstance(tc, dict) else tc
                    if isinstance(fn, dict):
                        name = fn.get("name", "") or ""
                        arguments = fn.get("arguments", {}) or {}
                    else:
                        name = getattr(fn, "name", "") or ""
                        arguments = getattr(fn, "arguments", {}) or {}
                    if isinstance(arguments, dict):
                        arguments = json.dumps(arguments)
                    elif not isinstance(arguments, str):
                        arguments = json.dumps(arguments or {})
                    call_id = "call_%d_%d" % (len(pending_tool_ids), i)
                    pending_tool_ids.append(call_id)
                    out["tool_calls"].append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    )
            if not out.get("tool_calls") and not content:
                out["content"] = ""
        return out

    # -- request building ----------------------------------------------------

    def _build_payload(self, model, messages, tools, options):
        pending_tool_ids = []
        normalized = [self._normalize_message(m, pending_tool_ids) for m in messages]
        payload = {"model": model, "messages": normalized, "stream": True}
        if tools:
            payload["tools"] = tools

        opts = {}
        if options:
            mapping = {
                "temperature": "temperature",
                "top_p": "top_p",
                "top_k": "top_k",
                "num_predict": "max_tokens",
                "repeat_penalty": "repeat_penalty",
                "presence_penalty": "presence_penalty",
                "frequency_penalty": "frequency_penalty",
                "min_p": "min_p",
                "seed": "seed",
            }
            for key, value in options.items():
                okey = mapping.get(key)
                if okey is None or value is None:
                    continue
                opts[okey] = value
            for key in ("num_ctx", "num_gpu"):
                if options.get(key) is not None and not self.launched and key not in self._warned:
                    self._warned.add(key)
                    print(
                        "[llamacpp] option '%s' is ignored (%s)" % (
                            key, self._ignored_reason(key)
                        ),
                        file=sys.stderr,
                    )
        if opts:
            payload.update(opts)
        return payload

    def _ignored_reason(self, key):
        """Explain why a server-managed option is ignored (one-time warning)."""
        if key == "num_ctx":
            ctx = self._load_server_ctx()
            if ctx:
                return (
                    "the server context is %d; launch with -c/--ctx-size "
                    "to change it" % ctx
                )
            return "the server configures its own context at launch (-c/--ctx-size)"
        return "the server manages its own context/sampling at launch"

    def _load_server_ctx(self):
        """Best-effort, cached lookup of the server's configured n_ctx."""
        if self._server_ctx_fetched:
            return self._server_ctx
        self._server_ctx_fetched = True
        try:
            info = self.show(None)
            self._server_ctx = (info.modelinfo or {}).get("llama.context_length")
        except Exception:
            self._server_ctx = None
        return self._server_ctx

    # -- chat ------------------------------------------------------------------

    def chat(self, model, messages, stream=True, tools=None, options=None,
             keep_alive=None):
        if keep_alive is not None and not self.launched and "keep_alive" not in self._warned:
            self._warned.add("keep_alive")
            print(
                "[llamacpp] --keep_alive is ignored (the llama.cpp server "
                "keeps its model resident; launch with --sleep-idle-seconds "
                "to unload after idle)",
                file=sys.stderr,
            )
        payload = self._build_payload(model, messages, tools, options)
        if not stream:
            return self._chat_once(payload)
        resp = self._request("POST", "/v1/chat/completions", payload)
        return self._iter_chunks(resp)

    def _chat_once(self, payload):
        payload = dict(payload)
        payload["stream"] = False
        with self._request("POST", "/v1/chat/completions", payload) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        chunk = StreamChunk(
            message=StreamMessage(
                content=message.get("content") or "",
                thinking=message.get("reasoning_content") or "",
            )
        )
        self._apply_usage(data, chunk, None)
        return chunk

    def _iter_chunks(self, resp):
        self._tool_acc = []
        started = time.monotonic()
        usage = {}
        timings = {}
        try:
            for event in iter_sse_events(resp):
                if not isinstance(event, dict):
                    continue
                if event.get("usage"):
                    usage = event["usage"]
                if event.get("timings"):
                    timings = event["timings"]
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                msg = StreamMessage()
                if delta.get("reasoning_content"):
                    msg.thinking = delta.get("reasoning_content") or ""
                if delta.get("content"):
                    msg.content = delta.get("content") or ""
                if delta.get("tool_calls"):
                    msg.tool_calls = self._accumulate_tool_calls(delta["tool_calls"])
                # A bare metrics event (empty delta, usage attached) must still
                # be surfaced so the engine records the token counts.
                if not (msg.content or msg.thinking or msg.tool_calls):
                    if not (usage or timings):
                        continue
                yield self._make_chunk(msg, usage, timings, started)
                if choices[0].get("finish_reason") == "stop":
                    break
        finally:
            resp.close()

    def _accumulate_tool_calls(self, deltas):
        """Merge fragment deltas of tool calls into complete calls.

        llama-server streams each tool call as fragments
        ``[{index, id, function: {name, arguments}}]``. Fragments for the same
        index are concatenated and the accumulated ``arguments`` JSON string is
        parsed into a dict. The engine keeps the last chunk's tool calls, so a
        later complete fragment set replaces an earlier partial one.
        """
        for frag in deltas:
            idx = frag.get("index", 0)
            while len(self._tool_acc) <= idx:
                self._tool_acc.append({"id": "", "name": "", "arguments": ""})
            slot = self._tool_acc[idx]
            if frag.get("id"):
                slot["id"] = frag["id"]
            fn = frag.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]
        out = []
        for slot in self._tool_acc:
            args_str = slot["arguments"].strip()
            parsed = {}
            if args_str:
                try:
                    parsed = json.loads(args_str)
                    if not isinstance(parsed, dict):
                        parsed = {"value": parsed}
                except json.JSONDecodeError:
                    parsed = {"raw": args_str}
            out.append(
                _ToolCall(
                    id=slot["id"],
                    function=_Function(name=slot["name"], arguments=parsed),
                )
            )
        return out

    @staticmethod
    def _usage_counts(usage, timings):
        """Extract prompt/eval token counts.

        The reference llama-server puts them in ``usage``
        (``prompt_tokens``/``completion_tokens``); newer Ollama-registry
        flavors only send ``timings`` with ``prompt_n``/``predicted_n``.
        """
        prompt = None
        eval_count = None
        if usage:
            prompt = usage.get("prompt_tokens")
            eval_count = usage.get("completion_tokens")
        if prompt is None and timings:
            prompt = timings.get("prompt_n")
        if eval_count is None and timings:
            eval_count = timings.get("predicted_n")
        return prompt, eval_count

    def _make_chunk(self, msg, usage, timings, started):
        prompt_eval_count, eval_count = self._usage_counts(usage, timings)
        prompt_eval_duration = None
        eval_duration = None
        if timings:
            if timings.get("prompt_ms"):
                prompt_eval_duration = int(timings["prompt_ms"] * 1_000_000)
            if timings.get("predicted_ms"):
                eval_duration = int(timings["predicted_ms"] * 1_000_000)
        if eval_duration is None:
            eval_duration = int((time.monotonic() - started) * 1_000_000_000)
        return StreamChunk(
            message=msg,
            prompt_eval_count=prompt_eval_count,
            eval_count=eval_count,
            eval_duration=eval_duration,
            prompt_eval_duration=prompt_eval_duration,
        )

    def _apply_usage(self, data, chunk, started):
        usage = data.get("usage") or {}
        timings = data.get("timings") or {}
        chunk.prompt_eval_count, chunk.eval_count = self._usage_counts(usage, timings)
        if timings.get("prompt_ms"):
            chunk.prompt_eval_duration = int(timings["prompt_ms"] * 1_000_000)
        if timings.get("predicted_ms"):
            chunk.eval_duration = int(timings["predicted_ms"] * 1_000_000)
        elif chunk.eval_duration is None and started is not None:
            chunk.eval_duration = int((time.monotonic() - started) * 1_000_000_000)

    # -- listing / introspection -------------------------------------------

    def list(self):
        data = self._get_json("/v1/models")
        models = []
        for m in data.get("data", []) or []:
            ident = m.get("id") or ""
            models.append(ModelEntry(model=ident, name=ident))
        return ListResponse(models=models)

    def ps(self):
        """Loaded slots; llama-server keeps one slot per model, so this is
        either one entry (a model is loaded) or none. Errors are swallowed."""
        try:
            slots = self._get_json("/slots")
        except RuntimeError:
            return ListResponse()
        models = []
        for slot in slots if isinstance(slots, list) else []:
            ident = slot.get("model", "") or ""
            models.append(
                ModelEntry(
                    model=ident,
                    name=ident,
                    context_length=slot.get("n_ctx"),
                )
            )
        return ListResponse(models=models)

    def show(self, model):
        """Return ``.modelinfo`` with the server's context window.

        llama-server has no per-model show endpoint; ``/props`` reports the
        configured ``n_ctx`` which is exactly what the ctx meter needs.
        """
        try:
            props = self._get_json("/props")
        except RuntimeError:
            return SimpleNamespace(modelinfo={}, parameters={})
        ctx = None
        dgs = props.get("default_generation_settings") or {}
        if dgs.get("n_ctx"):
            ctx = dgs["n_ctx"]
        modelinfo = {}
        if ctx:
            modelinfo["llama.context_length"] = ctx
        return SimpleNamespace(modelinfo=modelinfo, parameters={})

    def stop(self, model):
        return False

    def supports_native_websearch(self, model):
        return False
