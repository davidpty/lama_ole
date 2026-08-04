import os
import sys

C_PROMPT = "\033[95m"
C_THINK = "\033[93m"
C_OUTPUT = "\033[96m"
C_RESET = "\033[0m"


def color_mode_enabled(mode: str) -> bool:
    if mode == "never":
        return False
    if mode == "always":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def colored(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{code}{text}{C_RESET}"
