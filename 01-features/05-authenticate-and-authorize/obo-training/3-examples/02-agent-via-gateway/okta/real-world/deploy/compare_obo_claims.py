"""
Decode and diff the three tokens in the Use Case 2 (Okta) OBO chain.

Reads the most recent T_user (from a shell variable or file), then re-runs
the agent flow's token mints to produce T_gateway and T_downstream, and
prints all three side-by-side.

The point is to *see* the actor rotation (`cid` walks frontend -> agent
-> gateway) and the constant user identity (`sub` and `uid` stay the same)
across all three hops — the central observation in this use case.

Usage:
    # After signing in via the BFF, visit http://localhost:8000/debug/token
    # and copy the token into a shell variable:
    T_USER="eyJhbGci..."
    python deploy/compare_obo_claims.py --user-token "$T_USER"

    # Or read from a file:
    python deploy/compare_obo_claims.py --user-token-file /tmp/t_user.txt

The script performs OBO #1 and OBO #2 itself (from your local AWS
credentials) to show what each layer sees. In the real flow, T_downstream
only ever exists inside the Gateway — we replicate it locally to make the
claims visible.

This script is read-only against AWS APIs apart from the two OBO calls.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import boto3
from dotenv import load_dotenv


# Claims of interest for the side-by-side print. Okta-flavored:
#   iss, aud     — usual OIDC identifiers
#   cid          — client that requested the token (the actor)
#   sub          — user login; the seam claim that stays constant
#   uid          — Okta user's internal ID; also constant
#   scp          — scopes granted on the token
#   exp          — expiry
CLAIMS_OF_INTEREST = (
    "iss",
    "aud",
    "cid",
    "sub",
    "uid",
    "scp",
    "exp",
)


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying the signature."""
    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception as e:
        return {"_decode_error": str(e)}


def print_claims(label: str, claims: dict[str, Any]) -> None:
    print(f"\n=== {label} ===")
    for k in CLAIMS_OF_INTEREST:
        if k in claims:
            v = claims[k]
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            print(f"  {k:<10}: {v}")
    extra = sorted(set(claims) - set(CLAIMS_OF_INTEREST))
    if extra:
        print(f"  (other  : {', '.join(extra)})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-token",
        help="The inbound user JWT (T_user). aud should be OKTA_AUDIENCE.",
    )
    parser.add_argument(
        "--user-token-file",
        help="Path to a file containing the inbound user JWT.",
    )
    args = parser.parse_args()

    if args.user_token and args.user_token_file:
        parser.error("Pass either --user-token or --user-token-file, not both.")
    if not args.user_token and not args.user_token_file:
        parser.error("One of --user-token or --user-token-file is required.")

    user_jwt = args.user_token if args.user_token else Path(args.user_token_file).read_text().strip()

    # Validate the input actually looks like a JWT before we call AWS APIs
    # with garbage.
    if not user_jwt or len(user_jwt) < 300 or not user_jwt.startswith("eyJ"):
        preview = user_jwt[:60].replace("\n", " ")
        print(
            f"ERROR: The provided user token does not look like a valid JWT.\n"
            f"       length={len(user_jwt)}  starts_with={preview!r}\n"
            f"\n"
            f"       A valid Okta JWT is typically 800-1500 chars and starts "
            f"with 'eyJ'.\n"
            f"\n"
            f"How to get one:\n"
            f"       1. Ensure the BFF is running and you're signed in in the browser.\n"
            f"       2. Visit http://localhost:8000/debug/token in the browser.\n"
            f"       3. Triple-click the textarea, copy the whole token.\n"
            f"       4. Paste it as the argument between quotes:\n"
            f'            python deploy/compare_obo_claims.py --user-token "eyJ..."',
            file=sys.stderr,
        )
        sys.exit(1)

    real_world_root = Path(__file__).resolve().parent.parent
    load_dotenv(real_world_root / ".env")
    region = os.environ.get("AWS_REGION", "us-west-2")
    workload_name = must_env("AGENT_WORKLOAD_NAME")
    agent_provider = must_env("AGENT_OBO_PROVIDER_NAME")
    gateway_provider = must_env("GATEWAY_OBO_PROVIDER_NAME")
    gateway_scope = must_env("GATEWAY_SCOPE")
    downstream_scope = must_env("DOWNSTREAM_SCOPE")
    okta_audience = must_env("OKTA_AUDIENCE")

    ac = boto3.client("bedrock-agentcore", region_name=region)

    # T_user — already in hand.
    print_claims("T_user (held by BFF after sign-in)", decode_jwt_payload(user_jwt))

    # OBO #1: T_user -> T_gateway (this is what the agent does).
    workload_token = ac.get_workload_access_token_for_jwt(workloadName=workload_name, userToken=user_jwt)[
        "workloadAccessToken"
    ]
    t_gateway = ac.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=agent_provider,
        scopes=[gateway_scope],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        # Same customParameters + audiences as the agent's OBO #1 call.
        customParameters={
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        audiences=[okta_audience],
    )["accessToken"]
    print_claims(
        "T_gateway (after OBO #1, used by agent -> Gateway)",
        decode_jwt_payload(t_gateway),
    )

    # OBO #2: T_gateway -> T_downstream (this is what the Gateway does internally).
    # We re-do it here for visibility. In the real flow, T_downstream only
    # ever exists inside the Gateway boundary.
    workload_token2 = ac.get_workload_access_token_for_jwt(workloadName=workload_name, userToken=t_gateway)[
        "workloadAccessToken"
    ]
    t_downstream = ac.get_resource_oauth2_token(
        workloadIdentityToken=workload_token2,
        resourceCredentialProviderName=gateway_provider,
        scopes=[downstream_scope],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        customParameters={
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        audiences=[okta_audience],
    )["accessToken"]
    print_claims(
        "T_downstream (after OBO #2, used by Gateway -> mock downstream)",
        decode_jwt_payload(t_downstream),
    )

    print("\n--- Summary ---")
    print("Watch the `cid` claim rotate (FrontendApp -> AgentApp -> GatewayApp) —")
    print("that's the actor walking down the chain.")
    print("Watch the `sub` and `uid` claims STAY THE SAME — that's the user identity")
    print("propagating across all three hops.")
    print("Watch the `aud` claim stay constant at OKTA_AUDIENCE — Okta's default auth")
    print("server always mints tokens with the same audience regardless of client.")
    print("Watch the `scp` claim narrow across hops (agent.access -> gateway.access ->")
    print("downstream.access) — each layer requests only the scope it needs.")


if __name__ == "__main__":
    main()
