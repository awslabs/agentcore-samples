import boto3
from urllib.parse import urlparse

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


session = boto3.Session()
REGION = session.region_name


def get_signed_headers(ws_url):
    """Get SigV4 signed headers for WebSocket connection."""
    credentials = session.get_credentials()
    https_url = ws_url.replace("wss://", "https://")
    parsed = urlparse(https_url)

    request = AWSRequest(method="GET", url=https_url, headers={"host": parsed.netloc})
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(request)
    return {k: v for k, v in request.headers.items()}


def get_url(browser_id, session_id):
    return (
        f"wss://bedrock-agentcore.{REGION}.amazonaws.com/browser-streams/{browser_id}/sessions/{session_id}/automation"
    )
