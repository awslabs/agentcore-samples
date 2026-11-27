# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3==1.43.36", "botocore==1.43.36", "jmespath==1.0.1", "s3transfer==0.19.0", "python-dateutil==2.9.0.post0", "six==1.17.0", "urllib3==2.5.0"]
# ///
"""Reserve a uniquely tagged OAuth provider BEFORE CLI's name-based upsert.

Only fresh labs and explicit reuse of this ledger's owned provider are supported.
Never imports a pre-existing provider, rotates a secret, or erases CLI state.
Run one provisioning process at a time. Requires cloud authorization.
"""

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

import boto3
from policy_contract import API_VERSION, verified_policy_session
from resource_state import OWNER_TAG, atomic_save, read_present, require_equal

STATE_FILE = Path(__file__).parent / "oauth_provider_state.json"


def verify_provider(ctrl, state):
    current = ctrl.get_oauth2_credential_provider(name=state["name"])
    prefix = f"arn:aws:bedrock-agentcore:{state['region']}:{state['account_id']}:"
    require_equal(current.get("name"), state["name"], "OAuth name")
    require_equal(
        current.get("credentialProviderArn"),
        prefix + "token-vault/default/oauth2credentialprovider/" + state["name"],
        "OAuth ARN",
    )
    # GetOauth2CredentialProvider does not return tags in the pinned SDK model.
    # Read them separately only after validating the exact account/region/name ARN.
    tags = ctrl.list_tags_for_resource(resourceArn=current["credentialProviderArn"]).get("tags", {})
    require_equal(tags.get(OWNER_TAG), state["ownership_id"], "OAuth ownership")
    require_equal(current.get("credentialProviderVendor"), "CustomOauth2", "OAuth vendor")
    config = current.get("oauth2ProviderConfigOutput", {}).get("customOauth2ProviderConfig", {})
    require_equal(config.get("clientId"), state["client_id"], "OAuth client")
    require_equal(config.get("oauthDiscovery", {}).get("discoveryUrl"), state["discovery_url"], "OAuth discovery")
    secret = current.get("clientSecretArn", {}).get("secretArn", "")
    if not secret.startswith(f"arn:aws:secretsmanager:{state['region']}:{state['account_id']}:secret:") or any(
        c in secret for c in "*?"
    ):
        raise RuntimeError("Missing or invalid exact OAuth secret ARN")
    if not current.get("createdTime"):
        raise RuntimeError("Missing OAuth creation identity")
    identity = {
        "provider_arn": current["credentialProviderArn"],
        "secret_arn": secret,
        "created_time": current["createdTime"].isoformat(),
    }
    for key, value in identity.items():
        if key in state:
            require_equal(value, state[key], "OAuth " + key)
    return identity


def prepare(ctrl, account_id, region, client_id, discovery_url, secret, *, reuse_owned=False, name=None):
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None
    if state is None:
        if reuse_owned:
            raise RuntimeError("No OAuth ownership ledger; intentional reuse cannot import a provider")
        name = name or "banking-oauth-" + uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            raise ValueError("Invalid OAuth provider name")
        state = {
            "name": name,
            "account_id": account_id,
            "region": region,
            "client_id": client_id,
            "discovery_url": discovery_url,
            "ownership_id": str(uuid.uuid4()),
        }
        # Includes the unique tag before the first request, permitting response-loss recovery.
        atomic_save(STATE_FILE, state)
    else:
        for key, value in (
            ("account_id", account_id),
            ("region", region),
            ("client_id", client_id),
            ("discovery_url", discovery_url),
        ):
            require_equal(state[key], value, "OAuth " + key)
        if name:
            require_equal(state["name"], name, "OAuth selected name")
    current = read_present(lambda: ctrl.get_oauth2_credential_provider(name=state["name"]))
    if current is not None:
        # Even same-vendor/same-config collisions must prove our pre-request tag.
        identity = verify_provider(ctrl, state)
        if not reuse_owned:
            raise RuntimeError("Owned provider exists; use --reuse-owned intentionally (no secret update)")
    else:
        if state.get("provider_arn") or reuse_owned:
            raise RuntimeError("Owned OAuth provider absent; retain state and audit, do not recreate")
        if not secret:
            raise ValueError("Fresh OAuth reservation requires client secret on stdin")
        # Create only: a collision between GET and CREATE fails, NEVER calls update.
        ctrl.create_oauth2_credential_provider(
            name=state["name"],
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": secret,
                    "oauthDiscovery": {"discoveryUrl": discovery_url},
                }
            },
            tags={OWNER_TAG: state["ownership_id"]},
        )
        identity = verify_provider(ctrl, state)
    atomic_save(STATE_FILE, {**state, **identity})
    return state["name"]


def main():
    sdk = verified_policy_session()  # Fail before reservation/STS if model selection diverges.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reuse-owned",
        action="store_true",
        help="Verify/recover only the originally tagged provider; never import or rotate",
    )
    args = parser.parse_args()
    region = os.environ.get("REGION", "us-east-1")
    session = boto3.Session(botocore_session=sdk, region_name=region)
    name = prepare(
        session.client("bedrock-agentcore-control", api_version=API_VERSION),
        session.client("sts").get_caller_identity()["Account"],
        region,
        os.environ["MCP_CLIENT_ID"],
        os.environ["DISCOVERY_URL"],
        "" if args.reuse_owned else sys.stdin.read(),
        reuse_owned=args.reuse_owned,
        name=os.environ.get("MCP_CREDENTIAL_NAME"),
    )
    print(name)


if __name__ == "__main__":
    main()
