"""Shared protocol types for model backends.

Both the Ollama wrapper and the llama.cpp HTTP client produce the same small
set of objects so ``tool_base`` and the CLI can treat them interchangeably.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class StreamMessage:
    """One delta of a streamed model reply (content and/or thinking)."""

    content: str = ""
    thinking: str = ""
    tool_calls: Optional[list] = None


@dataclass
class StreamChunk:
    """A single streamed chunk plus any usage metrics attached to it.

    ``close()`` releases the underlying HTTP connection; callers should always
    invoke it (the engine does this inside a ``finally``).
    """

    message: StreamMessage
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None
    eval_duration: Optional[int] = None
    prompt_eval_duration: Optional[int] = None
    _stream: Optional[Any] = None

    def close(self):
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None


@dataclass
class ModelEntry:
    """One entry of a model list/ps response, in bare (un-namespaced) form.

    The router adds the ``<backend>.`` prefix when merging the backends.
    """

    model: str
    name: str = ""
    context_length: Optional[int] = None


@dataclass
class ListResponse:
    models: List[ModelEntry] = field(default_factory=list)

    def __iter__(self):
        return iter(self.models)


class ModelClient(ABC):
    """Minimal interface every backend client must implement."""

    name = "abstract"

    @abstractmethod
    def chat(self, model, messages, stream=True, tools=None, options=None,
             keep_alive=None):
        """Stream (or return) the model reply for ``messages``."""

    @abstractmethod
    def list(self):
        """Return a :class:`ListResponse` of installed models."""

    def ps(self):
        """Return a :class:`ListResponse` of currently loaded models."""
        return ListResponse()

    def show(self, model):
        """Return backend metadata for ``model`` (``.modelinfo`` etc.)."""
        return None

    def stop(self, model):
        """Unload ``model`` from memory. Returns True when supported."""
        return False

    def make_tools(self, tools):
        """Convert ``tool_base.Tool`` objects to the backend tool format."""
        from tool_base.engine import to_openai_tools
        return to_openai_tools(tools)

    def supports_native_websearch(self, model):
        """Whether the backend offers a built-in web search tool."""
        return True
