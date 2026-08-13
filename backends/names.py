"""Model identifier parsing for the Ollama and llama.cpp backends.

Model names are namespace-qualified with a mandatory display prefix so both
backends can share one /model completion list and one ``--model`` argument.
The rules are:

  * ``ollama:<name>``          -> Ollama backend, model ``<name>``
  * ``llamacpp:<name>``        -> llama.cpp backend, model ``<name>``
  * anything without prefix    -> DEPRECATED: warning, defaults to Ollama

The prefix is stripped before sending to the backend API.
"""

import sys

_BACKENDS = ("ollama", "llamacpp")

_DISPLAY_PREFIX = {
    "ollama": "ollama:",
    "llamacpp": "llamacpp:",
}


def _warn_bare(model_id):
    """Print deprecation warning for bare model ID (no prefix)."""
    print(
        f"Warning: Model '{model_id}' has no backend prefix. "
        f"Defaulting to 'ollama:'. Use 'ollama:{model_id}' or 'llamacpp:{model_id}' explicitly.",
        file=sys.stderr,
    )


def parse_model(model_id):
    """Split a model id into ``(backend, bare_name)``.

    ``backend`` is one of :data:`_BACKENDS`; ``bare_name`` is the identifier
    to hand to the backend itself. The display prefix (``ollama:`` /
    ``llamacpp:``) is mandatory and used for routing. Bare IDs without prefix
    are deprecated and default to Ollama with a warning.
    """
    if model_id is None:
        return None, None

    # 1. Check for display prefix (ollama: / llamacpp:) — MANDATORY PATH
    for backend in _BACKENDS:
        prefix = _DISPLAY_PREFIX[backend]
        if model_id.startswith(prefix):
            return backend, model_id[len(prefix):]

    # 2. NO PREFIX: Deprecated, default to Ollama with warning
    _warn_bare(model_id)
    return "ollama", model_id


def with_prefix(model_id):
    """Ensure model_id has display prefix for display/storage. Idempotent.

    Warns if the input has no backend prefix (user-facing).
    """
    if model_id is None:
        return None
    for prefix in _DISPLAY_PREFIX.values():
        if model_id.startswith(prefix):
            return model_id  # already has a display prefix; idempotent
    # No prefix - parse_model will warn and default to ollama
    backend, bare = parse_model(model_id)
    return f"{_DISPLAY_PREFIX[backend]}{bare}"


def with_prefix_silent(model_id, backend=None):
    """Ensure model_id has display prefix without warning (internal use).

    Does NOT warn if the input has no backend prefix. Idempotent: if already
    prefixed, returns as-is.

    Args:
        model_id: The model identifier to prefix.
        backend: Optional backend name ('ollama' or 'llamacpp') to determine
            which prefix to add when the model has no prefix. Defaults to
            'llamacpp' if not specified.
    """
    if model_id is None:
        return None
    for prefix in _DISPLAY_PREFIX.values():
        if model_id.startswith(prefix):
            return model_id  # already has display prefix; idempotent
    # No prefix — use the given backend or default to llamacpp
    target_backend = backend if backend else "llamacpp"
    return f"{_DISPLAY_PREFIX[target_backend]}{model_id}"


def strip_prefix(model_id):
    """Remove display prefix for backend API call. Idempotent (no warning)."""
    if model_id is None:
        return None
    for prefix in _DISPLAY_PREFIX.values():
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    return model_id


def canonicalize(model_id):
    """Return the display-prefixed canonical form of a model id.

    Adds prefix if missing (with deprecation warning). Returns prefixed form.
    """
    return with_prefix(model_id)