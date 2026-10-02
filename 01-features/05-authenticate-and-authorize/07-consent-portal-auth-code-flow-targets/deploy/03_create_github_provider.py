"""Create the GitHub outbound OAuth2 credential provider.

This is the *outbound* provider — the downstream resource your agent acts on,
and the thing the user grants consent to on the portal's Connections page. It
is a different provider from the portal's primary IdP (step 02): the primary
IdP is who the user signs in AS, this is what the agent gets consent to act ON.

GitHub is a valid outbound vendor (`GithubOauth2`) but could never be the
primary IdP: it issues no ID token and publishes no OIDC discovery document,
so portal login cannot work with it by construction.

Before running: create a GitHub OAuth App at https://github.com/settings/developers
and put anything in its "Authorization callback URL" — you will replace it in a
moment with a URL AgentCore vends. Then export its credentials:

    export GITHUB_CLIENT_ID="<your-github-client-id>"
    export GITHUB_CLIENT_SECRET="<your-github-client-secret>"

CreateOauth2CredentialProvider returns a `callbackUrl` unique to this provider,
shaped

    https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/callback/<uuid>

You do NOT choose this URL. AgentCore owns the redirect endpoint because it is
AgentCore that exchanges the authorization code for a token. Paste it into the
GitHub OAuth App's Authorization callback URL.

Writes to .env: GITHUB_PROVIDER_ARN, GITHUB_PROVIDER_CALLBACK_URL.

Run from the sample root:
    python deploy/03_create_github_provider.py --entra
    python deploy/03_create_github_provider.py --okta
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    check_boto_version,
    control_client,
    find_provider,
    github_provider_name,
    load_env,
    must_env,
    must_secret_env,
    save_env,
    select_idp,
)

# The scopes the gateway requests from GitHub on the user's behalf. `repo` and
# `workflow` are broad for a tutorial — trim to what your tools actually need
# before reusing this anywhere real. The names shown to end users on the
# portal's Connections page come from the provider and target names, not from
# these scopes.
GITHUB_SCOPES = ["repo", "user", "workflow"]


def main() -> None:
    idp = select_idp(__doc__)
    check_boto_version()
    load_env()

    client_id = must_env("GITHUB_CLIENT_ID")
    client_secret = must_secret_env("GITHUB_CLIENT_SECRET")
    name = github_provider_name(idp)

    control, _ = control_client()

    if find_provider(control, name):
        # get_oauth2_credential_provider returns the same callbackUrl the
        # create call would have. Stored credentials are left unchanged.
        print(f"--- GitHub credential provider exists — reusing: {name} ---")
        response = control.get_oauth2_credential_provider(name=name)
    else:
        print(f"--- Creating GitHub OAuth2 credential provider: {name} ---")
        response = control.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="GithubOauth2",
            oauth2ProviderConfigInput={
                "githubOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                }
            },
        )
        print("  ✓ Created")

    provider_arn = response["credentialProviderArn"]
    identity_callback = response["callbackUrl"]

    print(f"  arn:      {provider_arn}")
    print(f"  scopes:   {' '.join(GITHUB_SCOPES)} (set on the target in step 04)")

    save_env(GITHUB_PROVIDER_ARN=provider_arn, GITHUB_PROVIDER_CALLBACK_URL=identity_callback)
    print("  Saved to .env: GITHUB_PROVIDER_ARN, GITHUB_PROVIDER_CALLBACK_URL")

    print()
    print("=" * 68)
    print("  *** ACTION REQUIRED — do this before step 04 ***")
    print()
    print("  Set your GitHub OAuth App's Authorization callback URL to:")
    print()
    print(f"    {identity_callback}")
    print()
    print("  at https://github.com/settings/developers")
    print()
    print("  Skipping this does NOT fail step 04. The target creates cleanly")
    print("  and stays READY. The failure appears only when a real user clicks")
    print("  Connect on the portal and GitHub rejects the redirect_uri — by")
    print("  which point it looks like a portal bug.")
    print("=" * 68)
    print()
    print(f"Next: python deploy/04_create_github_target.py --{idp['name']}")


if __name__ == "__main__":
    main()
