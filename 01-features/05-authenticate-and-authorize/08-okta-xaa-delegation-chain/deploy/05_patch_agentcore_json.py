"""Patch the AgentCore CLI scaffold: Okta inbound auth plus the agent's environment.

Run from the sample root AFTER `agentcore create`. The CLI does not expose inbound JWT
auth or env vars, so both are written into agentcore.json directly.

What it sets:

  * authorizerConfiguration.customJWTAuthorizer against AS 1, with
    allowedAudience = api://agentcore and allowedScopes = [agent.access].
    Scope pinning, not `allowedClients` -- the latter does not work with Okta tokens
    (see README troubleshooting).
  * requestHeaderAllowlist = ["Authorization"], without which the agent cannot read
    the caller's token at all.
  * The env vars agent.py needs, all from .env.

    python deploy/05_patch_agentcore_json.py
    python deploy/05_patch_agentcore_json.py --project-dir xaatodoagent
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    SAMPLE_ROOT,
    account_id,
    discovery_url,
    env,
    load_env,
    must_env,
    obo_provider_name,
    region,
    save_env,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--project-dir",
        default=env("AGENT_RUNTIME_NAME", "xaatodoagent"),
        help="The scaffold directory created by `agentcore create` (default: %(default)s).",
    )
    args = ap.parse_args()
    load_env()

    project = SAMPLE_ROOT / args.project_dir
    config_path = project / "agentcore.json"
    if not config_path.exists():
        print(
            f"ERROR: {config_path} not found.\n"
            "Create the scaffold first:\n"
            f"  agentcore create --project-name {args.project_dir} --name {args.project_dir} \\\n"
            "    --framework Strands --model-provider Bedrock --memory none \\\n"
            "    --build CodeZip --language Python --defaults",
            file=sys.stderr,
        )
        sys.exit(1)

    config = json.loads(config_path.read_text())
    agents = config.get("agents") or {}
    if not agents:
        sys.exit(f"no agents found in {config_path}")
    name = next(iter(agents))
    agent = agents[name]

    agent["authorizerConfiguration"] = {
        "customJWTAuthorizer": {
            "discoveryUrl": discovery_url(must_env("AGENTCORE_AS_ISSUER")),
            "allowedAudience": [env("AGENTCORE_AUDIENCE", "api://agentcore")],
            # The BFF's token carries agent.access. Pinning the scope is what stops a
            # token minted for some other purpose from invoking the agent.
            "allowedScopes": [env("SCOPE_AGENT_ACCESS", "agent.access")],
        }
    }
    # Without this the caller's bearer never reaches the handler, and the OBO exchange
    # has no subject token to work from.
    agent["requestHeaderAllowlist"] = ["Authorization"]

    envvars = agent.setdefault("environment", {})
    envvars.update(
        {
            "GATEWAY_MCP_URL": must_env("GATEWAY_MCP_URL", "Run deploy/02_create_gateway.py first."),
            "AGENT_OBO_PROVIDER_NAME": obo_provider_name(),
            "AGENTCORE_AUDIENCE": env("AGENTCORE_AUDIENCE", "api://agentcore"),
            "SCOPE_TOOLS_ACCESS": env("SCOPE_TOOLS_ACCESS", "tools.access"),
            "AGENT_WORKLOAD_NAME": env("AGENT_WORKLOAD_NAME", "xaa-todo-agent"),
            "ID_TOKEN_HEADER": env("ID_TOKEN_HEADER", "X-Okta-Id-Token"),
        }
    )
    if env("MODEL_ID"):
        envvars["MODEL_ID"] = env("MODEL_ID")

    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  ✓ patched {config_path.relative_to(SAMPLE_ROOT)} (agent: {name})")
    print(f"    discoveryUrl : {agent['authorizerConfiguration']['customJWTAuthorizer']['discoveryUrl']}")
    print(f"    audience     : {agent['authorizerConfiguration']['customJWTAuthorizer']['allowedAudience']}")
    print(f"    scopes       : {agent['authorizerConfiguration']['customJWTAuthorizer']['allowedScopes']}")
    print(f"    headers      : {agent['requestHeaderAllowlist']}")
    print(f"    env vars     : {sorted(envvars)}")

    # The CLI deploys through CDK and needs an explicit account/region target.
    targets = project / "agentcore" / "aws-targets.json"
    if targets.parent.exists():
        targets.write_text(json.dumps([{"name": "default", "account": account_id(), "region": region()}]) + "\n")
        print(f"  ✓ wrote {targets.relative_to(SAMPLE_ROOT)}")

    save_env(AGENT_RUNTIME_NAME=args.project_dir)
    print(
        f"\n  Next:\n    cp agent/agent.py {args.project_dir}/app/{args.project_dir}/main.py\n"
        f"    ( cd {args.project_dir} && agentcore validate && agentcore deploy -y )\n"
        "    python deploy/06_grant_iam.py"
    )


if __name__ == "__main__":
    main()
