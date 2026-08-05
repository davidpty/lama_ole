import inspect
from typing import Any

def create_uuid_15() -> str:
    import uuid
    return uuid.uuid4().hex[:15]


def _infer_params(fn) -> dict:
    sig = inspect.signature(fn)
    properties = {}
    required = []
    for name, param in sig.parameters.items():
        if param.annotation is not inspect.Parameter.empty:
            param_type = _type_to_json_schema(param.annotation)
        else:
            param_type = {"type": "string"}
        properties[name] = {
            "type": param_type["type"],
            "description": name,
        }
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _type_to_json_schema(annotation: Any) -> dict:
    mapping = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
    }
    return mapping.get(annotation, {"type": "string"})
