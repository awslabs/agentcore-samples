"""
Patch agentcore/agentcore.json to add Entra JWT inbound auth and env vars
for the Use Case 2 agent.

Run AFTER `agentcore create --defaults` has scaffolded the project.

Paths:
  - This script lives at <real-world>/deploy/03_patch_agentcore_json.py
  - It loads .env from <real-world>/.env
  - It reads/writes <real-world>/<AGENT_RUNTIME_NAME>/agentcore/agentcore.json

What it does:
  1. Adds requestHeaderAllowlist (Authorization) so the JWT reaches the agent.
  2. Sets authorizerType = "CUSTOM_JWT" with Entra OIDC discovery and
     allowedAudience = [AGENT_CLIENT_ID, api://AGENT_CLIENT_ID].
  3. Adds environment variables the agent reads at runtime:
       AGENT_WORKLOAD_NAME, AGENT_OBO_PROVIDER_NAME, GATEWAY_SCOPE,
       GATEWAY_MCP_URL, AWS_REGION.
  4. Writes the file back, idempotently.

Typical invocation:
    python ../deploy/03_patch_agentcore_json.py   # from inside $AGENT_RUNTIME_NAME/
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def main() -> None:
    deploy_dir = Path(__file__).resolve().parent
    real_world_root = deploy_dir.parent
    load_dotenv(real_world_root / ".env")

    region = os.environ.get("AWS_REGION", "us-west-2")
    tenant_id = must_env("TENANT_ID")
    agent_client_id = must_env("AGENT_CLIENT_ID")
    agent_runtime_name = must_env("AGENT_RUNTIME_NAME")
    workload_name = must_env("AGENT_WORKLOAD_NAME")
    agent_obo_provider_name = must_env("AGENT_OBO_PROVIDER_NAME")
    gateway_scope = must_env("GATEWAY_SCOPE")
    gateway_mcp_url = must_env("GATEWAY_MCP_URL")

    project_dir = real_world_root / agent_runtime_name
    agentcore_json = project_dir / "agentcore" / "agentcore.json"

    if not agentcore_json.exists():
        print(
            f"ERROR: {agentcore_json} does not exist.\n"
            f"Run the scaffold step first:\n"
            f'  agentcore create --name "$AGENT_RUNTIME_NAME" '
            f"--framework Strands --model-provider Bedrock "
            f"--memory none --build CodeZip --defaults",
            file=sys.stderr,
        )
        sys.exit(1)

    config = json.loads(agentcore_json.read_text())
    runtimes = config.setdefault("runtimes", [])
    runtime = next((r for r in runtimes if r.get("name") == agent_runtime_name), None)
    if runtime is None:
        print(
            f"ERROR: Runtime '{agent_runtime_name}' not found in agentcore.json. "
            f"Available: {[r.get('name') for r in runtimes]}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 1) requestHeaderAllowlist
    allowlist = set(runtime.get("requestHeaderAllowlist", []))
    allowlist.add("Authorization")
    runtime["requestHeaderAllowlist"] = sorted(allowlist)

    # 2) Inbound auth — CUSTOM_JWT against Entra
    discovery_url = f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
    runtime["authorizerType"] = "CUSTOM_JWT"
    runtime["authorizerConfiguration"] = {
        "customJwtAuthorizer": {
            "discoveryUrl": discovery_url,
            "allowedAudience": [
                agent_client_id,
                f"api://{agent_client_id}",
            ],
        }
    }

    # 3) Environment variables — array of {name, value} (the schema requires
    #    this shape; an `environmentVariables` object map is silently dropped).
    env_map = {
        "AGENT_WORKLOAD_NAME": workload_name,
        "AGENT_OBO_PROVIDER_NAME": agent_obo_provider_name,
        "GATEWAY_SCOPE": gateway_scope,
        "GATEWAY_MCP_URL": gateway_mcp_url,
        "AWS_REGION": region,
    }
    existing = {e["name"]: e["value"] for e in runtime.get("envVars", []) if "name" in e and "value" in e}
    existing.update(env_map)
    runtime["envVars"] = [{"name": k, "value": v} for k, v in existing.items()]

    # Backward-compat: drop the wrong-shape key if a previous patch wrote it.
    runtime.pop("environmentVariables", None)

    agentcore_json.write_text(json.dumps(config, indent=2) + "\n")

    print(f"✓ Patched {agentcore_json.relative_to(real_world_root)}")
    print(f"  requestHeaderAllowlist: {runtime['requestHeaderAllowlist']}")
    print("  authorizerType:         CUSTOM_JWT")
    print(f"  discoveryUrl:           {discovery_url}")
    print(f"  allowedAudience:        {runtime['authorizerConfiguration']['customJwtAuthorizer']['allowedAudience']}")
    print("  envVars:")
    for k, v in env_map.items():
        masked = v if not k.endswith("SECRET") else "***"
        print(f"    {k}={masked}")
    print()
    print("Next steps (from inside the project folder):")
    print(f"  cd {project_dir.relative_to(real_world_root)}")
    print("  agentcore validate")
    print("  agentcore deploy -y -v")
    print("  agentcore status   # note the runtime ARN / invoke URL")
    print("  python ../deploy/04_grant_agent_iam_permissions.py")


if __name__ == "__main__":
    main()
