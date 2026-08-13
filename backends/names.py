"""Model identifier parsing for the Ollama and llama.cpp backends.

Model names are namespace-qualified so both backends can share one /model
completion list and one ``--model`` argument. The rules are deliberately
simple:

  * ``ollama.<name>``          -> Ollama backend, model ``<name>``
  * ``llamacpp.<name>``        -> llama.cpp backend, model ``<name>``
  * path-like names (a GGUF)   -> llama.cpp backend (basename is the id)
  * anything else              -> Ollama backend

The namespace is always the text before the *first* dot, but only when it is
a known backend name: ``llamacpp.qwen2.5-7b`` therefore routes to the
llama.cpp backend even though the model id itself contains dots.
"""

_BACKENDS = ("ollama", "llamacpp")

_GGUF_SUFFIXES = (".gguf", ".gguf.part", ".bin", ".safetensors")


def _is_path_like(name):
    """True when ``name`` looks like a local model file rather than an id."""
    if "/" in name or "\\" in name:
        return True
    return name.lower().endswith(_GGUF_SUFFIXES)


def _display_id(model_id):
    """Return the shortest human-useful form of a model id.

    Path-like ids are reduced to their basename (a GGUF path carries no
    prefix the model itself knows about); everything else is returned
    unchanged.
    """
    if "/" in model_id or "\\" in model_id:
        base = model_id.rstrip("/\\")
        for sep in ("/", "\\"):
            if sep in base:
                base = base.rsplit(sep, 1)[-1]
        return base
    return model_id


def parse_model(model_id):
    """Split a model id into ``(backend, bare_name)``.

    ``backend`` is one of :data:`_BACKENDS`; ``bare_name`` is the identifier
    to hand to the backend itself.
    """
    if model_id is None:
        return None, None
    backend, sep, rest = model_id.partition(".")
    if sep and backend in _BACKENDS:
        return backend, rest
    if _is_path_like(model_id):
        return "llamacpp", _display_id(model_id)
    return "ollama", model_id


def canonicalize(model_id):
    """Return the namespaced canonical form of a model id.

    ``gemma2:2b`` and ``ollama.gemma2:2b`` both canonicalize to
    ``ollama.gemma2:2b``; a GGUF path canonicalizes to
    ``llamacpp.<basename>``.
    """
    backend, bare = parse_model(model_id)
    if backend is None:
        return None
    if backend == "llamacpp":
        bare = _display_id(bare)
    return f"{backend}.{bare}"
