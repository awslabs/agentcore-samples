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


def summarise() -> None:
    """Print the provider's non-sensitive settings.

    Deliberately reads from .env and from the literals below rather than from the
    GetOauth2CredentialProvider response. Those two agree, and .env is the source both
    were built from -- but a dict that holds `clientSecret` cannot be shown to be safe
    to print one key at a time, because taint analysis cannot tell the keys apart. This
    avoids the question instead of arguing with it.
    """
    print(f"    clientId:  {must_env('AGENT_APP_CLIENT_ID')}")
    print(f"    discovery: {discovery_url(must_env('AGENTCORE_AS_ISSUER'))}")
    print("    obo:       grantType=TOKEN_EXCHANGE actorTokenContent=NONE")


def provider_config() -> dict:
    """The provider config, including the Agent app's client secret.

    Kept in its own function so the secret is never a local in the same scope as the
    code that prints a summary -- which is both clearer and keeps static analysis from
    flagging an adjacent print as clear-text logging.
    """
    return {
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

    print(f"region {region()}\nprovider {name}\n")
    # No workload identity is created here. Runtime creates and manages one for the agent
    # and delivers its workload access token to the agent as a request header, so a named
    # identity of our own would be unused -- see README "Why the agent does not fetch its
    # own workload access token".
    print("[1/1] Credential provider")
    existing = None
    try:
        existing = aws["acc"].get_oauth2_credential_provider(name=name)
    except aws["acc"].exceptions.ResourceNotFoundException:
        pass

    if existing and not args.rotate_secret:
        arn = existing["credentialProviderArn"]
        print(f"  • reusing existing provider\n    {arn}")
        summarise()
    elif existing:
        # Build the config only in the branches that send it, so the client secret is
        # never in scope while the summary above is printed.
        config = provider_config()
        arn = aws["acc"].update_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )["credentialProviderArn"]
        print(f"  ✓ updated provider (secret refreshed)\n    {arn}")
    else:
        config = provider_config()
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
