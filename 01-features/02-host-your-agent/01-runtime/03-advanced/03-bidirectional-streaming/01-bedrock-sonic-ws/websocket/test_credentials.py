"""Verify role credentials before the bidirectional signing background task starts."""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from tempfile import TemporaryDirectory
from unittest.mock import patch

from s2s_session_manager import S2sSessionManager
from smithy_aws_core.identity.chain.exceptions import IdentityChainError


class RoleCredentials(unittest.IsolatedAsyncioTestCase):
    async def test_missing_identity_fails_before_creating_stream(self):
        with (
            TemporaryDirectory() as home,
            patch.dict(os.environ, {"HOME": home, "AWS_EC2_METADATA_DISABLED": "true"}, clear=True),
        ):
            manager = S2sSessionManager("us-east-1", "fixture")
            with self.assertRaises(IdentityChainError):
                await manager.initialize_stream()
            self.assertFalse(manager.is_active)
            self.assertIsNone(manager.bedrock_client)

    async def test_real_container_provider(self):
        requests = []

        class Metadata(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                body = json.dumps(
                    {
                        "AccessKeyId": "fixture",
                        "SecretAccessKey": "fixture",
                        "Token": "fixture",
                        "Expiration": "2099-01-01T00:00:00Z",
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with (
            HTTPServer(("127.0.0.1", 0), Metadata) as server,
            TemporaryDirectory() as home,
            patch.dict(
                os.environ,
                {
                    "HOME": home,
                    "AWS_EC2_METADATA_DISABLED": "true",
                    "AWS_CONTAINER_CREDENTIALS_FULL_URI": f"http://127.0.0.1:{server.server_port}/credentials",
                },
                clear=True,
            ),
        ):
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                manager = S2sSessionManager("us-east-1", "fixture")
                await manager._initialize_client()
                await manager.bedrock_client.close()
            finally:
                server.shutdown()
                thread.join()
        self.assertIn("/credentials", requests)
