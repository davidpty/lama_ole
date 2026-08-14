"""Backend router: dispatches every call to Ollama or llama.cpp.

The router keeps one client per backend and selects the target by parsing the
model's display prefix (``ollama:`` / ``llamacpp:``). Backend reachability is
probed lazily and cached for a short TTL, so an unavailable server is simply
absent from the merged listing instead of producing errors.
"""

import time
import urllib.error
import urllib.request

from . import names
from .base import ListResponse, ModelClient, ModelEntry
from .llamacpp import LlamaCppClient
from .ollama import OllamaClient

_PROBE_TTL = 60.0

_BACKEND_ORDER = ("ollama", "llamacpp")


class RouterClient(ModelClient):
    """Dispatches model calls to the Ollama and llama.cpp backends."""

    name = "router"

    def __init__(self, ollama_host=None, llamacpp_host=None, api_key=None,
                 llamacpp_launched=False, llamacpp_launch_values=None):
        self._ollama = OllamaClient(host=ollama_host)
        self._llamacpp = LlamaCppClient(
            host=llamacpp_host,
            api_key=api_key,
            launched=llamacpp_launched,
            launch_values=llamacpp_launch_values,
        )
        self._clients = {"ollama": self._ollama, "llamacpp": self._llamacpp}
        self._probe_ts = {"ollama": 0.0, "llamacpp": 0.0}
        self._probe_ok = {"ollama": False, "llamacpp": False}

    @property
    def ollama_host(self):
        return self._ollama.host

    @property
    def llamacpp_host(self):
        return self._llamacpp.host

    def mark_llamacpp_launched(self, options=None, keep_alive=None):
        """Mark the llama.cpp server as one we started.

        The launch-time options are honored by that server, so the client stops
        warning about ``num_ctx``/``num_gpu``/``keep_alive`` when the request
        matches them (and warns again when it differs).
        """
        self._llamacpp.mark_llamacpp_launched(options, keep_alive)

    # -- reachability --------------------------------------------------------

    def _probe(self, backend):
        """One reachability probe; cheap for ollama (root endpoint)."""
        try:
            if backend == "ollama":
                with urllib.request.urlopen(self._ollama.host, timeout=2.0):
                    pass
            else:
                self._llamacpp.list()
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            return False

    def _is_reachable(self, backend):
        now = time.monotonic()
        if now - self._probe_ts.get(backend, 0.0) < _PROBE_TTL:
            return self._probe_ok.get(backend, False)
        ok = self._probe(backend)
        self._probe_ts[backend] = now
        self._probe_ok[backend] = ok
        return ok

    # -- routing helpers -----------------------------------------------------

    def _route(self, model):
        backend, bare = names.parse_model(model)
        if backend is None:
            raise ValueError("no backend for model '%s'" % model)
        client = self._clients.get(backend)
        if client is None:
            raise ValueError("unknown backend '%s' for model '%s'" % (backend, model))
        return client, bare

    def _merged(self, method):
        models = []
        for backend in _BACKEND_ORDER:
            if not self._is_reachable(backend):
                continue
            try:
                resp = getattr(self._clients[backend], method)()
            except Exception:
                continue
            for m in resp.models:
                bare = m.model
                # with_prefix() is idempotent: no warning/warn if already has prefix,
                # adds prefix if bare. Since router knows which backend each list came from,
                # this correctly sets the display prefix.
                prefixed = names.with_prefix_silent(bare, backend=backend)
                models.append(
                    ModelEntry(
                        model=prefixed,
                        name=prefixed,
                        context_length=m.context_length,
                    )
                )
        return ListResponse(models=models)

    # -- ModelClient interface ----------------------------------------------

    def chat(self, model, messages, stream=True, tools=None, options=None,
             keep_alive=None):
        client, bare = self._route(model)
        return client.chat(
            model=bare,
            messages=messages,
            stream=stream,
            tools=tools,
            options=options,
            keep_alive=keep_alive,
        )

    def list(self):
        return self._merged("list")

    def ps(self):
        return self._merged("ps")

    def show(self, model):
        client, bare = self._route(model)
        return client.show(bare)

    def stop(self, model):
        client, bare = self._route(model)
        return client.stop(bare)

    def canonicalize(self, model_id):
        return names.canonicalize(model_id)

    def supports_native_websearch(self, model):
        backend, _ = names.parse_model(model)
        return backend != "llamacpp"

    def resolve_default_model(self):
        """Return a default namespaced model when the choice is unambiguous.

        Only used when ``-m`` is omitted: if Ollama is unreachable but a
        llama.cpp server is serving models, default to its first model. Returns
        None when the situation is ambiguous (both up, or nothing up).
        """
        ollama_up = self._is_reachable("ollama")
        llamacpp_up = self._is_reachable("llamacpp")
        if ollama_up or not llamacpp_up:
            return None
        try:
            resp = self._llamacpp.list()
        except Exception:
            return None
        if not resp.models:
            return None
        bare = resp.models[0].model
        # with_prefix() is idempotent — no warning if already has prefix,
        # adds prefix if bare. Internal model IDs from llama.cpp may already
        # have 'llamacpp:' prefix; this ensures correct output.
        return names.with_prefix_silent(bare, backend="llamacpp")