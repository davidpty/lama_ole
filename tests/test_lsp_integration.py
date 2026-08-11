"""Optional integration test against a real language server.

These tests are opt-in: they only run when ``LAMA_OLE_LSP_INTEGRATION=1`` is
set AND a real LSP server binary is found on ``PATH`` (the deterministic fake
server is used everywhere else). By default the whole module self-skips so the
suite stays green on any machine, regardless of which language servers happen
to be installed.
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

from lsp.registry import DEFAULT_LSP_SERVERS
from lsp.session import LspSessionManager

_EXT_FOR_LANGUAGE = {
    "python": "py",
    "typescript": "ts",
    "javascript": "js",
    "rust": "rs",
    "cpp": "cpp",
    "c": "c",
    "go": "go",
    "json": "json",
}


def _available_server():
    """Return a (language, command) whose binary exists, or (None, None)."""
    for language, command in sorted(DEFAULT_LSP_SERVERS.items()):
        if shutil.which(command[0]) is not None:
            return language, command
    return None, None


LANGUAGE, COMMAND = _available_server()

_INTEGRATION_ENABLED = os.environ.get("LAMA_OLE_LSP_INTEGRATION", "") == "1"

pytestmark = pytest.mark.skipif(
    not _INTEGRATION_ENABLED or LANGUAGE is None,
    reason="LSP integration tests are opt-in; set LAMA_OLE_LSP_INTEGRATION=1 "
    "and have a working language server on PATH to run them",
)

_SAMPLE = "fn add(a: i32, b: i32) -> i32 { a + b }\n\nfn main() { let _ = add(1, 2); }\n"


def _sample_path(tmp):
    ext = _EXT_FOR_LANGUAGE.get(LANGUAGE, LANGUAGE)
    return os.path.join(tmp, "sample.%s" % ext)


@pytest.fixture(scope="module")
def real_server_ok():
    """Skip when the found server is unusable (e.g. a rustup shim).

    ``shutil.which`` can find a proxy binary that is not a working server; a
    quick handshake probe distinguishes real servers from dead shims.
    """
    if LANGUAGE is None:
        pytest.skip("no real language server on PATH")
    probe = tempfile.mkdtemp()
    manager = LspSessionManager(root_dir=probe, request_timeout=10.0)
    try:
        manager.get_client(LANGUAGE)
    except Exception as exc:  # noqa: BLE001 - environmental skip
        manager.stop_all()
        shutil.rmtree(probe, ignore_errors=True)
        pytest.skip("%r found on PATH but unusable: %s" % (COMMAND[0], exc))
    else:
        manager.stop_all()
        shutil.rmtree(probe, ignore_errors=True)
        return True


class TestRealServerSmoke:
    def test_handshake_and_document_sync(self, real_server_ok):
        tmp = tempfile.mkdtemp()
        try:
            file_path = _sample_path(tmp)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(_SAMPLE)
            manager = LspSessionManager(root_dir=tmp, request_timeout=20.0)
            try:
                manager.get_client(LANGUAGE)
                language = manager.sync_document(file_path)
                assert language == LANGUAGE
                status = manager.status()
                assert status["sessions"][LANGUAGE]["running"] is True
            finally:
                manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_definition_query(self, real_server_ok):
        tmp = tempfile.mkdtemp()
        try:
            file_path = _sample_path(tmp)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(_SAMPLE)
            manager = LspSessionManager(root_dir=tmp, request_timeout=20.0)
            try:
                manager.sync_document(file_path)
                client = manager.get_client(LANGUAGE)
                uri = manager.path_to_uri(file_path)
                result = client.request(
                    "textDocument/definition",
                    {"textDocument": {"uri": uri}, "position": {"line": 2, "character": 13}},
                )
                assert isinstance(result, list)
                if result:  # servers may answer empty on bare files
                    assert result[0]["uri"].startswith("file://")
            finally:
                manager.stop_all()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
