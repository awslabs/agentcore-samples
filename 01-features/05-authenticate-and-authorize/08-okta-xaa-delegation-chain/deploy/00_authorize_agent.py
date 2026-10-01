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
from _common import OktaAdmin, env, load_env, must_env, okta_org_url

POLICY_NAME = "XAA sample - Resource jwt-bearer"


def assign_user_to_agent_app(okta: OktaAdmin, email: str) -> None:
    """Assign a user to the Agent app, which leg 1 needs on the access_token path.

    Easy to miss, because it is a *different* app from the one the user signs in to, and
    the error names neither: leg 1 fails with "the user is not assigned to the client
    application". The Agent app is created with no assignments because nothing needed them
    before Machine access existed.
    """
    app = env("AGENT_APP_CLIENT_ID")
    if not app:
        print("  - AGENT_APP_CLIENT_ID is not set; skipping the assignment")
        return
    users = okta.get(f"/users?q={email}&limit=5") or []
    match = next((u for u in users if (u.get("profile") or {}).get("login") == email), None)
    if not match:
        print(f"  ! no Okta user with login {email}; assign them by hand (IDP_SETUP_OKTA.md step 6d)")
        return
    okta.post(f"/apps/{app}/users", {"id": match["id"], "scope": "USER"})
    # Okta sometimes answers this POST with a 404 or reports zero assigned users while the
    # assignment has in fact taken effect, so do not report success from the response. A
    # working leg 1 is the only reliable confirmation.
    print(f"  ✓ requested assignment of {email} to the Agent app ({app})")
    print("    verify with scripts/test_chain.py -- Okta's response here is unreliable")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--assign-user",
        metavar="EMAIL",
        help=(
            "Also assign this user to the Agent app, which ID-JAG leg 1 requires on the "
            "access_token path. Omit to skip."
        ),
    )
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

    already_correct = agent in current and len(current) == 1
    if already_correct:
        print(f"  • already scoped to {agent} only -- nothing to do")

    include = sorted({*current, agent}) if args.keep_existing else [agent]
    if not already_correct:
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

    if args.assign_user:
        print("\nAgent app assignment (needed by leg 1 on the access_token path)")
        assign_user_to_agent_app(okta, args.assign_user)

    print("\n  Next: python scripts/verify_ai_agent.py")


if __name__ == "__main__":
    main()
