"""Unit tests for the shared length-prefixed JSON IPC framing."""

import socket
import struct

from shared.ipc import HEADER_SIZE, recv_message, send_message


def test_round_trip_simple_message():
    """A message sent on one end is received identically on the other."""
    a, b = socket.socketpair()
    try:
        msg = {"id": "abc-123", "method": "ping", "params": {"domain": "google.com"}}
        send_message(a, msg)
        assert recv_message(b) == msg
    finally:
        a.close()
        b.close()


def test_round_trip_preserves_unicode_and_nesting():
    """Nested structures and non-ASCII content survive the round trip."""
    a, b = socket.socketpair()
    try:
        msg = {"result": {"stdout": "café ✓ 日本", "nested": [1, 2, {"x": True}]}}
        send_message(a, msg)
        assert recv_message(b) == msg
    finally:
        a.close()
        b.close()


def test_recv_returns_none_on_closed_connection():
    """recv_message returns None (not an exception) when the peer closed."""
    a, b = socket.socketpair()
    a.close()
    try:
        assert recv_message(b) is None
    finally:
        b.close()


def test_wire_format_is_big_endian_length_prefix():
    """The 4-byte header is a big-endian uint32 of the JSON payload length."""
    a, b = socket.socketpair()
    try:
        send_message(a, {"k": "v"})
        header = b.recv(HEADER_SIZE)
        (length,) = struct.unpack(">I", header)
        payload = b.recv(length)
        assert length == len(payload)
        assert payload == b'{"k": "v"}'
    finally:
        a.close()
        b.close()
