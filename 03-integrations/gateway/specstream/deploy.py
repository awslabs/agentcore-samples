"""Deploy an Amazon Bedrock AgentCore Gateway for SpecStream.

Supports two modes:
  openapi         Register SpecStream /v1/web-search and /v1/extract as MCP tools
                  on an AWS_IAM gateway with an API_KEY credential provider.
  mcp-passthrough Front https://api.specstream.ai/mcp as an HTTP passthrough
                  target with JWT_PASSTHROUGH outbound auth.

Usage:
    python deploy.py
    python deploy.py --mode mcp-passthrough
    python deploy.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from config import (
    AWS_REGION,
    CREDENTIAL_PROVIDER_NAME,
    DEFAULT_MODE,
    GATEWAY_NAME,
    GATEWAY_ROLE_NAME,
    STATE_FILE,
    SUPPORTED_MODES,
    TARGET_NAME,
    build_gateway_role_policy,
    build_gateway_trust_policy,
    build_target_request,
    load_env_file,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for deployment."""
    parser = argparse.ArgumentParser(description="Deploy SpecStream integration on AgentCore Gateway")
    parser.add_argument(
        "--mode",
        choices=SUPPORTED_MODES,
        default=DEFAULT_MODE if DEFAULT_MODE in SUPPORTED_MODES else "openapi",
        help="Gateway integration mode (default: openapi)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the gateway and target configuration without calling AWS.",
    )
    return parser.parse_args(argv)


def _find_gateway(control, name: str) -> tuple[str | None, str | None]:
    """Return (gateway_id, gateway_url) if a gateway with the given name exists."""
    next_token = None
    while True:
        kwargs: dict[str, object] = {"maxResults": 50}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = control.list_gateways(**kwargs)
        for gw in resp.get("items", []):
            if gw.get("name") == name:
                detail = control.get_gateway(gatewayIdentifier=gw["gatewayId"])
                return detail["gatewayId"], detail["gatewayUrl"]
        next_token = resp.get("nextToken")
        if not next_token:
            return None, None


def _ensure_api_key_provider(control, api_key: str) -> str:
    """Create or update the SpecStream API key credential provider and return its ARN."""
    try:
        resp = control.create_api_key_credential_provider(
            name=CREDENTIAL_PROVIDER_NAME,
            apiKey=api_key,
        )
        print(f"  [OK] Created API key credential provider: {CREDENTIAL_PROVIDER_NAME}")
        return resp["credentialProviderArn"]
    except control.exceptions.ConflictException:
        resp = control.update_api_key_credential_provider(
            name=CREDENTIAL_PROVIDER_NAME,
            apiKey=api_key,
        )
        print(f"  [INFO] Updated existing API key credential provider: {CREDENTIAL_PROVIDER_NAME}")
        return resp["credentialProviderArn"]


def _ensure_gateway_role(iam, account_id: str, region: str, mode: str) -> str:
    """Create or update the Gateway IAM role and return its ARN."""
    trust_policy = build_gateway_trust_policy(account_id, region)
    role_policy = build_gateway_role_policy(mode)

    try:
        resp = iam.create_role(
            RoleName=GATEWAY_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for AgentCore SpecStream Gateway",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"  [OK] Created IAM role: {GATEWAY_ROLE_NAME}")
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(
            RoleName=GATEWAY_ROLE_NAME,
            PolicyDocument=json.dumps(trust_policy),
        )
        role_arn = iam.get_role(RoleName=GATEWAY_ROLE_NAME)["Role"]["Arn"]
        print(f"  [INFO] IAM role already exists: {GATEWAY_ROLE_NAME}")

    iam.put_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyName="SpecStreamGatewayPolicy",
        PolicyDocument=json.dumps(role_policy),
    )
    return role_arn


def _wait_for_gateway(control, gateway_id: str, timeout_s: int = 180) -> str:
    """Poll until the gateway reaches READY or ACTIVE."""
    deadline = time.time() + timeout_s
    status = "UNKNOWN"
    while time.time() < deadline:
        status = control.get_gateway(gatewayIdentifier=gateway_id).get("status", "UNKNOWN")
        if status in ("READY", "ACTIVE"):
            return status
        if "FAILED" in status:
            raise RuntimeError(f"Gateway entered {status} state")
        time.sleep(5)
    return status


def _wait_for_target(control, gateway_id: str, target_id: str, timeout_s: int = 180) -> str:
    """Poll until the target reaches READY."""
    deadline = time.time() + timeout_s
    status = "UNKNOWN"
    while time.time() < deadline:
        status = control.get_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
        ).get("status", "UNKNOWN")
        if status == "READY":
            return status
        if "FAILED" in status:
            raise RuntimeError(f"Gateway target entered {status} state")
        time.sleep(5)
    return status


def deploy(argv: list[str] | None = None) -> int:
    """Provision the SpecStream AgentCore Gateway and target."""
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    args = parse_args(argv)
    mode = args.mode

    if args.dry_run:
        placeholder_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/apikeycredentialprovider/specstream-api-key"
        target_payload = build_target_request(
            gateway_id="dry-run-gateway-id",
            mode=mode,
            credential_provider_arn=placeholder_arn if mode == "openapi" else None,
        )
        print(f"Dry run (mode={mode}):")
        print(json.dumps(target_payload, indent=2))
        return 0

    api_key = os.environ.get("SPECSTREAM_API_KEY", "").strip()
    if mode == "openapi" and (not api_key or api_key == "your-specstream-api-key"):
        print(
            "ERROR: SPECSTREAM_API_KEY is required in 'openapi' mode.\n"
            "Set SPECSTREAM_API_KEY in .env or the environment, or deploy in passthrough mode:\n"
            "  python deploy.py --mode mcp-passthrough"
        )
        return 1

    import boto3

    sts = boto3.client("sts", region_name=AWS_REGION)
    account_id = sts.get_caller_identity()["Account"]
    iam = boto3.client("iam", region_name=AWS_REGION)
    control = boto3.client("bedrock-agentcore-control", region_name=AWS_REGION)

    print(f"Region:  {AWS_REGION}")
    print(f"Account: {account_id}")
    print(f"Mode:    {mode}")

    provider_arn = None
    if mode == "openapi":
        provider_arn = _ensure_api_key_provider(control, api_key)

    role_arn = _ensure_gateway_role(iam, account_id, AWS_REGION, mode)

    gateway_name = GATEWAY_NAME if mode == "openapi" else f"{GATEWAY_NAME}-passthrough"
    gateway_id, gateway_url = _find_gateway(control, gateway_name)
    if not gateway_id:
        create_kwargs: dict[str, object] = {
            "name": gateway_name,
            "roleArn": role_arn,
            "description": f"SpecStream {mode} gateway",
        }
        if mode == "openapi":
            create_kwargs["authorizerType"] = "AWS_IAM"
            create_kwargs["protocolType"] = "MCP"
        else:
            create_kwargs["authorizerType"] = "NONE"

        gw_resp = control.create_gateway(**create_kwargs)
        gateway_id = gw_resp["gatewayId"]
        gateway_url = gw_resp["gatewayUrl"]
        print(f"  [OK] Created gateway: {gateway_name} ({gateway_id})")
    else:
        print(f"  [INFO] Reusing gateway: {gateway_name} ({gateway_id})")

    gw_status = _wait_for_gateway(control, gateway_id)
    print(f"  [OK] Gateway status: {gw_status}")

    target_req = build_target_request(
        gateway_id=gateway_id,
        mode=mode,
        credential_provider_arn=provider_arn,
        target_name=TARGET_NAME,
    )

    targets = control.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
    existing_target = next((t for t in targets if t.get("name") == TARGET_NAME), None)
    if existing_target:
        target_id = existing_target["targetId"]
        update_req = {k: v for k, v in target_req.items() if k != "gatewayIdentifier"}
        control.update_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
            **update_req,
        )
        print(f"  [INFO] Updated existing target: {TARGET_NAME} ({target_id})")
    else:
        target_resp = control.create_gateway_target(**target_req)
        target_id = target_resp["targetId"]
        print(f"  [OK] Created target: {TARGET_NAME} ({target_id})")

    target_status = _wait_for_target(control, gateway_id, target_id)
    print(f"  [OK] Target status: {target_status}")

    state = {
        "mode": mode,
        "region": AWS_REGION,
        "gateway_id": gateway_id,
        "gateway_name": gateway_name,
        "gateway_url": gateway_url,
        "target_id": target_id,
        "target_name": TARGET_NAME,
        "role_name": GATEWAY_ROLE_NAME,
        "credential_provider_name": CREDENTIAL_PROVIDER_NAME if mode == "openapi" else None,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    print(f"\nSaved deployment state to {STATE_FILE}")
    print(f"Gateway URL: {gateway_url}")
    print("Run 'python invoke.py' to query SpecStream through the gateway.")
    return 0


if __name__ == "__main__":
    sys.exit(deploy())
