"""Unit tests for the local OAuth callback server."""

from __future__ import annotations

from urllib.request import urlopen

import pytest

from callback_server import start_callback_server


def _find_free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestCallbackServer:
    def test_captures_authorization_code(self):
        port = _find_free_port()
        server, future = start_callback_server(port)
        try:
            # Simulate the IdP redirect
            with urlopen(f"http://127.0.0.1:{port}/callback?code=abc123&state=xyz") as r:
                assert r.status == 200
                assert b"OAuth callback received" in r.read()

            params = future.result(timeout=5)
            assert params["code"] == "abc123"
            assert params["state"] == "xyz"
        finally:
            server.shutdown()

    def test_reports_oauth_error(self):
        port = _find_free_port()
        server, future = start_callback_server(port)
        try:
            urlopen(f"http://127.0.0.1:{port}/callback?error=access_denied&error_description=user+denied").read()

            with pytest.raises(RuntimeError, match="access_denied"):
                future.result(timeout=5)
        finally:
            server.shutdown()

    def test_404_on_other_paths(self):
        from urllib.error import HTTPError

        port = _find_free_port()
        server, _ = start_callback_server(port)
        try:
            with pytest.raises(HTTPError) as exc:
                urlopen(f"http://127.0.0.1:{port}/wrong-path").read()
            assert exc.value.code == 404
        finally:
            server.shutdown()
