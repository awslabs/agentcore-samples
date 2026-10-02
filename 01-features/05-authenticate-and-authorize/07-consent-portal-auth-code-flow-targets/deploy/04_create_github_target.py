"""Attach the GitHub MCP server as an authorization-code-flow target (schema upfront).

Creates one gateway target pointing at https://api.githubcopilot.com/mcp with:

  * `mcpToolSchema.inlinePayload` — the tool schema supplied UPFRONT, from
    gateway/github-tools.json. The alternative (implicit sync) makes the
    gateway fetch the tool list at create time, which would require an admin
    to complete a three-legged OAuth flow right then — the exact thing the
    consent portal exists to avoid. Supplying the schema means the target is
    immediately READY and `tools/list` works for everyone without anyone
    authorizing anything; only `tools/call` triggers the consent flow.

  * `grantType: AUTHORIZATION_CODE` — three-legged, per-user consent. This is
    what puts a row on the portal's Connections page. A CLIENT_CREDENTIALS
    (two-legged, machine-to-machine) target needs no per-user consent and
    never appears there.

  * `defaultReturnUrl` = exactly `<portalUrl>/connect/callback`, read from
    PORTAL_CONNECT_RETURN_URL. This is the consent leg, and it is distinct
    from the portal's login callback (`<portalUrl>/callback`). If it is
    missing or wrong, the user authorizes at GitHub successfully and then gets
    returned somewhere other than the portal, so the consent never binds to
    their session — a failure that presents as "consent did nothing".

Three different URLs are involved in this sample, none interchangeable:

  | URL                                            | Registered with          |
  |------------------------------------------------|--------------------------|
  | https://<portalUrl>/callback                   | the primary IdP app      |
  | https://<portalUrl>/connect/callback           | nobody — set here        |
  | https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/callback/<uuid> | the GitHub OAuth App |

This script requires PORTAL_CONNECT_RETURN_URL: the sample is portal-only, so
there is deliberately no localhost-callback-server fallback.

Writes to .env: GITHUB_TARGET_ID.

Run from the sample root:
    python deploy/04_create_github_target.py --entra
    python deploy/04_create_github_target.py --okta
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    SAMPLE_ROOT,
    check_boto_version,
    control_client,
    find_target_by_name,
    github_target_name,
    load_env,
    must_env,
    save_env,
    select_idp,
)

# Kept in step with 03_create_github_provider.py's GITHUB_SCOPES. Broad for a
# tutorial — trim to what your tools actually need before reusing this.
GITHUB_SCOPES = ["repo", "user", "workflow"]

GITHUB_MCP_ENDPOINT = "https://api.githubcopilot.com/mcp"
TOOL_SCHEMA_PATH = SAMPLE_ROOT / "gateway" / "github-tools.json"


def main() -> None:
    idp = select_idp(__doc__)
    check_boto_version()
    load_env()

    gateway_id = must_env("GATEWAY_ID")
    provider_arn = must_env("GITHUB_PROVIDER_ARN")
    return_url = must_env("PORTAL_CONNECT_RETURN_URL")
    target_name = github_target_name(idp)

    if not TOOL_SCHEMA_PATH.exists():
        print(f"ERROR: tool schema not found: {TOOL_SCHEMA_PATH}", file=sys.stderr)
        sys.exit(1)
    tool_schema = TOOL_SCHEMA_PATH.read_text()

    control, _ = control_client()

    existing = find_target_by_name(control, gateway_id, target_name)
    if existing:
        target_id = existing["targetId"]
        print(f"--- GitHub target exists — reusing: {target_name} ({target_id}) ---")
        # Report the live defaultReturnUrl: a target created before the portal
        # existed carries a different one, and its consent will never bind to
        # the portal session until it is updated.
        configs = existing.get("credentialProviderConfigurations") or []
        for cfg in configs:
            oauth = (cfg.get("credentialProvider") or {}).get("oauthCredentialProvider") or {}
            live = oauth.get("defaultReturnUrl")
            if live and live != return_url:
                print(
                    f"  ⚠ defaultReturnUrl is {live}\n"
                    f"    but this portal expects {return_url}\n"
                    f"    Consent will not bind to the portal session until it matches.\n"
                    f"    Delete the target and re-run this script, or update it with\n"
                    f"    UpdateGatewayTarget.",
                    file=sys.stderr,
                )
        save_env(GITHUB_TARGET_ID=target_id)
        print("  Saved to .env: GITHUB_TARGET_ID")
        return

    print(f"--- Creating GitHub MCP target: {target_name} (schema upfront) ---")
    print(f"  endpoint:         {GITHUB_MCP_ENDPOINT}")
    print("  grantType:        AUTHORIZATION_CODE (appears on the portal)")
    print(f"  scopes:           {' '.join(GITHUB_SCOPES)}")
    print(f"  defaultReturnUrl: {return_url}")
    print("  No browser authorization happens during creation.")

    response = control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description=(
            "GitHub MCP server, outbound authorization code flow, consent granted on the AgentCore consent portal"
        ),
        targetConfiguration={
            "mcp": {
                "mcpServer": {
                    "endpoint": GITHUB_MCP_ENDPOINT,
                    "mcpToolSchema": {"inlinePayload": tool_schema},
                }
            }
        },
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": provider_arn,
                        "scopes": GITHUB_SCOPES,
                        "grantType": "AUTHORIZATION_CODE",
                        "defaultReturnUrl": return_url,
                    }
                },
            }
        ],
    )
    target_id = response["targetId"]
    print(f"  ✓ Created. ID: {target_id}  status: {response['status']}")

    save_env(GITHUB_TARGET_ID=target_id)
    print("  Saved to .env: GITHUB_TARGET_ID")

    print()
    print("=" * 68)
    print("  Consent as a user now:")
    print(f"    open https://{must_env('PORTAL_URL')}")
    print()
    print("  1. Sign in with your IdP user.")
    print("  2. A GitHub row appears on the Connections page. A NEW target does")
    print("     not appear immediately — the connections list is cached for up")
    print("     to 5 MINUTES. An empty page right now is expected; wait it out")
    print("     before debugging.")
    print("  3. Choose Connect. GitHub asks you to authorize, then returns you")
    print("     to /connect/callback, where the portal calls")
    print("     CompleteResourceTokenAuth to bind that consent to YOU.")
    print()
    print("  Notes: authorization URLs and session URIs are valid for 10")
    print("  minutes — an interrupted flow fails in a way that looks like a")
    print("  config error, so start over rather than debugging it. GitHub also")
    print("  does not re-prompt once you have authorized an OAuth App, so Connect")
    print("  may complete with no visible consent screen at all.")
    print()
    print("  Consent is per user and there is NO Disconnect action on the portal,")
    print("  so to show the unconsented path again, sign in as another IdP user.")
    print("=" * 68)
    print()
    print("Next: deploy the agent — see README.md step 7 (agentcore create/deploy),")
    print(f"  then python deploy/05_patch_agentcore_json.py --{idp['name']}")


if __name__ == "__main__":
    main()
