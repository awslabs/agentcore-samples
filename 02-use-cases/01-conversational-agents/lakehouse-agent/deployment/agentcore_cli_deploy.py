#!/usr/bin/env python3
"""
Deploy (or destroy) ONE AgentCore Runtime with the AgentCore CLI.

Each runtime is its own AgentCore CLI project, inside its source directory:

    selector         runtime                 CLI project                                   notebook
    lakehouse-mcp    lakehouse_mcp_server    deployment/4a-mcp-lakehouse-server/agentcore/  04
    opensearch-mcp   opensearch_mcp_server   deployment/4b-mcp-opensearch-server/agentcore/ 05b
    lakehouse-agent  lakehouse_agent         deployment/6-lakehouse-agent/agentcore/        06

Each project's agentcore.json is committed with placeholders only: no account ID,
no IdP identifiers, no deployment-specific values. For the selected runtime this script:

  1. finds the npm AgentCore CLI 0.30.x on PATH, skipping the pip tool of the same
     name inside a virtual environment, and calls it by absolute path;
  2. checks the runtime's execution role exists (created by
     2-lakehouse-tenant-roles-setup/setup_runtime_roles.py);
  3. reads the IdP flag and the values earlier steps stored in SSM Parameter Store,
     failing with the notebook that produces any value that is missing;
  4. writes the project's aws-targets.json from the caller's identity (gitignored);
  5. injects the authorizer, environment variables and role ARN into agentcore.json,
     runs `agentcore deploy`, and restores the placeholders afterwards, even on failure;
  6. stores the runtime ARN/ID in SSM under the names the later steps read.

Usage (with the sample's venv Python; an activated venv is fine):
    .venv/bin/python deployment/agentcore_cli_deploy.py lakehouse-mcp
    .venv/bin/python deployment/agentcore_cli_deploy.py opensearch-mcp
    .venv/bin/python deployment/agentcore_cli_deploy.py lakehouse-agent
    .venv/bin/python deployment/agentcore_cli_deploy.py lakehouse-agent --destroy --yes
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import boto3

DEPLOYMENT_DIR = Path(__file__).resolve().parent  # .../lakehouse-agent/deployment
SAMPLE_DIR = DEPLOYMENT_DIR.parent

sys.path.insert(0, str(SAMPLE_DIR))
from utils.idp_config import get_idp_provider

SSM_PREFIX = "/app/lakehouse-agent/"
REQUIRED_CLI_MAJOR_MINOR = "0.30."
TARGET_NAME = "default"
PLACEHOLDER_ACCOUNT = "000000000000"
PLACEHOLDER_DISCOVERY_URL = "https://idp.placeholder.invalid/.well-known/openid-configuration"
ROLE_SCRIPT = "deployment/2-lakehouse-tenant-roles-setup/setup_runtime_roles.py"

NB_IDP = "01-deploy-idp.ipynb"
NB_S3TABLES = "03-deploy-s3tables.ipynb"
NB_CLAIMS_GATEWAY = "05a-deploy-claims-gateway.ipynb"
NB_NOTES_GATEWAY = "05b-deploy-notes-gateway.ipynb (OpenSearch collection step)"


@dataclass(frozen=True)
class RuntimeSpec:
    project_dir: str  # runtime source directory under deployment/; the CLI project root
    runtime_name: str  # runtime name in that project's agentcore.json
    caller: str  # "gateway" (validates the M2M client) or "user" (validates the user app client)
    # placeholder -> (SSM key under SSM_PREFIX, notebook that writes it)
    inputs: dict[str, tuple[str, str]]
    # (ARN key, ID key) the later steps read
    ssm_runtime_keys: tuple[str, str]
    extra_ssm_keys: tuple[str, ...] = field(default=())
    next_step: str = ""


RUNTIMES: dict[str, RuntimeSpec] = {
    "lakehouse-mcp": RuntimeSpec(
        project_dir="4a-mcp-lakehouse-server",
        runtime_name="lakehouse_mcp_server",
        caller="gateway",
        inputs={
            "__S3_BUCKET_NAME__": ("s3-bucket-name", NB_S3TABLES),
            "__ATHENA_DATABASE_NAME__": ("database-name", NB_S3TABLES),
            "__CATALOG_NAME__": ("catalog-name", NB_S3TABLES),
        },
        ssm_runtime_keys=("mcp-server-runtime-arn", "mcp-server-runtime-id"),
        next_step="create the claims gateway (05a-deploy-claims-gateway.ipynb)",
    ),
    "opensearch-mcp": RuntimeSpec(
        project_dir="4b-mcp-opensearch-server",
        runtime_name="opensearch_mcp_server",
        caller="gateway",
        inputs={"__OPENSEARCH_COLLECTION_ENDPOINT__": ("opensearch-collection-endpoint", NB_NOTES_GATEWAY)},
        ssm_runtime_keys=("opensearch-mcp-runtime-arn", "opensearch-mcp-runtime-id"),
        next_step="create the notes gateway (rest of 05b-deploy-notes-gateway.ipynb)",
    ),
    "lakehouse-agent": RuntimeSpec(
        project_dir="6-lakehouse-agent",
        runtime_name="lakehouse_agent",
        caller="user",
        inputs={"__GATEWAY_ARN__": ("gateway-arn", NB_CLAIMS_GATEWAY)},
        ssm_runtime_keys=("agent-runtime-arn", "agent-runtime-id"),
        extra_ssm_keys=("agent-name",),
        next_step="run the UI (08-streamlit-ui.ipynb)",
    ),
}


def fail(message: str) -> None:
    print(f"\n❌ {message}")
    sys.exit(1)


class Project:
    """Paths of one runtime's CLI project."""

    def __init__(self, selector: str):
        self.selector = selector
        self.spec = RUNTIMES[selector]
        self.root = DEPLOYMENT_DIR / self.spec.project_dir  # codeLocation "./" resolves here
        self.config_dir = self.root / "agentcore"
        self.config_path = self.config_dir / "agentcore.json"
        self.targets_path = self.config_dir / "aws-targets.json"
        self.cdk_dir = self.config_dir / "cdk"

    @property
    def name(self) -> str:
        return json.loads(self.config_path.read_text())["name"]

    def stack_name(self) -> str:
        # Matches toStackName() in the generated cdk/bin/cdk.ts: underscores become hyphens.
        return f"AgentCore-{self.name.replace('_', '-')}-{TARGET_NAME.replace('_', '-')}"

    def deployed_runtime_name(self) -> str:
        # The CDK construct names the runtime "<project>_<runtime>".
        return f"{self.name}_{self.spec.runtime_name}"


# ─────────────────────────────────────────────────────────────────────────
# 1. CLI preflight
# ─────────────────────────────────────────────────────────────────────────

CLI_CLASH_HELP = """
Two different tools install a command named `agentcore`:
  - the AgentCore CLI (npm package @aws/agentcore), which this script needs, and
  - the older Python starter toolkit, if you previously installed it,
    which installs into a Python virtual environment's bin/ directory.
This script skips any `agentcore` inside a virtual environment and uses the first
other one on PATH that reports version 0.30.x. None was found.
Fix: install the CLI with `npm install -g @aws/agentcore@0.30.0` and make sure npm's
global bin directory is on PATH (`npm prefix -g` shows it; the CLI is in its bin/).
"""


def _venv_roots() -> list[Path]:
    roots = []
    if os.environ.get("VIRTUAL_ENV"):
        roots.append(Path(os.environ["VIRTUAL_ENV"]).resolve())
    if sys.prefix != sys.base_prefix:  # this interpreter is itself a venv's Python
        roots.append(Path(sys.prefix).resolve())
    return roots


def _in_venv(path_dir: Path, roots: list[Path]) -> bool:
    resolved = path_dir.resolve()
    if any(resolved == root or root in resolved.parents for root in roots):
        return True
    return (path_dir.parent / "pyvenv.cfg").exists()  # any other venv's bin/ that is on PATH


def resolve_agentcore_cli() -> str:
    """Absolute path of the first non-venv `agentcore` on PATH that reports 0.30.x."""
    roots = _venv_roots()
    seen: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / "agentcore"
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            continue
        cli = str(candidate.absolute())
        if _in_venv(Path(entry), roots):
            seen.append(f"{cli} (skipped: inside a Python virtual environment)")
            continue
        result = subprocess.run([cli, "--version"], capture_output=True, text=True, check=False)
        lines = (result.stdout or "").strip().splitlines()
        version = lines[0].strip() if lines else ""
        if result.returncode == 0 and version.startswith(REQUIRED_CLI_MAJOR_MINOR):
            print(f"✅ AgentCore CLI {version} at {cli}")
            return cli
        seen.append(f"{cli} (skipped: version {version or result.stderr.strip()!r}, need {REQUIRED_CLI_MAJOR_MINOR}x)")
    found = "".join(f"\n   - {s}" for s in seen) or "\n   - (no `agentcore` on PATH)"
    fail(f"No usable AgentCore CLI {REQUIRED_CLI_MAJOR_MINOR}x on PATH. Found:{found}\n" + CLI_CLASH_HELP)


# ─────────────────────────────────────────────────────────────────────────
# 2-3. AWS context, role check, configuration from SSM
# ─────────────────────────────────────────────────────────────────────────


class AwsContext:
    def __init__(self):
        session = boto3.Session()
        self.region = session.region_name
        if not self.region:
            fail("No AWS region configured (set AWS_DEFAULT_REGION or a profile region).")
        self.session = session
        self.ssm = session.client("ssm", region_name=self.region)
        self.account_id = session.client("sts", region_name=self.region).get_caller_identity()["Account"]

    def client(self, name: str):
        return self.session.client(name, region_name=self.region)

    def get(self, key: str, produced_by: str) -> str:
        try:
            return self.ssm.get_parameter(Name=f"{SSM_PREFIX}{key}")["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            fail(f"SSM parameter {SSM_PREFIX}{key} not found. It is written by {produced_by}; run that step first.")


def check_role_exists(project: Project, aws: AwsContext) -> str:
    rt = json.loads(project.config_path.read_text())["runtimes"][0]
    role_name = rt["executionRoleArn"].split("/")[-1]
    iam = aws.client("iam")
    try:
        iam.get_role(RoleName=role_name)
    except iam.exceptions.NoSuchEntityException:
        fail(
            f"Execution role {role_name} not found. Create it first:\n"
            f"   python {ROLE_SCRIPT} create --role {project.selector}"
        )
    print(f"✅ Execution role {role_name} exists")
    return role_name


def authorizer(project: Project, aws: AwsContext, idp: str) -> dict:
    """customJwtAuthorizer for this runtime, from the same SSM keys the toolkit scripts read."""
    if idp == "okta":
        # Okta access tokens carry `aud`: validate by resource-server audience.
        return {
            "discoveryUrl": aws.get("okta-discovery-url", NB_IDP),
            "allowedAudience": [aws.get("okta-resource-server-audience", NB_IDP)],
        }
    # Cognito access tokens carry no `aud`: validate by client ID.
    if project.spec.caller == "user":
        # The agent is invoked by the end user, so it validates the user app client.
        pool_id = aws.get("cognito-user-pool-id", NB_IDP)
        clients = [aws.get("cognito-app-client-id", NB_IDP)]
    else:
        # The MCP runtimes are invoked by a gateway with the M2M token.
        pool_id = aws.get("cognito-user-pool-arn", NB_IDP).split("/")[-1]
        clients = [aws.get("cognito-m2m-client-id", NB_IDP)]
    return {
        "discoveryUrl": f"https://cognito-idp.{aws.region}.amazonaws.com/{pool_id}/.well-known/openid-configuration",
        "allowedClients": clients,
    }


def placeholder_values(project: Project, aws: AwsContext, idp: str) -> dict[str, str]:
    values = {"__AWS_REGION__": aws.region, "__IDP_PROVIDER__": idp}
    for placeholder, (key, produced_by) in project.spec.inputs.items():
        values[placeholder] = aws.get(key, produced_by)
    return values


# ─────────────────────────────────────────────────────────────────────────
# 4-5. Inject, deploy, restore
# ─────────────────────────────────────────────────────────────────────────


def assert_placeholder_state(project: Project, spec: dict) -> None:
    """Refuse to inject into an agentcore.json that already holds deployment values."""
    names = [rt["name"] for rt in spec["runtimes"]]
    if names != [project.spec.runtime_name]:
        fail(f"{project.config_path} defines runtimes {names}; expected [{project.spec.runtime_name!r}].")
    rt = spec["runtimes"][0]
    problems = []
    if rt["authorizerConfiguration"]["customJwtAuthorizer"].get("discoveryUrl") != PLACEHOLDER_DISCOVERY_URL:
        problems.append("discoveryUrl")
    if f"::{PLACEHOLDER_ACCOUNT}:" not in rt.get("executionRoleArn", ""):
        problems.append("executionRoleArn")
    if problems:
        rel = project.config_path.relative_to(SAMPLE_DIR)
        fail(
            f"{rel} is not in its committed placeholder state ({', '.join(problems)}); "
            f"refusing to inject twice. Restore it with `git checkout -- {rel}`."
        )


def inject(spec: dict, jwt: dict, values: dict[str, str], account_id: str) -> dict:
    rt = spec["runtimes"][0]
    rt["authorizerConfiguration"] = {"customJwtAuthorizer": jwt}
    rt["executionRoleArn"] = rt["executionRoleArn"].replace(f"::{PLACEHOLDER_ACCOUNT}:", f"::{account_id}:")
    rt["envVars"] = [{"name": v["name"], "value": values.get(v["value"], v["value"])} for v in rt.get("envVars", [])]
    leftover = [v["name"] for v in rt["envVars"] if re.fullmatch(r"__[A-Z_]+__", v["value"])]
    if leftover:
        fail(f"Unresolved placeholders in {rt['name']}: {leftover}")
    return spec


def write_targets(project: Project, aws: AwsContext) -> None:
    targets = [{"name": TARGET_NAME, "account": aws.account_id, "region": aws.region}]
    project.targets_path.write_text(json.dumps(targets, indent=2) + "\n")
    print(f"✅ Wrote {project.targets_path.relative_to(SAMPLE_DIR)} for region {aws.region} (gitignored)")


def run_cli(cli: str, cwd: Path, *args: str) -> None:
    print(f"\n▶ agentcore {' '.join(args)}   (in {cwd.relative_to(SAMPLE_DIR)})")
    result = subprocess.run([cli, *args], cwd=cwd, check=False)
    if result.returncode != 0:
        fail(f"`agentcore {' '.join(args)}` exited {result.returncode}")


def read_runtime_outputs(project: Project, aws: AwsContext) -> dict[str, str]:
    """Runtime ARN and ID from the stack outputs the CDK construct emits.

    The construct describes them as "Runtime ARN for agent: <name>" and
    "Runtime ID for agent: <name>"; the descriptions are matched rather than the
    hashed output logical IDs.
    """
    cfn = aws.client("cloudformation")
    outputs = cfn.describe_stacks(StackName=project.stack_name())["Stacks"][0].get("Outputs", [])
    found: dict[str, str] = {}
    for out in outputs:
        match = re.fullmatch(r"Runtime (ARN|ID) for agent: (\w+)", out.get("Description", ""))
        if match and match.group(2) == project.spec.runtime_name:
            found[match.group(1).lower()] = out["OutputValue"]
    if set(found) != {"arn", "id"}:
        fail(f"Stack {project.stack_name()} outputs are missing the runtime ARN/ID for {project.spec.runtime_name}")
    return found


def ssm_writes(project: Project, runtime: dict[str, str]) -> list[tuple[str, str]]:
    arn_key, id_key = project.spec.ssm_runtime_keys
    puts = [(arn_key, runtime["arn"]), (id_key, runtime["id"])]
    if "agent-name" in project.spec.extra_ssm_keys:
        puts.append(("agent-name", project.deployed_runtime_name()))
    return puts


def deploy(project: Project, cli: str) -> None:
    aws = AwsContext()
    check_role_exists(project, aws)
    idp = get_idp_provider(aws.ssm)
    print(f"✅ IdP: {idp} · region: {aws.region}")

    original = project.config_path.read_bytes()
    spec = json.loads(original)
    assert_placeholder_state(project, spec)
    jwt = authorizer(project, aws, idp)
    spec = inject(spec, jwt, placeholder_values(project, aws, idp), aws.account_id)
    write_targets(project, aws)

    if not (project.cdk_dir / "node_modules").is_dir():
        print("\n▶ npm ci (installing the pinned CDK dependencies)")
        subprocess.run(["npm", "ci", "--no-fund", "--no-audit"], cwd=project.cdk_dir, check=True)

    try:
        project.config_path.write_text(json.dumps(spec, indent=2) + "\n")
        run_cli(cli, project.root, "validate")
        run_cli(cli, project.root, "deploy", "--yes", "--verbose")
    finally:
        project.config_path.write_bytes(original)  # never leave deployment values in a tracked file
        print(f"\n↩️  Restored placeholders in {project.config_path.relative_to(SAMPLE_DIR)}")

    print("\n💾 Storing runtime ARN/ID in SSM Parameter Store...")
    for key, value in ssm_writes(project, read_runtime_outputs(project, aws)):
        aws.ssm.put_parameter(Name=f"{SSM_PREFIX}{key}", Value=value, Type="String", Overwrite=True)
        print(f"   ✅ {SSM_PREFIX}{key} = {value}")
    print(f"\n✅ {project.spec.runtime_name} deployed. Next: {project.spec.next_step}.")


# ─────────────────────────────────────────────────────────────────────────
# 6. Destroy
# ─────────────────────────────────────────────────────────────────────────


def destroy(project: Project, assume_yes: bool) -> None:
    session = boto3.Session()
    region = session.region_name
    if not region:
        fail("No AWS region configured.")
    name = project.stack_name()
    if not assume_yes:
        if not sys.stdin.isatty():
            fail("Refusing to prompt on a non-terminal stdin; re-run with --destroy --yes.")
        if input(f"Delete stack {name} in {region} and its SSM keys? (yes/no): ").strip().lower() != "yes":
            print("Cancelled.")
            return

    cfn = session.client("cloudformation", region_name=region)
    print(f"🗑️  Deleting stack {name} ...")
    cfn.delete_stack(StackName=name)
    cfn.get_waiter("stack_delete_complete").wait(StackName=name, WaiterConfig={"Delay": 15, "MaxAttempts": 80})
    print(f"   ✅ Stack {name} deleted")

    ssm = session.client("ssm", region_name=region)
    for key in (*project.spec.ssm_runtime_keys, *project.spec.extra_ssm_keys):
        try:
            ssm.delete_parameter(Name=f"{SSM_PREFIX}{key}")
            print(f"   ✅ Deleted {SSM_PREFIX}{key}")
        except ssm.exceptions.ParameterNotFound:
            print(f"   ℹ️  {SSM_PREFIX}{key} already absent")

    state = project.config_dir / ".cli" / "deployed-state.json"
    if state.exists():
        state.unlink()
        print(f"   ✅ Removed local {state.relative_to(SAMPLE_DIR)}")
    print(
        "\nℹ️  The execution role is left in place. Delete it with:\n"
        f"   python {ROLE_SCRIPT} delete --role {project.selector}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Deploy or destroy one lakehouse AgentCore Runtime via the AgentCore CLI"
    )
    parser.add_argument("runtime", choices=sorted(RUNTIMES), help="Which runtime's CLI project to deploy or destroy")
    parser.add_argument("--destroy", action="store_true", help="Delete the runtime's CloudFormation stack and SSM keys")
    parser.add_argument("--yes", "-y", action="store_true", help="Destroy: skip the confirmation prompt")
    args = parser.parse_args(argv)
    project = Project(args.runtime)
    if args.destroy:
        destroy(project, args.yes)
    else:
        deploy(project, resolve_agentcore_cli())
    return 0


if __name__ == "__main__":
    sys.exit(main())
