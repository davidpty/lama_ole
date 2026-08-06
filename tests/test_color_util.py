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
        state = chat.ChatState(client=None, model="m", color="never")
        prompt = self._capture_prompt(monkeypatch, state)
        assert prompt == ">>> "
        assert "\x1b[" not in prompt

    def test_none_gives_plain_prompt(self, monkeypatch):
        state = chat.ChatState(client=None, model="m", color="none")
        prompt = self._capture_prompt(monkeypatch, state)
        assert prompt == ">>> "
        assert "\x1b[" not in prompt

    def test_always_gives_colored_prompt(self, monkeypatch):
        state = chat.ChatState(client=None, model="m", color="always")
        prompt = self._capture_prompt(monkeypatch, state)
        assert "\x1b[" in prompt
