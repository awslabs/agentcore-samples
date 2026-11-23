"""Check the JWT configuration of a sandbox Runtime, then exercise its HTTP auth boundary."""

import argparse
import getpass
import json
import re
import uuid
from http.client import HTTPSConnection
from urllib.parse import quote

import boto3


def endpoint(client, region, account, runtime_id, tenant_id, audience):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}-[A-Za-z0-9]{10}", runtime_id):
        raise ValueError("Use a Runtime ID, not a URL")
    if not re.fullmatch(r"[a-z0-9-]+", region) or not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("Expected commercial AWS Region and account")
    tenant = str(uuid.UUID(tenant_id))
    if not audience.strip():
        raise ValueError("Expected Entra token audience")
    info = client.get_agent_runtime(agentRuntimeId=runtime_id)
    arn = f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{runtime_id}"
    cfg = info.get("authorizerConfiguration", {}).get("customJWTAuthorizer", {})
    issuers = {
        f"https://login.microsoftonline.com/{tenant}/{suffix}.well-known/openid-configuration"
        for suffix in ("", "v2.0/")
    }
    if (
        info["agentRuntimeArn"] != arn
        or info["status"] != "READY"
        or cfg.get("discoveryUrl") not in issuers
        or audience not in cfg.get("allowedAudience", [])
    ):
        raise ValueError("Runtime identity, readiness, tenant issuer or allowed audience mismatch")
    return f"bedrock-agentcore.{region}.amazonaws.com", f"/runtimes/{quote(arn, safe='')}/invocations?qualifier=DEFAULT"


def probe(host, path, get_token, connect=HTTPSConnection):
    def send(token=None):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": str(uuid.uuid4()),
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        conn = connect(host, timeout=30)
        try:
            conn.request("POST", path, body=json.dumps({"prompt": "Reply with OK"}).encode(), headers=headers)
            return conn.getresponse().status
        finally:
            conn.close()

    if send() not in (401, 403):
        raise RuntimeError("Expected denial without a bearer; did not send a token")
    token = get_token()
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+", token):
        raise ValueError("Expected a short-lived Entra access JWT")
    if send(token) != 200:
        raise RuntimeError("Authenticated invocation was not HTTP 200")
    return "Unauthenticated request denied; authenticated request returned HTTP 200"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("region", "account", "runtime-id", "tenant-id", "audience"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != args.account:
        parser.error("Wrong account")
    client = session.client("bedrock-agentcore-control")
    host, path = endpoint(client, args.region, args.account, args.runtime_id, args.tenant_id, args.audience)
    print(probe(host, path, lambda: getpass.getpass("Entra access JWT (hidden): ").strip()))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- do not expose a token or remote response on failure.
        raise SystemExit(
            f"Check failed ({type(exc).__name__}); inspect approved configuration and permissions."
        ) from None
