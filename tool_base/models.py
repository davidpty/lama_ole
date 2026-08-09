from dataclasses import dataclass
from typing import Any, Callable, Optional

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable
    readonly: bool = False

    def __call__(self, *args, **kwargs):
        return self.fn(*args, **kwargs)


@dataclass
class ToolModuleInfo:
    module_name: str
    tools: list[Tool]
    env_vars: dict[str, str]
