"""Create the AgentCore Identity credential provider for hop C's OBO exchange.

The agent calls `GetResourceOauth2Token(..., ON_BEHALF_OF_TOKEN_EXCHANGE)` and
AgentCore Identity performs an RFC 8693 exchange at AS 1 as the **Agent app**, turning
the user's `T_user` into `T_gateway` with `scp=tools.access`. The agent never holds the
Agent app's secret.

Why `CustomOauth2` rather than the built-in `OktaOauth2` vendor: only the custom config
exposes `onBehalfOfTokenExchangeConfig`, which is where the grant type and
`actorTokenContent` live. The built-in vendor has no way to express them.

Two details that are easy to get wrong:

  * `grantType` is `TOKEN_EXCHANGE`. Okta implements RFC 8693, not
    JWT_AUTHORIZATION_GRANT, for this hop. (`JWT_AUTHORIZATION_GRANT` exists in the
    enum and is what ID-JAG leg 2 uses -- but that leg cannot be driven through a
    credential provider at all, which is why the interceptor does it directly.)
  * `actorTokenContent: NONE` -- no actor token is sent, so the resulting `T_gateway`
    carries no nested `act` claim. Sending one would need extra Okta trust setup.

Writes AGENT_OBO_PROVIDER_ARN to .env.

    python deploy/04_create_obo_provider.py
    python deploy/04_create_obo_provider.py --rotate-secret
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    clients,
    discovery_url,
    env,
    load_env,
    must_env,
    obo_provider_name,
    region,
    save_env,
)


def ensure_workload_identity(aws, name: str) -> str:
    """Create the workload identity the agent names in GetWorkloadAccessTokenForJWT.

    This is NOT created by the AgentCore CLI, and its absence fails at runtime with

        AccessDeniedException ... GetWorkloadAccessTokenForJWT ...
        Workload Identity does not belong to caller account

    which reads like an IAM or cross-account problem rather than a missing resource.
    The name must match AGENT_WORKLOAD_NAME in the agent's environment.
    """
    from botocore.exceptions import ClientError

    try:
        arn = aws["acc"].create_workload_identity(name=name)["workloadIdentityArn"]
        print(f"  ✓ created workload identity {name}")
        return arn
    except ClientError as exc:
        msg = exc.response["Error"].get("Message", "")
        if (
            exc.response["Error"]["Code"] not in ("ConflictException", "ResourceAlreadyExistsException")
            and "already exists" not in msg.lower()
        ):
            raise
        arn = aws["acc"].get_workload_identity(name=name)["workloadIdentityArn"]
        print(f"  • workload identity {name} exists")
        return arn


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rotate-secret",
        action="store_true",
        help="Push the current AGENT_APP_CLIENT_SECRET again (use after rotating it in Okta).",
    )
    args = ap.parse_args()
    load_env()
    aws = clients()
    name = obo_provider_name()

    config = {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": discovery_url(must_env("AGENTCORE_AS_ISSUER"))},
            "clientId": must_env("AGENT_APP_CLIENT_ID"),
            "clientSecret": must_env(
                "AGENT_APP_CLIENT_SECRET", "Re-run deploy/00_create_okta_apps.py --rotate-secrets."
            ),
            "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
            "onBehalfOfTokenExchangeConfig": {
                "grantType": "TOKEN_EXCHANGE",
                "tokenExchangeGrantTypeConfig": {"actorTokenContent": "NONE"},
            },
        }
    }

    print(f"region {region()}\nprovider {name}\n")
    print("[1/2] Workload identity")
    wi_arn = ensure_workload_identity(aws, env("AGENT_WORKLOAD_NAME", "xaa-todo-agent"))
    save_env(AGENT_WORKLOAD_IDENTITY_ARN=wi_arn)

    print("\n[2/2] Credential provider")
    existing = None
    try:
        existing = aws["acc"].get_oauth2_credential_provider(name=name)
    except aws["acc"].exceptions.ResourceNotFoundException:
        pass

    if existing and not args.rotate_secret:
        arn = existing["credentialProviderArn"]
        print(f"  • reusing existing provider\n    {arn}")
        out = existing.get("oauth2ProviderConfigOutput", {}).get("customOauth2ProviderConfig", {})
        print(f"    clientId: {out.get('clientId')}")
        print(f"    discovery: {(out.get('oauthDiscovery') or {}).get('discoveryUrl')}")
        obo = out.get("onBehalfOfTokenExchangeConfig") or {}
        print(
            f"    obo: grantType={obo.get('grantType')} "
            f"actorTokenContent={(obo.get('tokenExchangeGrantTypeConfig') or {}).get('actorTokenContent')}"
        )
    elif existing:
        arn = aws["acc"].update_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )["credentialProviderArn"]
        print(f"  ✓ updated provider (secret refreshed)\n    {arn}")
    else:
        arn = aws["acc"].create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )["credentialProviderArn"]
        print(f"  ✓ created provider\n    {arn}")

    save_env(AGENT_OBO_PROVIDER_ARN=arn)
    print("\n  Next: deploy the agent — see README.md 'Deploy the agent and the BFF'")


if __name__ == "__main__":
    main()
