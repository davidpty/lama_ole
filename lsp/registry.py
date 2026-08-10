"""Language → server command resolution for the LSP toolset.

Server commands come exclusively from this module (a built-in table overridable
via the ``LAMA_OLE_LSP_SERVERS`` environment variable). The LLM never supplies a
raw command, which keeps arbitrary command execution out of the model's hands.
"""

import json
import os
import shutil
import shlex

from typing import List, Optional

from .client import LspConfigError


DEFAULT_LSP_SERVERS = {
    "python": ["pyright-langserver", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "rust": ["rust-analyzer"],
    "cpp": ["clangd"],
    "c": ["clangd"],
    "go": ["gopls"],
    "json": ["vscode-json-language-server", "--stdio"],
}

_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".go": "go",
    ".json": "json",
}

_ENV_SERVERS_VAR = "LAMA_OLE_LSP_SERVERS"


def _env_override() -> dict:
    """Parse ``LAMA_OLE_LSP_SERVERS`` into a {language: [argv]} dict."""
    raw = os.environ.get(_ENV_SERVERS_VAR)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LspConfigError(
            "Invalid %s (must be a JSON object): %s" % (_ENV_SERVERS_VAR, exc)
        )
    if not isinstance(data, dict):
        raise LspConfigError(
            "Invalid %s: expected a JSON object mapping language to command"
            % _ENV_SERVERS_VAR
        )
    result = {}
    for language, command in data.items():
        if isinstance(command, str):
            result[str(language)] = shlex.split(command)
        elif isinstance(command, list):
            result[str(language)] = [str(part) for part in command]
        else:
            raise LspConfigError(
                "Invalid %s: command for '%s' must be a string or list"
                % (_ENV_SERVERS_VAR, language)
            )
    return result


def resolve_server(language: str) -> List[str]:
    """Return the argv for *language*, validating that the binary exists.

    Raises ``LspConfigError`` for unknown languages, missing binaries or a
    malformed ``LAMA_OLE_LSP_SERVERS`` override.
    """
    table = dict(DEFAULT_LSP_SERVERS)
    table.update(_env_override())
    if language not in table:
        known = ", ".join(known_languages())
        raise LspConfigError(
            "No language server configured for '%s'. Known languages: %s. "
            "Install a server or set %s."
            % (language, known, _ENV_SERVERS_VAR)
        )
    command = list(table[language])
    if not command:
        raise LspConfigError("Empty server command for '%s'" % language)
    if shutil.which(command[0]) is None:
        raise LspConfigError(
            "Language server executable not found: %r (for '%s'). Install it "
            "or set %s." % (command[0], language, _ENV_SERVERS_VAR)
        )
    return command


def language_for_path(path: str) -> Optional[str]:
    """Infer a language id from a file extension, or None if unknown."""
    import os

    _, ext = os.path.splitext(os.path.basename(path))
    return _EXTENSION_TO_LANGUAGE.get(ext.lower())


def known_languages() -> List[str]:
    """Sorted list of language ids resolvable by :func:`resolve_server`."""
    table = dict(DEFAULT_LSP_SERVERS)
    table.update(_env_override())
    return sorted(table)
