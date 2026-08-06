"""Tests for readline Tab completion candidates in the chat REPL."""

import os
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestCommandCompletion:
    def test_completes_command_prefix(self):
        assert chat._completion_candidates("/sk") == ["/skill"]

    def test_slash_lists_all_commands(self):
        matches = chat._completion_candidates("/")
        assert set(matches) == set(chat._COMMANDS)

    def test_exact_command(self):
        assert chat._completion_candidates("/help") == ["/help"]

    def test_no_completion_for_plain_text(self):
        assert chat._completion_candidates("hello") == []

    def test_empty_buffer(self):
        assert chat._completion_candidates("") == []


class TestSubcommandCompletion:
    def test_tools_subcommands(self):
        matches = chat._completion_candidates("/tools l")
        assert set(matches) == {"load", "loaded"}

    def test_tools_all_from_trailing_space(self):
        matches = chat._completion_candidates("/tools ")
        assert set(matches) == set(chat._COMMAND_SUBCOMMANDS["/tools"])

    def test_skill_subcommands(self):
        matches = chat._completion_candidates("/skill l")
        assert set(matches) == {"list", "load"}

    def test_systemprompt_subcommand(self):
        assert chat._completion_candidates("/systemprompt u") == ["unset"]


class TestFilePathCompletion:
    def test_feed_completes_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_text(str(tmp_path / "notes.txt"), "hi")
        assert chat._completion_candidates("/feed not") == ["notes.txt"]

    def test_save_completes_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_text(str(tmp_path / "conv.json"), "{}")
        assert chat._completion_candidates("/save conv") == ["conv.json"]

    def test_load_completes_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_text(str(tmp_path / "state.json"), "{}")
        assert chat._completion_candidates("/load sta") == ["state.json"]

    def test_subdirectory_trailing_separator(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.mkdir(str(tmp_path / "subdir"))
        _write_text(str(tmp_path / "subdir" / "inner.txt"), "x")
        assert chat._completion_candidates("/feed sub") == ["subdir/"]
        assert chat._completion_candidates("/feed subdir/") == ["subdir/inner.txt"]

    def test_skill_load_completes_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_text(str(tmp_path / "code.md"), "skill")
        assert chat._completion_candidates("/skill load co") == ["code.md"]

    def test_systemprompt_completes_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_text(str(tmp_path / "prompt.txt"), "prompt")
        matches = chat._completion_candidates("/systemprompt prom")
        assert "prompt.txt" in matches

    def test_systemprompt_merges_subcommands_and_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_text(str(tmp_path / "showcase.txt"), "x")
        matches = chat._completion_candidates("/systemprompt sho")
        assert "show" in matches
        assert "showcase.txt" in matches

    def test_no_matches_for_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert chat._completion_candidates("/feed nofile") == []
