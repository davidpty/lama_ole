"""Example tool module for lama_ole."""

__tool_readonly__ = True

import os
from tool_base import tool
from tools_security.validate_path import validate_path as _validate_path


@tool(description="Get the current weather for a city")
def get_weather(city: str) -> str:
    return f"Weather in {city}: 22°C, partly cloudy"


@tool(description="Calculate a mathematical expression")
def calculate(expression: str) -> str:
    allowed = {"abs": abs, "min": min, "max": max, "sum": sum}
    result = eval(expression, {"__builtins__": {}}, allowed)
    return str(result)


@tool(description="Read the contents of a file")
def read_file(path: str) -> str:
    safety_error = _validate_path(path)
    if safety_error:
        return {"status": "error", "message": [safety_error]}

    with open(path, "rb") as f:
        raw_content = f.read()

    # Entropy check: reject binary / random content before it reaches the LLM
    from security.entropychecker import EntropyChecker

    result = EntropyChecker().feed(raw_content)
    if result.is_suspicious:
        return {
            "status": "error",
            "message": [f"File rejected by entropy check: {result.reason}"],
        }

    return raw_content.decode("utf-8", errors="replace")
