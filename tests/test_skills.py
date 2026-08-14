"""Tests for the skill feature: system prompt composition, skill file
loading with entropy check, and the --skill CLI parameter."""

import importlib.util
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

# Load the CLI module by path (same pattern as test_env_config.py).
_spec = importlib.util.spec_from_file_location(
    "lama_ole_cli", os.path.join(lama_ole_dir, "lama_ole.py")
)
lama_ole_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lama_ole_cli)

from tool_base.engine import compose_system_prompt, run_with_tools  # noqa: E402


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _fake_chat_stream(content="reply"):
    chunk = SimpleNamespace(
        message=SimpleNamespace(thinking=None, content=content, tool_calls=None)
    )
    return iter([chunk])


class FakeClient:
    def __init__(self, content="reply"):
        self.content = content
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return _fake_chat_stream(self.content)


class TestComposeSystemPrompt:
    def test_no_inputs(self):
        assert compose_system_prompt() != ""
        assert "You operate with tools" in compose_system_prompt()

    def test_base_prompt_first(self):
        result = compose_system_prompt(system_prompt="BE KIND", skill_text=None)
        assert result.startswith("BE KIND")

    def test_skill_block_between_base_and_safety(self):
        result = compose_system_prompt(
            system_prompt="BASE", skill_text="SKILL", no_safety_system_prompt=False
        )
        assert "[SKILL BEGIN]" in result
        assert "[SKILL END]" in result
        base_idx = result.index("BASE")
        skill_idx = result.index("[SKILL BEGIN]")
        safety_idx = result.index("You operate with tools")
        assert base_idx < skill_idx < safety_idx

    def test_no_safety_omits_safety_prompt(self):
        result = compose_system_prompt(skill_text="SKILL", no_safety_system_prompt=True)
        assert "You operate with tools" not in result
        assert "SKILL" in result

    def test_no_skill_no_block(self):
        result = compose_system_prompt(system_prompt="BASE")
        assert "[SKILL BEGIN]" not in result


class TestRunWithToolsSkillInjection:
    def test_skill_text_goes_into_system_message(self):
        client = FakeClient()
        messages = [{"role": "user", "content": "hi"}]
        run_with_tools(
            client=client,
            model="m",
            messages=messages,
            loaded_tools=[],
            ollama_tools=None,
            options={},
            keep_alive=None,
            show_thinking=False,
            no_safety_system_prompt=True,
            skill_text="YOU ARE A CODE REVIEWER",
        )
        system = messages[0]
        assert system["role"] == "system"
        assert "YOU ARE A CODE REVIEWER" in system["content"]

    def test_skill_kept_when_system_already_present(self):
        client = FakeClient()
        messages = [
            {"role": "system", "content": "EXISTING"},
            {"role": "user", "content": "hi"},
        ]
        run_with_tools(
            client=client,
            model="m",
            messages=messages,
            loaded_tools=[],
            ollama_tools=None,
            options={},
            keep_alive=None,
            show_thinking=False,
            no_safety_system_prompt=True,
            skill_text="SHOULD NOT OVERWRITE",
        )
        assert messages[0]["content"] == "EXISTING"


class TestLoadSkillText:
    def test_single_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            _write_text(f.name, "You are a German assistant.")
        try:
            result = lama_ole_cli._load_skill_text([f.name])
            assert result == "You are a German assistant."
        finally:
            os.unlink(f.name)

    def test_multiple_files_concatenated(self):
        p1 = tempfile.mktemp(suffix=".md")
        p2 = tempfile.mktemp(suffix=".md")
        _write_text(p1, "First skill")
        _write_text(p2, "Second skill")
        try:
            result = lama_ole_cli._load_skill_text([p1, p2])
            assert result == "First skill\n\nSecond skill"
        finally:
            os.unlink(p1)
            os.unlink(p2)

    def test_missing_file_exits(self, capsys):
        with pytest.raises(SystemExit):
            lama_ole_cli._load_skill_text(["/nonexistent/skill.md"])
        out, err = capsys.readouterr()
        assert "not found" in err

    def test_binary_file_rejected(self, capsys):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(os.urandom(1000))
            path = f.name
        try:
            with pytest.raises(SystemExit):
                lama_ole_cli._load_skill_text([path])
            out, err = capsys.readouterr()
            assert "entropy check" in err
        finally:
            os.unlink(path)


class TestSkillCliArg:
    @pytest.fixture
    def env_patch(self, monkeypatch):
        saved = {k: v for k, v in os.environ.items() if k.startswith("LAMA_OLE_")}
        for k in saved:
            monkeypatch.delenv(k, raising=False)
        yield
        for k, v in saved.items():
            monkeypatch.setenv(k, v)

    def test_skill_repeatable(self, env_patch):
        args = lama_ole_cli.build_parser().parse_args(
            ["--skill", "a.md", "--skill", "b.md"]
        )
        assert args.skills == ["a.md", "b.md"]

    def test_env_skill_default(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_SKILL", "a.md b.md")
        args = lama_ole_cli.build_parser().parse_args([])
        lama_ole_cli._resolve_env_defaults(args)
        assert args.skills == ["a.md", "b.md"]

    def test_cli_skill_merges_with_env(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_SKILL", "env.md")
        args = lama_ole_cli.build_parser().parse_args(["--skill", "cli.md"])
        lama_ole_cli._resolve_env_defaults(args)
        assert args.skills == ["env.md", "cli.md"]


import chat  # noqa: E402


def _make_chat_state(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    state = chat.ChatState(client=None, model="m")
    state.skills_dir = str(skills_dir)
    return state, skills_dir


class TestApplySkill:
    def test_rewrites_existing_system_message(self, tmp_path):
        state, _ = _make_chat_state(tmp_path)
        state.messages = [
            {"role": "system", "content": "OLD"},
            {"role": "user", "content": "hi"},
        ]
        state.skill_text = "NEW SKILL"
        state.apply_skill()
        sys_msg = state.messages[0]
        assert "OLD" not in sys_msg["content"]
        assert "NEW SKILL" in sys_msg["content"]

    def test_inserts_system_message_when_absent(self, tmp_path):
        state, _ = _make_chat_state(tmp_path)
        state.messages = [{"role": "user", "content": "hi"}]
        state.skill_text = "SKILL"
        state.apply_skill()
        assert state.messages[0]["role"] == "system"
        assert "SKILL" in state.messages[0]["content"]

    def test_unload_removes_skill_block(self, tmp_path):
        state, _ = _make_chat_state(tmp_path)
        state.messages = [{"role": "user", "content": "hi"}]
        state.system_prompt = "BASE"
        state.skill_text = "SKILL"
        state.apply_skill()
        assert "[SKILL BEGIN]" in state.messages[0]["content"]

        state.skill_text = None
        state.apply_skill()
        assert "[SKILL BEGIN]" not in state.messages[0]["content"]
        assert state.messages[0]["content"].startswith("BASE")


class TestCmdSkill:
    def test_load_by_name_from_skills_dir(self, tmp_path, capsys):
        state, skills_dir = _make_chat_state(tmp_path)
        _write_text(str(skills_dir / "code-reviewer.md"), "Review code carefully.")
        state.messages = [{"role": "user", "content": "hi"}]

        chat._cmd_skill("load code-reviewer", state)
        out = capsys.readouterr().out
        assert "Skill loaded" in out
        assert state.skill == "code-reviewer"
        assert "Review code carefully." in state.skill_text
        assert "Review code carefully." in state.messages[0]["content"]

    def test_load_by_path(self, tmp_path, capsys):
        state, _ = _make_chat_state(tmp_path)
        skill_file = tmp_path / "skills" / "custom.md"
        _write_text(str(skill_file), "Custom skill text.")
        state.messages = [{"role": "user", "content": "hi"}]

        chat._cmd_skill(f"load {skill_file}", state)
        assert "Custom skill text." in state.skill_text

    def test_load_multiple_names_concatenated(self, tmp_path, capsys):
        state, skills_dir = _make_chat_state(tmp_path)
        _write_text(str(skills_dir / "s1.md"), "First skill")
        _write_text(str(skills_dir / "s2.md"), "Second skill")
        state.messages = [{"role": "user", "content": "hi"}]

        chat._cmd_skill("load s1 s2", state)
        assert state.skill == "s1 s2"
        assert state.skill_text == "First skill\n\nSecond skill"
        sys_msg = state.messages[0]["content"]
        assert "First skill" in sys_msg
        assert "Second skill" in sys_msg

    def test_load_mixed_paths_and_names(self, tmp_path, capsys):
        state, skills_dir = _make_chat_state(tmp_path)
        _write_text(str(skills_dir / "named.md"), "Named skill")
        other = skills_dir / "subdir" / "other.md"
        other.parent.mkdir()
        _write_text(str(other), "Other skill")
        state.messages = [{"role": "user", "content": "hi"}]

        chat._cmd_skill(f"load named {other}", state)
        assert state.skill_text == "Named skill\n\nOther skill"

    def test_load_multiple_atomic_on_failure(self, tmp_path, capsys):
        state, skills_dir = _make_chat_state(tmp_path)
        _write_text(str(skills_dir / "good.md"), "Good skill")
        state.messages = [{"role": "user", "content": "hi"}]
        state.skill = "previous"
        state.skill_text = "Previous skill"

        chat._cmd_skill("load good missing", state)
        assert state.skill == "previous"
        assert state.skill_text == "Previous skill"

    def test_load_missing_skill_reports_error(self, tmp_path, capsys):
        state, _ = _make_chat_state(tmp_path)
        state.messages = [{"role": "user", "content": "hi"}]
        chat._cmd_skill("load nope", state)
        out = capsys.readouterr().out
        assert "not found" in out.lower() or "rejected" in out.lower()

    def test_load_binary_rejected(self, tmp_path, capsys):
        state, _ = _make_chat_state(tmp_path)
        state.messages = [{"role": "user", "content": "hi"}]
        bad = tmp_path / "bad.bin"
        with open(bad, "wb") as f:
            f.write(os.urandom(1000))
        chat._cmd_skill(f"load {bad}", state)
        assert state.skill is None
        assert state.skill_text is None

    def test_unload(self, tmp_path, capsys):
        state, _ = _make_chat_state(tmp_path)
        state.messages = [{"role": "user", "content": "hi"}]
        state.skill = "x"
        state.skill_text = "TEXT"
        state.apply_skill()
        chat._cmd_skill("unload", state)
        assert state.skill is None
        assert state.skill_text is None
        assert "[SKILL BEGIN]" not in state.messages[0]["content"]

    def test_list(self, tmp_path, capsys):
        state, skills_dir = _make_chat_state(tmp_path)
        _write_text(str(skills_dir / "a.md"), "A")
        _write_text(str(skills_dir / "b.md"), "B")
        chat._cmd_skill("list", state)
        out = capsys.readouterr().out
        assert "a.md" in out
        assert "b.md" in out

    def test_show_no_skill(self, tmp_path, capsys):
        state, _ = _make_chat_state(tmp_path)
        chat._cmd_skill("show", state)
        assert "No skill loaded." in capsys.readouterr().out

    def test_help_includes_skill_commands(self, capsys):
        chat._show_help()
        out = capsys.readouterr().out
        assert "/skill load" in out
        assert "/skill unload" in out
        assert "/skill list" in out
        assert "/skill show" in out

    def test_handle_command_dispatches_skill(self, tmp_path, capsys):
        state, skills_dir = _make_chat_state(tmp_path)
        _write_text(str(skills_dir / "s.md"), "S")
        state.messages = [{"role": "user", "content": "hi"}]
        chat._handle_command("/skill load s", state)
        assert state.skill == "s"


class TestResolveSkillPath:
    def test_bare_name_resolves_in_skills_dir(self, tmp_path, monkeypatch):
        state, skills_dir = _make_chat_state(tmp_path)
        _write_text(str(skills_dir / "code-reviewer.md"), "Review code carefully.")
        monkeypatch.chdir(tmp_path)
        assert (
            chat._resolve_skill_path("code-reviewer", state)
            == str(skills_dir / "code-reviewer.md")
        )

    def test_md_preferred_over_txt(self, tmp_path, monkeypatch):
        state, skills_dir = _make_chat_state(tmp_path)
        _write_text(str(skills_dir / "s.md"), "md")
        _write_text(str(skills_dir / "s.txt"), "txt")
        monkeypatch.chdir(tmp_path)
        assert chat._resolve_skill_path("s", state) == str(skills_dir / "s.md")

    def test_path_style_prefers_skills_dir_over_cwd_file(self, tmp_path, monkeypatch):
        state, skills_dir = _make_chat_state(tmp_path)
        sub = tmp_path / "subdir"
        sub.mkdir()
        _write_text(str(sub / "web"), "cwd skill")
        (skills_dir / "subdir").mkdir()
        _write_text(str(skills_dir / "subdir" / "web.md"), "skills-dir skill")
        monkeypatch.chdir(tmp_path)
        assert (
            chat._resolve_skill_path("subdir/web", state)
            == str(skills_dir / "subdir" / "web.md")
        )

    def test_path_style_resolves_inside_skills_dir(self, tmp_path, monkeypatch):
        state, skills_dir = _make_chat_state(tmp_path)
        (skills_dir / "subdir").mkdir()
        _write_text(str(skills_dir / "subdir" / "web.md"), "skills-dir skill")
        monkeypatch.chdir(tmp_path)
        assert (
            chat._resolve_skill_path("subdir/web", state)
            == str(skills_dir / "subdir" / "web.md")
        )

    def test_path_style_rejects_out_of_tree_path(self, tmp_path, monkeypatch):
        state, skills_dir = _make_chat_state(tmp_path)
        outside = tmp_path / "outside.md"
        _write_text(str(outside), "outside skill")
        monkeypatch.chdir(tmp_path)
        assert chat._resolve_skill_path(str(outside), state) is None


class TestSkillPersistence:
    def test_save_load_persists_skill(self, tmp_path, capsys):
        state, _ = _make_chat_state(tmp_path)
        state.skill = "code-reviewer"
        state.skill_text = "Review code."
        path = str(tmp_path / "conv.json")
        chat._cmd_save(path, state)

        state2 = chat.ChatState(client=None, model="m")
        chat._cmd_load(path, state2)
        assert state2.skill == "code-reviewer"
        assert state2.skill_text == "Review code."
