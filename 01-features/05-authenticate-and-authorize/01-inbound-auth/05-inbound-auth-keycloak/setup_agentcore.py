# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Create (or delete) the AgentCore Identity and Gateway resources for the private Keycloak IdP.

Creates:
1. An OAuth2 credential provider (outbound OAuth) that reaches Keycloak through a managed
   VPC Lattice private endpoint and trusts the sample's private CA (certificateConfigurations).
2. An AgentCore Gateway with CUSTOM_JWT inbound auth that validates Keycloak tokens. The gateway
   fetches Keycloak's discovery document and JWKS through the same private endpoint and CA trust.
3. A gateway target backed by the MCP Echo Lambda.

It then writes the gateway URL into the agent project (agentcore.json) so `agentcore deploy`
picks it up.

Usage:
    python setup_agentcore.py            # create
    python setup_agentcore.py --cleanup  # delete gateway, target and credential provider
"""

import argparse
import json
import pathlib
import time

import boto3
from botocore.exceptions import ClientError

CREDENTIAL_PROVIDER_NAME = "keycloak-private-idp"
GATEWAY_NAME = "KeycloakPrivateIdpGateway"
TARGET_NAME = "McpEchoTarget"
STATE_FILE = pathlib.Path("agentcore_state.json")
AGENT_CONFIG = pathlib.Path("agent/private-idp-keycloak-agent/agentcore/agentcore.json")

VPC_STACK = "PrivateIdpKeycloakVpcStack"
KEYCLOAK_STACK = "PrivateIdpKeycloakStack"
GATEWAY_INFRA_STACK = "PrivateIdpKeycloakGatewayInfraStack"

TOOLS = [
    {
        "name": "get_time",
        "description": "Get the current UTC time",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "echo",
        "description": "Echo a message back",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "Message to echo"}},
            "required": ["message"],
        },
    },
]

session = boto3.Session()
REGION = session.region_name or "us-east-1"
cfn = session.client("cloudformation", region_name=REGION)
secrets = session.client("secretsmanager", region_name=REGION)
control = session.client("bedrock-agentcore-control", region_name=REGION)


def stack_outputs(stack_name: str) -> dict:
    """Return CloudFormation outputs as a dict."""
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def private_endpoint(vpc_id: str, subnet_ids: list[str], security_group_id: str) -> dict:
    """AgentCore-managed VPC Lattice endpoint into the Keycloak VPC."""
    return {
        "managedVpcResource": {
            "vpcIdentifier": vpc_id,
            "subnetIds": subnet_ids,
            "endpointIpAddressType": "IPV4",
            "securityGroupIds": [security_group_id],
        }
    }


def certificate_configurations(ca_secret_arn: str) -> list[dict]:
    """Trust the sample's private CA. The secret holds the PEM CA certificate, not a private key."""
    return [{"secretsManager": {"secretArn": ca_secret_arn}}]


def wait_for(getter, ready: str, failed: set[str], label: str, timeout: int = 900) -> dict:
    """Poll a get-call until the resource is ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = getter()
        status = resp.get("status")
        print(f"  {label}: {status}")
        if status == ready:
            return resp
        if status in failed:
            raise RuntimeError(f"{label} failed: {resp.get('failureReason') or resp.get('statusReasons')}")
        time.sleep(15)
    raise TimeoutError(f"Timed out waiting for {label}")


def create_credential_provider(kc: dict, endpoint: dict) -> str:
    """Create the OAuth2 credential provider for the private Keycloak IdP."""
    client = json.loads(secrets.get_secret_value(SecretId=kc["ClientSecretArn"])["SecretString"])
    config = {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": kc["DiscoveryUrl"]},
            "clientId": client["clientId"],
            "clientSecret": client["clientSecret"],
            "privateEndpoint": endpoint,
            "certificateConfigurations": certificate_configurations(kc["CaCertificateSecretArn"]),
        }
    }
    print(f"Creating credential provider '{CREDENTIAL_PROVIDER_NAME}'...")
    try:
        resp = control.create_oauth2_credential_provider(
            name=CREDENTIAL_PROVIDER_NAME,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("ConflictException", "ValidationException") or (
            "already exists" not in str(e)
        ):
            raise
        # Updates must include certificateConfigurations, otherwise the CA trust is removed.
        print("  Already exists, updating (keeping certificateConfigurations)...")
        resp = control.update_oauth2_credential_provider(
            name=CREDENTIAL_PROVIDER_NAME,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )
    resp = wait_for(
        lambda: control.get_oauth2_credential_provider(name=CREDENTIAL_PROVIDER_NAME),
        "READY",
        {"CREATE_FAILED", "UPDATE_FAILED"},
        "credential provider",
    )
    return resp["credentialProviderArn"]


def find_gateway() -> dict | None:
    """Find the sample gateway by name."""
    for page in control.get_paginator("list_gateways").paginate():
        for gw in page["items"]:
            if gw["name"] == GATEWAY_NAME:
                return gw
    return None


def create_gateway(kc: dict, infra: dict, endpoint: dict) -> dict:
    """Create the gateway with CUSTOM_JWT auth against the private Keycloak IdP."""
    existing = find_gateway()
    if existing:
        print(f"Gateway '{GATEWAY_NAME}' already exists ({existing['gatewayId']}).")
        gateway_id = existing["gatewayId"]
    else:
        print(f"Creating gateway '{GATEWAY_NAME}'...")
        gateway_id = control.create_gateway(
            name=GATEWAY_NAME,
            description="Validates JWTs from a private Keycloak IdP that uses a private CA",
            roleArn=infra["GatewayRoleArn"],
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={
                "customJWTAuthorizer": {
                    "discoveryUrl": kc["DiscoveryUrl"],
                    "allowedAudience": [kc["Audience"]],
                    "privateEndpoint": endpoint,
                    "certificateConfigurations": certificate_configurations(kc["CaCertificateSecretArn"]),
                }
            },
        )["gatewayId"]
    gateway = wait_for(
        lambda: control.get_gateway(gatewayIdentifier=gateway_id),
        "READY",
        {"FAILED", "UPDATE_UNSUCCESSFUL"},
        "gateway",
    )

    targets = control.list_gateway_targets(gatewayIdentifier=gateway_id)["items"]
    if not any(t["name"] == TARGET_NAME for t in targets):
        print(f"Adding gateway target '{TARGET_NAME}'...")
        target_id = control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=TARGET_NAME,
            targetConfiguration={
                "mcp": {"lambda": {"lambdaArn": infra["McpEchoLambdaArn"], "toolSchema": {"inlinePayload": TOOLS}}}
            },
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )["targetId"]
        wait_for(
            lambda: control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id),
            "READY",
            {"FAILED", "UPDATE_UNSUCCESSFUL"},
            "gateway target",
        )
    return gateway


def configure_agent(gateway_url: str) -> None:
    """Set GATEWAY_URL on the runtime in agentcore.json."""
    spec = json.loads(AGENT_CONFIG.read_text())
    runtime = spec["runtimes"][0]
    env_vars = [v for v in runtime.get("envVars", []) if v["name"] != "GATEWAY_URL"]
    env_vars.append({"name": "GATEWAY_URL", "value": gateway_url})
    runtime["envVars"] = env_vars
    AGENT_CONFIG.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"  GATEWAY_URL written to {AGENT_CONFIG}")


def setup() -> None:
    """Create the AgentCore resources."""
    vpc = stack_outputs(VPC_STACK)
    kc = stack_outputs(KEYCLOAK_STACK)
    infra = stack_outputs(GATEWAY_INFRA_STACK)
    endpoint = private_endpoint(vpc["VpcId"], vpc["PrivateSubnetIds"].split(","), kc["EndpointSecurityGroupId"])

    provider_arn = create_credential_provider(kc, endpoint)
    gateway = create_gateway(kc, infra, endpoint)
    configure_agent(gateway["gatewayUrl"])

    state = {
        "region": REGION,
        "credentialProviderArn": provider_arn,
        "gatewayId": gateway["gatewayId"],
        "gatewayUrl": gateway["gatewayUrl"],
    }
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    print("\nDone:")
    print(json.dumps(state, indent=2))


def cleanup() -> None:
    """Delete the gateway (and targets) and the credential provider."""
    gateway = find_gateway()
    if gateway:
        gateway_id = gateway["gatewayId"]
        for target in control.list_gateway_targets(gatewayIdentifier=gateway_id)["items"]:
            print(f"Deleting gateway target {target['targetId']}...")
            control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target["targetId"])
        deadline = time.time() + 300
        while control.list_gateway_targets(gatewayIdentifier=gateway_id)["items"] and time.time() < deadline:
            time.sleep(5)
        print(f"Deleting gateway {gateway_id}...")
        control.delete_gateway(gatewayIdentifier=gateway_id)
    else:
        print(f"Gateway '{GATEWAY_NAME}' not found (skipping).")
    try:
        control.delete_oauth2_credential_provider(name=CREDENTIAL_PROVIDER_NAME)
        print(f"Deleted credential provider '{CREDENTIAL_PROVIDER_NAME}'.")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        print(f"Credential provider '{CREDENTIAL_PROVIDER_NAME}' not found (skipping).")
    STATE_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cleanup", action="store_true", help="Delete the AgentCore resources")
    args = parser.parse_args()
    cleanup() if args.cleanup else setup()
