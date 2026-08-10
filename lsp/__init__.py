"""LSP toolset engine package.

Provides the JSON-RPC transport, the language server client and the session
manager used by ``tools/lsp_tools.py``. See ``llm_blueprint/lsp_tools/`` for the
design documents.
"""

from .jsonrpc import JsonRpcCodec, MAX_BUFFER_SIZE
from .client import (
    LspClient,
    LspError,
    LspClientCrashed,
    LspTimeout,
    LspConfigError,
)
from .session import LspSessionManager, get_manager
from .registry import resolve_server, language_for_path, known_languages

__all__ = [
    "JsonRpcCodec",
    "MAX_BUFFER_SIZE",
    "LspClient",
    "LspError",
    "LspClientCrashed",
    "LspTimeout",
    "LspConfigError",
    "LspSessionManager",
    "get_manager",
    "resolve_server",
    "language_for_path",
    "known_languages",
]
