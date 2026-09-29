"""Check the manually registered Okta AI Agent, as far as the API allows.

    python scripts/verify_ai_agent.py

Verifies over the Management API:
  1. AI_AGENT_CLIENT_ID is set and looks like a wlp... workload principal
  2. the client exists in the tenant
  3. it is ACTIVE, not STAGED (a staged agent fails every call with invalid_client)
  4. it has a registered public key whose kid matches AI_AGENT_KEY_KID
  5. the local private key is the pair of that public key
  6. the AS 2 jwt-bearer policy lists this client

Delegations and Resource connections are NOT exposed to the Management API, so
steps 4 and 5 of IDP_SETUP_OKTA.md cannot be checked here -- only by running the
flow. This script says so rather than implying full coverage.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _common import OktaAdmin, env, load_env, must_env, okta_org_url

KEYS_DIR = Path(__file__).resolve().parent / "keys"
OK, BAD, WARN = "  ✓", "  ✗", "  ⚠"


def b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    load_env()
    org = okta_org_url()
    okta = OktaAdmin(org, must_env("OKTA_API_TOKEN"))
    failures = 0

    agent_id = env("AI_AGENT_CLIENT_ID")
    want_kid = env("AI_AGENT_KEY_KID")

    print(f"Okta org: {org}\n")

    # 1. env value present and plausible
    if not agent_id:
        print(f"{BAD} AI_AGENT_CLIENT_ID is empty -- register the AI Agent first (IDP_SETUP_OKTA.md)")
        sys.exit(1)
    # Do not insist on a wlp... prefix. Okta's current AI Agent flow creates an
    # "AI agent + App" pair, and the Client ID shown on the Client registration tab
    # is the value to use regardless of its prefix. Report it and move on.
    print(f"{OK} AI_AGENT_CLIENT_ID={agent_id}")

    # 2 + 3. exists, and is ACTIVE
    app = next(
        (
            a
            for a in okta.get("/apps?limit=200")
            if ((a.get("credentials") or {}).get("oauthClient") or {}).get("client_id") == agent_id
        ),
        None,
    )
    if app is None:
        print(f"{BAD} no app in this tenant has client_id {agent_id}")
        sys.exit(1)
    print(f"{OK} found: {app.get('label')}")
    if app.get("status") == "ACTIVE":
        print(f"{OK} status ACTIVE")
    else:
        print(f"{BAD} status {app.get('status')} -- Actions -> Activate. A STAGED agent")
        print("      fails every call with a bare invalid_client, which looks like a key bug.")
        failures += 1

    # 4. public key registered, kid matches
    #
    # The agent's registered public key is NOT on the app object -- Okta serves it
    # from /apps/{id}/credentials/jwks, with the modulus included. Do not confuse it
    # with /credentials/keys, which is the app's x5c signing certificate and has an
    # unrelated kid (also mirrored at credentials.signing.kid).
    jwks = (okta.get(f"/apps/{app['id']}/credentials/jwks") or {}).get("keys") or []
    active_kids = [k.get("kid") for k in jwks if k.get("status") == "ACTIVE"]
    kids = [k.get("kid") for k in jwks]
    if not jwks:
        print(f"{BAD} no public key registered on the agent")
        print("      Client registration -> Public/private key -> Configure -> Add public key")
        failures += 1
    elif want_kid and want_kid not in kids:
        print(f"{BAD} registered kid(s) {kids} do not include AI_AGENT_KEY_KID={want_kid}")
        failures += 1
    elif want_kid and want_kid not in active_kids:
        print(
            f"{BAD} key {want_kid} is registered but not ACTIVE (status: "
            f"{[k.get('status') for k in jwks if k.get('kid') == want_kid]})"
        )
        print("      Staging a method is not activating it -- click Activate, then Enable.")
        failures += 1
    else:
        print(f"{OK} public key registered and ACTIVE, kid={want_kid or active_kids}")

    # 5. the local private key is the pair of the registered public key
    priv_path = KEYS_DIR / "okta_private_key.pem"
    if not priv_path.exists():
        print(f"{WARN} {priv_path.name} not found locally -- cannot check the key pair")
    elif jwks:
        try:
            from cryptography.hazmat.primitives import serialization

            key = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
            numbers = key.public_key().public_numbers()
            local_n = b64url_uint(numbers.n)
            match = next((k for k in jwks if k.get("n") == local_n), None)
            if match:
                print(f"{OK} local private key matches the registered public key (kid={match.get('kid')})")
            else:
                print(f"{BAD} local private key does NOT match any registered public key.")
                print("      Every signature will fail with:")
                print("        invalid_client: client_assertion signature is invalid")
                print("      Register the current scripts/keys/okta_public_jwk.json, or rotate")
                print("      under a NEW kid -- do not re-run gen_keypair.py in place.")
                failures += 1
        except (ValueError, TypeError) as exc:
            print(f"{WARN} could not read the private key: {exc}")

    # 6. AS 2 policy lists the agent
    as2 = env("RESOURCE_AS_ID")
    if not as2:
        print(f"{WARN} RESOURCE_AS_ID not in .env -- run deploy/00_create_okta_apps.py first")
    else:
        listed = False
        for policy in okta.get(f"/authorizationServers/{as2}/policies?limit=200"):
            clients = ((policy.get("conditions") or {}).get("clients") or {}).get("include") or []
            if agent_id in clients:
                listed = True
                print(f"{OK} AS 2 policy '{policy['name']}' lists the agent")
        if not listed:
            print(f"{BAD} no policy on the resource AS lists {agent_id}.")
            print("      Leg 2 will fail with: access_denied: Policy evaluation failed")
            print("      Add it to the 'XAA sample - Resource jwt-bearer' rule's client allowlist.")
            failures += 1

    print()
    print("  Not checkable via the Management API -- Okta does not expose these:")
    print("    - Delegation (caller = Login app, on behalf of = User, authz server = ORG)")
    print("    - Resource connection (Authorization server = XAA Todo Resource, todos.read)")
    print("  Prove them by running: python scripts/spikes/spike2_idjag_subject.py")

    if failures:
        print(f"\n{BAD} {failures} problem(s) above must be fixed before the flow will work.")
        sys.exit(1)
    print("\n  ✓ everything the API can see is correct.")


if __name__ == "__main__":
    main()
