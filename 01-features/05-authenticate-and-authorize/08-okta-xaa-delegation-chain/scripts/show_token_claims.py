"""Show the claims of every token in the chain, side by side.

    .venv/bin/python scripts/show_token_claims.py

Signs you in, then walks hops A and C and decodes what each step produced:

    T_id       the ID token from the AI Agent's linked app
    T_user     the access token that invokes the runtime   (scp=agent.access)
    T_gateway  the OBO exchange's output                   (scp=tools.access)

`T_tool` (hop D) is not minted here -- the interceptor already logs its claims,
including the `act` claim, which `scripts/show_trace.py` prints.

Why this exists: nothing in the deployed chain logs these claims. The interceptor logs
hop D, but the OBO token at hop C is never written down anywhere, so "does the OBO step
keep the user?" could only be answered indirectly before this.

It prints CLAIMS ONLY -- never token material. The signature and payload bytes of a live
credential have no business in a terminal or a transcript.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _common import env, load_env, must_env, okta_org_url, region

sys.path.insert(0, str(Path(__file__).resolve().parent))
from okta_signin import sign_in

# Claims worth showing, in a deliberate order: who, for whom, where, what.
INTERESTING = ["iss", "aud", "sub", "cid", "client_id", "act", "scp", "scope", "uid", "exp", "iat"]


def claims(token: str) -> dict:
    """Decode a JWT payload without verifying it.

    Safe here because we are inspecting tokens we just obtained over TLS, not making a
    trust decision. Anything that *authorizes* on these claims must verify the signature.
    """
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def header(token: str) -> dict:
    part = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def show(label: str, token: str, note: str = "") -> dict:
    c = claims(token)
    h = header(token)
    print(f"\n  ══ {label} ══")
    if note:
        print(f"     {note}")
    print(f"     header: alg={h.get('alg')} kid={str(h.get('kid'))[:24]}")
    for k in INTERESTING:
        if k in c:
            v = c[k]
            print(f"     {k:10} = {json.dumps(v) if isinstance(v, (dict, list)) else v}")
    extra = [k for k in c if k not in INTERESTING]
    if extra:
        print(f"     (other claims: {', '.join(sorted(extra))})")
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args()
    load_env()

    print("Signing in -- a browser will open.")
    tokens = sign_in(
        okta_org_url(),
        must_env("LOGIN_CLIENT_ID"),
        env("FRONTEND_REDIRECT_URI", "http://localhost:8000/auth/callback"),
        f"openid profile email {env('SCOPE_AGENT_ACCESS', 'agent.access')}",
        must_env("AGENTCORE_AS_ISSUER"),
    )
    t_id, t_user = tokens["id_token"], tokens["access_token"]

    show("T_id  (hop A: ID token from the linked app)", t_id, "leg 1 takes this in id_token mode")
    show("T_user  (hop B: invokes the runtime)", t_user, "scp=agent.access; aud is AGENTCORE_AUDIENCE")

    # Hop C, exactly as agent.py does it.
    idp = boto3.client("bedrock-agentcore", region_name=region())
    wat = idp.get_workload_access_token_for_jwt(
        workloadName=env("AGENT_WORKLOAD_NAME", "xaa-todo-agent"), userToken=t_user
    )["workloadAccessToken"]
    t_gateway = idp.get_resource_oauth2_token(
        workloadIdentityToken=wat,
        resourceCredentialProviderName=env("AGENT_OBO_PROVIDER_NAME", "xaa-agent-obo-provider"),
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        scopes=[env("SCOPE_TOOLS_ACCESS", "tools.access")],
        audiences=[must_env("AGENTCORE_AUDIENCE")],
        customParameters={"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"},
    )["accessToken"]
    cg = show(
        "T_gateway  (hop C: the OBO exchange's output)",
        t_gateway,
        "Cedar evaluates this, and leg 1 exchanges it by default",
    )

    cu = claims(t_user)
    print("\n  ── did the OBO exchange keep the user? ──")
    print(f"     T_user.sub    = {cu.get('sub')}")
    print(f"     T_gateway.sub = {cg.get('sub')}")
    print(f"     same subject  = {cu.get('sub') == cg.get('sub')}")
    print(f"     cid rotated   = {cu.get('cid')} -> {cg.get('cid')}")
    print(f"     scope narrowed= {cu.get('scp')} -> {cg.get('scp')}")
    print(f"     act present   = {'act' in cg}  (no actor token is sent at this hop)")

    print("\n  T_tool's claims are logged by the interceptor -- see scripts/show_trace.py,")
    print("  which prints sub, cid and act_sub for every ID-JAG exchange.")


if __name__ == "__main__":
    main()
