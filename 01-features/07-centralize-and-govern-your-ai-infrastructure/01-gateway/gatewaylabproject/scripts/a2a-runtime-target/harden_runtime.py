"""Harden the A2A runtime so only the gateway's workloads can invoke it.

Adds `allowedWorkloadConfiguration` to the runtime's CUSTOM_JWT inbound
authorizer, restricting which workloads in the request's identity chain may
invoke the runtime. With `hostingEnvironments=[{arn: <gateway-arn>}]`, only
calls that flowed through that specific AgentCore Gateway are accepted; a direct
invocation of the runtime URL (no gateway in the chain) is rejected.

The AgentCore CLI does not expose this field, so it is applied via a post-create
`update_agent_runtime`. `update_agent_runtime` has several required fields, so
this script first calls `get_agent_runtime` and round-trips every returned
config field into the update, then merges the new authorizer configuration so no
existing setting is lost.

    uv run python scripts/a2a-runtime-target/harden_runtime.py \
           --runtime-id "$RUNTIME_ID"

Requires GATEWAY_ID in environment or .env (run deploy_gateway.py first), or
pass --gateway-arn directly.
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

# Fields returned by get_agent_runtime that update_agent_runtime also accepts.
# We echo back every one that is present so the update preserves the runtime's
# existing configuration (only the authorizer is changed below).
ROUND_TRIP_FIELDS = [
    "agentRuntimeArtifact",  # required
    "roleArn",  # required
    "networkConfiguration",  # required
    "protocolConfiguration",
    "lifecycleConfiguration",
    "environmentVariables",
    "requestHeaderConfiguration",
    "metadataConfiguration",
    "filesystemConfigurations",
    "description",
]

# Keys carried in the existing customJWTAuthorizer that we preserve verbatim.
JWT_PRESERVE_FIELDS = [
    "discoveryUrl",
    "allowedAudience",
    "allowedClients",
    "allowedScopes",
    "customClaims",
]


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def build_update_kwargs(runtime_id, current, authorizer_config):
    """Build update_agent_runtime kwargs from a get_agent_runtime response.

    Pure function (no AWS calls) so it can be unit-checked against a sample
    response shape. Echoes back the round-trip fields and swaps in the new
    authorizer configuration.
    """
    kwargs = {"agentRuntimeId": runtime_id}
    for field in ROUND_TRIP_FIELDS:
        if field in current and current[field] is not None:
            kwargs[field] = current[field]
    kwargs["authorizerConfiguration"] = authorizer_config
    return kwargs


def build_authorizer_config(current, gateway_arn, workload_identities):
    """Merge allowedWorkloadConfiguration into the existing JWT authorizer."""
    existing = current.get("authorizerConfiguration", {}).get("customJWTAuthorizer", {})
    jwt_config = {k: existing[k] for k in JWT_PRESERVE_FIELDS if k in existing}
    if "discoveryUrl" not in jwt_config:
        raise SystemExit(
            "ERROR: runtime has no customJWTAuthorizer.discoveryUrl; "
            "allowedWorkloadConfiguration only applies to CUSTOM_JWT runtimes."
        )

    workload_config = {"hostingEnvironments": [{"arn": gateway_arn}]}
    if workload_identities:
        workload_config["workloadIdentities"] = workload_identities
    jwt_config["allowedWorkloadConfiguration"] = workload_config

    return {"customJWTAuthorizer": jwt_config}


def main():
    parser = argparse.ArgumentParser(
        description="Restrict an A2A runtime to the gateway's workloads"
    )
    parser.add_argument(
        "--runtime-id",
        help="AgentCore Runtime id (defaults to parsing --runtime-arn)",
    )
    parser.add_argument(
        "--runtime-arn",
        help="AgentCore Runtime ARN (the id is its last segment)",
    )
    parser.add_argument(
        "--gateway-id",
        help="Gateway id to resolve the ARN from (defaults to GATEWAY_ID in .env)",
    )
    parser.add_argument(
        "--gateway-arn",
        help="Gateway ARN to allow (overrides --gateway-id lookup)",
    )
    parser.add_argument(
        "--workload-identity",
        action="append",
        default=[],
        help="Advanced: workload identity name to allow (repeatable)",
    )
    args = parser.parse_args()

    load_env()

    runtime_id = args.runtime_id
    if not runtime_id and args.runtime_arn:
        runtime_id = args.runtime_arn.rstrip("/").split("/")[-1]
    if not runtime_id:
        print("ERROR: pass --runtime-id or --runtime-arn")
        sys.exit(1)

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    gateway_arn = args.gateway_arn
    if not gateway_arn:
        gateway_id = args.gateway_id or os.environ.get("GATEWAY_ID")
        if not gateway_id:
            print(
                "ERROR: no --gateway-arn and no GATEWAY_ID in .env. "
                "Run deploy_gateway.py first or pass --gateway-arn."
            )
            sys.exit(1)
        gateway_arn = control.get_gateway(gatewayIdentifier=gateway_id)["gatewayArn"]

    print(f"--- Hardening runtime '{runtime_id}' ---")
    print(f"  Allowed gateway ARN: {gateway_arn}")

    current = control.get_agent_runtime(agentRuntimeId=runtime_id)
    authorizer_config = build_authorizer_config(
        current, gateway_arn, args.workload_identity
    )
    kwargs = build_update_kwargs(runtime_id, current, authorizer_config)

    control.update_agent_runtime(**kwargs)
    print("  update_agent_runtime submitted; waiting for READY...")

    while True:
        time.sleep(10)
        status = control.get_agent_runtime(agentRuntimeId=runtime_id)["status"]
        print(f"    Status: {status}")
        if status in ["READY", "UPDATE_FAILED", "CREATE_FAILED"]:
            break

    print("\n  Applied authorizer configuration:")
    print(
        f"    {authorizer_config['customJWTAuthorizer']['allowedWorkloadConfiguration']}"
    )
    print(
        "\n  Only calls through the allowed gateway are now accepted. "
        "Direct invocation of the runtime URL is rejected."
    )


if __name__ == "__main__":
    main()
