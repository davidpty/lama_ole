"""Model backends: Ollama and llama.cpp behind one router interface."""

from .base import ListResponse, ModelClient, ModelEntry, StreamChunk, StreamMessage
from .names import canonicalize, parse_model, strip_prefix
from .router import RouterClient

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_LLAMACPP_HOST = "http://localhost:8080"


def create_router(ollama_host=None, llamacpp_host=None, api_key=None,
                  llamacpp_launched=False, llamacpp_launch_values=None):
    """Build the dispatch client for the given backend hosts.

    ``llamacpp_launch_values`` (dict of ``num_ctx``/``num_gpu``/``keep_alive``
    honored at launch) marks a llama.cpp server that lama_ole started; the
    client then only warns about an "ignored" option when the request differs
    from those launch-time values.
    """
    return RouterClient(
        ollama_host=ollama_host or DEFAULT_OLLAMA_HOST,
        llamacpp_host=llamacpp_host or DEFAULT_LLAMACPP_HOST,
        api_key=api_key,
        llamacpp_launched=llamacpp_launched,
        llamacpp_launch_values=llamacpp_launch_values,
    )


__all__ = [
    "ModelClient",
    "ListResponse",
    "ModelEntry",
    "StreamChunk",
    "StreamMessage",
    "canonicalize",
    "parse_model",
    "strip_prefix",
    "create_router",
    "RouterClient",
    "DEFAULT_OLLAMA_HOST",
    "DEFAULT_LLAMACPP_HOST",
]
