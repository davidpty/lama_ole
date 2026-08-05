import importlib
import sys
from typing import Optional, List

from .models import Tool, ToolModuleInfo
from .utils import _infer_params

_TOOL_REGISTRY: List[Tool] = []
_TOOL_MODULES: List[ToolModuleInfo] = []


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
    """Loads tools from a given module name."""
    if module_name not in sys.modules:
        importlib.import_module(module_name)
    mod = sys.modules[module_name]
    tools = []
    for obj in vars(mod).values():
        if isinstance(obj, Tool):
            tools.append(obj)
    env_vars = getattr(mod, "__tool_env__", {})
    _TOOL_MODULES.append(ToolModuleInfo(
        module_name=module_name,
        tools=list(tools),
        env_vars=dict(env_vars),
    ))
    return tools


def get_tool_modules_info() -> List[ToolModuleInfo]:
    """Returns information about loaded tool modules."""
    return list(_TOOL_MODULES)
