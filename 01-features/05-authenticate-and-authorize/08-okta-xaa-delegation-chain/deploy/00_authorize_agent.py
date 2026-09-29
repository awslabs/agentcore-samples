"""Authorize the registered AI Agent on the resource authorization server.

Run this AFTER registering the AI Agent (IDP_SETUP_OKTA.md) and setting
AI_AGENT_CLIENT_ID in .env.

Why this is a separate step: 00_create_okta_apps.py has to create the AS 2
jwt-bearer access policy before the AI Agent exists -- Okta requires a policy to
name at least one client, so it is created pointing at the Agent app as a
placeholder. That placeholder is wrong in two ways: the Agent app performs its
exchange at AS 1 and has no business at the resource server, and the client that
actually redeems the ID-JAG at leg 2 is the AI Agent's wlp... client. Leaving it
unfixed fails leg 2 with:

    access_denied: Policy evaluation failed

This script repoints the policy at the AI Agent, replacing the placeholder rather
than adding to it, and leaves the rule untouched.

    python deploy/00_authorize_agent.py
    python deploy/00_authorize_agent.py --keep-existing   # append instead of replace
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OktaAdmin, load_env, must_env, okta_org_url

POLICY_NAME = "XAA sample - Resource jwt-bearer"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--keep-existing",
        action="store_true",
        help="Append the agent to the policy's client list instead of replacing it.",
    )
    args = ap.parse_args()

    load_env()
    okta = OktaAdmin(okta_org_url(), must_env("OKTA_API_TOKEN"))
    as2 = must_env("RESOURCE_AS_ID", "Run deploy/00_create_okta_apps.py first.")
    agent = must_env(
        "AI_AGENT_CLIENT_ID",
        "Register the AI Agent first and copy its Client ID from the Client registration tab -- see IDP_SETUP_OKTA.md.",
    )

    policies = okta.get(f"/authorizationServers/{as2}/policies?limit=200")
    policy = next((p for p in policies if p["name"] == POLICY_NAME), None)
    if policy is None:
        names = [p["name"] for p in policies]
        print(
            f"ERROR: no policy named {POLICY_NAME!r} on authorization server {as2}.\n"
            f"Policies present: {names}\n"
            "Re-run deploy/00_create_okta_apps.py.",
            file=sys.stderr,
        )
        sys.exit(1)

    current = ((policy.get("conditions") or {}).get("clients") or {}).get("include") or []
    print(f"  policy {POLICY_NAME!r}")
    print(f"  clients before: {current}")

    if agent in current and len(current) == 1:
        print(f"  • already scoped to {agent} only -- nothing to do")
        return

    include = sorted({*current, agent}) if args.keep_existing else [agent]
    okta.put(
        f"/authorizationServers/{as2}/policies/{policy['id']}",
        {
            "type": "OAUTH_AUTHORIZATION_POLICY",
            "status": "ACTIVE",
            "name": policy["name"],
            "description": policy.get("description", ""),
            "priority": policy.get("priority", 1),
            "conditions": {"clients": {"include": include}},
        },
    )
    print(f"  ✓ clients after: {include}")

    # The rule carries the grant types and scopes; confirm it survived and is ACTIVE,
    # because an inactive rule is silently skipped during evaluation.
    for rule in okta.get(f"/authorizationServers/{as2}/policies/{policy['id']}/rules"):
        conditions = rule.get("conditions") or {}
        grants = (conditions.get("grantTypes") or {}).get("include")
        scopes = (conditions.get("scopes") or {}).get("include")
        flag = "✓" if rule.get("status") == "ACTIVE" else "✗ NOT ACTIVE"
        print(f"  {flag} rule {rule['name']}: grants={grants} scopes={scopes}")

    print("\n  Next: python scripts/verify_ai_agent.py")


if __name__ == "__main__":
    main()
