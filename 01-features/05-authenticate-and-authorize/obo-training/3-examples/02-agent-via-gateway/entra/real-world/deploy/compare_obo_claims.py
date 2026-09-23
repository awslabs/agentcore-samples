"""
Decode and diff the three tokens in the Use Case 2 OBO chain.

Reads the most recent T_user from a frontend session export (or stdin) plus
re-runs the agent flow's token mints to print T_user, T_gateway, and T_graph
side by side.

The point is to *see* the audience rotation and the constant `oid` claim
across all three hops — the central observation in this use case.

Usage:
    # Capture a T_user once via the BFF (it logs the token at level=DEBUG)
    # and feed it in here:
    python deploy/compare_obo_claims.py --user-token "<eyJ…>"

    # Or read from a file:
    python deploy/compare_obo_claims.py --user-token-file /tmp/t_user.txt

The script then performs OBO #1 itself (agent's perspective) to show
T_gateway. T_graph would normally only ever exist inside the Gateway; we
*can't* print it without invasive instrumentation. To partially compensate,
we make the OBO #2 exchange directly from this script using the
gateway-actor provider — the resulting token is functionally identical to
what the Gateway would mint.

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


CLAIMS_OF_INTEREST = (
    "iss",
    "aud",
    "azp",
    "appid",
    "oid",
    "sub",
    "scp",
    "upn",
    "preferred_username",
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
        help="The inbound user JWT (T_user). aud should be AGENT_CLIENT_ID.",
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
    # with garbage. Common learner mistake: pasting "<placeholder>" text or
    # a curl error message like "not signed in" as the token.
    if not user_jwt or len(user_jwt) < 500 or not user_jwt.startswith("eyJ"):
        preview = user_jwt[:60].replace("\n", " ")
        print(
            f"ERROR: The provided user token does not look like a valid JWT.\n"
            f"       length={len(user_jwt)}  starts_with={preview!r}\n"
            f"\n"
            f"       A valid Entra JWT is typically 1500-2500+ chars and starts "
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

    ac = boto3.client("bedrock-agentcore", region_name=region)

    # T_user — already in hand.
    print_claims("T_user (held by BFF after sign-in)", decode_jwt_payload(user_jwt))

    # OBO #1: T_user → T_gateway (this is what the agent does).
    workload_token = ac.get_workload_access_token_for_jwt(workloadName=workload_name, userToken=user_jwt)[
        "workloadAccessToken"
    ]
    t_gateway = ac.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=agent_provider,
        scopes=[gateway_scope],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        # Required for Microsoft OBO — see agent/agent.py for the full note.
        customParameters={"requested_token_use": "on_behalf_of"},
    )["accessToken"]
    print_claims(
        "T_gateway (after OBO #1, used by agent → Gateway)",
        decode_jwt_payload(t_gateway),
    )

    # OBO #2: T_gateway → T_graph (this is what the Gateway does internally).
    # We re-do it here for visibility. In the real flow, we don't see this
    # token from outside the Gateway.
    workload_token2 = ac.get_workload_access_token_for_jwt(workloadName=workload_name, userToken=t_gateway)[
        "workloadAccessToken"
    ]
    t_graph = ac.get_resource_oauth2_token(
        workloadIdentityToken=workload_token2,
        resourceCredentialProviderName=gateway_provider,
        scopes=["https://graph.microsoft.com/.default"],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        customParameters={"requested_token_use": "on_behalf_of"},
    )["accessToken"]
    print_claims("T_graph (after OBO #2, used by Gateway → Graph)", decode_jwt_payload(t_graph))

    print("\n--- Summary ---")
    print("Watch the `aud` claim rotate across the three tokens (agent → gateway → graph).")
    print("Watch the `azp`/`appid` claim rotate (frontend → agent → gateway).")
    print("Watch the `oid` claim STAY THE SAME — that's the user identity propagating.")
    print("`sub` will differ at every hop (Entra mints a new PPID per audience).")


if __name__ == "__main__":
    main()
