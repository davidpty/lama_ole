# Configuration for Ollama and Vision Models
import os

# Vision models configured via CLI --vision_model (populated at startup)
_VISION_MODELS: list[str] = []


def set_vision_models(models: list[str]):
    global _VISION_MODELS
    _VISION_MODELS.clear()
    _VISION_MODELS.extend(models)


def get_vision_models() -> list[str]:
    return list(_VISION_MODELS)


# Ollama host configured via CLI --host (fallback for tools)
_OLLAMA_HOST = "http://localhost:11434"


def set_ollama_host(host: str):
    global _OLLAMA_HOST
    _OLLAMA_HOST = host


def get_ollama_host() -> str:
    return _OLLAMA_HOST
