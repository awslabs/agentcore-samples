"""Patch the AgentCore CLI scaffold: Okta inbound auth plus the agent's environment.

Run from the sample root AFTER `agentcore create`. The CLI does not expose inbound JWT
auth or env vars, so both are written into agentcore.json directly.

What it sets:

  * authorizerConfiguration.customJWTAuthorizer against AS 1, with
    allowedAudience = AGENTCORE_AUDIENCE and allowedScopes = [agent.access].
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
    # The CLI keeps its config under agentcore/, not the project root.
    config_path = project / "agentcore" / "agentcore.json"
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
    # Schema v1 uses a `runtimes` ARRAY; there is no `agents` map.
    runtimes = config.get("runtimes") or []
    if not runtimes:
        sys.exit(f"no runtimes found in {config_path}")
    runtime = runtimes[0]
    name = runtime.get("name", args.project_dir)

    # The schema requires authorizerType to be set explicitly; supplying
    # authorizerConfiguration alone fails `agentcore validate` with
    # "authorizerConfiguration is only allowed when authorizerType is CUSTOM_JWT".
    runtime["authorizerType"] = "CUSTOM_JWT"
    runtime["authorizerConfiguration"] = {
        # NOTE the casing: the CLI's JSON schema spells this `customJwtAuthorizer`,
        # while the boto3 API uses `customJWTAuthorizer`. Using the API spelling here
        # fails validation with "authorizerConfiguration with customJwtAuthorizer is
        # required when authorizerType is CUSTOM_JWT", which does not obviously point
        # at a casing difference.
        "customJwtAuthorizer": {
            "discoveryUrl": discovery_url(must_env("AGENTCORE_AS_ISSUER")),
            "allowedAudience": [env("AGENTCORE_AUDIENCE", "https://xaa-agentcore.example.com")],
            # The BFF's token carries agent.access. Pinning the scope is what stops a
            # token minted for another purpose from invoking the agent.
            #
            # NOT allowedClients: with Okta that silently refuses a token whose `cid` is
            # exactly the listed client, and reports it as insufficient_scope.
            "allowedScopes": [env("SCOPE_AGENT_ACCESS", "agent.access")],
        }
    }
    # Without this the caller's bearer never reaches the handler, and the OBO exchange
    # has no subject token to work from.
    runtime["requestHeaderAllowlist"] = ["Authorization"]

    # The schema calls this `envVars`, and it is an ARRAY of {name, value} -- not a
    # map. Two traps in one field:
    #   * `environment` (a map) is silently ignored: validate passes, the deploy
    #     succeeds, and the runtime comes up with NO variables, so the agent fails at
    #     its first use of GATEWAY_MCP_URL.
    #   * `envVars` as a map fails validation with `expected "array"`.
    runtime.pop("environment", None)
    wanted = {
        "GATEWAY_MCP_URL": must_env("GATEWAY_MCP_URL", "Run deploy/02_create_gateway.py first."),
        "AGENT_OBO_PROVIDER_NAME": obo_provider_name(),
        "AGENTCORE_AUDIENCE": env("AGENTCORE_AUDIENCE", "https://xaa-agentcore.example.com"),
        "SCOPE_TOOLS_ACCESS": env("SCOPE_TOOLS_ACCESS", "tools.access"),
        "AGENT_WORKLOAD_NAME": env("AGENT_WORKLOAD_NAME", "xaa-todo-agent"),
        # The agent only forwards this header when a caller supplies an ID token, which
        # happens on the id_token fallback path. XAA_LEG1_SUBJECT is deliberately NOT set
        # here: the interceptor decides the mode, not the runtime.
        "ID_TOKEN_HEADER": env("ID_TOKEN_HEADER", "X-Okta-Id-Token"),
    }
    if env("MODEL_ID"):
        wanted["MODEL_ID"] = env("MODEL_ID")
    # Merge with anything already there rather than clobbering it.
    existing = {e["name"]: e["value"] for e in runtime.get("envVars") or [] if "name" in e}
    existing.update(wanted)
    runtime["envVars"] = [{"name": k, "value": v} for k, v in sorted(existing.items())]

    config_path.write_text(json.dumps(config, indent=2) + "\n")
    jwt_cfg = runtime["authorizerConfiguration"]["customJwtAuthorizer"]
    print(f"  ✓ patched {config_path.relative_to(SAMPLE_ROOT)} (runtime: {name})")
    print(f"    discoveryUrl : {jwt_cfg['discoveryUrl']}")
    print(f"    audience     : {jwt_cfg['allowedAudience']}")
    print(f"    scopes       : {jwt_cfg['allowedScopes']}")
    print(f"    headers      : {runtime['requestHeaderAllowlist']}")
    print(f"    envVars      : {[e['name'] for e in runtime['envVars']]}")

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
