"""Optional integration tests against a real llama.cpp server (llama-server).

These tests are opt-in: they only run when
``LAMA_OLE_HOST_LLAMACPP_INTEGRATION=1`` is set AND a llama-server answers at
the configured host (the deterministic in-process fake server is used
everywhere else). By default the whole module self-skips so the suite stays
green on any machine.

The host comes from ``LAMA_OLE_HOST_LLAMACPP`` (the same variable the CLI
uses) and defaults to ``http://localhost:8080``; the API key is read from
``LAMA_OLE_HOST_LLAMACPP_API_KEY`` when the server is behind Bearer auth.
The legacy ``LAMA_OLE_LLAMACPP_HOST`` / ``LAMA_OLE_LLAMACPP_API_KEY`` names
are still honored.
"""

import os
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)
tests_dir = os.path.dirname(current_file)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

import pytest

from backends.llamacpp import LlamaCppClient
from tool_base import run_with_tools, to_openai_tools
from tool_base.models import Tool

_INTEGRATION_ENABLED = os.environ.get(
    "LAMA_OLE_HOST_LLAMACPP_INTEGRATION", ""
) == "1"
HOST = (
    os.environ.get("LAMA_OLE_HOST_LLAMACPP")
    or os.environ.get("LAMA_OLE_LLAMACPP_HOST")
    or "http://localhost:8080"
)
API_KEY = (
    os.environ.get("LAMA_OLE_HOST_LLAMACPP_API_KEY")
    or os.environ.get("LAMA_OLE_LLAMACPP_API_KEY")
    or None
)

pytestmark = pytest.mark.skipif(
    not _INTEGRATION_ENABLED,
    reason="llama.cpp integration tests are opt-in; set "
    "LAMA_OLE_HOST_LLAMACPP_INTEGRATION=1 (and point "
    "LAMA_OLE_HOST_LLAMACPP at a running llama-server) to run them",
)


def _client():
    return LlamaCppClient(host=HOST, api_key=API_KEY)


@pytest.fixture(scope="module")
def real_server_ok():
    """Skip when no llama-server answers at the configured host.

    Mirrors ``real_server_ok`` in ``test_lsp_integration.py``: a broken
    environment produces a skip, not a hard error.
    """
    try:
        _client().list()
    except Exception as exc:  # noqa: BLE001 - environmental skip
        pytest.skip("no llama-server at %s: %s" % (HOST, exc))
    return HOST


def test_list_returns_served_models(real_server_ok):
    listing = _client().list()
    assert listing.models, "llama-server reported no models"
    for entry in listing.models:
        assert entry.model
        assert entry.name


def test_show_reports_context_length(real_server_ok):
    listing = _client().list()
    info = _client().show(listing.models[0].model)
    assert isinstance(info.modelinfo.get("llama.context_length"), int)


def test_chat_stream_reaches_server(real_server_ok):
    messages = [{"role": "user", "content": "Reply with exactly: OK"}]
    seen = []
    metrics_seen = False
    for chunk in _client().chat(
        model=_client().list().models[0].model,
        messages=messages,
        options={"max_tokens": 64},
    ):
        if chunk.message.content or chunk.message.thinking:
            seen.append(chunk.message.content or chunk.message.thinking)
        if chunk.eval_count or chunk.prompt_eval_count:
            metrics_seen = True
        chunk.close()
    assert "".join(seen), "no content or thinking received from the server"
    assert metrics_seen, "streamed reply carried no usage metrics"


def test_chat_non_stream(real_server_ok):
    messages = [{"role": "user", "content": "Reply with exactly: OK"}]
    chunk = _client().chat(
        model=_client().list().models[0].model,
        messages=messages,
        stream=False,
        options={"max_tokens": 64},
    )
    assert chunk.message.content or chunk.message.thinking


def test_ps_reports_slots(real_server_ok):
    listing = _client().ps()
    assert hasattr(listing, "models")
    for entry in listing.models:
        assert entry.model
        assert isinstance(entry.context_length, int)


def test_run_with_tools_smoke(real_server_ok):
    """A trivial tool round-trips through the real server.

    Requires the server to have been started with ``--jinja`` and the model to
    support tool calling; otherwise skip with a hint instead of failing. The
    round limit is capped so a tool-happy model cannot loop forever.
    """

    calls = []

    def ping(text):
        calls.append(text)
        return {"status": "success", "data": "pong:%s" % text}

    tool = Tool(
        name="ping",
        description="echo the given text back prefixed with pong",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "text to echo"},
            },
            "required": ["text"],
        },
        fn=ping,
    )
    model = _client().list().models[0].model
    messages = [{"role": "user", "content": "Call the ping tool with hi."}]
    try:
        result = run_with_tools(
            client=_client(),
            model=model,
            messages=messages,
            loaded_tools=[tool],
            ollama_tools=to_openai_tools([tool]),
            options={"max_tokens": 128},
            keep_alive=None,
            show_thinking=False,
            no_safety_system_prompt=True,
            system_prompt=None,
            skill_text=None,
            color="never",
            max_tool_rounds=2,
            max_tool_rounds_continuation="fallback",
        )
    except RuntimeError as exc:
        if "400" in str(exc) or "jinja" in str(exc).lower() or "template" in str(exc).lower():
            pytest.skip("server rejected tool requests (start llama-server with --jinja): %s" % exc)
        raise
    assert calls or (result and result.strip())
