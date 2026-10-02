"""Store the DCR-registered client id/secret on a dcr credential provider.

The second phase of Dynamic Client Registration. deploy_credential.py created
the provider with dummy credentials so it would vend a callbackUrl; after you
allowlist that domain and curl the DCR endpoint (RFC 7591), this script writes
the real client_id / client_secret back onto the same provider and switches its
discovery URL to the DCR-tenant one.

Only meaningful for authModel == "dcr" profiles. Running it against an oauth_app
profile (e.g. --github) is a runtime error -- there is nothing to update, the
provider already holds the hand-created credentials.

Reads the id from the profile's clientIdEnv; the secret from clientSecretEnv or,
if unset, an interactive getpass so it never has to appear in shell history.

Usage:
    uv run python scripts/deploy_credential_update.py --atlassian
"""

import getpass
import os
import sys

import boto3
from mcp_config import (
    credential_provider_name,
    get_required_env,
    load_env,
    select_profile,
)


def main():
    profile = select_profile(__doc__)

    if profile["authModel"] != "dcr":
        print(
            f"ERROR: --{profile['name']} uses authModel "
            f"{profile['authModel']!r}; there is nothing to update."
        )
        print(
            "  This step only applies to Dynamic Client Registration (dcr) providers."
        )
        sys.exit(1)

    load_env()

    client_id = get_required_env(profile["clientIdEnv"])
    client_secret = os.environ.get(profile["clientSecretEnv"]) or getpass.getpass(
        f"{profile['clientSecretEnv']} (DCR client_secret): "
    )
    if not client_secret:
        print(f"ERROR: {profile['clientSecretEnv']} not provided.")
        sys.exit(1)

    region = boto3.Session().region_name
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    provider_name = credential_provider_name(profile)

    print(
        f"--- Updating {profile['displayName']} credential provider with DCR client ---"
    )
    response = client.update_oauth2_credential_provider(
        name=provider_name,
        credentialProviderVendor=profile["vendor"],
        oauth2ProviderConfigInput={
            profile["providerConfigKey"]: {
                "clientId": client_id,
                "clientSecret": client_secret,
                # DCR-tenant discovery URL, not the generic one used at create.
                "oauthDiscovery": {"discoveryUrl": profile["dcrDiscoveryUrl"]},
            }
        },
    )

    print(f"  Provider name:  {provider_name}")
    print(f"  Credential ARN: {response['credentialProviderArn']}")
    print("  Stored the DCR client id/secret and switched the discovery URL.")
    print("\n  Next: attach the target with")
    print(f"    uv run python scripts/deploy_target_schema.py --{profile['name']}")


if __name__ == "__main__":
    main()
