"""Unit tests for the config-file / environment-variable default mechanism.

Covers the KEY=VALUE file parser, the loader precedence rules (shell env >
project file > user file), the typed env helpers, the argparse integration
(--no-* negation), and the strict-override resolution of repeatables.
"""

import importlib.util
import os
import re
import pytest
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

# Load the CLI module by path: the repo-root __init__.py makes this directory
# importable as package "lama_ole", which would shadow lama_ole.py otherwise.
_spec = importlib.util.spec_from_file_location(
    "lama_ole_cli", os.path.join(lama_ole_dir, "lama_ole.py")
)
lama_ole_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lama_ole_cli)

_env_bool = lama_ole_cli._env_bool
_env_choice = lama_ole_cli._env_choice
_env_float = lama_ole_cli._env_float
_env_int = lama_ole_cli._env_int
_env_list = lama_ole_cli._env_list
_env_str = lama_ole_cli._env_str
_parse_env_file = lama_ole_cli._parse_env_file
_resolve_env_defaults = lama_ole_cli._resolve_env_defaults
build_parser = lama_ole_cli.build_parser
load_env_files = lama_ole_cli.load_env_files


@pytest.fixture
def env_patch(monkeypatch):
    """Snapshot and clean LAMA_OLE_* env vars around each test."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("LAMA_OLE_")}
    for k in saved:
        monkeypatch.delenv(k, raising=False)
    yield
    for k, v in saved.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def config_paths(tmp_path, monkeypatch):
    user_dir = tmp_path / "user"
    proj_dir = tmp_path / "proj"
    user_dir.mkdir()
    proj_dir.mkdir()
    user_file = user_dir / "lama_ole.env"
    proj_file = proj_dir / "lama_ole.env"
    monkeypatch.setattr(lama_ole_cli, "_ENV_FILE_USER", str(user_file))
    monkeypatch.setattr(lama_ole_cli, "_ENV_FILE_PROJECT", str(proj_file))
    return user_file, proj_file


class TestParseEnvFile:
    def test_missing_file_returns_empty(self):
        assert _parse_env_file("/nonexistent/path/env") == {}

    def test_parses_values_comments_and_blanks(self, tmp_path):
        f = tmp_path / "env"
        f.write_text(
            "# comment line\n"
            "\n"
            "LAMA_OLE_CHAT=true\n"
            'LAMA_OLE_MODEL="qwen3:8b"\n'
            "LAMA_OLE_EMPTY=\n"
            "  LAMA_OLE_TOOL=  tools.web_tools  \n"
            "NO_EQUALS_IGNORED\n"
        )
        result = _parse_env_file(str(f))
        assert result["LAMA_OLE_CHAT"] == "true"
        assert result["LAMA_OLE_MODEL"] == "qwen3:8b"
        assert result["LAMA_OLE_EMPTY"] == ""
        assert result["LAMA_OLE_TOOL"] == "tools.web_tools"
        assert "NO_EQUALS_IGNORED" not in result


class TestLoadEnvFiles:
    def test_user_file_applies(self, env_patch, config_paths):
        user_file, _ = config_paths
        user_file.write_text("LAMA_OLE_CHAT=true\nLAMA_OLE_TEMPERATURE=0.7\n")
        load_env_files()
        assert os.environ.get("LAMA_OLE_CHAT") == "true"
        assert os.environ.get("LAMA_OLE_TEMPERATURE") == "0.7"

    def test_project_file_overrides_user(self, env_patch, config_paths):
        user_file, proj_file = config_paths
        user_file.write_text("LAMA_OLE_MODEL=llama3.2:3b\n")
        proj_file.write_text("LAMA_OLE_MODEL=qwen3:8b\n")
        load_env_files()
        assert os.environ.get("LAMA_OLE_MODEL") == "qwen3:8b"

    def test_shell_env_wins_over_files(self, env_patch, config_paths, monkeypatch):
        _, proj_file = config_paths
        proj_file.write_text("LAMA_OLE_MODEL=qwen3:8b\n")
        monkeypatch.setenv("LAMA_OLE_MODEL", "shell-model")
        load_env_files()
        assert os.environ.get("LAMA_OLE_MODEL") == "shell-model"

    def test_empty_values_ignored(self, env_patch, config_paths):
        user_file, _ = config_paths
        user_file.write_text("LAMA_OLE_MODEL=\n")
        load_env_files()
        assert "LAMA_OLE_MODEL" not in os.environ


class TestEnvHelpers:
    def test_env_str(self, env_patch, monkeypatch):
        assert _env_str("LAMA_OLE_X", "d") == "d"
        monkeypatch.setenv("LAMA_OLE_X", "value")
        assert _env_str("LAMA_OLE_X", "d") == "value"
        monkeypatch.setenv("LAMA_OLE_X", "")
        assert _env_str("LAMA_OLE_X", "d") == "d"

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "TRUE", "Yes"])
    def test_env_bool_true(self, env_patch, monkeypatch, raw):
        monkeypatch.setenv("LAMA_OLE_B", raw)
        assert _env_bool("LAMA_OLE_B", False) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "FALSE"])
    def test_env_bool_false(self, env_patch, monkeypatch, raw):
        monkeypatch.setenv("LAMA_OLE_B", raw)
        assert _env_bool("LAMA_OLE_B", True) is False

    def test_env_bool_invalid_falls_back(self, env_patch, monkeypatch, capsys):
        monkeypatch.setenv("LAMA_OLE_B", "banana")
        assert _env_bool("LAMA_OLE_B", True) is True
        assert "banana" in capsys.readouterr().err

    def test_env_bool_empty_falls_back(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_B", "")
        assert _env_bool("LAMA_OLE_B", True) is True

    def test_env_int(self, env_patch, monkeypatch, capsys):
        assert _env_int("LAMA_OLE_I", 7) == 7
        monkeypatch.setenv("LAMA_OLE_I", "8192")
        assert _env_int("LAMA_OLE_I", 7) == 8192
        monkeypatch.setenv("LAMA_OLE_I", "nope")
        assert _env_int("LAMA_OLE_I", 7) == 7
        assert "nope" in capsys.readouterr().err

    def test_env_float(self, env_patch, monkeypatch, capsys):
        assert _env_float("LAMA_OLE_F", 0.0) == 0.0
        monkeypatch.setenv("LAMA_OLE_F", "0.7")
        assert _env_float("LAMA_OLE_F", 0.0) == 0.7
        monkeypatch.setenv("LAMA_OLE_F", "x")
        assert _env_float("LAMA_OLE_F", 0.0) == 0.0

    def test_env_list(self, env_patch, monkeypatch):
        assert _env_list("LAMA_OLE_L") is None
        monkeypatch.setenv("LAMA_OLE_L", "a b,c d")
        assert _env_list("LAMA_OLE_L") == ["a", "b", "c", "d"]
        monkeypatch.setenv("LAMA_OLE_L", "")
        assert _env_list("LAMA_OLE_L") is None


    def test_env_choice_valid(self, env_patch, monkeypatch):
        assert _env_choice("LAMA_OLE_C", "auto", ["auto", "always", "never"]) == "auto"
        monkeypatch.setenv("LAMA_OLE_C", "never")
        assert _env_choice("LAMA_OLE_C", "auto", ["auto", "always", "never"]) == "never"

    def test_env_choice_invalid_falls_back(self, env_patch, monkeypatch, capsys):
        monkeypatch.setenv("LAMA_OLE_C", "banana")
        assert _env_choice("LAMA_OLE_C", "auto", ["auto", "always", "never"]) == "auto"
        assert "banana" in capsys.readouterr().err


class TestArgparseIntegration:
    def test_env_defaults_flow_into_parser(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_CHAT", "true")
        monkeypatch.setenv("LAMA_OLE_TEMPERATURE", "0.7")
        monkeypatch.setenv("LAMA_OLE_MODEL", "qwen3:8b")
        args = build_parser().parse_args([])
        assert args.chat is True
        assert args.temperature == 0.7
        assert args.model == "qwen3:8b"

    def test_cli_overrides_env(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_CHAT", "true")
        monkeypatch.setenv("LAMA_OLE_MODEL", "qwen3:8b")
        args = build_parser().parse_args(["--no-chat", "-m", "llama3.2:3b"])
        assert args.chat is False
        assert args.model == "llama3.2:3b"

    def test_boolean_negation_available(self, env_patch):
        parser = build_parser()
        assert parser.parse_args(["--chat"]).chat is True
        assert parser.parse_args(["--no-chat"]).chat is False
        assert parser.parse_args(["--thinking"]).thinking is True
        assert parser.parse_args(["--no-thinking"]).thinking is False
        assert parser.parse_args(["--no-safe"]).safe is False
        assert parser.parse_args(["--no-ollama_websearch"]).ollama_websearch is False

    def test_invalid_choice_default_falls_back(self, env_patch, monkeypatch, capsys):
        monkeypatch.setenv("LAMA_OLE_COLOR", "banana")
        args = build_parser().parse_args([])
        assert args.color == "auto"
        assert "banana" in capsys.readouterr().err

    def test_color_none_accepts_cli_choice(self):
        args = build_parser().parse_args(["--color", "none"])
        assert args.color == "none"

    def test_color_none_accepts_env_choice(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_COLOR", "none")
        args = build_parser().parse_args([])
        assert args.color == "none"


class TestResolveEnvDefaults:
    def test_env_tools_used_when_cli_absent(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_TOOL", "tools.web_tools tools.edit")
        args = build_parser().parse_args([])
        _resolve_env_defaults(args)
        assert args.tools == ["tools.web_tools", "tools.edit"]

    def test_cli_tools_merge_with_env(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_TOOL", "tools.web_tools tools.edit")
        args = build_parser().parse_args(["--tool", "tools.example_tools"])
        _resolve_env_defaults(args)
        assert args.tools == ["tools.web_tools", "tools.edit", "tools.example_tools"]

    def test_merge_dedups_env_first(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_TOOL", "tools.a tools.b")
        args = build_parser().parse_args(["--tool", "tools.b", "--tool", "tools.c"])
        _resolve_env_defaults(args)
        assert args.tools == ["tools.a", "tools.b", "tools.c"]

    def test_ignore_config_tools_uses_cli_only(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_TOOL", "tools.web_tools tools.edit")
        args = build_parser().parse_args(["--ignore-config-tools", "--tool", "tools.c"])
        _resolve_env_defaults(args)
        assert args.tools == ["tools.c"]

    def test_ignore_config_tools_without_cli_leaves_none(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_TOOL", "tools.web_tools tools.edit")
        args = build_parser().parse_args(["--ignore-config-tools"])
        _resolve_env_defaults(args)
        assert args.tools is None

    def test_cli_tools_deduped_when_repeated(self, env_patch):
        args = build_parser().parse_args(["--tool", "tools.a", "--tool", "tools.a"])
        _resolve_env_defaults(args)
        assert args.tools == ["tools.a"]

    def test_vision_models_env(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_VISION_MODEL", "llava, gemma3")
        args = build_parser().parse_args([])
        _resolve_env_defaults(args)
        assert args.vision_models == ["llava", "gemma3"]

    def test_vision_models_cli_strictly_overrides(self, env_patch, monkeypatch):
        monkeypatch.setenv("LAMA_OLE_VISION_MODEL", "llava, gemma3")
        args = build_parser().parse_args(["--vision_model", "qwen2.5-vl"])
        _resolve_env_defaults(args)
        assert args.vision_models == ["qwen2.5-vl"]

    def test_no_env_leaves_none(self, env_patch):
        args = build_parser().parse_args([])
        _resolve_env_defaults(args)
        assert args.tools is None
        assert args.vision_models is None


class TestExampleConfig:
    """Guard for lama_ole.env.example: it must stay inert and in sync.

    The example is a commented-out reference, so copying it must change
    nothing (``_parse_env_file`` returns an empty dict). Every key it
    documents must be a real runtime variable, and every CLI env default must
    be documented in it — otherwise a rename or a new flag leaves the
    reference config stale.
    """

    EXAMPLE = os.path.join(lama_ole_dir, "lama_ole.env.example")

    _RUNTIME_SOURCES = [
        "lama_ole.py",
        "chat.py",
        "history.py",
        os.path.join("tool_base", "engine.py"),
        os.path.join("lsp", "registry.py"),
        os.path.join("lsp", "session.py"),
        os.path.join("tools", "media_understanding_tools.py"),
        os.path.join("tools", "lsp_tools.py"),
    ]

    @classmethod
    def _documented_keys(cls):
        text = open(cls.EXAMPLE, encoding="utf-8").read()
        return set(re.findall(r"^#(LAMA_OLE_[A-Z0-9_]+)=", text, re.M))

    @classmethod
    def _known_runtime_vars(cls):
        found = set()
        for rel in cls._RUNTIME_SOURCES:
            path = os.path.join(lama_ole_dir, rel)
            found.update(
                re.findall(r"LAMA_OLE_[A-Z0-9_]+", open(path, encoding="utf-8").read())
            )
        return found

    def test_example_is_inert_when_copied(self):
        assert _parse_env_file(self.EXAMPLE) == {}

    def test_every_documented_key_is_a_real_var(self):
        documented = self._documented_keys()
        unknown = documented - self._known_runtime_vars()
        assert not unknown, "example documents unrecognized vars: %s" % sorted(unknown)

    def test_every_cli_env_default_is_documented(self):
        cli_envs = set(
            re.findall(
                r'_env_(?:str|int|float|bool|choice|list)\("(LAMA_OLE_[A-Z0-9_]+)"',
                open(os.path.join(lama_ole_dir, "lama_ole.py"), encoding="utf-8").read(),
            )
        )
        documented = self._documented_keys()
        missing = cli_envs - documented
        assert not missing, "CLI env defaults missing from example: %s" % sorted(missing)
