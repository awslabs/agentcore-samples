"""Patch agentcore/agentcore.json for Okta XAA (ID-JAG) inbound auth + env vars.

Run AFTER `agentcore create` has scaffolded the project, from INSIDE the
generated project directory (so ./agentcore/agentcore.json exists), e.g.:

    cd <project>
    python3 ../deploy/patch_agentcore_json.py

It reads configuration from the environment (source your .env first:
`set -a && source ../scripts/.env && set +a`) and patches the first runtime:

  * requestHeaderAllowlist: ["Authorization"]  (also passed in the payload as
    `id_token`, but allow-listing lets the header path work too).
  * authorizerType: CUSTOM_JWT.
  * authorizerConfiguration.customJwtAuthorizer:
        discoveryUrl   = <OKTA_ISSUER>/.well-known/openid-configuration  (ORG server)
        allowedAudience = [<OKTA_LOGIN_CLIENT_ID>]   (the ID token's aud = the login app)
  * envVars: array of {name,value} the agent needs to drive the XAA exchange.

Note: unlike the AgentCore-native OBO sample (which validates an access token
audienced at a custom AS), this XAA/ID-JAG sample's inbound token is the user's
ID token from the ORG server, so the audience is the login app client id.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv optional
    load_dotenv = None


def must_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"ERROR: {name} is not set (source your .env first).", file=sys.stderr)
        sys.exit(1)
    return v


# Env vars injected into the deployed agent container.
_PASSTHROUGH = [
    "OKTA_ISSUER",
    "OKTA_CLIENT_ID",
    "RESOURCE_AS_ISSUER",
    "RESOURCE_API",
    "RESOURCE_API_URL",
    "XAA_SCOPES",
    "CLIENT_AUTH_METHOD",
    "CLIENT_ASSERTION_ALG",
    "OKTA_PRIVATE_KEY_KID",
    "XAA_KEY_SECRET_ID",
    "RESOURCE_CALL_MODE",
    "RESOURCE_LAMBDA_NAME",
    "AWS_REGION",
]


def main() -> None:
    project_dir = Path(os.environ.get("AGENTCORE_PROJECT_DIR", ".")).resolve()
    if load_dotenv:
        # Convenience: pick up a .env in CWD if present.
        load_dotenv()

    okta_issuer = must_env("OKTA_ISSUER").rstrip("/")
    login_client_id = must_env("OKTA_LOGIN_CLIENT_ID")

    agentcore_json = project_dir / "agentcore" / "agentcore.json"
    if not agentcore_json.exists():
        print(
            f"ERROR: {agentcore_json} not found. Run `agentcore create` first and "
            f"run this from inside the project dir.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = json.loads(agentcore_json.read_text())
    runtimes = config.get("runtimes", [])
    if not runtimes:
        print("ERROR: no runtimes[] in agentcore.json", file=sys.stderr)
        sys.exit(1)
    runtime = runtimes[0]
    name = os.environ.get("AGENT_RUNTIME_NAME")
    if name:
        runtime = next((r for r in runtimes if r.get("name") == name), runtime)

    allowlist = set(runtime.get("requestHeaderAllowlist", []))
    allowlist.add("Authorization")
    runtime["requestHeaderAllowlist"] = sorted(allowlist)

    runtime["authorizerType"] = "CUSTOM_JWT"
    runtime["authorizerConfiguration"] = {
        "customJwtAuthorizer": {
            "discoveryUrl": f"{okta_issuer}/.well-known/openid-configuration",
            "allowedAudience": [login_client_id],
        }
    }

    existing = {e["name"]: e["value"] for e in runtime.get("envVars", []) if "name" in e and "value" in e}
    for key in _PASSTHROUGH:
        val = os.environ.get(key)
        if val:
            existing[key] = val
    runtime["envVars"] = [{"name": k, "value": v} for k, v in existing.items()]
    runtime.pop("environmentVariables", None)  # wrong key name; ensure it's gone

    agentcore_json.write_text(json.dumps(config, indent=2) + "\n")
    print(f"✓ Patched {agentcore_json}")
    print(f"  requestHeaderAllowlist: {runtime['requestHeaderAllowlist']}")
    print("  authorizerType:         CUSTOM_JWT")
    print(f"  discoveryUrl:           {okta_issuer}/.well-known/openid-configuration")
    print(f"  allowedAudience:        [{login_client_id}]")
    print(f"  envVars:                {[e['name'] for e in runtime['envVars']]}")


if __name__ == "__main__":
    main()
