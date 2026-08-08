import os
import sys

C_PROMPT = "\x01\033[95m\x02"
C_THINK = "\x01\033[96m\x02"
C_OUTPUT = "\x01\033[93m\x02"
C_METER_LOW = "\x01\033[92m\x02"
C_METER_MID = "\x01\033[93m\x02"
C_METER_HIGH = "\x01\033[91m\x02"
C_RESET = "\x01\033[0m\x02"

_DEFAULTS = {
    "prompt": C_PROMPT,
    "thinking": C_THINK,
    "output": C_OUTPUT,
    "meter_low": C_METER_LOW,
    "meter_mid": C_METER_MID,
    "meter_high": C_METER_HIGH,
}

_NAMED_FG = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
    "grey": 90,
    "gray": 90,
    "bright_black": 90,
    "bright_red": 91,
    "bright_green": 92,
    "bright_yellow": 93,
    "bright_blue": 94,
    "bright_magenta": 95,
    "bright_cyan": 96,
    "bright_white": 97,
}

_ATTRIBUTES = {
    "bold": 1,
    "dim": 2,
    "italic": 3,
    "underline": 4,
    "blink": 5,
    "reverse": 7,
}


_GLOBALS = {
    "prompt": "C_PROMPT",
    "thinking": "C_THINK",
    "output": "C_OUTPUT",
    "meter_low": "C_METER_LOW",
    "meter_mid": "C_METER_MID",
    "meter_high": "C_METER_HIGH",
}


def parse_color_spec(value):
    if value is None:
        return None
    spec = value.strip().lower()
    if not spec or spec in ("default", "none"):
        return None
    tokens = spec.split(",")
    if any(not tok.strip() for tok in tokens):
        return None
    codes = []
    for token in tokens:
        token = token.strip()
        if token in _NAMED_FG:
            codes.append(str(_NAMED_FG[token]))
        elif token in _ATTRIBUTES:
            codes.append(str(_ATTRIBUTES[token]))
        elif token.startswith("#") and len(token) in (4, 7):
            hex_digits = token[1:]
            if len(hex_digits) == 3:
                hex_digits = "".join(ch * 2 for ch in hex_digits)
            try:
                r = int(hex_digits[0:2], 16)
                g = int(hex_digits[2:4], 16)
                b = int(hex_digits[4:6], 16)
            except ValueError:
                return None
            codes.append("38;2;{0};{1};{2}".format(r, g, b))
        elif token.isdigit():
            n = int(token)
            if 0 <= n <= 255:
                codes.append("38;5;{0}".format(n))
            else:
                return None
        else:
            return None
    if not codes:
        return None
    return "\x01\033[{0}m\x02".format(";".join(codes))


def configure(prompt=None, thinking=None, output=None, meter_low=None, meter_mid=None, meter_high=None):
    for key, value in (
        ("prompt", prompt),
        ("thinking", thinking),
        ("output", output),
        ("meter_low", meter_low),
        ("meter_mid", meter_mid),
        ("meter_high", meter_high),
    ):
        if value is None:
            continue
        spec = value.strip().lower()
        if not spec or spec in ("default", "none"):
            globals()[_GLOBALS[key]] = _DEFAULTS[key]
            continue
        parsed = parse_color_spec(spec)
        if parsed is None:
            print(
                "warning: invalid color spec for '{0}': {1!r} (keeping default)".format(key, value),
                file=sys.stderr,
            )
            continue
        globals()[_GLOBALS[key]] = parsed


def color_mode_enabled(mode: str) -> bool:
    if mode in ("never", "none"):
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
