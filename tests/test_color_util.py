"""Tests for color mode resolution and the colored() helper."""

import os
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import color_util  # noqa: E402

import chat  # noqa: E402


class TestColorModeEnabled:
    def test_auto_default_false_without_tty(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        assert color_util.color_mode_enabled("auto") is False

    def test_auto_true_with_tty(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        assert color_util.color_mode_enabled("auto") is True

    def test_never(self, monkeypatch):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        assert color_util.color_mode_enabled("never") is False

    def test_none_equivalent_to_never(self, monkeypatch):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        assert color_util.color_mode_enabled("none") is False

    def test_always(self, monkeypatch):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        assert color_util.color_mode_enabled("always") is True

    def test_no_color_env_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        assert color_util.color_mode_enabled("auto") is False
        assert color_util.color_mode_enabled("always") is True


class TestColored:
    def test_disabled_returns_plain_text(self):
        assert color_util.colored("hi", color_util.C_OUTPUT, False) == "hi"

    def test_enabled_wraps_with_reset(self):
        result = color_util.colored("hi", color_util.C_OUTPUT, True)
        assert result == f"{color_util.C_OUTPUT}hi{color_util.C_RESET}"


class TestRunChatPromptColoring:
    def _capture_prompt(self, monkeypatch, state):
        captured = {}
        monkeypatch.setattr(chat, "_install_readline_completion", lambda: None)

        def fake_input(prompt=""):
            captured["prompt"] = prompt
            raise EOFError

        monkeypatch.setattr("builtins.input", fake_input)
        chat.run_chat(state)
        return captured["prompt"]

    def test_never_gives_plain_prompt(self, monkeypatch):
        state = chat.ChatState(client=None, model="m", color="never", ctx_meter=False)
        prompt = self._capture_prompt(monkeypatch, state)
        assert prompt == ">>> "
        assert "\x1b[" not in prompt

    def test_none_gives_plain_prompt(self, monkeypatch):
        state = chat.ChatState(client=None, model="m", color="none", ctx_meter=False)
        prompt = self._capture_prompt(monkeypatch, state)
        assert prompt == ">>> "
        assert "\x1b[" not in prompt

    def test_always_gives_colored_prompt(self, monkeypatch):
        state = chat.ChatState(client=None, model="m", color="always")
        prompt = self._capture_prompt(monkeypatch, state)
        assert "\x1b[" in prompt

    def test_always_prompt_ends_with_input_color(self, monkeypatch):
        state = chat.ChatState(client=None, model="m", color="always")
        prompt = self._capture_prompt(monkeypatch, state)
        assert prompt.endswith(color_util.C_INPUT)
        assert color_util.colored(">>> ", color_util.C_PROMPT, True) in prompt


class TestParseColorSpec:
    def test_named_color(self):
        assert color_util.parse_color_spec("red") == "\x01\033[31m\x02"

    def test_bright_named_color(self):
        assert color_util.parse_color_spec("bright_green") == "\x01\033[92m\x02"

    def test_grey_alias(self):
        assert color_util.parse_color_spec("grey") == "\x01\033[90m\x02"
        assert color_util.parse_color_spec("gray") == "\x01\033[90m\x02"

    def test_case_insensitive(self):
        assert color_util.parse_color_spec("RED") == "\x01\033[31m\x02"

    def test_256_color_number(self):
        assert color_util.parse_color_spec("208") == "\x01\033[38;5;208m\x02"

    def test_hex_color(self):
        assert color_util.parse_color_spec("#ff8700") == "\x01\033[38;2;255;135;0m\x02"

    def test_hex_color_short(self):
        assert color_util.parse_color_spec("#f80") == "\x01\033[38;2;255;136;0m\x02"

    def test_attribute_combination(self):
        assert color_util.parse_color_spec("bold,red") == "\x01\033[1;31m\x02"

    def test_attribute_with_256(self):
        assert color_util.parse_color_spec("underline,208") == "\x01\033[4;38;5;208m\x02"

    def test_default_and_none(self):
        assert color_util.parse_color_spec("default") is None
        assert color_util.parse_color_spec("none") is None
        assert color_util.parse_color_spec("") is None
        assert color_util.parse_color_spec(None) is None

    def test_invalid_returns_none(self):
        assert color_util.parse_color_spec("notacolor") is None
        assert color_util.parse_color_spec("999") is None
        assert color_util.parse_color_spec("#zzz") is None
        assert color_util.parse_color_spec("bold,") is None


class TestConfigure:
    def test_configure_updates_globals_and_colored(self, monkeypatch):
        monkeypatch.setattr(color_util, "C_PROMPT", "\x01\033[31m\x02")
        color_util.configure(prompt="red")
        assert color_util.C_PROMPT == "\x01\033[31m\x02"
        assert color_util.colored("hi", color_util.C_PROMPT, True) == "\x01\033[31m\x02hi\x01\033[0m\x02"

    def test_configure_thinking_and_output(self, monkeypatch):
        monkeypatch.setattr(color_util, "C_THINK", "\x01\033[96m\x02")
        monkeypatch.setattr(color_util, "C_OUTPUT", "\x01\033[93m\x02")
        color_util.configure(thinking="bold,cyan", output="#00ff00")
        assert color_util.C_THINK == "\x01\033[1;36m\x02"
        assert color_util.C_OUTPUT == "\x01\033[38;2;0;255;0m\x02"

    def test_configure_none_keeps_current(self, monkeypatch):
        monkeypatch.setattr(color_util, "C_PROMPT", "\x01\033[95m\x02")
        color_util.configure(prompt=None, thinking=None, output=None)
        assert color_util.C_PROMPT == "\x01\033[95m\x02"

    def test_configure_invalid_warns_and_keeps_default(self, monkeypatch, capsys):
        monkeypatch.setattr(color_util, "C_PROMPT", "\x01\033[95m\x02")
        color_util.configure(prompt="bogus")
        captured = capsys.readouterr()
        assert "invalid color spec" in captured.err
        assert color_util.C_PROMPT == "\x01\033[95m\x02"

    def test_configure_default_resets_to_builtin(self, monkeypatch):
        monkeypatch.setattr(color_util, "C_PROMPT", "\x01\033[95m\x02")
        color_util.configure(prompt="red")
        color_util.configure(prompt="default")
        assert color_util.C_PROMPT == color_util._DEFAULTS["prompt"]

    def test_configure_input(self, monkeypatch):
        monkeypatch.setattr(color_util, "C_INPUT", "\x01\033[96m\x02")
        color_util.configure(input="green")
        assert color_util.C_INPUT == "\x01\033[32m\x02"
        color_util.configure(input="default")
        assert color_util.C_INPUT == color_util._DEFAULTS["input"]


class TestRunChatConfiguredColor:
    def test_configured_prompt_color_used_by_run_chat(self, monkeypatch):
        monkeypatch.setattr(color_util, "C_PROMPT", "\x01\033[95m\x02")
        monkeypatch.setattr(chat, "_install_readline_completion", lambda: None)
        captured = {}

        def fake_input(prompt=""):
            captured["prompt"] = prompt
            raise EOFError

        monkeypatch.setattr("builtins.input", fake_input)
        color_util.configure(prompt="green")
        state = chat.ChatState(client=None, model="m", color="always")
        chat.run_chat(state)
        assert "\x1b[32m" in captured["prompt"]


class TestColorEnvWiring:
    def test_env_vars_feed_configure(self, monkeypatch, capsys):
        from lama_ole import lama_ole as lama_ole_module

        monkeypatch.setattr(color_util, "C_PROMPT", "\x01\033[95m\x02")
        monkeypatch.setenv("LAMA_OLE_COLOR_PROMPT", "blue")
        prompt_env = lama_ole_module._env_str("LAMA_OLE_COLOR_PROMPT", None)
        assert prompt_env == "blue"
        color_util.configure(prompt=prompt_env)
        assert color_util.C_PROMPT == "\x01\033[34m\x02"

    def test_unset_env_defaults_to_none(self):
        from lama_ole import lama_ole as lama_ole_module

        assert lama_ole_module._env_str("LAMA_OLE_COLOR_PROMPT", None) is None

    def test_input_env_var_feeds_configure(self, monkeypatch, capsys):
        from lama_ole import lama_ole as lama_ole_module

        monkeypatch.setattr(color_util, "C_INPUT", "\x01\033[96m\x02")
        monkeypatch.setenv("LAMA_OLE_COLOR_INPUT", "yellow")
        input_env = lama_ole_module._env_str("LAMA_OLE_COLOR_INPUT", None)
        assert input_env == "yellow"
        color_util.configure(input=input_env)
        assert color_util.C_INPUT == "\x01\033[33m\x02"
