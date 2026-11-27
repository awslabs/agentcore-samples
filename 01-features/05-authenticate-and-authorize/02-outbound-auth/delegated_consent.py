"""Request a read-only user grant with an existing AgentCore Identity provider.

This single-user terminal demo verifies consent; it never prints the grant or calls a provider API.
"""

import argparse
import getpass
import hmac
import secrets
import threading
import time
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import boto3

PORT = 9090
RETURN_URL = f"http://127.0.0.1:{PORT}/callback"
SCOPES = {"calendar": ["https://www.googleapis.com/auth/calendar.readonly"], "github": ["read:user"]}


def matches(expected, actual):
    return bool(expected and actual and hmac.compare_digest(expected.encode(), actual.encode()))


def consent(client, workload, provider, resource, inbound_jwt, visit):
    token = client.get_workload_access_token_for_jwt(workloadName=workload, userToken=inbound_jwt)[
        "workloadAccessToken"
    ]
    state = secrets.token_urlsafe(32)
    params = {
        "resourceCredentialProviderName": provider,
        "workloadIdentityToken": token,
        "oauth2Flow": "USER_FEDERATION",
        "scopes": SCOPES[resource],
        "resourceOauth2ReturnUrl": RETURN_URL,
        "customState": state,
    }
    initial = client.get_resource_oauth2_token(**params)
    if initial.get("accessToken"):
        return "Existing user grant available"
    session, url = initial.get("sessionUri"), initial.get("authorizationUrl")
    if not session or not url or urlsplit(url).scheme != "https":
        raise RuntimeError("Identity did not return an HTTPS authorization URL and session")
    # Only the response from the configured AWS client supplies the destination and expected session.
    visit(url, session, state)
    client.complete_resource_token_auth(sessionUri=session, userIdentifier={"userToken": inbound_jwt})
    for attempt in range(10):
        if client.get_resource_oauth2_token(**params, sessionUri=session).get("accessToken"):
            return "User grant available after consent"
        if attempt < 9:
            time.sleep(2)
    raise TimeoutError("Identity has not confirmed a grant")


def local_visit(url, session, state):
    completed = threading.Event()

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            target = urlsplit(self.path)
            values = parse_qs(target.query)
            # Accept exactly one echo of the opaque state. Do not rely on a global last-user token.
            states = [v for values_ in values.values() for v in values_ if matches(state, v)]
            ok = (
                self.headers.get("Host") == f"127.0.0.1:{PORT}"
                and target.path == "/callback"
                and len(states) == 1
                and len(values.get("session_id", [])) == 1
                and matches(session, values["session_id"][0])
            )
            self.send_response(200 if ok else 400)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Return to the terminal." if ok else b"Unrecognized callback.")
            if ok:
                completed.set()

        def log_message(self, fmt, *args):
            pass  # Never log the return URL, state or session.

    with HTTPServer(("127.0.0.1", PORT), Callback) as server:
        server.timeout = 1
        if not webbrowser.open(url):
            raise RuntimeError("Could not open local browser; use a desktop session")
        deadline = time.monotonic() + 180
        while not completed.is_set() and time.monotonic() < deadline:
            server.handle_request()
        if not completed.is_set():
            raise TimeoutError("No matching callback; authorization not completed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resource", choices=SCOPES)
    for field in ("workload", "provider", "region", "account"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    aws = boto3.Session(region_name=args.region)
    if aws.client("sts").get_caller_identity()["Account"] != args.account:
        parser.error("Wrong AWS account")
    jwt = getpass.getpass("User JWT from your configured inbound issuer (hidden): ").strip()
    if not jwt:
        parser.error("A user JWT is required")
    with closing(aws.client("bedrock-agentcore")) as client:
        print(consent(client, args.workload, args.provider, args.resource, jwt, local_visit))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- don't print arbitrary IdP/SDK exception bodies.
        raise SystemExit(f"Consent check failed ({type(exc).__name__}); credentials and response withheld") from None
