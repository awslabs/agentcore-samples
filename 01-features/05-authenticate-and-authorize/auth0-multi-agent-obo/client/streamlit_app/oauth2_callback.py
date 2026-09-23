# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
OAuth2 callback server using FastAPI.
Handles the redirect from Auth0 after user authorization.
"""

import asyncio
import threading
import time
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from auth0_handler import Auth0Handler

try:
    from shared.config.settings import settings

    STREAMLIT_PORT = settings.app.streamlit_port
except ImportError:
    import os

    STREAMLIT_PORT = int(os.getenv("STREAMLIT_PORT", "8501"))


class OAuth2CallbackServer:
    """FastAPI server to handle OAuth2 callbacks."""

    def __init__(self, host: str = "localhost", port: int = 9090):
        """
        Initialize OAuth2 callback server.

        Args:
            host: Server host
            port: Server port
        """
        self.host = host
        self.port = port
        self.app = FastAPI()
        self.auth_handler = Auth0Handler()

        # Callback state
        self.code: Optional[str] = None
        self.state: Optional[str] = None
        self.error: Optional[str] = None
        self.error_description: Optional[str] = None
        self.callback_received = False

        # Server control
        self.server: Optional[uvicorn.Server] = None
        self.server_thread: Optional[threading.Thread] = None

        self._setup_routes()

    def _setup_routes(self):
        """Setup FastAPI routes."""
        self.app.add_api_route("/callback", self._handle_callback, methods=["GET"])
        self.app.add_api_route("/health", self._handle_health, methods=["GET"])

    async def _handle_callback(self, request: Request):
        """Handle OAuth2 callback from Auth0."""
        # Extract query parameters
        self.code = request.query_params.get("code")
        self.state = request.query_params.get("state")
        self.error = request.query_params.get("error")
        self.error_description = request.query_params.get("error_description")

        self.callback_received = True

        # Return success page
        if self.error:
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Authentication Error</title>
                <style>
                    body {{
                        font-family: Arial, sans-serif;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        margin: 0;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    }}
                    .container {{
                        background: white;
                        padding: 40px;
                        border-radius: 10px;
                        box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                        text-align: center;
                        max-width: 500px;
                    }}
                    h1 {{
                        color: #e53e3e;
                        margin-bottom: 20px;
                    }}
                    p {{
                        color: #666;
                        line-height: 1.6;
                    }}
                    .error-code {{
                        background: #fee;
                        padding: 10px;
                        border-radius: 5px;
                        margin: 20px 0;
                        font-family: monospace;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>Authentication Error</h1>
                    <p>An error occurred during authentication:</p>
                    <div class="error-code">{self.error}: {self.error_description}</div>
                    <p>You can close this window and try again.</p>
                </div>
            </body>
            </html>
            """
        else:
            # Build redirect URL with auth code
            from urllib.parse import urlencode

            redirect_params = urlencode({"code": self.code, "state": self.state})
            redirect_url = f"http://localhost:{STREAMLIT_PORT}?{redirect_params}"

            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Authentication Successful</title>
                <style>
                    body {{
                        font-family: Arial, sans-serif;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        margin: 0;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    }}
                    .container {{
                        background: white;
                        padding: 40px;
                        border-radius: 10px;
                        box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                        text-align: center;
                    }}
                    .checkmark {{
                        width: 80px;
                        height: 80px;
                        border-radius: 50%;
                        display: block;
                        stroke-width: 2;
                        stroke: #48bb78;
                        stroke-miterlimit: 10;
                        margin: 10px auto;
                        box-shadow: inset 0px 0px 0px #48bb78;
                        animation: fill .4s ease-in-out .4s forwards, scale .3s ease-in-out .9s both;
                    }}
                    .checkmark-circle {{
                        stroke-dasharray: 166;
                        stroke-dashoffset: 166;
                        stroke-width: 2;
                        stroke-miterlimit: 10;
                        stroke: #48bb78;
                        fill: none;
                        animation: stroke 0.6s cubic-bezier(0.65, 0, 0.45, 1) forwards;
                    }}
                    .checkmark-check {{
                        transform-origin: 50% 50%;
                        stroke-dasharray: 48;
                        stroke-dashoffset: 48;
                        animation: stroke 0.3s cubic-bezier(0.65, 0, 0.45, 1) 0.8s forwards;
                    }}
                    @keyframes stroke {{
                        100% {{
                            stroke-dashoffset: 0;
                        }}
                    }}
                    @keyframes scale {{
                        0%, 100% {{
                            transform: none;
                        }}
                        50% {{
                            transform: scale3d(1.1, 1.1, 1);
                        }}
                    }}
                    @keyframes fill {{
                        100% {{
                            box-shadow: inset 0px 0px 0px 30px #48bb78;
                        }}
                    }}
                    h1 {{
                        color: #2d3748;
                        margin-top: 20px;
                    }}
                    p {{
                        color: #718096;
                        margin-top: 10px;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <svg class="checkmark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52">
                        <circle class="checkmark-circle" cx="26" cy="26" r="25" fill="none"/>
                        <path class="checkmark-check" fill="none" d="M14.1 27.2l7.1 7.2 16.7-16.8"/>
                    </svg>
                    <h1>Authentication Successful!</h1>
                    <p>Redirecting to the application...</p>
                </div>
                <script>
                    // Redirect to main app with auth code after 1.5 seconds
                    setTimeout(function() {{
                        window.location.href = '{redirect_url}';
                    }}, 1500);
                </script>
            </body>
            </html>
            """

        return HTMLResponse(content=html_content)

    async def _handle_health(self):
        """Health check endpoint."""
        return {"status": "ok"}

    def start(self):
        """Start the callback server in a background thread."""
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="error")
        self.server = uvicorn.Server(config)

        def run_server():
            asyncio.run(self.server.serve())

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

        # Wait for server to start
        time.sleep(1)  # nosemgrep: arbitrary-sleep — wait for uvicorn to bind

    def stop(self):
        """Stop the callback server."""
        if self.server:
            self.server.should_exit = True
            if self.server_thread:
                self.server_thread.join(timeout=5)

    def wait_for_callback(self, timeout: int = 300) -> bool:
        """
        Wait for OAuth callback to be received.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if callback received, False if timeout
        """
        start_time = time.time()
        while not self.callback_received:
            if time.time() - start_time > timeout:
                return False
            time.sleep(0.5)  # nosemgrep: arbitrary-sleep — polling interval
        return True

    def reset(self):
        """Reset callback state for next authentication."""
        self.code = None
        self.state = None
        self.error = None
        self.error_description = None
        self.callback_received = False

    def get_callback_data(self) -> dict:
        """
        Get callback data after callback is received.

        Returns:
            Dictionary containing callback parameters
        """
        return {
            "code": self.code,
            "state": self.state,
            "error": self.error,
            "error_description": self.error_description,
        }
