"""Single-user Cognito PKCE sign-in followed by JWT-authenticated Runtime chat."""

import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
import webbrowser
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import boto3

REDIRECT = "http://localhost:3000/callback"
SCOPE = "openid email supportAgentDemo-api/runtime:invoke supportAgentDemo-api/gateway:invoke"


def selected_stack(cfn, stack_id, region, account):
    if not re.fullmatch(
        rf"arn:aws:cloudformation:{re.escape(region)}:{re.escape(account)}:stack/supportAgentDemo-AgentCoreStack/[a-f0-9-]+",
        stack_id,
    ):
        raise ValueError("Use your recorded full AgentCore stack ID from this account/Region")
    (item,) = cfn.describe_stacks(StackName=stack_id)["Stacks"]
    if item["StackId"] != stack_id or item["StackStatus"] != "CREATE_COMPLETE":
        raise ValueError("Expected the newly created, completed stack")
    out = {entry["OutputKey"]: entry["OutputValue"] for entry in item["Outputs"]}
    domain = f"supportagentdemo-{account}-{region}.auth.{region}.amazoncognito.com"
    arn = out["RuntimeArn"]
    if out["CognitoDomain"] != domain or not re.fullmatch(
        rf"arn:aws:bedrock-agentcore:{re.escape(region)}:{account}:runtime/[A-Za-z][A-Za-z0-9_]{{0,47}}-[A-Za-z0-9]{{10}}",
        arn,
    ):
        raise ValueError("Unrecognized Cognito or Runtime destination")
    return domain, out["ClientId"], arn


def login(domain, client_id):
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    url = f"https://{domain}/oauth2/authorize?" + urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT,
            "scope": SCOPE,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
        }
    )
    codes = []

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            args = parse_qs(parsed.query)
            valid = (
                self.headers.get("Host") == "localhost:3000"
                and parsed.path == "/callback"
                and len(args.get("state", [])) == 1
                and hmac.compare_digest(args["state"][0].encode(), state.encode())
                and len(args.get("code", [])) == 1
            )
            self.send_response(200 if valid else 400)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Return to the terminal.")
            if valid:
                codes.append(args["code"][0])

        def log_message(self, fmt, *args):
            pass

    with HTTPServer(("127.0.0.1", 3000), Callback) as listener:
        listener.timeout = 1
        if not webbrowser.open(url):
            raise RuntimeError("A local browser is required")
        deadline = time.monotonic() + 180
        while not codes and time.monotonic() < deadline:
            listener.handle_request()
        if not codes:
            raise TimeoutError("No matching callback")
    conn = HTTPSConnection(domain, timeout=10)
    try:
        conn.request(
            "POST",
            "/oauth2/token",
            body=urlencode(
                {
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "redirect_uri": REDIRECT,
                    "code": codes[0],
                    "code_verifier": verifier,
                }
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        res = conn.getresponse()
        if res.status != 200:
            raise RuntimeError("Cognito token exchange refused")
        token = json.loads(res.read(128 * 1024))["access_token"]
        if not token:
            raise ValueError("Missing access token")
        return token
    finally:
        conn.close()


def invoke(region, arn, token, session, prompt):
    conn = HTTPSConnection(f"bedrock-agentcore.{region}.amazonaws.com", timeout=120)
    try:
        conn.request(
            "POST",
            f"/runtimes/{quote(arn, safe='')}/invocations?qualifier=DEFAULT",
            body=json.dumps({"prompt": prompt}).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": "Bearer " + token,
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session,
            },
        )
        res = conn.getresponse()
        if res.status != 200 or res.getheader("Content-Type", "").split(";")[0] != "text/event-stream":
            raise RuntimeError("Runtime did not return an SSE response")
        total = 0
        for line in res:
            total += len(line)
            if total > 1_000_000:
                raise ValueError("Response too large")
            if line.startswith(b"data:"):
                part = json.loads(line[5:])
                if not isinstance(part, str):
                    raise RuntimeError("Unexpected response event")
                print(part, end="", flush=True)
        print()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("account", "region", "stack-id"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    aws = boto3.Session(region_name=args.region)
    if aws.client("sts").get_caller_identity()["Account"] != args.account:
        parser.error("Wrong account")
    domain, client_id, arn = selected_stack(aws.client("cloudformation"), args.stack_id, args.region, args.account)
    token = login(domain, client_id)
    session = str(uuid.uuid4())
    while prompt := input("Support request (blank to exit): ").strip():
        invoke(args.region, arn, token, session, prompt)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- never log OAuth or remote response details.
        raise SystemExit(
            f"Request failed ({type(exc).__name__}); verify the stack, user and service permissions."
        ) from None
