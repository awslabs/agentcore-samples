"""
Minimal HTTP server that captures the authorization code from an OAuth 3LO redirect.

Usage (as library):
    from callback_server import start_callback_server
    server, code_future = start_callback_server(port=8081)
    # ... open browser to authorization URL ...
    code = code_future.result(timeout=300)  # blocks until redirect hits us
    server.shutdown()

The server binds to 127.0.0.1 only and serves a single request on /callback,
returns the code via the passed Future, and a tiny HTML page confirming success.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Tuple
from urllib.parse import parse_qs, urlparse


def start_callback_server(port: int = 8081) -> Tuple[HTTPServer, "Future[dict]"]:
    """Start the callback server on a background thread.

    Returns the server (so caller can shut it down) and a Future that resolves to
    a dict of the full query params the moment a /callback request is received.
    """
    result: "Future[dict]" = Future()

    class Handler(BaseHTTPRequestHandler):
        # Silence default request logging
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family: sans-serif; padding: 2rem;'>"
                b"<h2>OAuth callback received</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )

            if not result.done():
                if "error" in params:
                    result.set_exception(
                        RuntimeError(f"OAuth error: {params.get('error')} — {params.get('error_description', '')}")
                    )
                else:
                    result.set_result(params)

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Callback server listening on http://127.0.0.1:{port}/callback")
    return server, result
