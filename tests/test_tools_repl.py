"""Unit tests for the REPL tool-management commands (/tools ...).

Covers the registry helpers (idempotent load, available toolsets, module
lookup) and the chat.py command handlers: loaded / available / show / all /
load / unload, atomic multi-load, duplicate/unknown rejection, ollama_tools
refresh, and /save-/load persistence.
"""

import os
import shutil
import sys
import tempfile
import uuid

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402
from tool_base import (  # noqa: E402
    get_available_toolsets,
    get_tool_modules_info,
    get_tools_of_module,
    load_tools,
    peek_tools_of_module,
)

MODULE_GOOD = """\
from tool_base import tool

@tool(description="Adds two numbers")
def add(a: int, b: int) -> int:
    return a + b

@tool(description="Multiplies two numbers")
def mul(a: int, b: int) -> int:
    return a * b
"""

MODULE_GREET = """\
from tool_base import tool

@tool(description="Greets someone")
def greet(name: str) -> str:
    return "hi " + name
"""

MODULE_BAD = """\
raise RuntimeError("boom")
"""


@pytest.fixture()
def fake_tools_dir():
    """A temp dir on sys.path with two good toolset modules and one that
    raises on import. Returns the dir path."""
    tmp = tempfile.mkdtemp()
    uid = uuid.uuid4().hex[:8]
    sys.path.insert(0, tmp)
    with open(os.path.join(tmp, "good_a.py"), "w", encoding="utf-8") as f:
        f.write(MODULE_GOOD)
    with open(os.path.join(tmp, "good_b.py"), "w", encoding="utf-8") as f:
        f.write(MODULE_GREET)
    with open(os.path.join(tmp, f"bad_{uid}.py"), "w", encoding="utf-8") as f:
        f.write(MODULE_BAD)
    yield tmp, f"bad_{uid}"
    sys.path.remove(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


def make_state(tools_dir):
    state = chat.ChatState(client=None, model="test")
    state.tools_dir = tools_dir
    return state


# --- registry helpers -------------------------------------------------------


def test_get_available_toolsets_skips_private_and_init():
    tmp = tempfile.mkdtemp()
    try:
        for name in ("__init__.py", "_private.py", "ok_a.py", "ok_b.txt", "ok_c.py"):
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
        assert get_available_toolsets(tmp) == ["ok_a", "ok_c"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_available_toolsets_missing_dir():
    assert get_available_toolsets("/nonexistent/definitely/missing") == []


def test_load_tools_idempotent(fake_tools_dir):
    tmp, _ = fake_tools_dir
    load_tools("good_a")
    load_tools("good_a")
    matches = [m for m in get_tool_modules_info() if m.module_name == "good_a"]
    assert len(matches) == 1


def test_get_tools_of_module(fake_tools_dir):
    tmp, _ = fake_tools_dir
    load_tools("good_a")
    names = sorted(t.name for t in get_tools_of_module("good_a"))
    assert names == ["add", "mul"]
    assert get_tools_of_module("never_loaded_xyz") == []


def test_peek_tools_of_module_not_registered(fake_tools_dir):
    tmp, _ = fake_tools_dir
    tools = peek_tools_of_module("good_b")
    assert sorted(t.name for t in tools) == ["greet"]
    matches = [m for m in get_tool_modules_info() if m.module_name == "good_b"]
    assert matches == []


def test_resolve_toolset_module():
    assert chat._resolve_toolset_module("example_tools") == "tools.example_tools"
    assert chat._resolve_toolset_module("tools.example_tools") == "tools.example_tools"
    assert chat._resolve_toolset_module("no_such_module_zzz") == "tools.no_such_module_zzz"


def test_resolve_toolset_module_bare_fallback(fake_tools_dir):
    tmp, _ = fake_tools_dir
    assert chat._resolve_toolset_module("good_a") == "good_a"


# --- REPL commands ----------------------------------------------------------


def test_bare_tools_shows_usage(capsys):
    state = make_state(None)
    chat._cmd_tools("", state)
    out = capsys.readouterr().out
    assert "/tools load" in out
    assert "/tools unload" in out


def test_tools_available_command(fake_tools_dir, capsys):
    tmp, bad_name = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("available", state)
    out = capsys.readouterr().out
    assert "good_a" in out
    assert "good_b" in out
    assert bad_name in out


def test_tools_load_refreshes_ollama(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("load good_a good_b", state)
    capsys.readouterr()
    assert state.loaded_tool_modules == ["good_a", "good_b"]
    assert sorted(t.name for t in state.loaded_tools) == ["add", "greet", "mul"]
    assert state.ollama_tools is not None
    assert len(state.ollama_tools) == 3


def test_tools_load_duplicate_rejected(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("load good_a", state)
    capsys.readouterr()
    chat._cmd_tools("load good_a", state)
    out = capsys.readouterr().out
    assert "already loaded" in out
    assert state.loaded_tool_modules == ["good_a"]
    assert len(state.loaded_tools) == 2


def test_tools_load_unknown_rejected(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("load good_a", state)
    capsys.readouterr()
    chat._cmd_tools("load unknown_toolset_zzz", state)
    out = capsys.readouterr().out
    assert "unknown toolset" in out
    assert state.loaded_tool_modules == ["good_a"]
    assert len(state.loaded_tools) == 2


def test_tools_load_import_error_rolls_back(fake_tools_dir, capsys):
    tmp, bad_name = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools(f"load good_a {bad_name} good_b", state)
    out = capsys.readouterr().out
    assert "boom" in out or "Error loading toolset" in out
    assert state.loaded_tool_modules == []
    assert state.loaded_tools == []
    assert state.ollama_tools is None


def test_tools_unload(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("load good_a good_b", state)
    capsys.readouterr()
    chat._cmd_tools("unload good_a", state)
    out = capsys.readouterr().out
    assert "Unloaded toolset(s): good_a" in out
    assert state.loaded_tool_modules == ["good_b"]
    assert sorted(t.name for t in state.loaded_tools) == ["greet"]
    assert len(state.ollama_tools) == 1


def test_tools_unload_not_loaded(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("load good_a", state)
    capsys.readouterr()
    chat._cmd_tools("unload good_b", state)
    out = capsys.readouterr().out
    assert "is not loaded" in out
    assert state.loaded_tool_modules == ["good_a"]
    assert len(state.loaded_tools) == 2


def test_tools_unload_all_sets_ollama_none(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("load good_a good_b", state)
    capsys.readouterr()
    chat._cmd_tools("unload good_a good_b", state)
    capsys.readouterr()
    assert state.loaded_tool_modules == []
    assert state.loaded_tools == []
    assert state.ollama_tools is None


def test_tools_loaded_command(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("load good_a", state)
    capsys.readouterr()
    chat._cmd_tools("loaded", state)
    out = capsys.readouterr().out
    assert "good_a" in out
    assert "add" in out
    assert "mul" in out


def test_tools_show_command(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("show good_a", state)
    out = capsys.readouterr().out
    assert "add" in out
    assert "mul" in out
    assert "Adds two numbers" in out


def test_tools_all_command(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    chat._cmd_tools("all", state)
    out = capsys.readouterr().out
    assert "good_a" in out
    assert "good_b" in out
    assert "greet" in out


def test_help_lists_tools_subcommands(capsys):
    chat._show_help()
    out = capsys.readouterr().out
    assert "/tools load" in out
    assert "/tools unload" in out
    assert "/tools loaded" in out
    assert "/tools available" in out


def test_save_load_persistence(fake_tools_dir, capsys):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    state.messages = [{"role": "user", "content": "hi"}]
    chat._cmd_tools("load good_a", state)
    capsys.readouterr()

    save_path = os.path.join(tmp, "convo.json")
    chat._cmd_save(save_path, state)

    fresh = make_state(tmp)
    chat._cmd_load(save_path, fresh)
    assert fresh.loaded_tool_modules == ["good_a"]
    assert sorted(t.name for t in fresh.loaded_tools) == ["add", "mul"]
    assert len(fresh.ollama_tools) == 2


def test_refresh_ollama_tools_empty(fake_tools_dir):
    tmp, _ = fake_tools_dir
    state = make_state(tmp)
    state.refresh_ollama_tools()
    assert state.ollama_tools is None
