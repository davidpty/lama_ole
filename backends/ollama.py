"""Ollama backend client.

A thin adapter over the official ``ollama`` SDK so the rest of the codebase
can talk to one common interface. Tools are converted through the shared
``to_openai_tools``; the SDK accepts plain dicts.
"""

from ollama import Client

from .base import ListResponse, ModelClient, ModelEntry


class OllamaClient(ModelClient):
    """Adapts the official ollama SDK to the common backend interface."""

    name = "ollama"

    def __init__(self, host=None):
        self.host = (host or "http://localhost:11434").rstrip("/")
        self._client = Client(host=self.host)

    def chat(self, model, messages, stream=True, tools=None, options=None,
             keep_alive=None):
        kwargs = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            kwargs["tools"] = tools
        if options:
            kwargs["options"] = options
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        return self._client.chat(**kwargs)

    def list(self):
        resp = self._client.list()
        models = [
            ModelEntry(
                model=getattr(m, "model", None) or "",
                name=getattr(m, "name", "") or "",
                context_length=getattr(m, "context_length", None),
            )
            for m in getattr(resp, "models", []) or []
        ]
        return ListResponse(models=models)

    def ps(self):
        resp = self._client.ps()
        models = [
            ModelEntry(
                model=getattr(m, "model", None) or "",
                name=getattr(m, "name", "") or "",
                context_length=getattr(m, "context_length", None),
            )
            for m in getattr(resp, "models", []) or []
        ]
        return ListResponse(models=models)

    def show(self, model):
        return self._client.show(model=model)

    def stop(self, model):
        self._client.generate(model=model, keep_alive=0)
        return True
