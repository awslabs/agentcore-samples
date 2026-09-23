"""
Microsoft Entra ID On-Behalf-Of (OBO) with AgentCore Runtime + MCP Server.

Demonstrates the OBO token exchange pattern: an agent on AgentCore Runtime calls a
downstream MCP server on a separate AgentCore Runtime, preserving the end-user's
identity all the way to Microsoft Graph — without forwarding the raw user JWT.

Architecture:
    1. User authenticates with Entra ID (device code flow) → gets user JWT
    2. User invokes the Agent with Authorization: Bearer <user_jwt>
       AgentCore Runtime validates JWT against the Agent app (customJWTAuthorizer)
    3. Agent calls GetResourceOauth2Token(ON_BEHALF_OF_TOKEN_EXCHANGE)
       → AgentCore Identity exchanges user JWT for Graph-scoped delegation token
    4. Agent also gets an M2M token via @requires_access_token(auth_flow='M2M')
       → used to authorize the agent→MCP transport
    5. Agent calls MCP server with:
       - Authorization: Bearer <M2M token>  (transport auth)
       - X-Amzn-Bedrock-AgentCore-Runtime-Custom-Graph-Token: <OBO token>  (user delegation)
    6. MCP server reads Graph OBO token from request context, calls Microsoft Graph
    7. Microsoft Graph returns user's profile data

Why this pattern vs alternatives:
    - M2M only: agent calls Graph as itself (no user identity preserved)
    - USER_FEDERATION (3LO): requires mid-conversation consent URL popup
    - OBO (this sample): user consents once at sign-in; delegation flows silently on each call

Usage:
    python entra_obo_mcp_runtime.py
    python entra_obo_mcp_runtime.py --cleanup

Prerequisites:
    - AWS CLI configured
    - Two Microsoft Entra ID app registrations (see ENTRA_SETUP.md)
    - pip install -r requirements.txt
    - Set environment variables:
        ENTRA_TENANT_ID           - Directory (tenant) ID
        ENTRA_AGENT_CLIENT_ID     - AgentCore-Agent app client ID
        ENTRA_AGENT_CLIENT_SECRET - AgentCore-Agent app client secret
        ENTRA_MCP_CLIENT_ID       - AgentCore-MCP Server app client ID
"""

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.parse
import uuid
import webbrowser

import boto3
import msal
import requests
from boto3.session import Session

# ── Configuration ─────────────────────────────────────────────────────────────

CREDENTIAL_PROVIDER_NAME = "entra-agent-provider"
MCP_AGENT_NAME = f"entra_obo_mcp_{int(time.time()) % 100000}"
AGENT_NAME = f"entra_obo_agent_{int(time.time()) % 100000}"
GRAPH_TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Graph-Token"
CONFIG_FILE = "obo_config.json"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name or "us-west-2"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")


# ── Helper: Create IAM Execution Role ─────────────────────────────────────────


def create_execution_role(role_name: str, extra_policies: list = None) -> str:
    """Create IAM execution role for an AgentCore Runtime."""
    iam = boto3.client("iam", region_name=REGION)

    trust_policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )

    try:
        role = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust_policy)
        role_arn = role["Role"]["Arn"]
        print(f"  Created IAM role: {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"  Reusing IAM role: {role_name}")

    statements = [
        {
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": [
                f"arn:aws:bedrock:{REGION}::foundation-model/*",
                f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:inference-profile/*",
                "arn:aws:bedrock:*::foundation-model/*",
            ],
        },
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            "Resource": "arn:aws:logs:*:*:*",
        },
        {
            "Effect": "Allow",
            "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": ["ecr:GetAuthorizationToken"],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchCheckLayerAvailability",
            ],
            "Resource": f"arn:aws:ecr:{REGION}:{ACCOUNT_ID}:repository/agentcore-obo-*",
        },
    ]
    if extra_policies:
        statements.extend(extra_policies)

    policy = json.dumps({"Version": "2012-10-17", "Statement": statements})
    try:
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="agentcore-obo-policy",
            PolicyDocument=policy,
        )
    except Exception:
        pass

    # Wait for IAM role propagation across services.
    time.sleep(15)
    return role_arn


# ── Helper: Persist partial deployment state ─────────────────────────────────


def _save_partial_config(updates: dict) -> None:
    """Merge `updates` into CONFIG_FILE."""
    try:
        with open(CONFIG_FILE) as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing.update(updates)
    with open(CONFIG_FILE, "w") as f:
        json.dump(existing, f, indent=2)


def _append_runtime(name: str, runtime_id: str) -> list:
    """Return the existing `runtimes` list with this entry appended (deduped)."""
    try:
        with open(CONFIG_FILE) as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    runtimes = list(existing.get("runtimes", []))
    entry = {"name": name, "id": runtime_id}
    if entry not in runtimes:
        runtimes.append(entry)
    return runtimes


# ── Helper: Build and Push Container Image to ECR ────────────────────────────


def ensure_ecr_repository(repo_name: str) -> str:
    """Create the ECR repository if it doesn't exist; return its URI base."""
    ecr = boto3.client("ecr", region_name=REGION)
    try:
        ecr.create_repository(repositoryName=repo_name)
        print(f"  Created ECR repo: {repo_name}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass

    try:
        with open(CONFIG_FILE) as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    repos = set(existing.get("ecr_repos", []))
    repos.add(repo_name)
    _save_partial_config({"ecr_repos": sorted(repos)})

    return f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{repo_name}"


def build_and_push_image(code_dir: str, repo_name: str, image_tag: str) -> str:
    """Build a linux/arm64 image from <code_dir>/Dockerfile and push to ECR.

    Requires `docker` and `docker buildx` on PATH.
    """
    repo_uri_base = ensure_ecr_repository(repo_name)
    image_uri = f"{repo_uri_base}:{image_tag}"

    print(f"  Logging in to ECR ({REGION})...")
    auth_data = boto3.client("ecr", region_name=REGION).get_authorization_token()["authorizationData"][0]
    proxy_endpoint = auth_data["proxyEndpoint"]
    decoded = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
    _user, password = decoded.split(":", 1)
    login = subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", proxy_endpoint],
        input=password.encode(),
        capture_output=True,
        check=False,
    )
    if login.returncode != 0:
        raise RuntimeError(f"docker login failed: {login.stderr.decode().strip()}")

    src_dir = os.path.join(os.path.dirname(__file__), code_dir)
    print(f"  Building image (linux/arm64) from {code_dir}/Dockerfile...")
    result = subprocess.run(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/arm64",
            "-t",
            image_uri,
            "-f",
            os.path.join(src_dir, "Dockerfile"),
            "--push",
            src_dir,
        ],
        capture_output=False,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker buildx build failed for {code_dir}/. "
            "Ensure Docker daemon is running and 'docker buildx' is available."
        )
    print(f"  Pushed {code_dir}/ → {image_uri}")
    return image_uri


# ── Helper: Deploy Runtime ────────────────────────────────────────────────────


def deploy_runtime(
    name: str,
    role_arn: str,
    image_uri: str,
    authorizer_config: dict,
    env_vars: dict = None,
    request_header_config: dict = None,
    protocol: str = "HTTP",
) -> dict:
    """Create an AgentCore Runtime from a container image and wait for READY."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    kwargs = {
        "agentRuntimeName": name,
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image_uri}},
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "authorizerConfiguration": authorizer_config,
        "protocolConfiguration": {"serverProtocol": protocol},
    }

    if env_vars:
        kwargs["environmentVariables"] = env_vars
    if request_header_config:
        kwargs["requestHeaderConfiguration"] = request_header_config

    # Retry on IAM role propagation lag.
    response = None
    last_err = None
    for attempt in range(5):
        try:
            response = control.create_agent_runtime(**kwargs)
            break
        except control.exceptions.ValidationException as exc:
            if "Role validation failed" not in str(exc):
                raise
            last_err = exc
            wait = min(5 * (2**attempt), 30)
            print(f"  Role not yet propagated; retrying in {wait}s (attempt {attempt + 1}/5)")
            time.sleep(wait)
    if response is None:
        raise RuntimeError(f"Role validation kept failing: {last_err}")
    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    print(f"  Created runtime: {name} (ID: {runtime_id})")

    _save_partial_config({"runtimes": _append_runtime(name, runtime_id)})

    print("  Waiting for READY...")
    while True:
        s = control.get_agent_runtime(agentRuntimeId=runtime_id).get("status", "UNKNOWN")
        print(f"    Status: {s}")
        if s == "READY":
            break
        if s in ("CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"):
            raise RuntimeError(f"Runtime failed: {s}")
        time.sleep(15)

    endpoint_url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com"
        f"/runtimes/{urllib.parse.quote(runtime_arn, safe='')}/invocations"
        "?qualifier=DEFAULT"
    )
    return {"id": runtime_id, "arn": runtime_arn, "endpoint_url": endpoint_url}


# ── Step 1: Deploy MCP Server Runtime ────────────────────────────────────────


def deploy_mcp_server() -> dict:
    """Deploy mcp/mcp_server_obo.py to AgentCore Runtime with MCP protocol."""
    tenant_id = os.environ.get("ENTRA_TENANT_ID")
    mcp_client_id = os.environ.get("ENTRA_MCP_CLIENT_ID")

    if not tenant_id or not mcp_client_id:
        raise ValueError("Set ENTRA_TENANT_ID and ENTRA_MCP_CLIENT_ID.")

    role_name = f"agentcore-obo-mcp-{ACCOUNT_ID}-role"
    role_arn = create_execution_role(role_name)
    _save_partial_config({"mcp_role_name": role_name})

    image_uri = build_and_push_image(
        code_dir="mcp",
        repo_name="agentcore-obo-mcp",
        image_tag=MCP_AGENT_NAME,
    )

    mcp_authorizer = {
        "customJWTAuthorizer": {
            "discoveryUrl": (f"https://login.microsoftonline.com/{tenant_id}/.well-known/openid-configuration"),
            "allowedAudience": [f"api://{mcp_client_id}"],
            "customClaims": [
                {
                    "inboundTokenClaimName": "roles",
                    "inboundTokenClaimValueType": "STRING_ARRAY",
                    "authorizingClaimMatchValue": {
                        "claimMatchValue": {"matchValueString": "mcp_invoke"},
                        "claimMatchOperator": "CONTAINS",
                    },
                }
            ],
        }
    }

    request_header_config = {
        "requestHeaderAllowlist": [
            "Authorization",
            GRAPH_TOKEN_HEADER,
        ]
    }

    runtime_info = deploy_runtime(
        name=MCP_AGENT_NAME,
        role_arn=role_arn,
        image_uri=image_uri,
        authorizer_config=mcp_authorizer,
        request_header_config=request_header_config,
        protocol="MCP",
    )

    mcp_url = runtime_info["endpoint_url"]
    print(f"  MCP Server URL: {mcp_url}")

    return {
        "mcp_name": MCP_AGENT_NAME,
        "mcp_runtime_id": runtime_info["id"],
        "mcp_runtime_arn": runtime_info["arn"],
        "mcp_url": mcp_url,
        "mcp_role_name": role_name,
        "mcp_image_uri": image_uri,
    }


# ── Step 2: Create MicrosoftOauth2 Credential Provider ────────────────────────


def create_credential_provider() -> str:
    """Create a MicrosoftOauth2 credential provider for both M2M and OBO flows."""
    agent_client_id = os.environ.get("ENTRA_AGENT_CLIENT_ID")
    agent_client_secret = os.environ.get("ENTRA_AGENT_CLIENT_SECRET")
    tenant_id = os.environ.get("ENTRA_TENANT_ID")

    if not all([agent_client_id, agent_client_secret, tenant_id]):
        raise ValueError("Set ENTRA_AGENT_CLIENT_ID, ENTRA_AGENT_CLIENT_SECRET, ENTRA_TENANT_ID.")

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    try:
        resp = control.create_oauth2_credential_provider(
            name=CREDENTIAL_PROVIDER_NAME,
            credentialProviderVendor="MicrosoftOauth2",
            oauth2ProviderConfigInput={
                "microsoftOauth2ProviderConfig": {
                    "clientId": agent_client_id,
                    "clientSecret": agent_client_secret,
                    "tenantId": tenant_id,
                }
            },
        )
        provider_arn = resp["credentialProviderArn"]
        print(f"  Created credential provider: {CREDENTIAL_PROVIDER_NAME}")
    except control.exceptions.ConflictException:
        resp = control.get_oauth2_credential_provider(name=CREDENTIAL_PROVIDER_NAME)
        provider_arn = resp["credentialProviderArn"]
        print(f"  Reusing credential provider: {CREDENTIAL_PROVIDER_NAME}")
    except control.exceptions.ValidationException as exc:
        if "already exists" not in str(exc):
            raise
        resp = control.get_oauth2_credential_provider(name=CREDENTIAL_PROVIDER_NAME)
        provider_arn = resp["credentialProviderArn"]
        print(f"  Reusing credential provider: {CREDENTIAL_PROVIDER_NAME}")

    print(f"  Provider ARN: {provider_arn}")
    print("  This provider handles both M2M (client_credentials) and OBO flows.")
    _save_partial_config({"credential_provider_name": CREDENTIAL_PROVIDER_NAME})
    return provider_arn


# ── Step 3: Deploy Agent Runtime ──────────────────────────────────────────────


def deploy_agent(mcp_url: str) -> dict:
    """Deploy agent/agent_obo.py to AgentCore Runtime."""
    tenant_id = os.environ.get("ENTRA_TENANT_ID")
    agent_client_id = os.environ.get("ENTRA_AGENT_CLIENT_ID")
    mcp_client_id = os.environ.get("ENTRA_MCP_CLIENT_ID")

    if not all([tenant_id, agent_client_id, mcp_client_id]):
        raise ValueError("Set ENTRA_TENANT_ID, ENTRA_AGENT_CLIENT_ID, ENTRA_MCP_CLIENT_ID.")

    role_name = f"agentcore-obo-agent-{ACCOUNT_ID}-role"
    role_arn = create_execution_role(
        role_name,
        extra_policies=[
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": "arn:aws:secretsmanager:*:*:secret:bedrock-agentcore*",
            },
        ],
    )
    _save_partial_config({"agent_role_name": role_name})

    image_uri = build_and_push_image(
        code_dir="agent",
        repo_name="agentcore-obo-agent",
        image_tag=AGENT_NAME,
    )

    agent_authorizer = {
        "customJWTAuthorizer": {
            "discoveryUrl": (f"https://login.microsoftonline.com/{tenant_id}/.well-known/openid-configuration"),
            "allowedAudience": [agent_client_id],
        }
    }

    runtime_info = deploy_runtime(
        name=AGENT_NAME,
        role_arn=role_arn,
        image_uri=image_uri,
        authorizer_config=agent_authorizer,
        env_vars={
            "AWS_REGION": REGION,
            "MCP_URL": mcp_url,
            "ENTRA_MCP_CLIENT_ID": mcp_client_id,
            "CREDENTIAL_PROVIDER_NAME": CREDENTIAL_PROVIDER_NAME,
        },
    )

    print(f"  Agent URL: {runtime_info['endpoint_url']}")
    return {
        "agent_name": AGENT_NAME,
        "agent_runtime_id": runtime_info["id"],
        "agent_runtime_arn": runtime_info["arn"],
        "agent_endpoint_url": runtime_info["endpoint_url"],
        "agent_role_name": role_name,
        "agent_image_uri": image_uri,
    }


# ── Step 4: Get User JWT via MSAL Device Code Flow ─────────────────────────────


def get_user_token() -> str:
    """Acquire a user JWT from Entra ID using the device code flow."""
    tenant_id = os.environ.get("ENTRA_TENANT_ID")
    agent_client_id = os.environ.get("ENTRA_AGENT_CLIENT_ID")

    if not tenant_id or not agent_client_id:
        raise ValueError("Set ENTRA_TENANT_ID and ENTRA_AGENT_CLIENT_ID.")

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    # Use bare-GUID .default scope (required for OBO; api:// form fails with AADSTS90009)
    scopes = [f"{agent_client_id}/.default"]

    msal_app = msal.PublicClientApplication(
        client_id=agent_client_id,
        authority=authority,
    )

    # Try silent first
    result = msal_app.acquire_token_silent(scopes, account=None)

    if not result:
        flow = msal_app.initiate_device_flow(scopes=scopes)
        if "error" in flow:
            raise RuntimeError(f"Device flow error: {flow.get('error_description')}")

        print("\n" + "=" * 60)
        print(f"  Go to: {flow['verification_uri']}")
        print(f"  Enter code: {flow['user_code']}")
        print("=" * 60 + "\n")
        webbrowser.open(flow["verification_uri"])

        result = msal_app.acquire_token_by_device_flow(flow)

    if result and "access_token" in result:
        print(f"  User JWT acquired: {result['access_token'][:50]}...")
        return result["access_token"]
    else:
        raise RuntimeError(
            f"Failed to acquire user token: {result.get('error_description') if result else 'No result'}"
        )


# ── Step 5: Invoke Agent ───────────────────────────────────────────────────────


def invoke_agent(endpoint_url: str, bearer_token: str):
    """Invoke the agent with the user's JWT and observe the OBO delegation flow."""
    session_id = str(uuid.uuid4())

    prompts = [
        "Who am I and what's my email address?",
        "What is my display name in the directory?",
    ]

    for prompt in prompts:
        print(f"\n  Prompt: {prompt}")
        resp = requests.post(
            endpoint_url,
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
            },
            json={"prompt": prompt},
            timeout=120,
        )
        resp.raise_for_status()
        print(f"  Response: {resp.text[:400]}")


# ── Cleanup ────────────────────────────────────────────────────────────────────


def cleanup():
    """Delete resources tracked in CONFIG_FILE.

    The deploy script writes to CONFIG_FILE as each resource is created,
    so this function deletes only what was provisioned by this sample
    on this machine.
    """
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"No {CONFIG_FILE} found — nothing to clean up.")
        return

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    ecr = boto3.client("ecr", region_name=REGION)

    for rt in config.get("runtimes", []):
        rt_id = rt.get("id")
        rt_name = rt.get("name", rt_id)
        if not rt_id:
            continue
        try:
            control.delete_agent_runtime(agentRuntimeId=rt_id)
            print(f"  Deleted runtime: {rt_name} ✓")
        except control.exceptions.ResourceNotFoundException:
            pass
        except Exception as e:
            print(f"  Runtime {rt_name}: {e}")

    if config.get("credential_provider_name"):
        try:
            control.delete_oauth2_credential_provider(name=config["credential_provider_name"])
            print(f"  Deleted credential provider: {config['credential_provider_name']} ✓")
        except control.exceptions.ResourceNotFoundException:
            pass
        except Exception as e:
            print(f"  Provider delete: {e}")

    for role_key in ("agent_role_name", "mcp_role_name"):
        role_name = config.get(role_key)
        if not role_name:
            continue
        try:
            for p in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            iam.delete_role(RoleName=role_name)
            print(f"  Deleted IAM role: {role_name} ✓")
        except iam.exceptions.NoSuchEntityException:
            pass
        except Exception as e:
            print(f"  Role {role_name}: {e}")

    for repo_name in config.get("ecr_repos", []):
        try:
            ecr.delete_repository(repositoryName=repo_name, force=True)
            print(f"  Deleted ECR repo: {repo_name} ✓")
        except ecr.exceptions.RepositoryNotFoundException:
            pass
        except Exception as e:
            print(f"  ECR {repo_name}: {e}")

    try:
        os.remove(CONFIG_FILE)
        print(f"  Removed config file: {CONFIG_FILE} ✓")
    except FileNotFoundError:
        pass

    print("Cleanup complete.")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Entra ID OBO: AgentCore Runtime + MCP Server")
    parser.add_argument("--cleanup", action="store_true", help="Delete created resources")
    parser.add_argument(
        "--test-only",
        action="store_true",
        help="Only invoke agent using existing config",
    )
    args = parser.parse_args()

    if args.cleanup:
        print("\n=== Cleaning Up ===")
        cleanup()
        return

    if args.test_only:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        bearer_token = get_user_token()
        invoke_agent(config["agent_endpoint_url"], bearer_token)
        return

    print("=== Entra ID OBO: AgentCore Runtime + MCP Server ===\n")

    print("=== Step 1: Deploying MCP Server to AgentCore Runtime ===")
    mcp_info = deploy_mcp_server()

    print("\n=== Step 2: Creating MicrosoftOauth2 Credential Provider ===")
    provider_arn = create_credential_provider()

    print("\n=== Step 3: Deploying Agent to AgentCore Runtime ===")
    agent_info = deploy_agent(mcp_info["mcp_url"])

    print("\n=== Step 4: Getting User JWT via MSAL Device Code Flow ===")
    bearer_token = get_user_token()

    print("\n=== Step 5: Invoking Agent ===")
    invoke_agent(agent_info["agent_endpoint_url"], bearer_token)

    _save_partial_config(
        {
            **mcp_info,
            **agent_info,
            "provider_arn": provider_arn,
            "region": REGION,
        }
    )

    print("\n=== Summary ===")
    print(f"  MCP Server: {mcp_info['mcp_name']}")
    print(f"  Agent: {agent_info['agent_name']}")
    print(f"  Credential Provider: {CREDENTIAL_PROVIDER_NAME}")
    print("\n  To clean up: python entra_obo_mcp_runtime.py --cleanup")


if __name__ == "__main__":
    main()
