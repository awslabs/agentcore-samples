"""Local browser signer for the commercial AgentCore Runtime WebSocket endpoint."""

import os
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import boto3
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest


class LocalHandler(BaseHTTPRequestHandler):
    """Do not serve presigned URLs to rebinding hosts or cross-site browser requests."""

    def parse_request(self):
        if not super().parse_request():
            return False
        hosts = {f"{name}:{self.server.server_port}" for name in ("localhost", "127.0.0.1")}
        if self.headers.get("Host") not in hosts or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_error(403)
            return False
        return True

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def log_message(self, fmt, *args):
        # A malformed request can contain a credential in its URL. Do not log request targets.
        pass


def create_presigned_url(url, region=None, service="bedrock-agentcore", expires=300):
    region = region or os.getenv("AWS_REGION", "us-west-2")
    parts = urlsplit(url)
    host = f"bedrock-agentcore.{region}.amazonaws.com"
    arn = re.fullmatch(
        r"/runtimes/(arn:aws:bedrock-agentcore:([a-z0-9-]+):[0-9]{12}:runtime/[A-Za-z][A-Za-z0-9_]{0,47}-[A-Za-z0-9]{10})/ws",
        unquote(parts.path),
    )
    query = parse_qsl(parts.query, keep_blank_values=True)
    if (
        service != "bedrock-agentcore"
        or parts.scheme != "wss"
        or parts.netloc != host
        or parts.fragment
        or not arn
        or arn[2] != region
        or len(query) != 1
        or query[0][0] != "qualifier"
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", query[0][1])
        or not 1 <= expires <= 3600
    ):
        raise ValueError("Expected an AgentCore Runtime WebSocket ARN and qualifier in the selected Region")
    creds = boto3.Session().get_credentials()
    if creds is None:
        raise ValueError("Configure your AWS credential chain")
    https_url = f"https://{host}/runtimes/{quote(arn[1], safe='')}/ws?qualifier={query[0][1]}"
    request = AWSRequest(method="GET", url=https_url, headers={"Host": host})
    SigV4QueryAuth(creds.get_frozen_credentials(), service, region, expires=expires).add_auth(request)
    return request.url.replace("https://", "wss://", 1)
