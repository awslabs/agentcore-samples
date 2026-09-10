"""Patch agentcore.json with inbound JWT auth and the agent's env vars.

Run AFTER `agentcore create … --defaults` has scaffolded the project, from
either the sample root or the scaffolded project folder.

What it sets on the runtime:

  1. requestHeaderAllowlist += Authorization — without this the caller's JWT
     never reaches the handler, and the agent has nothing to forward.

  2. authorizerType CUSTOM_JWT, with the SAME discoveryUrl and allowedAudience
     as the gateway (step 01). That shared audience is the passthrough
     contract: the agent forwards the caller's token to the gateway verbatim,
     so one token must satisfy both authorizers. Drift here shows up as a 401
     from the gateway during MCP initialization, which reads like a gateway
     problem rather than a runtime config one.

  3. envVars the agent reads at runtime: GATEWAY_MCP_URL, PORTAL_URL (so the
     agent can tell an unconsented user where to go), AWS_REGION.

Idempotent — safe to re-run after any deploy script changes a value.

Run from the sample root:
    python deploy/05_patch_agentcore_json.py --entra
    python deploy/05_patch_agentcore_json.py --okta
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    SAMPLE_ROOT,
    discovery_url,
    load_env,
    must_env,
    select_idp,
)


def allowed_audiences(idp: dict, audience: str) -> list[str]:
    """Must match 01_create_gateway.py's list exactly — see the docstring."""
    if idp["name"] == "entra":
        return [audience, f"api://{audience}"]
    return [audience]


def main() -> None:
    idp = select_idp(__doc__)
    load_env()

    # `or`, not a get() default: .env ships the key present but empty, and a
    # get() default does not fire for an empty string.
    region = os.environ.get("AWS_REGION") or "us-west-2"
    runtime_name = must_env("AGENT_RUNTIME_NAME")
    disco = discovery_url(idp)
    audiences = allowed_audiences(idp, must_env("IDP_AUDIENCE"))
    gateway_mcp_url = must_env("GATEWAY_MCP_URL")
    portal_url = must_env("PORTAL_URL")

    project_dir = SAMPLE_ROOT / runtime_name
    agentcore_json = project_dir / "agentcore" / "agentcore.json"
    if not agentcore_json.exists():
        print(
            f"ERROR: {agentcore_json} does not exist.\n"
            f"Scaffold the project first, from the sample root:\n"
            f'  agentcore create --name "$AGENT_RUNTIME_NAME" --framework Strands \\\n'
            f"    --model-provider Bedrock --memory none --build CodeZip --defaults",
            file=sys.stderr,
        )
        sys.exit(1)

    config = json.loads(agentcore_json.read_text())
    runtimes = config.setdefault("runtimes", [])
    runtime = next((r for r in runtimes if r.get("name") == runtime_name), None)
    if runtime is None:
        print(
            f"ERROR: runtime '{runtime_name}' not found in agentcore.json. "
            f"Available: {[r.get('name') for r in runtimes]}",
            file=sys.stderr,
        )
        sys.exit(1)

    allowlist = set(runtime.get("requestHeaderAllowlist", []))
    allowlist.add("Authorization")
    runtime["requestHeaderAllowlist"] = sorted(allowlist)

    runtime["authorizerType"] = "CUSTOM_JWT"
    runtime["authorizerConfiguration"] = {"customJwtAuthorizer": {"discoveryUrl": disco, "allowedAudience": audiences}}

    # envVars is an array of {name, value}; an `environmentVariables` object
    # map is silently dropped by the schema.
    env_map = {
        "GATEWAY_MCP_URL": gateway_mcp_url,
        # Bare host in .env; the agent shows it to users, so give it a scheme.
        "PORTAL_URL": f"https://{portal_url.removeprefix('https://').rstrip('/')}",
        "AWS_REGION": region,
    }
    # Only forwarded when set, so the agent's own default model stands otherwise.
    model_id = os.environ.get("MODEL_ID", "").strip()
    if model_id:
        env_map["MODEL_ID"] = model_id

    existing = {e["name"]: e["value"] for e in runtime.get("envVars", []) if "name" in e and "value" in e}
    existing.update(env_map)
    runtime["envVars"] = [{"name": k, "value": v} for k, v in existing.items()]
    runtime.pop("environmentVariables", None)

    agentcore_json.write_text(json.dumps(config, indent=2) + "\n")

    print(f"✓ Patched {agentcore_json.relative_to(SAMPLE_ROOT)}")
    print(f"  requestHeaderAllowlist: {runtime['requestHeaderAllowlist']}")
    print("  authorizerType:         CUSTOM_JWT")
    print(f"  discoveryUrl:           {disco}")
    print(f"  allowedAudience:        {audiences}")
    print("  envVars:")
    for k, v in env_map.items():
        print(f"    {k}={v}")
    print()
    print("Next, from inside the project folder:")
    print(f"  cd {runtime_name}")
    print("  agentcore validate && agentcore deploy -y -v")
    print("  agentcore status    # copy the invoke URL")
    print("  # append ?qualifier=DEFAULT and save it to .env as AGENT_RUNTIME_INVOKE_URL")
    print("  # (without the qualifier the invoke returns 404 UnknownOperationException)")


if __name__ == "__main__":
    main()
