"""Per-MCP-server configuration and shared .env state for the auth-code scripts.

Nothing in scripts/ knows which MCP server it is deploying. Every
provider-specific value -- OAuth2 vendor, MCP endpoint, scopes, tool schema,
the demo tool, the resource-name prefix -- comes from a profile in
scripts/servers/<name>.json.

Every script names its profile with a **required** flag, one per file in
servers/:

    uv run python scripts/deploy_credential.py --github

There is deliberately no default and no environment-variable fallback. A script
that silently assumed a provider could deploy or delete the wrong one, and the
deletes are not recoverable. Omitting the flag is an argparse error listing the
profiles that exist.

Attaching a different MCP server is therefore a new profile plus a new flag that
appears on its own, not a code edit.

This module also owns the .env state that steps pass between each other. That
logic used to be copy-pasted into every script, which is why changing it meant
editing six files.
"""

import argparse
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SERVERS_DIR = os.path.join(SCRIPTS_DIR, "servers")
ENV_PATH = os.path.join(SCRIPTS_DIR, ".env")

# Keys every profile must define regardless of how its OAuth client is obtained.
# Absent any one of them a script would fail later with a KeyError deep inside a
# boto3 call, so check up front.
REQUIRED_CORE = (
    "name",
    "displayName",
    "vendor",
    "providerConfigKey",
    "endpoint",
    "scopes",
    "toolSchema",
    "demoTool",
    "demoArgs",
    "resourcePrefix",
    "authModel",
)

# How the OAuth client is obtained decides which extra keys a profile needs.
#
#   oauth_app  the admin hand-creates an OAuth app and supplies its id/secret
#              through env vars (GitHub). appSettingsUrl is where they do it.
#   dcr        the client is registered dynamically (RFC 7591) against the
#              provider's own endpoint (Atlassian). There is no settings page and
#              no pre-existing secret; discoveryUrl seeds the dummy create, the
#              dcr* urls drive registration, and clientIdEnv/clientSecretEnv name
#              the env vars the DCR-returned values are read from at update time.
REQUIRED_BY_AUTH_MODEL = {
    "oauth_app": ("clientIdEnv", "clientSecretEnv", "appSettingsUrl"),
    "dcr": (
        "clientIdEnv",
        "clientSecretEnv",
        "discoveryUrl",
        "dcrRegisterUrl",
        "dcrDiscoveryUrl",
    ),
}


def available_profiles():
    if not os.path.isdir(SERVERS_DIR):
        return []
    return sorted(
        f[: -len(".json")] for f in os.listdir(SERVERS_DIR) if f.endswith(".json")
    )


def load_profile(name):
    """Load scripts/servers/<name>.json, validating its required keys."""
    path = os.path.join(SERVERS_DIR, f"{name}.json")

    if not os.path.exists(path):
        print(f"ERROR: no such MCP server profile: {name}")
        print(f"  looked for: {path}")
        found = available_profiles()
        print(f"  available:  {', '.join(found) if found else '(none)'}")
        sys.exit(1)

    with open(path) as f:
        profile = json.load(f)

    missing = [k for k in REQUIRED_CORE if k not in profile]
    if missing:
        print(
            f"ERROR: profile {name}.json is missing required keys: {', '.join(missing)}"
        )
        sys.exit(1)

    auth_model = profile["authModel"]
    if auth_model not in REQUIRED_BY_AUTH_MODEL:
        known = ", ".join(sorted(REQUIRED_BY_AUTH_MODEL))
        print(
            f"ERROR: profile {name}.json has unknown authModel {auth_model!r}; "
            f"expected one of: {known}"
        )
        sys.exit(1)

    missing = [k for k in REQUIRED_BY_AUTH_MODEL[auth_model] if k not in profile]
    if missing:
        print(
            f"ERROR: profile {name}.json (authModel {auth_model}) is missing "
            f"required keys: {', '.join(missing)}"
        )
        sys.exit(1)

    return profile


# --- Profile selection on the command line -----------------------------------
# One flag per profile, generated from servers/. Required, with no default and no
# environment fallback: a script that guessed its provider could deploy -- or
# delete -- the wrong one.


def dest_for(name):
    """argparse dest for a profile flag. Shared so callers agree on the mangling."""
    return name.replace("-", "_")


# Kept as the private alias the rest of this module already uses.
_dest = dest_for


def add_choice_flags(parser, names, *, kind, all_help=None):
    """Add one required, mutually exclusive --<name> flag per profile.

    Generic over the profile directory so idp_config.py can reuse it rather than
    re-rolling the same argparse wiring. Pass all_help to also offer --all.
    """
    group = parser.add_mutually_exclusive_group(required=True)
    for name in names:
        group.add_argument(
            f"--{name}",
            dest=dest_for(name),
            action="store_true",
            help=f"use the {name} {kind} profile",
        )
    if all_help:
        group.add_argument("--all", action="store_true", help=all_help)
    return names


def chosen_flag(args, names):
    """Return the profile name whose flag was passed."""
    return next(name for name in names if getattr(args, dest_for(name)))


def _add_profile_flags(parser, allow_all=False):
    names = available_profiles()
    if not names:
        print(f"ERROR: no MCP server profiles found in {SERVERS_DIR}")
        print("  Add a servers/<name>.json profile first.")
        sys.exit(1)

    return add_choice_flags(
        parser,
        names,
        kind="MCP server",
        all_help=(
            "every profile in servers/, and every target on the gateway"
            if allow_all
            else None
        ),
    )


def select_profile(description):
    """Parse argv for a required --<profile> flag and return that profile."""
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    names = _add_profile_flags(parser)
    args = parser.parse_args()
    chosen = next(name for name in names if getattr(args, _dest(name)))
    return load_profile(chosen)


def select_profiles_or_all(description, extra_args=None):
    """Parse argv for --<profile> or --all.

    Returns (profiles, is_all, args). `profiles` is a list so callers can treat
    the single-profile and --all cases identically.
    """
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    names = _add_profile_flags(parser, allow_all=True)
    for flags, kwargs in extra_args or []:
        parser.add_argument(*flags, **kwargs)
    args = parser.parse_args()

    if getattr(args, "all", False):
        return [load_profile(name) for name in names], True, args
    chosen = next(name for name in names if getattr(args, _dest(name)))
    return [load_profile(chosen)], False, args


# --- Gateway protocol configuration ------------------------------------------
# Every gateway created in this directory gets exactly this, whichever deploy
# script creates it. It lives here, and not in either script, so the Cognito and
# Entra gateways cannot drift apart.

# Newest first. 2025-11-25 is what enables URL-mode elicitation (the
# authorization code flow); the older two exist so a client that cannot speak it
# still connects instead of failing at version negotiation.
MCP_SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]


def mcp_protocol_configuration():
    """The protocolConfiguration every gateway here is created with."""
    return {
        "mcp": {
            "supportedVersions": MCP_SUPPORTED_VERSIONS,
            "searchType": "SEMANTIC",
            # 3600s is the API default, stated rather than inherited so the
            # value is visible. Valid range is 900-28800.
            "sessionConfiguration": {"sessionTimeoutInSeconds": 3600},
            "streamingConfiguration": {"enableResponseStreaming": True},
        }
    }


# --- Derived resource names --------------------------------------------------
# Each of these is needed by the script that creates the resource and again by
# the cleanup script that deletes it. Deriving both from the profile keeps the pair in
# agreement; when they were two separate literals, drift silently orphaned an
# IAM role and a credential provider.


def gateway_name(profile):
    return f"{profile['resourcePrefix']}-auth-code-gateway"


def credential_provider_name(profile):
    return f"{profile['resourcePrefix']}-oauth-credential"


def implicit_target_name(profile):
    return f"{profile['resourcePrefix']}-mcp-server-implicit"


def schema_target_name(profile):
    return f"{profile['resourcePrefix']}-mcp-server-schema-target"


def tool_schema_path(profile):
    """Resolve the profile's toolSchema, which is relative to scripts/."""
    return os.path.normpath(os.path.join(SCRIPTS_DIR, profile["toolSchema"]))


def custom_parameters(profile):
    """Extra OAuth authorization parameters the target must forward, or {}.

    Optional per profile. Some providers need parameters like `aud`/`resource`
    on the authorization request; profiles without the key send none and the
    target call is unchanged for them.
    """
    return profile.get("customParameters", {})


# --- .env state --------------------------------------------------------------


def load_env():
    """Read scripts/.env into the environment without clobbering exports.

    setdefault, not assignment: a value passed on the command line always wins
    over one saved by an earlier step.
    """
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def save_env(**kwargs):
    """Upsert keys into scripts/.env and export them for the rest of this run."""
    env_vars: dict[str, str] = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars.update({k: str(v) for k, v in kwargs.items()})
    with open(ENV_PATH, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    for key, value in kwargs.items():
        os.environ[key] = str(value)
