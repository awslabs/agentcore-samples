"""
Patch agentcore/agentcore.json to add Okta JWT inbound auth and env vars.

Run AFTER `agentcore create --defaults` has scaffolded the project.

Paths:
  - This script lives at <real-world>/deploy/02_patch_agentcore_json.py
  - It loads .env from <real-world>/.env
  - It reads/writes <real-world>/<AGENT_RUNTIME_NAME>/agentcore/agentcore.json

You can invoke this from anywhere — it resolves paths from its own location.

What it does:
  1. Finds the runtime entry for $AGENT_RUNTIME_NAME in runtimes[].
  2. Adds requestHeaderAllowlist (includes "Authorization" so the JWT reaches the agent).
  3. Sets authorizerType = "CUSTOM_JWT" and authorizerConfiguration with Okta's
     OIDC discovery URL and allowedAudience set to your custom auth server audience.
  4. Adds the runtime environmentVariables the agent needs (workload name,
     credential provider name, downstream scope, Okta coordinates, region).
  5. Writes the file back.

Idempotent and preserves any other fields already in agentcore.json.

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
    deploy_dir = Path(__file__).resolve().parent
    real_world_root = deploy_dir.parent

    load_dotenv(real_world_root / ".env")

    region = os.environ.get("AWS_REGION", "us-west-2")
    domain = must_env("OKTA_DOMAIN")
    auth_server_id = must_env("OKTA_AUTH_SERVER_ID")
    audience = must_env("OKTA_AUDIENCE")
    # AGENT_CLIENT_ID is validated (fails fast if missing) but not used to
    # patch agentcore.json — Okta's customJwtAuthorizer validates by
    # audience only, not by cid.
    must_env("AGENT_CLIENT_ID")
    frontend_client_id = must_env("FRONTEND_CLIENT_ID")  # printed for reference below
    agent_runtime_name = must_env("AGENT_RUNTIME_NAME")
    workload_name = must_env("WORKLOAD_NAME")
    actor_provider_name = must_env("ACTOR_PROVIDER_NAME")
    downstream_scope = must_env("DOWNSTREAM_SCOPE")

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
            f"Available runtimes: {[r.get('name') for r in runtimes]}",
            file=sys.stderr,
        )
        sys.exit(1)

    oidc_discovery_url = f"https://{domain}/oauth2/{auth_server_id}/.well-known/openid-configuration"

    # 1. requestHeaderAllowlist: allow the Authorization header through so the
    #    agent handler can read the user's JWT.
    allowlist = set(runtime.get("requestHeaderAllowlist", []))
    allowlist.add("Authorization")
    runtime["requestHeaderAllowlist"] = sorted(allowlist)

    # 2. Inbound auth — CUSTOM_JWT against Okta.
    #    Okta issues tokens with aud = OKTA_AUDIENCE (typically `api://default`).
    #    The Web App's access token has cid = FRONTEND_CLIENT_ID (for reference),
    #    but the Runtime only validates signature + issuer + audience + exp.
    runtime["authorizerType"] = "CUSTOM_JWT"
    runtime["authorizerConfiguration"] = {
        "customJwtAuthorizer": {
            "discoveryUrl": oidc_discovery_url,
            "allowedAudience": [audience],
        }
    }

    # 3. Environment variables for the deployed agent container.
    #    The schema at https://schema.agentcore.aws.dev/v1/agentcore.json
    #    declares `envVars` on the runtime as an ARRAY of {name, value} objects
    #    — not an `environmentVariables` object map. Using the wrong key
    #    makes the CLI silently drop the env vars at deploy time and your
    #    agent container sees None for every expected variable.
    env_map = {
        "WORKLOAD_NAME": workload_name,
        "ACTOR_PROVIDER_NAME": actor_provider_name,
        "DOWNSTREAM_SCOPE": downstream_scope,
        "OKTA_AUDIENCE": audience,
        "OKTA_DOMAIN": domain,
        "OKTA_AUTH_SERVER_ID": auth_server_id,
        "AWS_REGION": region,
    }

    # Start with whatever the runtime already has (preserves anything the
    # user manually set) then overwrite with our managed values.
    existing = {e["name"]: e["value"] for e in runtime.get("envVars", []) if "name" in e and "value" in e}
    existing.update(env_map)
    runtime["envVars"] = [{"name": k, "value": v} for k, v in existing.items()]

    # Backward-compatibility: if an older version of this script wrote the
    # wrong key name, remove it now so it stops confusing readers.
    runtime.pop("environmentVariables", None)

    agentcore_json.write_text(json.dumps(config, indent=2) + "\n")
    print(f"✓ Patched {agentcore_json.relative_to(real_world_root)}")
    print(f"  requestHeaderAllowlist: {runtime['requestHeaderAllowlist']}")
    print("  authorizerType:         CUSTOM_JWT")
    print(f"  discoveryUrl:           {oidc_discovery_url}")
    print(f"  allowedAudience:        {runtime['authorizerConfiguration']['customJwtAuthorizer']['allowedAudience']}")
    print(f"  frontend cid (for ref): {frontend_client_id}")
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
