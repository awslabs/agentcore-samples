"""
Invoke AgentCore Runtime with a JWT from a private Keycloak instance.
"""

import argparse
import json
import urllib.request
import urllib.parse


def get_keycloak_token(keycloak_url: str, client_id: str, client_secret: str,
                       realm: str = "orion") -> str:
    """Get a client_credentials JWT from Keycloak."""
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        f"{keycloak_url}/realms/{realm}/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["access_token"]


def invoke_runtime(runtime_arn: str, token: str, prompt: str, region: str = "us-east-1"):
    """Invoke AgentCore Runtime with Bearer token."""
    encoded_arn = urllib.parse.quote(runtime_arn, safe="")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

    payload = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    resp = urllib.request.urlopen(req)
    print(f"Status: {resp.status}")
    print(f"Response: {resp.read().decode()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Invoke AgentCore Runtime with Keycloak JWT")
    parser.add_argument("--keycloak-url", required=True, help="Keycloak base URL")
    parser.add_argument("--client-id", default="content-export-adapter")
    parser.add_argument("--client-secret", default="test-secret-12345")
    parser.add_argument("--runtime-id", required=True, help="AgentCore Runtime ID")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--prompt", default="Hello, what can you do?")
    args = parser.parse_args()

    account_id = input("AWS Account ID: ") if not args.runtime_id.startswith("arn:") else ""
    runtime_arn = (
        args.runtime_id if args.runtime_id.startswith("arn:")
        else f"arn:aws:bedrock-agentcore:{args.region}:{account_id}:runtime/{args.runtime_id}"
    )

    print("Getting Keycloak token...")
    token = get_keycloak_token(args.keycloak_url, args.client_id, args.client_secret)
    print(f"✅ Token obtained ({len(token)} chars)")

    print(f"\nInvoking runtime: {args.runtime_id}")
    invoke_runtime(runtime_arn, token, args.prompt, args.region)
