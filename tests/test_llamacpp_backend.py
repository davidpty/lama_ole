"""Tests for the llama.cpp backend, the router, and tool-format dicts.

Covers the SSE parser, message normalization, streamed content/thinking,
fragmented tool-call accumulation, metric mapping, HTTP error surfacing, the
fake-server list/show/ps endpoints, router routing and merged listing, the
engine tool round-trip against the fake server, and the dict tool format
shared with Ollama.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

from tool_base import run_with_tools, to_openai_tools  # noqa: E402
from tool_base.models import Tool  # noqa: E402

from backends import (  # noqa: E402
    canonicalize,
    parse_model,
    create_router,
)
from backends.llamacpp import LlamaCppClient  # noqa: E402
from backends.ollama import OllamaClient  # noqa: E402
from backends.sse import iter_sse_events  # noqa: E402
from tests.fakes.fake_llama_server import (  # noqa: E402
    FakeLlamaServer,
    content_chunk,
    final_chunk,
    sse_body,
    thinking_chunk,
    tool_call_fragment,
)


# -- name parsing ------------------------------------------------------------


def test_parse_model_table():
    cases = [
        ("gemma2:2b", ("ollama", "gemma2:2b")),
        ("ollama.gemma2:2b", ("ollama", "gemma2:2b")),
        ("llamacpp.my-model", ("llamacpp", "my-model")),
        ("/models/qwen.gguf", ("llamacpp", "qwen.gguf")),
        ("llamacpp.qwen2.5-7b-q4_k_m.gguf", ("llamacpp", "qwen2.5-7b-q4_k_m.gguf")),
        (None, (None, None)),
    ]
    for name, expected in cases:
        assert parse_model(name) == expected, name


def test_canonicalize_forms():
    assert canonicalize("gemma2:2b") == "ollama.gemma2:2b"
    assert canonicalize("ollama.gemma2:2b") == "ollama.gemma2:2b"
    assert canonicalize("llamacpp.my-model") == "llamacpp.my-model"
    assert canonicalize("/models/qwen.gguf") == "llamacpp.qwen.gguf"


# -- SSE parser --------------------------------------------------------------


def test_sse_parser_yields_events_and_skips_noise():
    frames = [
        ": hello\n\n",
        "data: {\"a\": 1}\n\n",
        "\n",
        "data: {\"b\": 2}\n\n",
        "data: [DONE]\n\n",
        "data: {\"c\": 3}\n\n",
    ]
    events = list(iter_sse_events(frames))
    assert events == [{"a": 1}, {"b": 2}]


def test_sse_parser_tolerates_malformed_json(capsys):
    frames = ["data: not-json\n\n", "data: {\"ok\": true}\n\n"]
    assert list(iter_sse_events(frames)) == [{"ok": True}]
    assert "malformed SSE" in capsys.readouterr().err


# -- message normalization ---------------------------------------------------


def _normalize(messages):
    pending = []
    from backends.llamacpp import LlamaCppClient
    return [LlamaCppClient._normalize_message(m, pending) for m in messages]


def test_normalize_drops_internal_keys_and_strips_thinking():
    out = _normalize(
        [
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "answer",
                "thinking": "secret",
                "timestamp": "2026-01-01 00:00:00",
            },
        ]
    )
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[1] == {"role": "assistant", "content": "answer"}


def test_normalize_pairs_tool_calls_and_results_by_sequence():
    out = _normalize(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"function": {"name": "a", "arguments": {"x": 1}}},
                    {"function": {"name": "b", "arguments": {}}},
                ],
            },
            {"role": "tool", "content": "r1", "tool_name": "a", "diff": None},
            {"role": "tool", "content": "r2", "tool_name": "b"},
        ]
    )
    assert out[0]["tool_calls"][0]["id"] == "call_0_0"
    assert out[0]["tool_calls"][0]["function"]["arguments"] == '{"x": 1}'
    assert out[0]["tool_calls"][1]["id"] == "call_1_1"
    assert out[1] == {"role": "tool", "content": "r1", "tool_call_id": "call_0_0"}
    assert out[2] == {"role": "tool", "content": "r2", "tool_call_id": "call_1_1"}


# -- streaming content + thinking -------------------------------------------


def test_chat_stream_content_and_thinking():
    stream = sse_body(
        thinking_chunk("Let me think"),
        content_chunk("Hello"),
        content_chunk(" world"),
        final_chunk(
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            timings={"prompt_ms": 12.5, "predicted_ms": 34.0},
        ),
    )
    with FakeLlamaServer(streams=[stream]) as server:
        client = LlamaCppClient(host=server.url)
        chunks = list(client.chat(model="q", messages=[{"role": "user", "content": "hi"}]))
    text = "".join(c.message.content for c in chunks)
    thinking = "".join(c.message.thinking for c in chunks)
    assert text == "Hello world"
    assert thinking == "Let me think"
    assert chunks[-1].prompt_eval_count == 10
    assert chunks[-1].eval_count == 5
    assert chunks[-1].prompt_eval_duration == 12_500_000
    assert chunks[-1].eval_duration == 34_000_000


# -- fragmented tool calls ---------------------------------------------------


def test_chat_stream_accumulates_fragmented_tool_calls():
    stream = sse_body(
        tool_call_fragment(0, "call_9", "calculate", '{"a":'),
        tool_call_fragment(0, None, None, " 2, "),
        tool_call_fragment(0, None, None, '"b": 3}'),
        final_chunk(usage={"prompt_tokens": 1, "completion_tokens": 2}),
    )
    with FakeLlamaServer(streams=[stream]) as server:
        client = LlamaCppClient(host=server.url)
        chunks = list(client.chat(model="q", messages=[{"role": "user", "content": "hi"}]))
    calls = [c.message.tool_calls for c in chunks if c.message.tool_calls][-1]
    assert len(calls) == 1
    assert calls[0].id == "call_9"
    assert calls[0].function.name == "calculate"
    assert calls[0].function.arguments == {"a": 2, "b": 3}


# -- HTTP errors -------------------------------------------------------------


def test_http_error_surfaced_as_runtime_error():
    with FakeLlamaServer(streams=[]) as server:
        server.fail_next()
        client = LlamaCppClient(host=server.url)
        with pytest.raises(RuntimeError) as exc:
            list(client.chat(model="q", messages=[{"role": "user", "content": "hi"}]))
    assert "HTTP 500" in str(exc.value)
    assert "simulated failure" in str(exc.value)


# -- list / show / ps --------------------------------------------------------


def test_list_show_ps():
    with FakeLlamaServer(models=["q.gguf", "r.bin"], n_ctx=8192, slots=["q.gguf"]) as server:
        client = LlamaCppClient(host=server.url)
        resp = client.list()
        assert [m.model for m in resp.models] == ["q.gguf", "r.bin"]
        show = client.show("q.gguf")
        assert show.modelinfo.get("llama.context_length") == 8192
        running = client.ps()
        assert [m.model for m in running.models] == ["q.gguf"]
        assert running.models[0].context_length == 8192


def test_show_swallows_props_failure():
    client = LlamaCppClient(host="http://127.0.0.1:1")
    show = client.show("whatever")
    assert show.modelinfo == {}


# -- router ------------------------------------------------------------------


def test_router_merged_list_namespaced():
    stream = sse_body(content_chunk("ok"), final_chunk())
    with FakeLlamaServer(
        models=["q.gguf"], n_ctx=4096, slots=[], streams=[stream]
    ) as server:
        router = create_router(
            ollama_host="http://127.0.0.1:1", llamacpp_host=server.url
        )
        resp = router.list()
        assert [m.model for m in resp.models] == ["llamacpp.q.gguf"]
        assert router.canonicalize("q.gguf") == "llamacpp.q.gguf"


def test_router_routes_chat_to_llamacpp():
    stream = sse_body(content_chunk("ok"), final_chunk())
    with FakeLlamaServer(streams=[stream]) as server:
        router = create_router(
            ollama_host="http://127.0.0.1:1", llamacpp_host=server.url
        )
        chunks = list(
            router.chat(
                model="llamacpp.my-model",
                messages=[{"role": "user", "content": "hi"}],
            )
        )
    assert "".join(c.message.content for c in chunks) == "ok"
    sent = server.requests[-1]["body"]
    assert sent["model"] == "my-model"
    assert sent["messages"][0]["role"] == "user"


def test_router_resolve_default_model_llamacpp_only():
    with FakeLlamaServer(models=["q.gguf"]) as server:
        router = create_router(
            ollama_host="http://127.0.0.1:1", llamacpp_host=server.url
        )
        assert router.resolve_default_model() == "llamacpp.q.gguf"
        assert router.supports_native_websearch("llamacpp.q.gguf") is False
        assert router.supports_native_websearch("ollama.q") is True


def test_router_both_unreachable_resolve_none():
    router = create_router(
        ollama_host="http://127.0.0.1:1", llamacpp_host="http://127.0.0.1:2"
    )
    assert router.resolve_default_model() is None
    assert router.list().models == []


# -- engine round-trip through the fake server ------------------------------


def test_run_with_tools_tool_round_trip():
    def calculate(a, b):
        return {"status": "success", "data": a + b}

    tool = Tool(
        name="calculate",
        description="add two numbers",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "first"},
                "b": {"type": "number", "description": "second"},
            },
            "required": ["a", "b"],
        },
        fn=calculate,
    )
    tool_stream = sse_body(
        tool_call_fragment(
            0, "call_1", "calculate", '{"a": 2, "b": 3}'
        ),
        final_chunk(),
    )
    answer_stream = sse_body(content_chunk("The sum is 5."), final_chunk())
    with FakeLlamaServer(streams=[tool_stream, answer_stream]) as server:
        client = LlamaCppClient(host=server.url)
        messages = [{"role": "user", "content": "what is 2+3?"}]
        result = run_with_tools(
            client=client,
            model="q.gguf",
            messages=messages,
            loaded_tools=[tool],
            ollama_tools=to_openai_tools([tool]),
            options={},
            keep_alive=None,
            show_thinking=False,
            no_safety_system_prompt=True,
            system_prompt=None,
            skill_text=None,
            color="never",
        )
    assert result == "The sum is 5."
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "assistant"]
    assert "[data from calculate" in messages[-2]["content"]
    # Second request carried the paired tool_call_id per OpenAI convention.
    assert len(server.requests) == 2
    sent_tool = server.requests[1]["body"]["messages"][-1]
    assert sent_tool["role"] == "tool"
    assert sent_tool["tool_call_id"].startswith("call_")


def test_run_with_tools_websearch_skipped_for_llamacpp(capsys):
    stream = sse_body(content_chunk("no websearch"), final_chunk())
    with FakeLlamaServer(streams=[stream]) as server:
        client = LlamaCppClient(host=server.url)
        messages = [{"role": "user", "content": "hi"}]
        result = run_with_tools(
            client=client,
            model="q.gguf",
            messages=messages,
            loaded_tools=[],
            ollama_tools=None,
            options={},
            keep_alive=None,
            show_thinking=False,
            no_safety_system_prompt=True,
            system_prompt=None,
            skill_text=None,
            color="never",
            ollama_websearch=True,
        )
    assert result == "no websearch"
    assert "llama.cpp" in capsys.readouterr().err
    sent = server.requests[0]["body"]
    assert "tools" not in sent or "web_search" not in json.dumps(sent["tools"])


# -- tool format + make_tools ------------------------------------------------


def test_to_openai_tools_returns_dicts():
    tool = Tool(
        name="ping",
        description="ping",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string", "description": "arg"}},
            "required": ["x"],
        },
        fn=lambda x: {"status": "success", "data": x},
    )
    tools = to_openai_tools([tool])
    assert isinstance(tools, list)
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "ping"
    assert tools[0]["function"]["parameters"]["required"] == ["x"]


def test_router_make_tools_uses_dict_format():
    router = create_router()
    tool = Tool(
        name="ping",
        description="ping",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string", "description": "arg"}},
            "required": ["x"],
        },
        fn=lambda x: {"status": "success", "data": x},
    )
    tools = router.make_tools([tool])
    assert isinstance(tools[0], dict)
    assert tools[0]["function"]["name"] == "ping"


# -- Ollama wrapper delegation ----------------------------------------------


class _FakeOllamaSdk:
    def __init__(self):
        self.chat_calls = []
        self.generate_calls = []

    def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        return "stream"

    def list(self):
        return SimpleNamespace(models=[SimpleNamespace(model="q:1", name="q:1")])

    def ps(self):
        return SimpleNamespace(
            models=[SimpleNamespace(model="q:1", name="q:1", context_length=2048)]
        )

    def show(self, model):
        return SimpleNamespace(modelinfo={"x.context_length": 4096})

    def generate(self, model, keep_alive=0):
        self.generate_calls.append((model, keep_alive))


def test_ollama_client_delegation(monkeypatch):
    sdk = _FakeOllamaSdk()
    monkeypatch.setattr(
        "backends.ollama.Client", lambda host: sdk
    )
    client = OllamaClient(host="http://localhost:11434")
    assert client.chat(model="m", messages=[{"role": "user", "content": "hi"}]) == "stream"
    assert sdk.chat_calls[0]["model"] == "m"
    assert sdk.chat_calls[0]["stream"] is True
    resp = client.list()
    assert resp.models[0].model == "q:1"
    running = client.ps()
    assert running.models[0].context_length == 2048
    assert client.stop("q:1") is True
    assert sdk.generate_calls == [("q:1", 0)]
