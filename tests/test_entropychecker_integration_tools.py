"""Integration tests for the entropy checker in the remaining tools.

Covers the /feed command (chat.py), the defensive layer (tool_base.py) and
the other file-reading tool modules: dev_tools_safer, dev_tools_safer_readonly,
example_tools, read_lines_patch_lines, read_lines_patch_lines_zero_based and
read_base64.
"""

import base64
import os
import shutil
import sys
import tempfile

import pytest

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

from tools.dev_tools_safer import read_file as safer_read_file
from tools.dev_tools_safer import grep as safer_grep
from tools.dev_tools_safer_readonly import read_file as readonly_read_file
from tools.dev_tools_safer_readonly import grep as readonly_grep
from tools.example_tools import read_file as example_read_file
from tools.read_lines_patch_lines import grep_from_file, read_lines
from tools.read_lines_patch_lines_zero_based import grep0_from_file, read_lines0
from tools.read_base64 import read_file_as_base64
from tool_base import _entropy_check_tool_result

import chat


def _write(path, data, mode="wb"):
    with open(path, mode) as f:
        f.write(data)


class TestDevToolsSaferEntropy:
    """dev_tools_safer read_file / grep entropy integration."""

    def test_read_file_valid_text(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("print('hello')\n")
            path = f.name
        try:
            result = safer_read_file(path)
            assert isinstance(result, str), "Text file should return content string"
            assert "print('hello')" in result
        finally:
            os.unlink(path)

    def test_read_file_binary_rejected(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(os.urandom(1000))
            path = f.name
        try:
            result = safer_read_file(path)
            assert isinstance(result, dict), "Binary file should return error dict"
            assert result["status"] == "error"
            assert "entropy" in result["message"][0].lower()
        finally:
            os.unlink(path)

    def test_grep_skips_binary_files(self):
        temp_dir = tempfile.mkdtemp()
        try:
            _write(os.path.join(temp_dir, "text.txt"), b"hello world\n")
            _write(os.path.join(temp_dir, "binary.bin"), b"hello\x00world\x00" * 50)
            result = safer_grep("hello", temp_dir)
            assert "text.txt:1: hello world" in result
            assert "binary.bin" in result, "Binary file is reported in skipped summary"
            assert "binary.bin:1:" not in result, "Binary file must not be a grep match"
            assert "skipped" in result.lower()
        finally:
            shutil.rmtree(temp_dir)


class TestDevToolsSaferReadonlyEntropy:
    """dev_tools_safer_readonly read_file / grep entropy integration."""

    def test_read_file_valid_text(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world\n")
            path = f.name
        try:
            result = readonly_read_file(path)
            assert result["status"] == "success"
            assert "hello world" in result["data"]
        finally:
            os.unlink(path)

    def test_read_file_binary_rejected(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(os.urandom(1000))
            path = f.name
        try:
            result = readonly_read_file(path)
            assert result["status"] == "error"
            assert "entropy" in result["message"][0].lower()
        finally:
            os.unlink(path)

    def test_grep_skips_binary_files(self):
        temp_dir = tempfile.mkdtemp()
        try:
            _write(os.path.join(temp_dir, "text.txt"), b"hello world\n")
            _write(os.path.join(temp_dir, "binary.bin"), b"hello\x00world\x00" * 50)
            result = readonly_grep("hello", temp_dir)
            assert result["status"] == "success"
            assert "text.txt:1: hello world" in result["data"]
            assert "binary.bin" in result["data"], "Binary file is reported in skipped summary"
            assert "binary.bin:1:" not in result["data"], "Binary file must not be a grep match"
            assert "skipped" in result["data"].lower()
        finally:
            shutil.rmtree(temp_dir)


class TestExampleToolsEntropy:
    """example_tools read_file entropy integration."""

    def test_read_file_valid_text(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("plain text\n")
            path = f.name
        try:
            result = example_read_file(path)
            assert isinstance(result, str)
            assert "plain text" in result
        finally:
            os.unlink(path)

    def test_read_file_binary_rejected(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(os.urandom(1000))
            path = f.name
        try:
            result = example_read_file(path)
            assert isinstance(result, dict)
            assert result["status"] == "error"
            assert "entropy" in result["message"][0].lower()
        finally:
            os.unlink(path)


class TestReadLinesPatchLinesEntropy:
    """read_lines_patch_lines entropy integration."""

    def test_grep_from_file_text_ok(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello\nworld\n")
            path = f.name
        try:
            result = grep_from_file("hello", path)
            assert "1: hello" in result
        finally:
            os.unlink(path)

    def test_grep_from_file_binary_rejected(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(os.urandom(1000))
            path = f.name
        try:
            result = grep_from_file("hello", path)
            assert isinstance(result, str)
            assert "rejected by entropy check" in result
        finally:
            os.unlink(path)

    def test_read_lines_binary_rejected(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(os.urandom(1000))
            path = f.name
        try:
            result = read_lines(path, 1, 5)
            assert "rejected by entropy check" in result
        finally:
            os.unlink(path)


class TestReadLinesPatchLinesZeroBasedEntropy:
    """read_lines_patch_lines_zero_based entropy integration."""

    def test_grep0_from_file_text_ok(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello\nworld\n")
            path = f.name
        try:
            result = grep0_from_file("hello", path)
            assert "0: hello" in result
        finally:
            os.unlink(path)

    def test_grep0_from_file_binary_rejected(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(os.urandom(1000))
            path = f.name
        try:
            result = grep0_from_file("hello", path)
            assert "rejected by entropy check" in result
        finally:
            os.unlink(path)

    def test_read_lines0_binary_rejected(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(os.urandom(1000))
            path = f.name
        try:
            result = read_lines0(path, 0, 5)
            assert "rejected by entropy check" in result
        finally:
            os.unlink(path)


class TestReadBase64Entropy:
    """read_base64 is intentionally exempt from the entropy check.

    Its purpose is to transport binary data as ASCII base64 text. Base64 output
    actually trips the unique-byte threshold (64 alphabet chars + '\\n'), so an
    entropy check on it would reject every real file. Documenting the exemption:
    binary files must remain readable via this tool.
    """

    def test_binary_file_still_readable_as_base64(self):
        raw = os.urandom(1000)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(raw)
            path = f.name
        try:
            result = read_file_as_base64(path)
            assert isinstance(result, bytes)
            assert base64.b64decode(result) == raw
        finally:
            os.unlink(path)


class TestFeedCommandEntropy:
    """chat.py /feed command entropy integration."""

    def _make_state(self):
        return chat.ChatState(client=None, model="test-model")

    def test_feed_binary_rejected(self, capsys, monkeypatch):
        called = []
        monkeypatch.setattr(chat, "run_with_tools", lambda **kw: called.append(kw))
        state = self._make_state()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(os.urandom(1000))
            path = f.name
        try:
            chat._cmd_feed(path, state)
            out = capsys.readouterr().out
            assert "rejected by entropy check" in out.lower()
            assert state.messages == [], "No message should be added on rejection"
            assert called == [], "run_with_tools should not be called on rejection"
        finally:
            os.unlink(path)

    def test_feed_text_accepted(self, capsys, monkeypatch):
        called = []
        monkeypatch.setattr(chat, "run_with_tools", lambda **kw: called.append(kw))
        state = self._make_state()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, suffix=".txt") as f:
            f.write("Hello world\n")
            path = f.name
        try:
            chat._cmd_feed(path, state)
            out = capsys.readouterr().out
            assert "Loaded" in out
            assert len(state.messages) == 1
            assert state.messages[0]["content"] == "Hello world\n"
            assert len(called) == 1
        finally:
            os.unlink(path)


class TestDefensiveLayerEntropy:
    """tool_base.py run_with_tools defensive entropy check."""

    def test_suspicious_bytes_result_truncated(self, capsys):
        data = os.urandom(5000)
        result = {"status": "success", "data": data}
        _entropy_check_tool_result(result, "test_tool")
        assert result["status"] == "success"
        assert isinstance(result["data"], bytes)
        assert b"[TRUNCATED" in result["data"]
        out, err = capsys.readouterr()
        assert "WARNING" in err

    def test_suspicious_str_result_truncated(self, capsys):
        import random
        import string

        random.seed(42)
        data = "".join(
            random.choice(string.ascii_letters + string.digits + string.punctuation)
            for _ in range(5000)
        )
        result = {"status": "success", "data": data}
        _entropy_check_tool_result(result, "test_tool")
        assert isinstance(result["data"], str)
        assert "[TRUNCATED" in result["data"]
        out, err = capsys.readouterr()
        assert "WARNING" in err

    def test_clean_result_untouched(self):
        data = "hello world\n" * 500
        result = {"status": "success", "data": data}
        _entropy_check_tool_result(result, "test_tool")
        assert result["data"] == data

    def test_error_result_untouched(self):
        result = {"status": "error", "message": ["boom"]}
        _entropy_check_tool_result(result, "test_tool")
        assert result["message"] == ["boom"]

    def test_non_dict_result_untouched(self):
        result = "some plain string"
        _entropy_check_tool_result(result, "test_tool")
        assert result == "some plain string"
