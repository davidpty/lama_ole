import importlib
import os
import sys
from typing import Optional, List

from .models import Tool, ToolModuleInfo
from .utils import _infer_params

_TOOL_REGISTRY: List[Tool] = []
_TOOL_MODULES: List[ToolModuleInfo] = []

_TOOLS_PACKAGE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
)


def tool(description: str = "", params: Optional[dict] = None):
    """Decorator to register a function as a tool."""
    def wrapper(fn):
        name = fn.__name__
        resolved_params = params if params is not None else _infer_params(fn)
        t = Tool(
            name=name,
            description=description or fn.__doc__ or "",
            parameters=resolved_params,
            fn=fn,
        )
        _TOOL_REGISTRY.append(t)
        return t
    return wrapper


def load_tools(module_name: str) -> List[Tool]:
    """Loads tools from a given module name (idempotent).

    The module is imported once (cached in ``sys.modules``); subsequent calls
    return the same ``Tool`` objects and do not duplicate the module entry in
    the registry.
    """
    if module_name not in sys.modules:
        importlib.import_module(module_name)
    mod = sys.modules[module_name]
    tools = []
    readonly = bool(getattr(mod, "__tool_readonly__", False))
    for obj in vars(mod).values():
        if isinstance(obj, Tool):
            obj.readonly = readonly
            tools.append(obj)
    env_vars = getattr(mod, "__tool_env__", {})
    if not any(m.module_name == module_name for m in _TOOL_MODULES):
        _TOOL_MODULES.append(ToolModuleInfo(
            module_name=module_name,
            tools=list(tools),
            env_vars=dict(env_vars),
        ))
    return tools


def get_available_toolsets(tools_dir: Optional[str] = None) -> List[str]:
    """Module names loadable via the REPL, i.e. ``*.py`` in the tools package.

    ``tools_dir`` overrides the default ``lama_ole/tools`` directory (used by
    tests). Private files (leading underscore) and ``__init__.py`` are skipped.
    """
    if tools_dir is None:
        tools_dir = _TOOLS_PACKAGE_DIR
    names = []
    if not os.path.isdir(tools_dir):
        return names
    for f in sorted(os.listdir(tools_dir)):
        if f.startswith("_") or f == "__init__.py":
            continue
        if f.endswith(".py"):
            names.append(f[:-3])
    return names


def get_tools_of_module(module_name: str) -> List[Tool]:
    """Tool objects registered for a loaded module (or ``[]`` if unknown)."""
    for info in _TOOL_MODULES:
        if info.module_name == module_name:
            return list(info.tools)
    return []


def peek_tools_of_module(module_name: str) -> List[Tool]:
    """Import a module and return its ``Tool`` objects WITHOUT registering it.

    Used for ``/tools show`` / ``/tools all`` so that listing never changes the
    set of loaded tools. Raises on import errors; caller is expected to handle.
    """
    if module_name not in sys.modules:
        importlib.import_module(module_name)
    mod = sys.modules[module_name]
    readonly = bool(getattr(mod, "__tool_readonly__", False))
    tools = []
    for obj in vars(mod).values():
        if isinstance(obj, Tool):
            obj.readonly = readonly
            tools.append(obj)
    return tools


def get_tool_modules_info() -> List[ToolModuleInfo]:
    """Returns information about loaded tool modules."""
    return list(_TOOL_MODULES)
