"""Model backends: Ollama and llama.cpp behind one router interface."""

from .base import ListResponse, ModelClient, ModelEntry, StreamChunk, StreamMessage
from .names import canonicalize, parse_model
from .router import RouterClient

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_LLAMACPP_HOST = "http://localhost:8080"


def create_router(ollama_host=None, llamacpp_host=None, api_key=None):
    """Build the dispatch client for the given backend hosts."""
    return RouterClient(
        ollama_host=ollama_host or DEFAULT_OLLAMA_HOST,
        llamacpp_host=llamacpp_host or DEFAULT_LLAMACPP_HOST,
        api_key=api_key,
    )


__all__ = [
    "ModelClient",
    "ListResponse",
    "ModelEntry",
    "StreamChunk",
    "StreamMessage",
    "canonicalize",
    "parse_model",
    "create_router",
    "RouterClient",
    "DEFAULT_OLLAMA_HOST",
    "DEFAULT_LLAMACPP_HOST",
]
