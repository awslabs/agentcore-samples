"""Primary-IdP configuration for the consent portal scripts.

Sibling to mcp_config.py, and deliberately the same shape. mcp_config.py answers
"which MCP server am I attaching?" from scripts/servers/<name>.json; this module
answers "which identity provider do end users sign in to?" from
scripts/idps/<name>.json. Two directories because the two things vary
independently: one gateway's portal can front a GitHub target under Entra ID
today and an Atlassian target under Okta tomorrow.

Every script that touches the portal takes a **required** --<idp> flag, one per
file in idps/, for the same reason the MCP scripts do: no default, no environment
fallback, because a script that guessed its IdP could delete the wrong portal.

The profile holds the IdP's *shape* -- vendor, which env vars carry its values,
how its discovery URL and scopes are spelled. The tenant-specific *values* stay
in the environment, because they belong to the reader's tenant and not to this
repository:

    ENTRA_DISCOVERY_URL   OIDC discovery document; MUST contain /v2.0/
    ENTRA_RESOURCE        idpConfig.audience -- the resource app GUID
    ENTRA_ALLOWED_SCOPES  space-separated, fully qualified
    PORTAL_CLIENT_ID      the portal's own confidential client app
    PORTAL_CLIENT_SECRET  its secret (getpass fallback; never printed)

Adding Okta is a new idps/okta.json plus a --okta flag that appears on its own.

The .env helpers are reused from mcp_config rather than re-rolled.
"""

import argparse
import getpass
import json
import os
import sys

from mcp_config import add_choice_flags, chosen_flag, get_required_env

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
IDPS_DIR = os.path.join(SCRIPTS_DIR, "idps")

# Absent any one of these a script would fail later inside a boto3 call, so check
# up front. Same discipline as mcp_config.REQUIRED_KEYS.
REQUIRED_KEYS = (
    "name",
    "displayName",
    "vendor",
    "providerConfigKey",
    "clientIdEnv",
    "clientSecretEnv",
    "discoveryUrlEnv",
    "audienceEnv",
    "scopesEnv",
    "gatewayScopes",
    "scopeTemplate",
    "resourcePrefix",
)


def available_idps():
    if not os.path.isdir(IDPS_DIR):
        return []
    return sorted(
        f[: -len(".json")] for f in os.listdir(IDPS_DIR) if f.endswith(".json")
    )


def load_idp(name):
    """Load scripts/idps/<name>.json, validating its required keys."""
    path = os.path.join(IDPS_DIR, f"{name}.json")

    if not os.path.exists(path):
        print(f"ERROR: no such IdP profile: {name}")
        print(f"  looked for: {path}")
        found = available_idps()
        print(f"  available:  {', '.join(found) if found else '(none)'}")
        sys.exit(1)

    with open(path) as f:
        idp = json.load(f)

    missing = [k for k in REQUIRED_KEYS if k not in idp]
    if missing:
        print(
            f"ERROR: profile {name}.json is missing required keys: {', '.join(missing)}"
        )
        sys.exit(1)

    return idp


def select_idp(description):
    """Parse argv for a required --<idp> flag and return that profile."""
    names = available_idps()
    if not names:
        print(f"ERROR: no IdP profiles found in {IDPS_DIR}")
        print("  Add an idps/<name>.json profile first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_choice_flags(parser, names, kind="IdP")
    args = parser.parse_args()
    return load_idp(chosen_flag(args, names))


# --- Derived resource names --------------------------------------------------
# Needed by the script that creates each resource and again by the one that
# deletes it. Derived from the profile so the pair agrees by construction; when
# they were two separate literals, drift silently orphaned an IAM role.


def portal_name(idp):
    return os.environ.get("PORTAL_NAME", f"{idp['resourcePrefix']}-consent-portal")


def idp_provider_name(idp):
    return os.environ.get(
        "IDP_PROVIDER_NAME", f"{idp['resourcePrefix']}-portal-idp-provider"
    )


def portal_role_name(idp):
    default = f"{idp['resourcePrefix'].capitalize()}ConsentPortalRole"
    return os.environ.get("PORTAL_ROLE_NAME", default)


def gateway_name(idp):
    """The gateway deploy_gateway_entra.py creates for this IdP.

    Distinct from mcp_config.gateway_name, which prefixes with the MCP server. An
    inbound-auth gateway belongs to its IdP, not to any one target, so the name
    comes from here.
    """
    return f"{idp['resourcePrefix']}-auth-code-gateway"


def interceptor_lambda_name(idp):
    """The Lambda deploy_interceptor.py wires onto this IdP's gateway.

    Derived from the same prefix as the gateway so the deploy and cleanup sites
    agree by construction. Env-overridable for the rare case of a pre-existing
    function name.
    """
    return os.environ.get(
        "INTERCEPTOR_LAMBDA_NAME", f"{idp['resourcePrefix']}-jit-interceptor"
    )


def interceptor_role_name(idp):
    """Execution role for the interceptor Lambda (CloudWatch Logs only)."""
    default = f"{idp['resourcePrefix'].capitalize()}JitInterceptorRole"
    return os.environ.get("INTERCEPTOR_ROLE_NAME", default)


# --- IdP inputs --------------------------------------------------------------


def discovery_url(idp):
    """Return the IdP's discovery URL, enforcing the profile's required substring.

    For Entra that substring is /v2.0/, and it is not cosmetic: the v1.0 document
    advertises a different issuer than the tokens Entra actually issues, so
    validation fails later with insufficient_scope -- a failure that looks like a
    scope bug and is not one. Checking here turns it into an error at step one.
    """
    url = get_required_env(idp["discoveryUrlEnv"])
    required = idp.get("discoveryUrlMustContain")
    if required and required not in url:
        print(f"ERROR: {idp['discoveryUrlEnv']} must contain {required}")
        print(f"  got:      {url}")
        example = idp.get("discoveryUrlExample")
        if example:
            print(f"  expected: {example}")
        why = idp.get("discoveryUrlWhy")
        if why:
            print(f"  why:      {why}")
        sys.exit(1)
    return url


def audience(idp):
    return get_required_env(idp["audienceEnv"])


def portal_scopes(idp):
    """Return idpConfig.scopes, with openid guaranteed present.

    A consent portal always *requests* openid on top of the scopes you configure,
    and every scope it requests -- openid included -- must be defined and
    permitted on the IdP application or authorization fails with invalid_scope.
    The docs therefore say to list openid explicitly, so it is prepended here
    rather than left to the reader to remember.

    The qualified-scope warning is profile-driven. With Entra the gateway
    validates the short `access_as_user` from the token's scp claim, but Entra
    only accepts the fully qualified URI on /authorize, and the portal is not an
    MCP client so advertisedScopeMapping never reaches it. Other IdPs (Okta) use
    plain scope names and set requireQualifiedScopes false.
    """
    scopes = get_required_env(idp["scopesEnv"]).split()

    if idp.get("requireQualifiedScopes"):
        unqualified = [s for s in scopes if "/" not in s]
        if unqualified:
            print("WARNING: these scopes look unqualified, which the IdP may reject")
            print(f"         on /authorize: {', '.join(unqualified)}")
            example = idp.get("qualifiedScopeExample")
            if example:
                print(f"         Expected form: {example}")

    if "openid" not in scopes:
        scopes.insert(0, "openid")

    return scopes


def gateway_advertised_scope_mapping(idp):
    """Map each gateway-validated scope to the form clients must request.

    Key is what the gateway checks in the token's scp claim; value is what it
    advertises in its RFC 9728 metadata and WWW-Authenticate headers. Only MCP
    clients read that mapping -- the portal does not.
    """
    aud = audience(idp)
    return {
        scope: idp["scopeTemplate"].format(audience=aud, scope=scope)
        for scope in idp["gatewayScopes"]
    }


def client_secret(idp):
    """Read the portal app's client secret without putting it in argv."""
    env_var = idp["clientSecretEnv"]
    secret = os.environ.get(env_var)
    if not secret:
        secret = getpass.getpass(f"{idp['displayName']} portal client secret: ")
    if not secret:
        print(f"ERROR: {env_var} not set and nothing entered.")
        sys.exit(1)
    return secret


def client_id(idp):
    return get_required_env(idp["clientIdEnv"])
