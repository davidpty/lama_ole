"""Tests for LspClient against the deterministic fake language server."""

import os
import sys
import time

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)
tests_dir = os.path.dirname(current_file)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

import pytest

from lsp.client import (
    LspClient,
    LspError,
    LspClientCrashed,
    LspTimeout,
)

from lsp_fixtures import fake_server_command, standard_script


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _is_reaped(pid):
    try:
        return os.waitpid(pid, os.WNOHANG) != (0, 0)
    except ChildProcessError:
        return True  # already reaped by Popen.wait()


class TestClientLifecycle:
    def test_handshake_and_capabilities(self):
        argv, path = fake_server_command(standard_script())
        client = LspClient(argv, language="python", root_dir="/tmp")
        try:
            client.start()
            assert client.is_running
            assert client.pid is not None
            assert client.capabilities.get("hoverProvider") is True
            assert client.capabilities.get("definitionProvider") is True
        finally:
            client.shutdown()

    def test_request_returns_result(self):
        argv, path = fake_server_command(standard_script())
        client = LspClient(argv, language="python", root_dir="/tmp")
        try:
            client.start()
            result = client.request("textDocument/hover", {})
            assert result == {"contents": "hover-info: int", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}}}
        finally:
            client.shutdown()

    def test_request_id_correlation(self):
        script = standard_script()
        script["answers"]["textDocument/definition"] = [
            {"uri": "file:///a.py", "range": {}}
        ]
        argv, path = fake_server_command(script)
        client = LspClient(argv, language="python", root_dir="/tmp")
        try:
            client.start()
            first = client.request("textDocument/hover", {})
            second = client.request("textDocument/definition", {})
            assert first["contents"].startswith("hover-info")
            assert second[0]["uri"] == "file:///a.py"
        finally:
            client.shutdown()

    def test_error_response_raises_lsp_error(self):
        script = standard_script()
        script["errors"] = {
            "textDocument/hover": {"code": -32602, "message": "bad position"}
        }
        argv, path = fake_server_command(script)
        client = LspClient(argv, language="python", root_dir="/tmp")
        try:
            client.start()
            with pytest.raises(LspError) as excinfo:
                client.request("textDocument/hover", {})
            assert excinfo.value.code == -32602
            assert "bad position" in excinfo.value.message
        finally:
            client.shutdown()

    def test_timeout_raises_lsp_timeout(self):
        script = standard_script()
        script["delay_ms"] = {"textDocument/hover": 2000}
        argv, path = fake_server_command(script)
        client = LspClient(argv, language="python", root_dir="/tmp", request_timeout=0.3)
        try:
            client.start()
            with pytest.raises(LspTimeout):
                client.request("textDocument/hover", {})
        finally:
            client.shutdown()

    def test_timeout_does_not_poison_next_request(self):
        script = standard_script()
        script["delay_ms"] = {"textDocument/hover": 2000}
        argv, path = fake_server_command(script)
        client = LspClient(argv, language="python", root_dir="/tmp", request_timeout=0.2)
        try:
            client.start()
            with pytest.raises(LspTimeout):
                client.request("textDocument/hover", {})
            # A late response for the timed-out request must not confuse the next.
            result = client.request("textDocument/definition", {})
            assert isinstance(result, list)
        finally:
            client.shutdown()

    def test_notification_capture(self):
        script = standard_script()
        script["notify"] = [
            {
                "method": "window/logMessage",
                "params": {"type": 3, "message": "indexing started"},
                "after_ms": 50,
            }
        ]
        received = []
        argv, path = fake_server_command(script)
        client = LspClient(
            argv,
            language="python",
            root_dir="/tmp",
            on_notification=lambda message: received.append(message),
        )
        try:
            client.start()
            assert _wait_until(lambda: len(received) >= 1)
            assert received[0]["method"] == "window/logMessage"
            assert received[0]["params"]["message"] == "indexing started"
        finally:
            client.shutdown()

    def test_server_to_client_request_is_refused(self):
        script = standard_script()
        script["client_requests"] = [
            {
                "id": 900,
                "method": "workspace/applyEdit",
                "params": {"label": "x"},
                "after_ms": 50,
            }
        ]
        argv, path = fake_server_command(script)
        client = LspClient(argv, language="python", root_dir="/tmp")
        try:
            client.start()

            def _refused_received():
                try:
                    result = client.request("__received_responses", {})
                except Exception:
                    return False
                return any(
                    resp.get("id") == 900
                    and resp.get("error", {}).get("code") == -32601
                    for resp in result
                )

            assert _wait_until(_refused_received)
        finally:
            client.shutdown()

    def test_crash_detection_fails_pending_requests(self):
        script = standard_script()
        script["crash_on"] = "boom"
        argv, path = fake_server_command(script)
        client = LspClient(argv, language="python", root_dir="/tmp")
        try:
            client.start()
            with pytest.raises(LspClientCrashed):
                client.request("textDocument/hover", {"text": "boom"})
            assert not client.is_running
        finally:
            client.shutdown()

    def test_shutdown_is_graceful(self):
        argv, path = fake_server_command(standard_script())
        client = LspClient(argv, language="python", root_dir="/tmp")
        client.start()
        pid = client.pid
        client.shutdown()
        assert not client.is_running
        # Process should be gone after graceful shutdown.
        assert _wait_until(lambda: _is_reaped(pid), timeout=3.0)

    def test_kill_terminates_process(self):
        argv, path = fake_server_command(standard_script())
        client = LspClient(argv, language="python", root_dir="/tmp")
        client.start()
        pid = client.pid
        client.kill()
        assert not client.is_running
        assert _wait_until(lambda: _is_reaped(pid), timeout=3.0)

    def test_request_after_shutdown_raises(self):
        argv, path = fake_server_command(standard_script())
        client = LspClient(argv, language="python", root_dir="/tmp")
        client.start()
        client.shutdown()
        with pytest.raises(LspClientCrashed):
            client.request("textDocument/hover", {})
