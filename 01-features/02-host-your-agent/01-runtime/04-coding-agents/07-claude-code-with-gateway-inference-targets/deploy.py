"""
Build, push and deploy the Claude Code agent to AgentCore Runtime.

Run `python setup.py` first, then:

    python deploy.py [--region us-east-1]

Creates an ECR repository, an ARM64 image (required by AgentCore Runtime), an
AgentCore Identity OAuth2 credential provider holding the OAuth client credentials, an
IAM execution role scoped to that provider plus ECR pull and CloudWatch Logs, and the
runtime itself. Only the provider's name reaches the container as an environment
variable; the credential values do not.

Gateway configuration is read from .provision-state.json (written by setup.py). Every
created resource is appended to .runtime-state.json as it is created, and cleanup.py
walks it in reverse, so a run that fails midway is still cleanable.
"""

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

HERE = Path(__file__).parent
STATE_FILE = HERE / ".runtime-state.json"
GATEWAY_STATE = HERE / ".provision-state.json"
NAME = "claude-code-gw-agent"
TAGS = {"Project": "claude-code-gateway-inference-sample", "ManagedBy": "deploy.py"}


# ── State and helpers ─────────────────────────────────────────────────────────


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"resources": [], "region": None}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def record(state: dict[str, Any], kind: str, **ids: Any) -> None:
    # Flush every created resource to disk immediately, so a crash still leaves a
    # cleanable record for cleanup.py.
    state["resources"].append({"kind": kind, **ids})
    save_state(state)
    print(f"  recorded {kind}: {ids}")


def sh(cmd: list[str], **kw: Any) -> None:
    print("  $ " + " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def gateway_outputs() -> dict[str, Any]:
    if not GATEWAY_STATE.exists():
        sys.exit(f"{GATEWAY_STATE} not found -- run `python setup.py` first")
    out = json.loads(GATEWAY_STATE.read_text()).get("outputs")
    if not out or not out.get("inference_url") or not out.get("user_pool_id"):
        sys.exit("gateway state has no usable outputs -- re-run `python setup.py`")
    return out


def ensure_credential_provider(region: str, state: dict[str, Any], gw: dict[str, Any]) -> dict[str, str]:
    # The client secret must not travel as a runtime environment variable: those are
    # readable via GetAgentRuntime and appear in container metadata. AgentCore Identity
    # holds it instead and runs the client-credentials exchange itself, so the
    # container is given only the provider's name.
    idp = boto3.client("cognito-idp", region_name=region)
    client = idp.describe_user_pool_client(UserPoolId=gw["user_pool_id"], ClientId=gw["client_id"])
    agc = boto3.client("bedrock-agentcore-control", region_name=region)
    name = f"{NAME}-oauth"
    config = {
        "customOauth2ProviderConfig": {
            # The token endpoint is read from the discovery document, so pointing this
            # at an enterprise IdP needs no other change.
            "oauthDiscovery": {"discoveryUrl": gw["discovery_url"]},
            "clientId": gw["client_id"],
            "clientSecret": client["UserPoolClient"]["ClientSecret"],
        }
    }
    # Check for an existing provider first, so a re-run refreshes its secret instead of
    # failing on a name that is already taken.
    try:
        agc.get_oauth2_credential_provider(name=name)
        exists = True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        exists = False
    if exists:
        resp = agc.update_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )
        print(f"  reusing credential provider {name}, secret refreshed")
    else:
        resp = agc.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
            tags=TAGS,
        )
        record(state, "credential_provider", name=name)
        print(f"  created credential provider {name}")
    return {
        "name": name,
        "provider_arn": resp["credentialProviderArn"],
        "secret_arn": resp["clientSecretArn"]["secretArn"],
    }


def runtime_env(gw: dict[str, Any], provider: dict[str, str]) -> dict[str, str]:
    # Carries no credential values, only the name of the provider holding them. Shared
    # by deploy.py and update.py so the two cannot drift.
    return {
        "OAUTH_PROVIDER_NAME": provider["name"],
        "OAUTH_SCOPE": gw["scope"],
        "ANTHROPIC_BASE_URL": gw["inference_url"],
        "ANTHROPIC_MODEL": "mantle/anthropic.claude-sonnet-5",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "mantle/anthropic.claude-haiku-4-5",
    }


# ── Build and push ────────────────────────────────────────────────────────────


def build_and_push(region: str, state: dict[str, Any]) -> str:
    session = boto3.Session(region_name=region)
    ecr = session.client("ecr")
    account = session.client("sts").get_caller_identity()["Account"]
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com"
    uri = f"{registry}/{NAME}:latest"

    try:
        ecr.create_repository(repositoryName=NAME, tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])
        record(state, "ecr_repo", repository_name=NAME)
        print(f"  created ECR repo {NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "RepositoryAlreadyExistsException":
            raise
        print(f"  ECR repo {NAME} already exists")
        if not any(r["kind"] == "ecr_repo" for r in state["resources"]):
            record(state, "ecr_repo", repository_name=NAME)

    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    sh(["docker", "login", "-u", user, "--password-stdin", registry], input=password.encode())

    # AgentCore Runtime only runs ARM64. --provenance=false keeps the manifest a plain
    # image rather than an OCI index, which the runtime's image resolution prefers.
    sh(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/arm64",
            "--provenance=false",
            "-t",
            uri,
            "--push",
            str(HERE),
        ]
    )
    print(f"  pushed {uri}")
    return uri


# ── Execution role ────────────────────────────────────────────────────────────


def ensure_role(region: str, state: dict[str, Any], provider: dict[str, str]) -> str:
    # provider is the one credential provider the role may get tokens from, so the
    # AgentCore Identity grants stay scoped to it rather than being granted
    # account-wide.
    session = boto3.Session(region_name=region)
    iam = session.client("iam")
    account = session.client("sts").get_caller_identity()["Account"]
    role_name = f"{NAME}-exec-role"
    identity_dir = f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default"
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account}},
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                # GetAuthorizationToken is not resource-scopable, so it stays on "*".
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                # The image pulls are scoped to this sample's repository.
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                ],
                "Resource": f"arn:aws:ecr:{region}:{account}:repository/{NAME}",
            },
            {
                # Scoped to the AgentCore Runtime log-group namespace. The runtime id is
                # not known until after CreateAgentRuntime, so the prefix is as narrow
                # as this can be at role-creation time.
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": [
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*",
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*:*",
                ],
            },
            {
                # Fetching a token is checked against the token vault, the provider, and
                # the runtime's workload identity. That identity is created with the
                # runtime, so its id is not known yet; it is matched by the runtime name,
                # which starts every id.
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account}:token-vault/default",
                    provider["provider_arn"],
                    identity_dir,
                    f"{identity_dir}/workload-identity/{NAME.replace('-', '_')}-*",
                ],
            },
            {
                # AgentCore Identity reads the provider's client secret with this role's
                # permissions, so the role needs access to that one secret. The condition
                # allows the read only when it comes through AgentCore, so code running in
                # the container cannot fetch the secret itself.
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": provider["secret_arn"],
                "Condition": {"ForAnyValue:StringEquals": {"aws:CalledVia": ["bedrock-agentcore.amazonaws.com"]}},
            },
        ],
    }
    try:
        arn = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="AgentCore Runtime execution role for the Claude Code gateway sample",
            Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
        )["Role"]["Arn"]
        record(state, "iam_role", role_name=role_name)
        print("  created execution role; waiting 12s to propagate")
        time.sleep(12)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print("  execution role already exists")
    iam.put_role_policy(RoleName=role_name, PolicyName=f"{NAME}-exec", PolicyDocument=json.dumps(policy))
    # CreateAgentRuntime validates the ECR URI using this role immediately, and fails
    # with "Access denied while validating ECR URI" if the inline policy has not
    # propagated yet.
    print("  waiting 15s for the inline policy to propagate")
    time.sleep(15)
    return arn


# ── Deploy ────────────────────────────────────────────────────────────────────


def deploy(region: str) -> None:
    state = load_state()
    state["region"] = region
    save_state(state)
    gw = gateway_outputs()

    uri = build_and_push(region, state)
    provider = ensure_credential_provider(region, state, gw)
    role_arn = ensure_role(region, state, provider)

    agc = boto3.client("bedrock-agentcore-control", region_name=region)
    env = runtime_env(gw, provider)
    print("  creating AgentCore Runtime")
    resp = agc.create_agent_runtime(
        agentRuntimeName=NAME.replace("-", "_"),
        agentRuntimeArtifact={"containerConfiguration": {"containerUri": uri}},
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
        environmentVariables=env,
        description="Claude Code on AgentCore Runtime via AgentCore Gateway",
        tags=TAGS,
    )
    arn = resp["agentRuntimeArn"]
    record(
        state,
        "agent_runtime",
        agent_runtime_arn=arn,
        agent_runtime_id=resp.get("agentRuntimeId"),
    )

    status = None
    for _ in range(60):
        d = agc.get_agent_runtime(agentRuntimeId=resp["agentRuntimeId"])
        status = d.get("status")
        if status == "READY":
            print("  runtime READY")
            break
        if status in ("CREATE_FAILED", "FAILED"):
            print(f"  runtime {status}: {d.get('statusReason') or d}")
            break
        time.sleep(5)
    # Outputs are saved even on failure so cleanup.py can clean up the half-made stack.
    state["outputs"] = {"agent_runtime_arn": arn, "image_uri": uri}
    save_state(state)
    if status != "READY":
        sys.exit(f"deploy did not reach READY (status={status}); fix and re-run, or `python cleanup.py`")
    print("\nDeployed. Try: python invoke.py 'Reply with exactly the word: ok'")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()
    deploy(args.region)
    return 0


if __name__ == "__main__":
    sys.exit(main())
