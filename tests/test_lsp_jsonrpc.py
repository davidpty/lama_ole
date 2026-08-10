"""Unit tests for the JSON-RPC 2.0 Content-Length framing codec."""

import os
import sys

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

from lsp.jsonrpc import JsonRpcCodec, MAX_BUFFER_SIZE


class TestEncode:
    def test_round_trip_request(self):
        codec = JsonRpcCodec()
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "textDocument/hover",
            "params": {},
        }
        data = codec.encode(message)
        assert data.startswith(b"Content-Length: ")
        assert b"\r\n\r\n" in data
        decoded = codec.feed(data)
        assert len(decoded) == 1
        assert decoded[0] == message

    def test_content_length_matches_payload(self):
        codec = JsonRpcCodec()
        data = codec.encode({"jsonrpc": "2.0", "id": 2, "method": "x"})
        header, _, body = data.partition(b"\r\n\r\n")
        length = int(header.split(b":")[1].strip())
        assert length == len(body)

    def test_unicode_payload_byte_length(self):
        codec = JsonRpcCodec()
        data = codec.encode({"jsonrpc": "2.0", "id": 3, "method": "ü", "params": {"s": "héllo"}})
        header, _, body = data.partition(b"\r\n\r\n")
        length = int(header.split(b":")[1].strip())
        # Content-Length is the byte length of the UTF-8 body.
        assert length == len(body)
        assert length > len(body.decode("utf-8"))
        assert b"\xc3\xbc" in body  # 'ü' as UTF-8


class TestFeed:
    def test_single_message(self):
        codec = JsonRpcCodec()
        data = codec.encode({"jsonrpc": "2.0", "id": 1, "method": "m", "params": {}})
        assert len(codec.feed(data)) == 1

    def test_two_messages_in_one_chunk(self):
        codec = JsonRpcCodec()
        first = codec.encode({"jsonrpc": "2.0", "id": 1, "method": "a"})
        second = codec.encode({"jsonrpc": "2.0", "id": 2, "method": "b"})
        messages = codec.feed(first + second)
        assert [m["id"] for m in messages] == [1, 2]

    def test_split_across_chunks(self):
        codec = JsonRpcCodec()
        data = codec.encode({"jsonrpc": "2.0", "id": 7, "method": "split"})
        messages = []
        for byte in data:
            messages.extend(codec.feed(bytes([byte])))
        assert len(messages) == 1
        assert messages[0]["id"] == 7

    def test_empty_feed(self):
        codec = JsonRpcCodec()
        assert codec.feed(b"") == []

    def test_header_split_across_chunks(self):
        codec = JsonRpcCodec()
        data = codec.encode({"jsonrpc": "2.0", "id": 9, "method": "m"})
        half = len(data) // 2
        assert codec.feed(data[:half]) == []
        assert len(codec.feed(data[half:])) == 1

    def test_unknown_headers_tolerated(self):
        codec = JsonRpcCodec()
        payload = b'{"jsonrpc":"2.0","id":1,"method":"m"}'
        framed = (
            b"Content-Length: "
            + str(len(payload)).encode()
            + b"\r\n"
            + b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
            + b"\r\n"
            + payload
        )
        messages = codec.feed(framed)
        assert len(messages) == 1
        assert messages[0]["id"] == 1


class TestMalformed:
    def test_missing_content_length(self):
        codec = JsonRpcCodec()
        try:
            codec.feed(b'\r\n\r\n{"jsonrpc":"2.0","id":1,"method":"m"}')
        except ValueError as exc:
            assert "Content-Length" in str(exc)
        else:
            raise AssertionError("expected ValueError for missing Content-Length")

    def test_duplicate_content_length(self):
        codec = JsonRpcCodec()
        payload = b'{"jsonrpc":"2.0","id":1,"method":"m"}'
        framed = (
            b"Content-Length: "
            + str(len(payload)).encode()
            + b"\r\nContent-Length: 4\r\n\r\n"
            + payload
        )
        try:
            codec.feed(framed)
        except ValueError as exc:
            assert "Duplicate" in str(exc)
        else:
            raise AssertionError("expected ValueError for duplicate Content-Length")

    def test_invalid_content_length_value(self):
        codec = JsonRpcCodec()
        try:
            codec.feed(b"Content-Length: abc\r\n\r\n{}")
        except ValueError as exc:
            assert "Content-Length" in str(exc)
        else:
            raise AssertionError("expected ValueError for non-numeric Content-Length")

    def test_non_object_json(self):
        codec = JsonRpcCodec()
        payload = b"[1, 2, 3]"
        framed = (
            b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload
        )
        try:
            codec.feed(framed)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for non-object JSON")

    def test_buffer_cap_exceeded(self):
        codec = JsonRpcCodec()
        # A header with no terminator keeps growing the buffer past the cap.
        chunk = b"X" * (MAX_BUFFER_SIZE + 1)
        try:
            codec.feed(chunk)
        except ValueError as exc:
            assert "exceeded" in str(exc)
        else:
            raise AssertionError("expected ValueError when the buffer cap is exceeded")


class TestReset:
    def test_reset_clears_buffer(self):
        codec = JsonRpcCodec()
        data = codec.encode({"jsonrpc": "2.0", "id": 5, "method": "m"})
        half = len(data) // 2
        codec.feed(data[:half])
        codec.reset()
        # After reset the partial frame is gone; feeding the first half again
        # still yields nothing, and completing the frame yields the message.
        assert codec.feed(data[:half]) == []
        assert len(codec.feed(data[half:])) == 1
