import os
import sys

C_PROMPT = "\x01\033[95m\x02"
C_THINK = "\x01\033[93m\x02"
C_OUTPUT = "\x01\033[96m\x02"
C_RESET = "\x01\033[0m\x02"


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
