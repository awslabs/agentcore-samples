#!/usr/bin/env python3
"""
Deploy (or destroy) the three AgentCore Runtimes with the AgentCore CLI.

Runtimes, defined in agentcore/agentcore.json:
    lakehouse_mcp_server   claims MCP server   deployment/4a-mcp-lakehouse-server/
    opensearch_mcp_server  notes MCP server    deployment/4b-mcp-opensearch-server/
    lakehouse_agent        Strands agent       deployment/6-lakehouse-agent/

agentcore.json is committed with placeholders only: no account ID, no IdP
identifiers, no deployment-specific values. This script:

  1. checks the AgentCore CLI on PATH is the npm CLI (0.30.x), not the pip tool;
  2. reads the IdP flag and the values earlier steps stored in SSM Parameter Store;
  3. writes agentcore/aws-targets.json from the caller's identity (gitignored);
  4. injects the IdP authorizer, environment variables and role ARNs into
     agentcore.json, runs `agentcore deploy`, then restores the placeholders;
  5. stores the three runtime ARNs/IDs in SSM under the names the gateway steps read.

The execution roles must already exist (AgentCoreRuntimeRole-lakehouse-mcp,
-opensearch-mcp, -lakehouse-agent). The OpenSearch collection grants access to
the opensearch-mcp role by name, so the runtimes reuse those roles rather than
letting the CLI generate new ones.

Usage (run with the sample's venv Python by absolute path; do NOT activate the venv):
    .venv/bin/python agentcore/deploy_runtimes.py
    .venv/bin/python agentcore/deploy_runtimes.py --yes        # allow deploying without a Gateway ARN
    .venv/bin/python agentcore/deploy_runtimes.py --destroy --yes
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import boto3

PROJECT_DIR = Path(__file__).resolve().parent  # .../lakehouse-agent/agentcore
SAMPLE_DIR = PROJECT_DIR.parent  # CLI project root; codeLocation paths are relative to it
CONFIG_PATH = PROJECT_DIR / "agentcore.json"
TARGETS_PATH = PROJECT_DIR / "aws-targets.json"
CDK_DIR = PROJECT_DIR / "cdk"

sys.path.insert(0, str(SAMPLE_DIR))
from utils.idp_config import get_idp_provider

SSM_PREFIX = "/app/lakehouse-agent/"
REQUIRED_CLI_MAJOR_MINOR = "0.30."
TARGET_NAME = "default"
PLACEHOLDER_ACCOUNT = "000000000000"
PLACEHOLDER_DISCOVERY_URL = "https://idp.placeholder.invalid/.well-known/openid-configuration"

# runtime name -> SSM keys the downstream steps read for its ARN / ID.
#   mcp-server-runtime-arn:     5a-gateway-setup/create_gateway.py
#   opensearch-mcp-runtime-arn: 5b-obo-gateway-setup/04_create_obo_gateway.py
#   agent-runtime-arn:          streamlit-ui/streamlit_app.py, test/
RUNTIME_SSM_KEYS = {
    "lakehouse_mcp_server": ("mcp-server-runtime-arn", "mcp-server-runtime-id"),
    "opensearch_mcp_server": ("opensearch-mcp-runtime-arn", "opensearch-mcp-runtime-id"),
    "lakehouse_agent": ("agent-runtime-arn", "agent-runtime-id"),
}
AGENT_NAME_KEY = "agent-name"


def fail(message: str) -> None:
    print(f"\n❌ {message}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────
# 1. CLI preflight
# ─────────────────────────────────────────────────────────────────────────

CLI_CLASH_HELP = """
Two different tools install a command named `agentcore`:
  - the AgentCore CLI (npm package @aws/agentcore), which this script needs, and
  - the older starter toolkit (pip package bedrock-agentcore-starter-toolkit),
    which is in this sample's requirements.txt and therefore in .venv/bin.
Activating the venv puts .venv/bin first on PATH, so `agentcore` resolves to the
pip tool. Fix: run `deactivate`, then invoke this script with the venv's Python by
absolute path (e.g. .venv/bin/python agentcore/deploy_runtimes.py). Install the CLI
with: npm install -g @aws/agentcore@0.30.0
"""


def resolve_agentcore_cli() -> str:
    cli = shutil.which("agentcore")
    if not cli:
        fail("`agentcore` is not on PATH." + CLI_CLASH_HELP)
    real = Path(cli).resolve()
    in_venv = any((parent / "pyvenv.cfg").exists() for parent in (Path(cli).parent.parent, real.parent.parent))
    if in_venv or os.environ.get("VIRTUAL_ENV"):
        fail(f"`agentcore` resolves to {cli}, inside a Python virtual environment." + CLI_CLASH_HELP)
    result = subprocess.run([cli, "--version"], capture_output=True, text=True, check=False)
    version = (result.stdout or "").strip().splitlines()[0] if result.stdout.strip() else ""
    if result.returncode != 0 or not version.startswith(REQUIRED_CLI_MAJOR_MINOR):
        fail(
            f"`{cli} --version` returned {version or result.stderr.strip()!r}; "
            f"this sample is pinned to AgentCore CLI {REQUIRED_CLI_MAJOR_MINOR}x." + CLI_CLASH_HELP
        )
    print(f"✅ AgentCore CLI {version} at {cli}")
    return cli


# ─────────────────────────────────────────────────────────────────────────
# 2. Configuration from SSM
# ─────────────────────────────────────────────────────────────────────────


class DeployConfig:
    """Values the toolkit deploy scripts used to read, read from the same SSM keys."""

    def __init__(self):
        session = boto3.Session()
        self.region = session.region_name
        if not self.region:
            fail("No AWS region configured (set AWS_DEFAULT_REGION or a profile region).")
        self.ssm = session.client("ssm", region_name=self.region)
        self.account_id = session.client("sts", region_name=self.region).get_caller_identity()["Account"]
        self.idp_provider = get_idp_provider(self.ssm)

        # claims MCP server inputs
        self.s3_bucket_name = self.get("s3-bucket-name")
        self.database_name = self.get("database-name")
        self.catalog_name = self.get("catalog-name", required=False)
        # notes MCP server input
        self.opensearch_endpoint = self.get("opensearch-collection-endpoint")
        # agent input
        self.gateway_arn = self.get("gateway-arn", required=False)

        if self.idp_provider == "cognito":
            pool_id = self.get("cognito-user-pool-arn").split("/")[-1]
            self.discovery_url = (
                f"https://cognito-idp.{self.region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
            )
            # MCP runtimes are called by the gateways with the M2M token; the agent is
            # called by the end user, so it validates the user app client instead.
            self.mcp_jwt = {"allowedClients": [self.get("cognito-m2m-client-id")]}
            self.agent_jwt = {"allowedClients": [self.get("cognito-app-client-id")]}
        else:  # okta: tokens carry `aud`, so validate by resource-server audience
            self.discovery_url = self.get("okta-discovery-url")
            audience = {"allowedAudience": [self.get("okta-resource-server-audience")]}
            self.mcp_jwt = dict(audience)
            self.agent_jwt = dict(audience)

    def get(self, name: str, required: bool = True) -> str | None:
        try:
            return self.ssm.get_parameter(Name=f"{SSM_PREFIX}{name}")["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            if required:
                fail(f"SSM parameter {SSM_PREFIX}{name} not found. Run the earlier notebooks first.")
            return None


# ─────────────────────────────────────────────────────────────────────────
# 3-4. Inject, deploy, restore
# ─────────────────────────────────────────────────────────────────────────


def assert_placeholder_state(spec: dict) -> None:
    """Refuse to run on an agentcore.json that already holds deployment values."""
    for rt in spec["runtimes"]:
        jwt = rt["authorizerConfiguration"]["customJwtAuthorizer"]
        problems = []
        if jwt.get("discoveryUrl") != PLACEHOLDER_DISCOVERY_URL:
            problems.append("discoveryUrl")
        if f"::{PLACEHOLDER_ACCOUNT}:" not in rt.get("executionRoleArn", ""):
            problems.append("executionRoleArn")
        if problems:
            fail(
                f"agentcore.json runtime {rt['name']!r} is not in its committed placeholder state "
                f"({', '.join(problems)}). Restore it with `git checkout -- agentcore/agentcore.json`."
            )


def env_values(cfg: DeployConfig) -> dict[str, str | None]:
    """Placeholder -> value. A value of None drops that env var (optional SSM key absent)."""
    return {
        "__AWS_REGION__": cfg.region,
        "__S3_BUCKET_NAME__": cfg.s3_bucket_name,
        "__ATHENA_DATABASE_NAME__": cfg.database_name,
        "__CATALOG_NAME__": cfg.catalog_name,
        "__OPENSEARCH_COLLECTION_ENDPOINT__": cfg.opensearch_endpoint,
        "__IDP_PROVIDER__": cfg.idp_provider,
        "__GATEWAY_ARN__": cfg.gateway_arn,
    }


def inject(spec: dict, cfg: DeployConfig) -> dict:
    values = env_values(cfg)
    for rt in spec["runtimes"]:
        jwt = cfg.agent_jwt if rt["name"] == "lakehouse_agent" else cfg.mcp_jwt
        rt["authorizerConfiguration"] = {"customJwtAuthorizer": {"discoveryUrl": cfg.discovery_url, **jwt}}
        rt["executionRoleArn"] = rt["executionRoleArn"].replace(f"::{PLACEHOLDER_ACCOUNT}:", f"::{cfg.account_id}:")
        env = []
        for var in rt.get("envVars", []):
            value = values.get(var["value"], var["value"])
            if value is None:
                print(f"   ℹ️  {rt['name']}: {var['name']} not set (optional SSM key absent)")
                continue
            env.append({"name": var["name"], "value": value})
        rt["envVars"] = env
        leftover = [v["name"] for v in env if re.fullmatch(r"__[A-Z_]+__", v["value"])]
        if leftover:
            fail(f"Unresolved placeholders in {rt['name']}: {leftover}")
    return spec


def check_roles_exist(spec: dict, cfg: DeployConfig) -> None:
    iam = boto3.client("iam", region_name=cfg.region)
    missing = []
    for rt in spec["runtimes"]:
        role_name = rt["executionRoleArn"].split("/")[-1]
        try:
            iam.get_role(RoleName=role_name)
        except iam.exceptions.NoSuchEntityException:
            missing.append(role_name)
    if missing:
        fail(f"Execution role(s) not found: {missing}. Create them before deploying the runtimes.")


def write_targets(cfg: DeployConfig) -> None:
    targets = [{"name": TARGET_NAME, "account": cfg.account_id, "region": cfg.region}]
    TARGETS_PATH.write_text(json.dumps(targets, indent=2) + "\n")
    print(f"✅ Wrote {TARGETS_PATH.name} for region {cfg.region} (gitignored)")


def run_cli(cli: str, *args: str) -> None:
    print(f"\n▶ agentcore {' '.join(args)}")
    result = subprocess.run([cli, *args], cwd=SAMPLE_DIR, check=False)
    if result.returncode != 0:
        fail(f"`agentcore {' '.join(args)}` exited {result.returncode}")


def stack_name() -> str:
    # Matches the vended cdk/bin/cdk.ts toStackName(): underscores become hyphens.
    project = json.loads(CONFIG_PATH.read_text())["name"]
    return f"AgentCore-{project.replace('_', '-')}-{TARGET_NAME.replace('_', '-')}"


def read_runtime_outputs(region: str) -> dict[str, dict[str, str]]:
    """Runtime ARN/ID per runtime, from the stack outputs the CDK construct emits.

    Output descriptions are fixed by the construct ("Runtime ARN for agent: <name>"),
    so they are matched instead of the hashed output logical IDs.
    """
    cfn = boto3.client("cloudformation", region_name=region)
    outputs = cfn.describe_stacks(StackName=stack_name())["Stacks"][0].get("Outputs", [])
    found: dict[str, dict[str, str]] = {name: {} for name in RUNTIME_SSM_KEYS}
    for out in outputs:
        match = re.fullmatch(r"Runtime (ARN|ID) for agent: (\w+)", out.get("Description", ""))
        if match and match.group(2) in found:
            found[match.group(2)][match.group(1).lower()] = out["OutputValue"]
    incomplete = [name for name, vals in found.items() if set(vals) != {"arn", "id"}]
    if incomplete:
        fail(f"Stack outputs missing runtime ARN/ID for: {incomplete}")
    return found


def store_runtime_parameters(cfg: DeployConfig, runtimes: dict[str, dict[str, str]]) -> None:
    print("\n💾 Storing runtime ARNs/IDs in SSM Parameter Store...")
    puts = []
    for name, (arn_key, id_key) in RUNTIME_SSM_KEYS.items():
        puts.append((arn_key, runtimes[name]["arn"]))
        puts.append((id_key, runtimes[name]["id"]))
    project = json.loads(CONFIG_PATH.read_text())["name"]
    puts.append((AGENT_NAME_KEY, f"{project}_lakehouse_agent"))  # deployed runtime name
    for key, value in puts:
        cfg.ssm.put_parameter(Name=f"{SSM_PREFIX}{key}", Value=value, Type="String", Overwrite=True)
        print(f"   ✅ {SSM_PREFIX}{key} = {value}")


def deploy(cli: str, assume_yes: bool) -> None:
    cfg = DeployConfig()
    print(f"✅ IdP: {cfg.idp_provider} · region: {cfg.region}")
    if not cfg.gateway_arn and not assume_yes:
        fail(
            f"{SSM_PREFIX}gateway-arn is not set, so the agent would deploy with no claims gateway. "
            "Deploy the claims gateway first, or re-run with --yes to deploy without it on purpose."
        )

    original = CONFIG_PATH.read_bytes()
    spec = json.loads(original)
    assert_placeholder_state(spec)
    spec = inject(spec, cfg)
    check_roles_exist(spec, cfg)
    write_targets(cfg)

    if not (CDK_DIR / "node_modules").is_dir():
        print("\n▶ npm ci (installing the pinned CDK dependencies)")
        subprocess.run(["npm", "ci", "--no-fund", "--no-audit"], cwd=CDK_DIR, check=True)

    try:
        CONFIG_PATH.write_text(json.dumps(spec, indent=2) + "\n")
        run_cli(cli, "validate")
        run_cli(cli, "deploy", "--yes", "--verbose")
    finally:
        CONFIG_PATH.write_bytes(original)  # never leave deployment values in a tracked file
        print(f"\n↩️  Restored placeholders in {CONFIG_PATH.name}")

    store_runtime_parameters(cfg, read_runtime_outputs(cfg.region))
    print("\n✅ Runtimes deployed. Next: create the gateways (notebooks 05a, 05b).")


# ─────────────────────────────────────────────────────────────────────────
# 6. Destroy
# ─────────────────────────────────────────────────────────────────────────


def destroy(assume_yes: bool) -> None:
    session = boto3.Session()
    region = session.region_name
    if not region:
        fail("No AWS region configured.")
    name = stack_name()
    if not assume_yes:
        if not sys.stdin.isatty():
            fail("Refusing to prompt on a non-terminal stdin; re-run with --destroy --yes.")
        if input(f"Delete stack {name} in {region} and its runtime SSM keys? (yes/no): ").strip().lower() != "yes":
            print("Cancelled.")
            return

    cfn = session.client("cloudformation", region_name=region)
    print(f"🗑️  Deleting stack {name} ...")
    cfn.delete_stack(StackName=name)
    cfn.get_waiter("stack_delete_complete").wait(StackName=name, WaiterConfig={"Delay": 15, "MaxAttempts": 80})
    print(f"   ✅ Stack {name} deleted")

    ssm = session.client("ssm", region_name=region)
    keys = [k for pair in RUNTIME_SSM_KEYS.values() for k in pair] + [AGENT_NAME_KEY]
    for key in keys:
        try:
            ssm.delete_parameter(Name=f"{SSM_PREFIX}{key}")
            print(f"   ✅ Deleted {SSM_PREFIX}{key}")
        except ssm.exceptions.ParameterNotFound:
            print(f"   ℹ️  {SSM_PREFIX}{key} already absent")

    state = PROJECT_DIR / ".cli" / "deployed-state.json"
    if state.exists():
        state.unlink()
        print(f"   ✅ Removed local {state.relative_to(SAMPLE_DIR)}")
    print("\nℹ️  Execution roles are left in place; they belong to the role setup step.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Deploy or destroy the lakehouse AgentCore Runtimes via the AgentCore CLI"
    )
    parser.add_argument("--destroy", action="store_true", help="Delete the runtimes' CloudFormation stack and SSM keys")
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Deploy: proceed without a claims Gateway ARN. Destroy: skip the confirmation.",
    )
    args = parser.parse_args(argv)
    if args.destroy:
        destroy(args.yes)
    else:
        deploy(resolve_agentcore_cli(), args.yes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
