import os
import threading
import unittest
from http.client import HTTPConnection
from http.server import HTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from websocket_helpers import LocalHandler, create_presigned_url

REGION = "us-west-2"
ARN = "arn:aws:bedrock-agentcore:us-west-2:111111111111:runtime/voice-1234567890"
URL = f"wss://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{ARN}/ws?qualifier=DEFAULT"


class Signer(unittest.TestCase):
    def test_presign(self):
        with patch.dict(
            os.environ,
            {
                "AWS_ACCESS_KEY_ID": "fixture",
                "AWS_SECRET_ACCESS_KEY": "fixture",
                "AWS_SESSION_TOKEN": "fixture",
                "AWS_EC2_METADATA_DISABLED": "true",
            },
            clear=True,
        ):
            signed = urlsplit(create_presigned_url(URL, REGION))
        self.assertIn("runtime%2Fvoice-", signed.path)
        query = parse_qs(signed.query)
        self.assertEqual(query["qualifier"], ["DEFAULT"])
        self.assertEqual(query["X-Amz-Security-Token"], ["fixture"])
        self.assertEqual(len(query["X-Amz-Signature"][0]), 64)
        self.assertIn("/us-west-2/bedrock-agentcore/aws4_request", query["X-Amz-Credential"][0])

    def test_reject_foreign_destination_before_signing(self):
        for url in [
            URL.replace("amazonaws.com", "example.org"),
            URL + "&other=value",
            URL.replace(":us-west-2:", ":us-east-1:"),
        ]:
            with self.assertRaises(ValueError):
                create_presigned_url(url, REGION)

    def test_loopback_host_and_browser_site(self):
        class Fixture(LocalHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

        with HTTPServer(("127.0.0.1", 0), Fixture) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for headers, expected in [
                    ({}, 200),
                    ({"Host": "other.example"}, 403),
                    ({"Sec-Fetch-Site": "cross-site"}, 403),
                ]:
                    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                    try:
                        conn.request("GET", "/", headers=headers)
                        res = conn.getresponse()
                        self.assertEqual(res.status, expected)
                        self.assertEqual(res.getheader("Cache-Control"), "no-store")
                    finally:
                        conn.close()
            finally:
                server.shutdown()
                thread.join()
