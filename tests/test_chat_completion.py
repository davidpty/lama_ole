"""Tests for readline Tab completion candidates in the chat REPL."""

import json
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


_TOOL_SRC = (
    "from tool_base import tool\n"
    '@tool(description="Does x")\n'
    "def do_x() -> str:\n"
    '    return "x"\n'
)


def _write_tool_module(path):
    return _write_text(path, _TOOL_SRC)


def _write_package(pkg_dir):
    os.makedirs(pkg_dir, exist_ok=True)
    return _write_text(str(os.path.join(pkg_dir, "__init__.py")), "")


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

    def test_compact_subcommand(self):
        assert chat._completion_candidates("/compact a") == ["auto"]

    def test_compact_all_from_trailing_space(self):
        matches = chat._completion_candidates("/compact ")
        assert set(matches) == set(chat._COMMAND_SUBCOMMANDS["/compact"])


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
        matches = chat._completion_candidates("/skill load co")
        assert "code.md" in matches
        assert "code-reviewer" in matches

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


class TestBracketedPaste:
    def test_install_runs_without_error(self):
        chat._install_bracketed_paste()


class TestCompletionSpace:
    def test_command_word_gets_trailing_space(self):
        assert chat._maybe_append_completion_space("/tools", ["/tools"]) == ["/tools "]

    def test_partial_command_gets_trailing_space(self):
        assert chat._maybe_append_completion_space("/sk", ["/skill"]) == ["/skill "]

    def test_leading_whitespace_still_command_position(self):
        assert chat._maybe_append_completion_space("  /tools", ["/tools"]) == ["/tools "]

    def test_subcommand_word_gets_trailing_space(self):
        assert chat._maybe_append_completion_space("/tools av", ["available"]) == ["available "]
        assert chat._maybe_append_completion_space("/skill lo", ["load"]) == ["load "]
        assert chat._maybe_append_completion_space("/compact a", ["auto"]) == ["auto "]

    def test_toolset_arg_gets_trailing_space(self):
        assert chat._maybe_append_completion_space("/tools load we", ["web_tools"]) == ["web_tools "]

    def test_file_path_never_gets_trailing_space(self):
        assert chat._maybe_append_completion_space("/feed not", ["notes.txt"]) == ["notes.txt"]
        assert chat._maybe_append_completion_space("/systemprompt sho", ["showcase.txt"]) == ["showcase.txt"]

    def test_ambiguous_matches_never_get_trailing_space(self):
        matches = ["load", "loaded"]
        assert chat._maybe_append_completion_space("/tools l", matches) == matches


class TestToolsetArgCompletion:
    def _tools_dir(self, tmp_path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_module(str(tools_dir / "web_tools.py"))
        _write_tool_module(str(tools_dir / "audio_tools.py"))
        return str(tools_dir)

    def test_load_completes_toolset_prefix(self, tmp_path):
        assert (
            chat._completion_candidates("/tools load we", self._tools_dir(tmp_path), [])
            == ["web_tools"]
        )

    def test_load_lists_all_from_trailing_space(self, tmp_path):
        matches = chat._completion_candidates("/tools load ", self._tools_dir(tmp_path), [])
        assert set(matches) == {"web_tools", "audio_tools"}

    def test_show_completes_toolset(self, tmp_path):
        assert (
            chat._completion_candidates("/tools show we", self._tools_dir(tmp_path), [])
            == ["web_tools"]
        )

    def test_unload_completes_loaded_shorts(self, tmp_path):
        assert (
            chat._completion_candidates(
                "/tools unload we", self._tools_dir(tmp_path), ["tools.web_tools"]
            )
            == ["web_tools"]
        )

    def test_continuation_after_toolset_arg(self, tmp_path):
        matches = chat._completion_candidates(
            "/tools load web_tools ", self._tools_dir(tmp_path), []
        )
        assert set(matches) == {"web_tools", "audio_tools"}

    def test_load_autosync_scans_tools_dir(self, tmp_path):
        # A new tool module on disk completes immediately -- never hardcode the
        # toolset list in completion.
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_module(str(tools_dir / "fresh_tool.py"))
        assert (
            chat._completion_candidates("/tools load ", str(tools_dir), [])
            == ["fresh_tool"]
        )


class TestDottedToolsetCompletion:
    def _tree(self, tmp_path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_module(str(tools_dir / "web_tools.py"))
        _write_tool_module(str(tools_dir / "audio_tools.py"))
        _write_package(str(tools_dir / "security"))
        _write_tool_module(str(tools_dir / "security" / "crypto_tool.py"))
        _write_text(str(tools_dir / "security" / "helpers.py"), "x = 1")
        _write_package(str(tmp_path / "mycompany"))
        _write_tool_module(str(tmp_path / "mycompany" / "db_tools.py"))
        _write_text(str(tmp_path / "mycompany" / "helpers.py"), "x = 1")
        return str(tools_dir)

    def test_tools_prefix_lists_leaf_tools_and_packages(self, tmp_path):
        tools_dir = self._tree(tmp_path)
        matches = chat._completion_candidates("/tools load tools.", tools_dir, [])
        assert "tools.web_tools" in matches
        assert "tools.audio_tools" in matches
        assert "tools.security." in matches
        assert "tools.security.crypto_tool" not in matches

    def test_tools_prefix_descends_into_subpackage(self, tmp_path):
        tools_dir = self._tree(tmp_path)
        assert (
            chat._completion_candidates("/tools load tools.sec", tools_dir, [])
            == ["tools.security."]
        )

    def test_subpackage_lists_only_tool_modules(self, tmp_path):
        tools_dir = self._tree(tmp_path)
        matches = chat._completion_candidates(
            "/tools load tools.security.", tools_dir, []
        )
        assert matches == ["tools.security.crypto_tool"]

    def test_sibling_package_lists_only_tool_modules(self, tmp_path):
        tools_dir = self._tree(tmp_path)
        matches = chat._completion_candidates("/tools load mycompany.", tools_dir, [])
        assert matches == ["mycompany.db_tools"]

    def test_sibling_package_prefix(self, tmp_path):
        tools_dir = self._tree(tmp_path)
        assert (
            chat._completion_candidates("/tools load mycompany.db", tools_dir, [])
            == ["mycompany.db_tools"]
        )

    def test_non_tool_module_not_offered(self, tmp_path):
        tools_dir = self._tree(tmp_path)
        assert (
            chat._completion_candidates("/tools load mycompany.helpers", tools_dir, [])
            == []
        )
        assert (
            chat._completion_candidates("/tools load tools.security.helpers", tools_dir, [])
            == []
        )

    def test_unknown_package_no_matches(self, tmp_path):
        tools_dir = self._tree(tmp_path)
        assert chat._completion_candidates("/tools load nosuchpkg.", tools_dir, []) == []
        assert chat._completion_candidates("/tools load nosuchpkg.fo", tools_dir, []) == []

    def test_bare_completion_excludes_non_tool_modules(self, tmp_path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_module(str(tools_dir / "web_tools.py"))
        _write_text(str(tools_dir / "helpers.py"), "x = 1")
        assert (
            chat._completion_candidates("/tools load ", str(tools_dir), [])
            == ["web_tools"]
        )

    def test_loaded_toolset_completion_full_name_for_nested(self, tmp_path):
        tools_dir = self._tree(tmp_path)
        loaded = ["tools.web_tools", "tools.security.crypto_tool", "mycompany.db_tools"]
        assert (
            chat._completion_candidates(
                "/tools unload crypto", tools_dir, loaded
            )
            == ["tools.security.crypto_tool"]
        )
        assert (
            chat._completion_candidates("/tools unload web", tools_dir, loaded)
            == ["web_tools"]
        )

    def test_package_candidate_gets_no_trailing_space(self):
        assert (
            chat._maybe_append_completion_space(
                "/tools load tools.sec", ["tools.security."]
            )
            == ["tools.security."]
        )

    def test_leaf_module_gets_trailing_space(self):
        assert (
            chat._maybe_append_completion_space(
                "/tools load tools.security.crypto", ["tools.security.crypto_tool"]
            )
            == ["tools.security.crypto_tool "]
        )


def _write_session_file(sessions_dir, sid, title, cwd):
    proj = os.path.join(sessions_dir, os.path.basename(cwd or os.getcwd()))
    os.makedirs(proj, exist_ok=True)
    path = os.path.join(proj, sid + ".json")
    _write_text(path, json.dumps({"session_id": sid, "title": title, "cwd": cwd}))


class TestSessionsAndResumeCompletion:
    def test_sessions_lists_all_from_trailing_space(self):
        matches = chat._completion_candidates("/sessions ")
        assert set(matches) == {"all", "rm"}

    def test_sessions_completes_subcommand_prefix(self):
        assert chat._completion_candidates("/sessions r") == ["rm"]

    def test_sessions_rm_completes_all_and_numbers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(sessions_dir, "abc12345deadbeef", "one", str(tmp_path))
        _write_session_file(
            sessions_dir, "def67890beefcafe", "two", str(tmp_path / "other")
        )
        assert (
            chat._completion_candidates("/sessions rm ", sessions_dir=sessions_dir)
            == ["all", "1", "2"]
        )

    def test_sessions_rm_without_sessions_dir_returns_empty(self):
        assert chat._completion_candidates("/sessions rm ") == []

    def test_sessions_subcommand_gets_trailing_space(self):
        assert chat._maybe_append_completion_space("/sessions r", ["rm"]) == ["rm "]

    def test_resume_completes_current_session_title(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(sessions_dir, "abc12345deadbeef", "project planning", str(tmp_path))
        assert (
            chat._completion_candidates("/resume pro", sessions_dir=sessions_dir)
            == ["project planning"]
        )

    def test_resume_lists_all_from_trailing_space(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(sessions_dir, "abc12345deadbeef", "project planning", str(tmp_path))
        _write_session_file(sessions_dir, "def67890beefcafe", "fix bugs", str(tmp_path / "other"))
        matches = chat._completion_candidates("/resume ", sessions_dir=sessions_dir)
        assert "project planning" in matches
        assert any("fix bugs" in m and chat._RESUME_ANNOTATION_MARKER in m for m in matches)

    def test_resume_current_first_others_annotated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        other = str(tmp_path / "other")
        _write_session_file(sessions_dir, "abc12345deadbeef", "project planning", str(tmp_path))
        _write_session_file(sessions_dir, "def67890beefcafe", "fix bugs", other)
        matches = chat._completion_candidates("/resume ", sessions_dir=sessions_dir)
        current_idx = matches.index("project planning")
        other_cand = next(m for m in matches if m.startswith("fix bugs"))
        assert current_idx < matches.index(other_cand)
        assert other_cand == f"fix bugs  {chat._RESUME_ANNOTATION_MARKER}  {other}"

    def test_resume_completes_session_id_prefix(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(sessions_dir, "abc12345deadbeef", "project planning", str(tmp_path))
        assert chat._completion_candidates("/resume abc", sessions_dir=sessions_dir) == ["abc12345"]

    def test_resume_no_matches_for_unknown_prefix(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(sessions_dir, "abc12345deadbeef", "project planning", str(tmp_path))
        assert chat._completion_candidates("/resume zzz", sessions_dir=sessions_dir) == []

    def test_resume_without_sessions_dir_returns_empty(self):
        assert chat._completion_candidates("/resume pro") == []

    def test_resume_unique_match_gets_trailing_space(self):
        assert (
            chat._maybe_append_completion_space("/resume pro", ["project planning"])
            == ["project planning "]
        )


class TestModelCompletion:
    def test_model_completes_prefix(self):
        matches = chat._completion_candidates(
            "/model llama",
            list_models=lambda: ["llama3.2", "qwen2.5", "llama3.1"],
        )
        assert matches == ["llama3.1", "llama3.2"]

    def test_model_lists_all_from_trailing_space(self):
        matches = chat._completion_candidates(
            "/model ", list_models=lambda: ["llama3.2", "qwen2.5"]
        )
        assert set(matches) == {"llama3.2", "qwen2.5"}

    def test_model_without_lister_returns_empty(self):
        assert chat._completion_candidates("/model llama") == []

    def test_model_lister_error_returns_empty(self):
        def boom():
            raise RuntimeError("ollama down")

        assert chat._completion_candidates("/model llama", list_models=boom) == []

    def test_model_dedupes_names(self):
        matches = chat._completion_candidates(
            "/model llama", list_models=lambda: ["llama3.1", "llama3.1"]
        )
        assert matches == ["llama3.1"]

    def test_model_unique_match_gets_trailing_space(self):
        assert (
            chat._maybe_append_completion_space("/model llama3.1", ["llama3.1"])
            == ["llama3.1 "]
        )


class TestSkillNameCompletion:
    def _skills_dir(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _write_text(str(skills_dir / "code-reviewer.md"), "skill")
        _write_text(str(skills_dir / "german-assistant.md"), "skill")
        return str(skills_dir)

    def test_skill_load_completes_names(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        skills_dir = self._skills_dir(tmp_path)
        assert (
            chat._completion_candidates("/skill load code", skills_dir=skills_dir)
            == ["code-reviewer"]
        )

    def test_skill_load_lists_names_from_trailing_space(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        skills_dir = self._skills_dir(tmp_path)
        matches = chat._completion_candidates("/skill load ", skills_dir=skills_dir)
        assert "code-reviewer" in matches
        assert "german-assistant" in matches

    def test_skill_load_merges_names_and_paths(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_text(str(tmp_path / "code.md"), "skill")
        skills_dir = self._skills_dir(tmp_path)
        matches = chat._completion_candidates("/skill load co", skills_dir=skills_dir)
        assert "code-reviewer" in matches
        assert "code.md" in matches

    def test_skill_load_defaults_to_repo_skills_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        matches = chat._completion_candidates("/skill load code")
        assert "code-reviewer" in matches

    def test_skill_load_unique_match_gets_trailing_space(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        skills_dir = self._skills_dir(tmp_path)
        assert (
            chat._maybe_append_completion_space("/skill load code-reviewer", ["code-reviewer"])
            == ["code-reviewer "]
        )


class TestSessionsRmCompletion:
    def test_rm_completes_all_and_numbers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(sessions_dir, "abc12345deadbeef", "one", str(tmp_path))
        assert (
            chat._completion_candidates("/sessions rm ", sessions_dir=sessions_dir)
            == ["all", "1"]
        )

    def test_rm_completes_number_prefix(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(sessions_dir, "abc12345deadbeef", "one", str(tmp_path))
        _write_session_file(
            sessions_dir, "def67890beefcafe", "two", str(tmp_path / "other")
        )
        assert (
            chat._completion_candidates("/sessions rm 1", sessions_dir=sessions_dir)
            == ["1"]
        )

    def test_rm_completes_all_selector_from_prefix(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(sessions_dir, "abc12345deadbeef", "one", str(tmp_path))
        assert (
            chat._completion_candidates("/sessions rm al", sessions_dir=sessions_dir)
            == ["all"]
        )

    def test_rm_without_sessions_dir_returns_empty(self):
        assert chat._completion_candidates("/sessions rm ") == []

    def test_rm_unique_match_gets_trailing_space(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(sessions_dir, "abc12345deadbeef", "one", str(tmp_path))
        assert (
            chat._maybe_append_completion_space("/sessions rm al", ["all"])
            == ["all "]
        )


class TestRenameCompletion:
    def _sessions_dir(self, tmp_path):
        sessions_dir = str(tmp_path / "sessions")
        _write_session_file(
            sessions_dir, "abc12345deadbeef", "project planning", str(tmp_path)
        )
        _write_session_file(
            sessions_dir,
            "def67890beefcafe",
            "fix bugs",
            str(tmp_path / "other"),
        )
        return sessions_dir

    def test_rename_completes_id_prefix(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = self._sessions_dir(tmp_path)
        assert (
            chat._completion_candidates("/rename ab", sessions_dir=sessions_dir)
            == ["abc12345"]
        )

    def test_rename_case_insensitive_prefix(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = self._sessions_dir(tmp_path)
        assert (
            chat._completion_candidates("/rename ABC", sessions_dir=sessions_dir)
            == ["abc12345"]
        )

    def test_rename_unknown_prefix_no_matches(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = self._sessions_dir(tmp_path)
        assert chat._completion_candidates("/rename zzz", sessions_dir=sessions_dir) == []

    def test_rename_without_sessions_dir_returns_empty(self):
        assert chat._completion_candidates("/rename abc") == []

    def test_rename_unique_match_gets_trailing_space(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions_dir = self._sessions_dir(tmp_path)
        assert (
            chat._maybe_append_completion_space("/rename abc12345", ["abc12345"])
            == ["abc12345 "]
        )


class TestValueCompletion:
    def test_context_on_off(self):
        assert chat._completion_candidates("/context of") == ["off"]
        assert set(chat._completion_candidates("/context o")) == {"on", "off"}

    def test_context_all_from_trailing_space(self):
        assert set(chat._completion_candidates("/context ")) == {"on", "off"}

    def test_compact_auto_on_off(self):
        assert set(chat._completion_candidates("/compact auto o")) == {"on", "off"}

    def test_compact_auto_all_from_trailing_space(self):
        assert set(chat._completion_candidates("/compact auto ")) == {"on", "off"}

    def test_cut_undo(self):
        assert chat._completion_candidates("/cut u") == ["undo"]
        assert chat._completion_candidates("/cut ") == ["undo"]

    def test_cut_number_not_completed(self):
        assert chat._completion_candidates("/cut 2") == []

    def test_value_matches_get_trailing_space(self):
        assert chat._maybe_append_completion_space("/context o", ["off"]) == ["off "]
        assert (
            chat._maybe_append_completion_space("/compact auto o", ["off"]) == ["off "
        ]
        )
        assert chat._maybe_append_completion_space("/cut u", ["undo"]) == ["undo "]


class TestDispatchRegistryGuards:
    def test_commands_derived_from_handlers(self):
        assert chat._COMMANDS == list(chat._COMMAND_HANDLERS)

    def test_commands_order_preserved(self):
        legacy_order = [
            "/feed",
            "/new",
            "/compact",
            "/model",
            "/plan",
            "/build",
            "/save",
            "/load",
            "/resume",
            "/sessions",
            "/stats",
            "/rename",
            "/tools",
            "/skill",
            "/systemprompt",
            "/context",
            "/history",
            "/cut",
            "/help",
            "/exit",
            "/quit",
        ]
        assert chat._COMMANDS == legacy_order

    def test_subcommand_heads_are_commands(self):
        assert set(chat._SUBCOMMAND_HANDLERS).issubset(set(chat._COMMANDS))

    def test_subcommand_tables_well_formed(self):
        valid_kinds = {None, "toolset", "loaded_toolset", "path"}
        for head, table in chat._SUBCOMMAND_HANDLERS.items():
            assert head in chat._COMMANDS
            for entry in table.values():
                assert isinstance(entry, tuple) and len(entry) == 2
                handler, kind = entry
                assert callable(handler)
                assert kind in valid_kinds

    def test_every_command_appears_in_help(self, capsys):
        chat._show_help()
        out = capsys.readouterr().out
        for cmd in chat._COMMANDS:
            assert cmd in out

    def test_exit_and_quit_terminate_repl(self):
        state = chat.ChatState(client=None, model="test")
        assert chat._handle_command("/exit", state) is True
        assert chat._handle_command("/quit", state) is True

    def test_unknown_command_preserves_message(self, capsys):
        state = chat.ChatState(client=None, model="test")
        assert chat._handle_command("/nope", state) is False
        assert "Unknown command: /nope" in capsys.readouterr().out
