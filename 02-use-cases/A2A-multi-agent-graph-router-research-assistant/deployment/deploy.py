"""Deploy the distributed A2A router-supervisor to AgentCore Runtime.

Deploys 4 separate agents:
  1. Research agent  (A2A protocol)
  2. Analysis agent  (A2A protocol)
  3. Writer agent    (A2A protocol)
  4. Supervisor      (HTTP protocol, receives sub-agent URLs as env vars)

Usage:
    python -m deployment.deploy
    python -m deployment.deploy --region us-east-1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3
from bedrock_agentcore_starter_toolkit import Runtime

from .sigv4_auth import arn_to_runtime_url

# Sub-agent definitions: (name, entrypoint, protocol)
_SUB_AGENTS = [
    ("research_a2a", "deployment/agentcore_research.py", "A2A"),
    ("analysis_a2a", "deployment/agentcore_analysis.py", "A2A"),
    ("writer_a2a", "deployment/agentcore_writer.py", "A2A"),
]

_SUPERVISOR = ("supervisor_a2a", "deployment/agentcore_supervisor.py", "HTTP")

# Maps sub-agent names to the env var the supervisor expects
_ENV_VAR_MAP = {
    "research_a2a": "RESEARCH_AGENT_URL",
    "analysis_a2a": "ANALYSIS_AGENT_URL",
    "writer_a2a": "WRITER_AGENT_URL",
}


def _deploy_agent(
    name: str,
    entrypoint: str,
    protocol: str,
    region: str,
    *,
    env_vars: dict[str, str] | None = None,
) -> dict[str, str]:
    """Deploy a single agent and return its ARN and execution role.

    Args:
        name: Agent name (must match AgentCore naming rules).
        entrypoint: Path to the entrypoint Python file.
        protocol: Server protocol ("A2A" or "HTTP").
        region: AWS region.
        env_vars: Optional environment variables to inject.

    Returns:
        Dict with "arn" and "execution_role" keys.
    """
    runtime = Runtime()
    runtime.configure(
        entrypoint=entrypoint,
        agent_name=name,
        region=region,
        requirements_file="requirements.txt",
        auto_create_execution_role=True,
        deployment_type="direct_code_deploy",
        runtime_type="PYTHON_3_13",
        protocol=protocol,
    )
    runtime.launch(auto_update_on_conflict=True, env_vars=env_vars)

    # Read the ARN and execution role from the generated config
    config_path = Path(".bedrock_agentcore.yaml")
    if not config_path.exists():
        print(f"ERROR: Config file not found after deploying {name}", file=sys.stderr)
        sys.exit(1)

    import yaml

    config = yaml.safe_load(config_path.read_text())
    agent_config = config["agents"][name]
    arn = agent_config["bedrock_agentcore"]["agent_arn"]
    execution_role = agent_config["aws"]["execution_role"]
    print(f"\n  {name} ARN: {arn}")
    return {"arn": arn, "execution_role": execution_role}


def _grant_invoke_permissions(
    supervisor_role_arn: str,
    sub_agent_arns: list[str],
) -> None:
    """Add an inline IAM policy allowing the supervisor to invoke sub-agents.

    Uses a wildcard resource scoped to the account and region because the
    InvokeAgentRuntime data-plane API authorizes against a resource pattern
    that doesn't match the exact runtime ARN from the control plane.

    Args:
        supervisor_role_arn: Full ARN of the supervisor's execution role.
        sub_agent_arns: List of sub-agent runtime ARNs (used to derive account/region).
    """
    role_name = supervisor_role_arn.rsplit("/", 1)[-1]

    # Derive account and region from the first sub-agent ARN
    parts = sub_agent_arns[0].split(":")
    region = parts[3]
    account_id = parts[4]

    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/*",
            },
        ],
    }

    iam = boto3.client("iam")
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="InvokeSubAgentRuntimes",
        PolicyDocument=json.dumps(policy_document),
    )
    print(f"  Granted InvokeAgentRuntime on runtime/* to {role_name}")


def main() -> None:
    """Deploy all agents to AgentCore Runtime."""
    parser = argparse.ArgumentParser(description="Deploy distributed A2A agents to AgentCore")
    parser.add_argument("--region", default="us-west-2", help="AWS region (default: us-west-2)")
    args = parser.parse_args()

    print("=" * 70)
    print("Deploying distributed A2A router-supervisor to AgentCore")
    print("=" * 70)

    # Step 1: Deploy sub-agents
    agent_info: dict[str, dict[str, str]] = {}
    for name, entrypoint, protocol in _SUB_AGENTS:
        print(f"\n--- Deploying {name} ({protocol}) ---")
        info = _deploy_agent(name, entrypoint, protocol, args.region)
        agent_info[name] = info

    # Step 2: Build env vars for supervisor
    env_vars = {}
    for agent_name, env_var in _ENV_VAR_MAP.items():
        url = arn_to_runtime_url(agent_info[agent_name]["arn"])
        env_vars[env_var] = url
        print(f"  {env_var}={url}")

    env_vars["AWS_REGION"] = args.region

    # Step 3: Deploy supervisor with sub-agent URLs
    sup_name, sup_entry, sup_proto = _SUPERVISOR
    print(f"\n--- Deploying {sup_name} ({sup_proto}) ---")
    print(f"  Env vars: {json.dumps(list(env_vars.keys()))}")
    sup_info = _deploy_agent(sup_name, sup_entry, sup_proto, args.region, env_vars=env_vars)

    # Step 4: Grant supervisor permission to invoke sub-agents
    print("\n--- Granting cross-invoke IAM permissions ---")
    sub_agent_arns = [info["arn"] for info in agent_info.values()]
    _grant_invoke_permissions(sup_info["execution_role"], sub_agent_arns)

    print("\n" + "=" * 70)
    print("All 4 agents deployed successfully!")
    print("=" * 70)
    print("\nInvoke the supervisor:")
    print('  agentcore invoke -a supervisor_a2a \'{\"prompt\": \"Research the impact of AI\"}\'')


if __name__ == "__main__":
    main()
