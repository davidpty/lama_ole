"""Tests for the /systemprompt REPL command."""

import json
import os
import sys

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _make_state():
    return chat.ChatState(client=None, model="m")


class TestCmdSystemprompt:
    def test_load_from_file(self, tmp_path, capsys):
        state = _make_state()
        state.messages = [{"role": "user", "content": "hi"}]
        sp_file = tmp_path / "sp.txt"
        _write_text(str(sp_file), "You are a pirate.")
        chat._cmd_systemprompt(str(sp_file), state)
        out = capsys.readouterr().out
        assert "System prompt loaded" in out
        assert state.system_prompt == "You are a pirate."
        assert state.messages[0]["role"] == "system"
        assert "You are a pirate." in state.messages[0]["content"]

    def test_rewrites_existing_system_message(self, tmp_path, capsys):
        state = _make_state()
        state.messages = [
            {"role": "system", "content": "OLD"},
            {"role": "user", "content": "hi"},
        ]
        sp_file = tmp_path / "sp.txt"
        _write_text(str(sp_file), "NEW PROMPT")
        chat._cmd_systemprompt(str(sp_file), state)
        assert "OLD" not in state.messages[0]["content"]
        assert "NEW PROMPT" in state.messages[0]["content"]

    def test_keeps_active_skill(self, tmp_path, capsys):
        state = _make_state()
        state.messages = [{"role": "user", "content": "hi"}]
        state.skill_text = "SKILL"
        sp_file = tmp_path / "sp.txt"
        _write_text(str(sp_file), "BASE PROMPT")
        chat._cmd_systemprompt(str(sp_file), state)
        content = state.messages[0]["content"]
        assert content.startswith("BASE PROMPT")
        assert "[SKILL BEGIN]" in content

    def test_show_bare(self, tmp_path, capsys):
        state = _make_state()
        state.system_prompt = "CURRENT"
        chat._cmd_systemprompt("", state)
        assert "CURRENT" in capsys.readouterr().out

    def test_show_subcommand(self, tmp_path, capsys):
        state = _make_state()
        state.system_prompt = "CURRENT"
        chat._cmd_systemprompt("show", state)
        assert "CURRENT" in capsys.readouterr().out

    def test_show_no_prompt(self, tmp_path, capsys):
        state = _make_state()
        chat._cmd_systemprompt("", state)
        assert "No system prompt set." in capsys.readouterr().out

    def test_unset(self, tmp_path, capsys):
        state = _make_state()
        state.messages = [{"role": "user", "content": "hi"}]
        state.system_prompt = "BASE"
        state.apply_skill()
        assert "BASE" in state.messages[0]["content"]
        chat._cmd_systemprompt("unset", state)
        out = capsys.readouterr().out
        assert "System prompt unset." in out
        assert state.system_prompt is None
        assert "BASE" not in state.messages[0]["content"]

    def test_unset_no_prompt(self, tmp_path, capsys):
        state = _make_state()
        chat._cmd_systemprompt("unset", state)
        assert "No system prompt set." in capsys.readouterr().out

    def test_missing_file(self, tmp_path, capsys):
        state = _make_state()
        chat._cmd_systemprompt(str(tmp_path / "nope.txt"), state)
        out = capsys.readouterr().out
        assert "not found" in out.lower()
        assert state.system_prompt is None

    def test_binary_rejected(self, tmp_path, capsys):
        state = _make_state()
        bad = tmp_path / "bad.bin"
        with open(bad, "wb") as f:
            f.write(os.urandom(1000))
        chat._cmd_systemprompt(str(bad), state)
        out = capsys.readouterr().out
        assert "entropy check" in out.lower()
        assert state.system_prompt is None

    def test_handle_command_dispatches(self, tmp_path, capsys):
        state = _make_state()
        state.messages = [{"role": "user", "content": "hi"}]
        sp_file = tmp_path / "sp.txt"
        _write_text(str(sp_file), "YOU ARE A HAIKU MASTER")
        chat._handle_command(f"/systemprompt {sp_file}", state)
        assert "YOU ARE A HAIKU MASTER" in state.messages[0]["content"]

    def test_help_includes_systemprompt(self, capsys):
        chat._show_help()
        out = capsys.readouterr().out
        assert "/systemprompt" in out


class TestSystempromptPersistence:
    def test_save_load_persists_system_prompt(self, tmp_path, capsys):
        state = _make_state()
        state.system_prompt = "PERSISTED PROMPT"
        path = str(tmp_path / "conv.json")
        chat._cmd_save(path, state)

        state2 = _make_state()
        chat._cmd_load(path, state2)
        assert state2.system_prompt == "PERSISTED PROMPT"

    def test_save_without_prompt_omits_field(self, tmp_path, capsys):
        state = _make_state()
        path = str(tmp_path / "conv.json")
        chat._cmd_save(path, state)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert "system_prompt" not in data
