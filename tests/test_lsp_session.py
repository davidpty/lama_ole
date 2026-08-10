"""Tests for the LSP session manager and server registry.

Uses the deterministic fake LSP server (``tests/fakes/fake_lsp_server.py``) via
a ``LAMA_OLE_LSP_SERVERS`` override, so no real language server is needed.
"""

import contextlib
import json
import os
import shutil
import shlex
import sys
import tempfile
import time

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)
tests_dir = os.path.dirname(current_file)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

import pytest

from lsp.client import LspClientCrashed, LspConfigError
from lsp.registry import (
    DEFAULT_LSP_SERVERS,
    known_languages,
    language_for_path,
    resolve_server,
)
from lsp.session import LspSessionManager

from lsp_fixtures import (
    diag_script,
    env_override,
    fake_server_command,
    standard_script,
)


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@contextlib.contextmanager
def _env(**updates):
    saved = {}
    for key in updates:
        saved[key] = os.environ.get(key)
    for key, value in updates.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _query_state(manager, language="python"):
    """Ask the fake server for the last didOpen/didChange document state."""
    return manager.get_client(language).request("__state", {})


def _bump_mtime(path):
    """Force a distinct mtime so the freshness check notices the change."""
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 2))


class TestRegistry:
    def test_language_for_path_extensions(self):
        assert language_for_path("foo.py") == "python"
        assert language_for_path("/abs/path/main.pyi") == "python"
        assert language_for_path("component.tsx") == "typescript"
        assert language_for_path("mod.js") == "javascript"
        assert language_for_path("lib.rs") == "rust"
        assert language_for_path("a.cpp") == "cpp"
        assert language_for_path("main.c") == "c"
        assert language_for_path("config.json") == "json"
        assert language_for_path("unknown.xyz") is None
        assert language_for_path("Makefile") is None

    def test_extension_match_is_case_insensitive(self):
        assert language_for_path("FILE.PY") == "python"

    def test_known_languages_are_sorted_and_defaults_covered(self):
        langs = known_languages()
        assert langs == sorted(langs)
        assert "python" in langs
        assert "typescript" in langs
        for language in langs:
            assert language in DEFAULT_LSP_SERVERS

    def test_resolve_server_uses_env_override_list_form(self):
        argv, _path = fake_server_command(standard_script())
        with _env(LAMA_OLE_LSP_SERVERS=env_override("python", argv)):
            assert resolve_server("python") == argv

    def test_resolve_server_uses_env_override_string_form(self):
        argv, _path = fake_server_command(standard_script())
        string_cmd = " ".join(shlex.quote(part) for part in argv)
        with _env(LAMA_OLE_LSP_SERVERS=env_override("python", string_cmd)):
            assert resolve_server("python") == argv

    def test_resolve_server_unknown_language(self):
        argv, _path = fake_server_command(standard_script())
        with _env(LAMA_OLE_LSP_SERVERS=env_override("python", argv)):
            with pytest.raises(LspConfigError):
                resolve_server("kotlin")

    def test_resolve_server_missing_binary(self):
        bogus = ["definitely-not-a-real-binary-xyz", "--stdio"]
        with _env(LAMA_OLE_LSP_SERVERS=env_override("python", bogus)):
            with pytest.raises(LspConfigError):
                resolve_server("python")

    def test_resolve_server_invalid_env_json(self):
        with _env(LAMA_OLE_LSP_SERVERS="not json at all"):
            with pytest.raises(LspConfigError):
                resolve_server("python")


class TestSessionManager:
    def test_get_client_starts_and_reuses_session(self):
        tmp = tempfile.mkdtemp()
        try:
            argv, _path = fake_server_command(standard_script())
            with _env(LAMA_OLE_LSP_SERVERS=env_override("python", argv)):
                manager = LspSessionManager(root_dir=tmp)
                try:
                    first = manager.get_client("python")
                    assert first.is_running
                    assert first.pid is not None
                    assert manager.get_client("python") is first
                    sessions = manager.status()["sessions"]
                    assert sessions["python"]["running"] is True
                finally:
                    manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_get_client_unknown_language_raises(self):
        tmp = tempfile.mkdtemp()
        try:
            argv, _path = fake_server_command(standard_script())
            with _env(LAMA_OLE_LSP_SERVERS=env_override("python", argv)):
                manager = LspSessionManager(root_dir=tmp)
                try:
                    with pytest.raises(LspConfigError):
                        manager.get_client("kotlin")
                finally:
                    manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_get_client_missing_server_mentions_env_var(self):
        tmp = tempfile.mkdtemp()
        try:
            bogus = ["definitely-not-a-real-binary-xyz", "--stdio"]
            with _env(LAMA_OLE_LSP_SERVERS=env_override("python", bogus)):
                manager = LspSessionManager(root_dir=tmp)
                try:
                    with pytest.raises(LspConfigError) as excinfo:
                        manager.get_client("python")
                    assert "LAMA_OLE_LSP_SERVERS" in str(excinfo.value)
                finally:
                    manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sync_document_did_open_then_did_change(self):
        tmp = tempfile.mkdtemp()
        try:
            file_path = os.path.join(tmp, "sample.py")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("def foo():\n    return 1\n")
            argv, _path = fake_server_command(standard_script())
            with _env(LAMA_OLE_LSP_SERVERS=env_override("python", argv)):
                manager = LspSessionManager(root_dir=tmp)
                try:
                    assert manager.sync_document(file_path) == "python"
                    state = _query_state(manager)
                    assert state["languageId"] == "python"
                    assert state["version"] == 1
                    assert "def foo" in state["text"]

                    # Unchanged file -> no new didChange (version stays 1).
                    manager.sync_document(file_path)
                    assert _query_state(manager)["version"] == 1

                    # Touched file -> didChange with full text, version bumped.
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("def bar():\n    return 2\n")
                    _bump_mtime(file_path)
                    manager.sync_document(file_path)
                    state = _query_state(manager)
                    assert state["version"] == 2
                    assert "def bar" in state["text"]
                finally:
                    manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_diagnostics_cached_from_push(self):
        tmp = tempfile.mkdtemp()
        try:
            file_path = os.path.join(tmp, "sample.py")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            uri = LspSessionManager.path_to_uri(os.path.abspath(file_path))
            diag = {
                "severity": 1,
                "message": "boom",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 5},
                },
            }
            script = diag_script(uri, [diag], after_ms=50)
            argv, _path = fake_server_command(script)
            with _env(LAMA_OLE_LSP_SERVERS=env_override("python", argv)):
                manager = LspSessionManager(root_dir=tmp)
                try:
                    manager.sync_document(file_path)
                    assert _wait_until(
                        lambda: len(manager.get_diagnostics(file_path)) > 0
                    )
                    found = manager.get_diagnostics(file_path)
                    assert found[0]["message"] == "boom"
                    assert found[0]["severity"] == 1
                finally:
                    manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_auto_restart_once_after_crash(self):
        tmp = tempfile.mkdtemp()
        try:
            file_path = os.path.join(tmp, "sample.py")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("print('hi')\n")
            script = standard_script()
            script["crash_on"] = "boom"
            argv, _path = fake_server_command(script)
            with _env(LAMA_OLE_LSP_SERVERS=env_override("python", argv)):
                manager = LspSessionManager(root_dir=tmp)
                try:
                    manager.sync_document(file_path)
                    client = manager.get_client("python")
                    assert client.is_running

                    # File content containing "boom" crashes the server on the
                    # didChange sync.
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("boom\n")
                    _bump_mtime(file_path)
                    manager.sync_document(file_path)
                    assert _wait_until(lambda: not client.is_running)

                    # Next sync auto-restarts once and re-syncs the document.
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("print('recovered')\n")
                    _bump_mtime(file_path)
                    assert manager.sync_document(file_path) == "python"
                    assert client.is_running
                finally:
                    manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_second_crash_requires_explicit_start(self):
        tmp = tempfile.mkdtemp()
        try:
            file_path = os.path.join(tmp, "sample.py")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("print('hi')\n")
            script = standard_script()
            script["crash_on"] = "boom"
            argv, _path = fake_server_command(script)
            with _env(LAMA_OLE_LSP_SERVERS=env_override("python", argv)):
                manager = LspSessionManager(root_dir=tmp)
                try:
                    manager.sync_document(file_path)
                    client = manager.get_client("python")

                    # Crash 1 -> auto-restart used up.
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("boom\n")
                    _bump_mtime(file_path)
                    manager.sync_document(file_path)
                    assert _wait_until(lambda: not client.is_running)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("print('ok')\n")
                    _bump_mtime(file_path)
                    assert manager.sync_document(file_path) == "python"
                    assert client.is_running

                    # Crash 2 -> next query raises, asking for explicit lsp_start.
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("boom\n")
                    _bump_mtime(file_path)
                    manager.sync_document(file_path)
                    assert _wait_until(lambda: not client.is_running)
                    with pytest.raises(LspClientCrashed) as excinfo:
                        manager.sync_document(file_path)
                    assert "lsp_start" in str(excinfo.value)
                finally:
                    manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stop_session_and_idempotent_stop_all(self):
        tmp = tempfile.mkdtemp()
        try:
            argv, _path = fake_server_command(standard_script())
            servers = json_dumps_two_languages(argv, argv)
            with _env(LAMA_OLE_LSP_SERVERS=servers):
                manager = LspSessionManager(root_dir=tmp)
                try:
                    manager.get_client("python")
                    manager.get_client("typescript")
                    assert len(manager.status()["sessions"]) == 2

                    assert manager.stop_session("python") is True
                    assert manager.stop_session("python") is False
                    assert "python" not in manager.status()["sessions"]
                    assert manager.get_active_client() is not None

                    manager.stop_all()
                    manager.stop_all()  # idempotent
                    assert manager.status()["sessions"] == {}
                    assert manager.get_active_client() is None
                finally:
                    manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_get_active_client_none_without_sessions(self):
        tmp = tempfile.mkdtemp()
        try:
            manager = LspSessionManager(root_dir=tmp)
            try:
                assert manager.get_active_client() is None
            finally:
                manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def json_dumps_two_languages(first, second):
    return json.dumps({"python": first, "typescript": second})
