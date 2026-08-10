"""Tests for the LSP toolset (``tools/lsp_tools.py``).

Exercises every tool's success/error return shape against the fake LSP server,
plus the module-level contract flags (``__tool_readonly__``, ``__tool_env__``)
and toolset discoverability. Uses the module-level singleton session manager,
so each test starts from a clean slate via ``stop_all``.
"""

import os
import shutil
import sys
import tempfile

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)
tests_dir = os.path.dirname(current_file)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

import pytest

from tool_base import Tool, get_available_toolsets
from tools_security.validate_path import register_basepath

# Allow absolute paths under /tmp (the tool env) so temp files pass validate_path.
register_basepath("/tmp")

import tools.lsp_tools as lsp_tools

from lsp import get_manager

from lsp_fixtures import env_override, fake_server_command, standard_script

_SAMPLE = "def foo():\n    return 1\n"


@pytest.fixture()
def tool_env():
    """Fresh temp file + python session wired to the fake LSP server."""
    get_manager().stop_all()
    tmp_dir = tempfile.mkdtemp()
    register_basepath(tmp_dir)
    file_path = os.path.join(tmp_dir, "sample.py")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(_SAMPLE)
    argv, _script_path = fake_server_command(standard_script())
    os.environ["LAMA_OLE_LSP_SERVERS"] = env_override("python", argv)
    try:
        yield {"tmp_dir": tmp_dir, "file_path": file_path, "argv": argv}
    finally:
        get_manager().stop_all()
        os.environ.pop("LAMA_OLE_LSP_SERVERS", None)
        shutil.rmtree(tmp_dir, ignore_errors=True)


class TestModuleContract:
    def test_readonly_flag(self):
        assert lsp_tools.__tool_readonly__ is True

    def test_tool_env_keys(self):
        for key in ("LAMA_OLE_LSP_SERVERS", "LAMA_OLE_LSP_ROOT", "LAMA_OLE_LSP_TIMEOUT"):
            assert key in lsp_tools.__tool_env__

    def test_lsp_tools_in_available_toolsets(self):
        assert "lsp_tools" in get_available_toolsets()

    def test_every_lsp_tool_is_registered(self):
        decorated = [name for name in dir(lsp_tools) if name.startswith("lsp_")]
        assert len(decorated) >= 11
        for name in decorated:
            assert isinstance(getattr(lsp_tools, name), Tool), name


class TestToolSuccess:
    def test_lsp_start_idempotent(self, tool_env):
        first = lsp_tools.lsp_start("python", root_dir=tool_env["tmp_dir"])
        assert first["status"] == "success"
        pid = first["pid"]
        second = lsp_tools.lsp_start("python", root_dir=tool_env["tmp_dir"])
        assert second["status"] == "success"
        assert second["pid"] == pid  # reuses the running session

    def test_lsp_open(self, tool_env):
        result = lsp_tools.lsp_open(tool_env["file_path"])
        assert result["status"] == "success"
        assert result["language"] == "python"
        assert "Synced" in result["data"]

    def test_lsp_hover(self, tool_env):
        result = lsp_tools.lsp_hover(tool_env["file_path"], 0, 4)
        assert result["status"] == "success"
        assert result["data"]["contents"] == "hover-info: int"
        assert result["data"]["range"]["start"]["line"] == 0

    def test_lsp_definition(self, tool_env):
        result = lsp_tools.lsp_definition(tool_env["file_path"], 0, 4)
        assert result["status"] == "success"
        locations = result["data"]["locations"]
        assert locations[0]["uri"] == "file:///def.py"

    def test_lsp_references(self, tool_env):
        result = lsp_tools.lsp_references(tool_env["file_path"], 0, 4)
        assert result["status"] == "success"
        assert result["data"]["locations"][0]["uri"] == "file:///ref.py"

    def test_lsp_completion(self, tool_env):
        result = lsp_tools.lsp_completion(tool_env["file_path"], 0, 4, limit=10)
        assert result["status"] == "success"
        items = result["data"]["items"]
        assert items[0]["label"] == "foo"
        assert result["data"]["is_incomplete"] is False

    def test_lsp_signature_help(self, tool_env):
        result = lsp_tools.lsp_signature_help(tool_env["file_path"], 0, 4)
        assert result["status"] == "success"
        data = result["data"]
        assert data["signatures"][0]["label"] == "fn(x: int)"
        assert data["active_parameter"] == 0

    def test_lsp_document_symbols(self, tool_env):
        result = lsp_tools.lsp_document_symbols(tool_env["file_path"], limit=10)
        assert result["status"] == "success"
        symbols = result["data"]["symbols"]
        assert symbols[0]["name"] == "main"
        assert symbols[0]["kind"] == 12

    def test_lsp_workspace_symbols(self, tool_env):
        lsp_tools.lsp_start("python", root_dir=tool_env["tmp_dir"])
        result = lsp_tools.lsp_workspace_symbols("main", limit=10)
        assert result["status"] == "success"
        assert result["data"]["symbols"][0]["name"] == "main"
        assert result["data"]["symbols"][0]["containerName"] == "mod"

    def test_lsp_status(self, tool_env):
        lsp_tools.lsp_start("python", root_dir=tool_env["tmp_dir"])
        result = lsp_tools.lsp_status()
        assert result["status"] == "success"
        sessions = result["data"]["sessions"]
        assert sessions["python"]["running"] is True

    def test_lsp_stop(self, tool_env):
        lsp_tools.lsp_start("python", root_dir=tool_env["tmp_dir"])
        result = lsp_tools.lsp_stop("python")
        assert result["status"] == "success"
        assert "stopped" in result["data"]

    def test_lsp_diagnostics(self, tool_env):
        result = lsp_tools.lsp_diagnostics(tool_env["file_path"])
        assert result["status"] == "success"
        assert result["data"] == {"diagnostics": [], "count": 0}


class TestToolErrors:
    def test_workspace_symbols_requires_session(self):
        get_manager().stop_all()
        result = lsp_tools.lsp_workspace_symbols("main")
        assert result["status"] == "error"
        assert any("lsp_start" in m for m in result["message"])

    def test_stop_missing_session(self):
        get_manager().stop_all()
        result = lsp_tools.lsp_stop("python")
        assert result["status"] == "error"
        assert any("No language server session" in m for m in result["message"])

    def test_missing_file(self, tool_env):
        result = lsp_tools.lsp_hover("ghost.py", 0, 0)
        assert result["status"] == "error"
        assert any("does not exist" in m for m in result["message"])

    def test_path_traversal_blocked(self, tool_env):
        result = lsp_tools.lsp_hover("../escape.py", 0, 0)
        assert result["status"] == "error"
        assert any("traversal" in m or "safety" in m for m in result["message"])

    def test_unknown_language(self, tool_env):
        result = lsp_tools.lsp_start("kotlin", root_dir=tool_env["tmp_dir"])
        assert result["status"] == "error"
        assert any("Known languages" in m for m in result["message"])

    def test_missing_server_binary(self, tool_env):
        argv = ["definitely-not-a-real-binary-xyz", "--stdio"]
        os.environ["LAMA_OLE_LSP_SERVERS"] = env_override("python", argv)
        try:
            get_manager().stop_all()
            result = lsp_tools.lsp_start("python", root_dir=tool_env["tmp_dir"])
            assert result["status"] == "error"
            assert any("LAMA_OLE_LSP_SERVERS" in m for m in result["message"])
        finally:
            os.environ["LAMA_OLE_LSP_SERVERS"] = env_override("python", tool_env["argv"])
            get_manager().stop_all()

    def test_lsp_error_translated(self, tool_env):
        script = standard_script()
        script["errors"] = {
            "textDocument/hover": {"code": -32602, "message": "bad position"}
        }
        argv, _script_path = fake_server_command(script)
        os.environ["LAMA_OLE_LSP_SERVERS"] = env_override("python", argv)
        try:
            get_manager().stop_all()
            result = lsp_tools.lsp_hover(tool_env["file_path"], 0, 4)
            assert result["status"] == "error"
            assert any("LSP error -32602" in m for m in result["message"])
        finally:
            os.environ["LAMA_OLE_LSP_SERVERS"] = env_override("python", tool_env["argv"])
            get_manager().stop_all()
