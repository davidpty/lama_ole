"""Security module for lama_ole.

Provides security-related utilities including entropy checking to prevent
binary data from leaking into LLM context.
"""

from .entropychecker import (
    EntropyChecker,
    EntropyCheckResult,
    check_entropy,
)

__all__ = [
    "EntropyChecker",
    "EntropyCheckResult", 
    "check_entropy",
]
