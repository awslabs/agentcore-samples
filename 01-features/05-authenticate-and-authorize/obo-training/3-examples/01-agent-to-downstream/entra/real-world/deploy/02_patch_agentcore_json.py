"""
Patch agentcore/agentcore.json to add Entra JWT inbound auth and env vars.

Run AFTER `agentcore create --defaults` has scaffolded the project.

Paths:
  - This script lives at <real-world>/deploy/02_patch_agentcore_json.py
  - It loads .env from <real-world>/.env
  - It reads/writes <real-world>/<AGENT_RUNTIME_NAME>/agentcore/agentcore.json

You can invoke this from anywhere — it resolves paths from its own location.

What it does:
  1. Finds the runtime entry for $AGENT_RUNTIME_NAME in runtimes[].
  2. Adds requestHeaderAllowlist (includes "Authorization" so the JWT reaches the agent).
  3. Sets authorizerType = "CUSTOM_JWT" and authorizerConfiguration with the Entra
     OIDC discovery URL and allowedAudience for the agent app.
  4. Adds the runtime environmentVariables the agent needs (workload name,
     credential provider name, Graph scope, region).
  5. Writes the file back.

This script is idempotent and preserves any other fields already in agentcore.json.

Typical usage:
    python ../deploy/02_patch_agentcore_json.py   # from inside $AGENT_RUNTIME_NAME/
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
    # The script's own location (always in <real-world>/deploy/).
    deploy_dir = Path(__file__).resolve().parent
    real_world_root = deploy_dir.parent

    # Load .env from the real-world root (not from CWD), so we pick up all the
    # env vars regardless of where the user invokes us.
    load_dotenv(real_world_root / ".env")

    region = os.environ.get("AWS_REGION", "us-west-2")
    tenant_id = must_env("TENANT_ID")
    agent_client_id = must_env("AGENT_CLIENT_ID")
    agent_runtime_name = must_env("AGENT_RUNTIME_NAME")
    workload_name = must_env("WORKLOAD_NAME")
    actor_provider_name = must_env("ACTOR_PROVIDER_NAME")
    graph_scope = must_env("GRAPH_SCOPE")

    # The AgentCore CLI scaffolds the project at <real-world>/<AGENT_RUNTIME_NAME>/.
    # Its config file is <project>/agentcore/agentcore.json.
    project_dir = real_world_root / agent_runtime_name
    agentcore_json = project_dir / "agentcore" / "agentcore.json"

    if not agentcore_json.exists():
        print(
            f"ERROR: {agentcore_json} does not exist.\n"
            f"Run step 5 first:\n"
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
            f"Available runtimes: {[r.get('name') for r in runtimes]}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Entra OIDC discovery URL. Entra issues v1.0 tokens by default;
    # change to /v2.0/.well-known/... if your app uses v2.0 tokens.
    oidc_discovery_url = f"https://login.microsoftonline.com/{tenant_id}/.well-known/openid-configuration"

    # 1. requestHeaderAllowlist: allow the Authorization header through so the agent
    #    can read the JWT inside its handler.
    allowlist = set(runtime.get("requestHeaderAllowlist", []))
    allowlist.add("Authorization")
    runtime["requestHeaderAllowlist"] = sorted(allowlist)

    # 2. Inbound auth — CUSTOM_JWT against Entra.
    runtime["authorizerType"] = "CUSTOM_JWT"
    runtime["authorizerConfiguration"] = {
        "customJwtAuthorizer": {
            "discoveryUrl": oidc_discovery_url,
            "allowedAudience": [
                agent_client_id,
                f"api://{agent_client_id}",
            ],
        }
    }

    # 3. Environment variables for the deployed agent container.
    #    The schema at https://schema.agentcore.aws.dev/v1/agentcore.json
    #    declares `envVars` on the runtime as an ARRAY of {name, value} objects
    #    — not an `environmentVariables` object map. Using the wrong key
    #    makes the CLI silently drop the env vars at deploy time.
    env_map = {
        "WORKLOAD_NAME": workload_name,
        "ACTOR_PROVIDER_NAME": actor_provider_name,
        "GRAPH_SCOPE": graph_scope,
        "AWS_REGION": region,
    }

    existing = {e["name"]: e["value"] for e in runtime.get("envVars", []) if "name" in e and "value" in e}
    existing.update(env_map)
    runtime["envVars"] = [{"name": k, "value": v} for k, v in existing.items()]

    # Backward-compatibility: remove the old wrong-shape key if present.
    runtime.pop("environmentVariables", None)

    agentcore_json.write_text(json.dumps(config, indent=2) + "\n")
    print(f"✓ Patched {agentcore_json.relative_to(real_world_root)}")
    print(f"  requestHeaderAllowlist: {runtime['requestHeaderAllowlist']}")
    print("  authorizerType:         CUSTOM_JWT")
    print(f"  discoveryUrl:           {oidc_discovery_url}")
    print(f"  allowedAudience:        {runtime['authorizerConfiguration']['customJwtAuthorizer']['allowedAudience']}")
    print()
    print("Next steps (from inside the project folder):")
    print("  cd " + str(project_dir.relative_to(real_world_root)))
    print("  agentcore validate")
    print("  agentcore deploy -y -v")
    print("  agentcore status       (note the runtime ARN / invoke URL)")
    print("  # then paste the invoke URL into .env as AGENT_RUNTIME_INVOKE_URL")
    print("  # and run frontend/app.py from the real-world/ folder")


if __name__ == "__main__":
    main()
