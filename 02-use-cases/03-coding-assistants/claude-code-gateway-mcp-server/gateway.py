"""Create a dedicated Cognito-authenticated Gateway and register AWS Knowledge as a target."""

import argparse
import json
import os
import re
import secrets
import sys
import time
from contextlib import contextmanager
from getpass import getpass
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError

STATE = Path("claude-gateway-receipt.json")
MCP_CONFIG = Path(".mcp.json")
KB_URL = "https://knowledge-mcp.global.api.aws"
TOKEN_VAR = "CLAUDE_GATEWAY_ACCESS_TOKEN"  # Only this reference goes into .mcp.json.


@contextmanager
def exclusive():
    lock = STATE.with_suffix(".lock")
    lock.touch(exist_ok=False)
    try:
        yield
    finally:
        lock.unlink()


def save(state):
    with NamedTemporaryFile("w", dir=STATE.parent, delete=False) as f:
        json.dump(state, f, indent=2)
    os.replace(f.name, STATE)


def wait_for(get, until_deleted=False):
    for _ in range(60):
        try:
            item = get()
        except ClientError as exc:
            if until_deleted and exc.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise
        if not until_deleted and item["status"] == "READY":
            return item
        if item["status"] in {"FAILED", "UPDATE_UNSUCCESSFUL", "SYNCHRONIZE_UNSUCCESSFUL"}:
            raise RuntimeError("Service operation failed; keep the receipt for cleanup.")
        time.sleep(5)
    raise TimeoutError("Service operation not confirmed; keep the receipt and check resource status.")


def checked_url(url, region, gateway_id):
    parsed = urlsplit(url)
    expected = f"{gateway_id}.gateway.bedrock-agentcore.{region}.amazonaws.com"
    if (
        parsed.scheme != "https"
        or parsed.netloc != expected
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Unexpected Gateway URL; refusing to configure an authorization header.")
    return url


def target_config(kind, region, account):
    """Bind outbound credentials to the operator-selected target, never to user input."""
    if kind == "knowledge":
        return {"name": "aws-knowledge", "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": KB_URL}}}}
    if kind != "dynatrace":
        raise ValueError("Unsupported target")
    tenant = os.environ["DYNATRACE_TENANT_ID"]
    provider = os.environ["DYNATRACE_PROVIDER_ARN"]
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", tenant):
        raise ValueError("Enter a Dynatrace .apps.dynatrace.com tenant label, not a URL.")
    prefix = f"arn:aws:bedrock-agentcore:{region}:{account}:token-vault/"
    if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_.-]+/oauth2credentialprovider/[A-Za-z0-9_.-]+", provider):
        raise ValueError("Use an approved Identity OAuth provider in this account and Region.")
    endpoint = f"https://{tenant}.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp"
    return {
        "name": "dynatrace-tools",
        "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": endpoint}}},
        "credentialProviderConfigurations": [
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": provider,
                        "grantType": "CLIENT_CREDENTIALS",
                        "scopes": ["mcp-gateway:servers:read", "mcp-gateway:servers:invoke"],
                    }
                },
            }
        ],
    }


def create(session, role_arn, pool, client, scope, kind="knowledge"):
    region = session.region_name
    account = session.client("sts").get_caller_identity()["Account"]
    if not re.fullmatch(r"[a-z]{2}-[a-z0-9-]+-\d+", region or "") or not re.fullmatch(
        re.escape(region) + r"_[A-Za-z0-9]+", pool
    ):
        raise ValueError("Use a Cognito pool in the selected commercial AWS Region.")
    if not role_arn.startswith(f"arn:aws:iam::{account}:role/") or not scope or len(scope.split()) != 1:
        raise ValueError("Use a Gateway service role in the selected account and one authorized scope.")
    if MCP_CONFIG.exists():
        raise FileExistsError("Use a fresh directory; .mcp.json already exists.")
    control = session.client("bedrock-agentcore-control")
    target_options = target_config(kind, region, account)
    name = f"claude-{kind}-" + secrets.token_hex(6)
    state = {"account": account, "region": region, "name": name, "pending": "gateway"}
    with STATE.open("x") as f:
        json.dump(state, f, indent=2)
    gateway = control.create_gateway(
        name=name,
        roleArn=role_arn,
        protocolType="MCP",
        protocolConfiguration={"mcp": {"supportedVersions": ["2025-03-26"], "searchType": "SEMANTIC"}},
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "allowedClients": [client],
                "allowedScopes": [scope],
                "discoveryUrl": f"https://cognito-idp.{region}.amazonaws.com/{pool}/.well-known/openid-configuration",
            }
        },
    )
    gid = gateway["gatewayId"]
    state.update(id=gid, arn=gateway["gatewayArn"])
    del state["pending"]
    save(state)
    ready = wait_for(lambda: control.get_gateway(gatewayIdentifier=gid))
    url = checked_url(ready["gatewayUrl"], region, gid)
    state["pending"] = "target"
    save(state)
    target = control.create_gateway_target(gatewayIdentifier=gid, **target_options)
    state["target_id"] = target["targetId"]
    del state["pending"]
    save(state)
    wait_for(lambda: control.get_gateway_target(gatewayIdentifier=gid, targetId=state["target_id"]))
    config = {
        "mcpServers": {name: {"type": "http", "url": url, "headers": {"Authorization": "Bearer ${" + TOKEN_VAR + "}"}}}
    }
    with MCP_CONFIG.open("x") as f:
        json.dump(config, f, indent=2)
    print(f"Gateway ready: {name}. Project configuration written to {MCP_CONFIG} (no token).")


def token(region, domain, client, scope):
    if not re.fullmatch(r"[a-z0-9-]+", domain) or not re.fullmatch(r"[a-z]{2}-[a-z0-9-]+-\d+", region):
        raise ValueError("Expected a Cognito domain prefix and AWS Region, not a URL.")
    if not sys.stdin.isatty() or sys.stdout.isatty():
        raise ValueError("Run the documented shell substitution in a private interactive terminal.")
    secret = getpass("Cognito app client secret: ")
    response = requests.post(
        f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token",
        auth=(client, secret),
        data={"grant_type": "client_credentials", "scope": scope},
        timeout=15,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Cognito refused the token request (HTTP {response.status_code}).")
    key = response.json()["access_token"]
    if not isinstance(key, str) or not key or any(c.isspace() for c in key):
        raise ValueError("Cognito returned no usable access token.")
    return key


def cleanup(state, session):
    if state.get("pending") or not state.get("id"):
        raise RuntimeError("Unconfirmed create; reconcile the recorded operation manually. Nothing deleted.")
    if session.client("sts").get_caller_identity()["Account"] != state["account"]:
        raise RuntimeError("Wrong account; nothing deleted.")
    control = session.client("bedrock-agentcore-control")
    params = {"gatewayIdentifier": state["id"]}
    try:
        current = control.get_gateway(**params)
    except control.exceptions.ResourceNotFoundException:
        STATE.unlink()
        return
    if current["gatewayArn"] != state["arn"] or current["name"] != state["name"]:
        raise RuntimeError("Gateway receipt mismatch; nothing deleted.")
    # Never delete extra targets that someone added to this Gateway.
    targets = control.list_gateway_targets(**params)
    if targets.get("nextToken") or any(t["targetId"] != state.get("target_id") for t in targets["items"]):
        raise RuntimeError("Gateway contains unrecorded targets; stopping cleanup.")
    if "target_id" in state:
        tp = dict(params, targetId=state["target_id"])
        try:
            if control.get_gateway_target(**tp)["status"] != "DELETING":
                control.delete_gateway_target(**tp)
            wait_for(lambda: control.get_gateway_target(**tp), until_deleted=True)
        except control.exceptions.ResourceNotFoundException:
            pass
        del state["target_id"]
        save(state)
    if current["status"] != "DELETING":
        control.delete_gateway(**params)
    wait_for(lambda: control.get_gateway(**params), until_deleted=True)
    STATE.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "token", "cleanup"])
    parser.add_argument(
        "--target", choices=["knowledge", "dynatrace"], default="knowledge", help="MCP server for a fresh create"
    )
    args = parser.parse_args()
    if args.action == "token":
        print(
            token(
                os.environ["AWS_REGION"],
                os.environ["COGNITO_DOMAIN"],
                os.environ["COGNITO_CLIENT_ID"],
                os.environ["COGNITO_SCOPE"],
            )
        )
        return
    with exclusive():
        if args.action == "create":
            create(
                boto3.Session(region_name=os.environ["AWS_REGION"]),
                os.environ["GATEWAY_ROLE_ARN"],
                os.environ["COGNITO_POOL_ID"],
                os.environ["COGNITO_CLIENT_ID"],
                os.environ["COGNITO_SCOPE"],
                args.target,
            )
        else:
            state = json.loads(STATE.read_text())
            cleanup(state, boto3.Session(region_name=state["region"]))
            print("Recorded Gateway removed. Review the local .mcp.json before removing it.")


if __name__ == "__main__":
    try:
        main()
    except (
        BotoCoreError,
        ClientError,
        KeyError,
        ValueError,
        RuntimeError,
        OSError,
        requests.RequestException,
    ) as error:
        # Do not print third-party error bodies, headers, credentials or callback payloads.
        raise SystemExit(
            f"Operation failed ({type(error).__name__}); check configuration and any saved receipt."
        ) from None
