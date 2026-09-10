"""Shared plumbing for the consent-portal sample's deploy scripts.

Owns three things every numbered script needs:

  1. `.env` state at the sample root — steps pass ids/URLs to each other
     through it (`load_env` / `save_env` / `must_env`).
  2. The required `--entra` / `--okta` flag. There is deliberately no default
     and no environment fallback: a script that guessed its IdP could create
     — or on teardown, delete — the wrong portal. The flag names a profile in
     `deploy/idps/<name>.json`, which holds the IdP's *shape* (vendor, which
     env vars carry its values, how scopes are spelled). The tenant-specific
     *values* stay in `.env`, because they belong to your tenant, not to this
     repository.
  3. The boto3 floor. The consent-portal operations were added to the
     `bedrock-agentcore-control` model in botocore 1.43.88; on an older SDK
     they fail with
         'BedrockAgentCoreControlPlaneFrontingLayer' object has no attribute
         'create_consent_portal'
     which reads like a missing feature rather than a stale SDK, so the guard
     turns it into a clear error up front.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import boto3

DEPLOY_DIR = Path(__file__).resolve().parent
SAMPLE_ROOT = DEPLOY_DIR.parent
IDPS_DIR = DEPLOY_DIR / "idps"

# `.env` holds one deployment's state. Running a second identity provider
# side by side would overwrite the first's GATEWAY_ID, PORTAL_ID and so on —
# the AWS resources coexist happily (their names are IdP-prefixed) but the
# state file is a single flat namespace. Set CONSENT_ENV_FILE to keep them
# apart:
#
#     CONSENT_ENV_FILE=.env.okta python deploy/01_create_gateway.py --okta
#
# Relative paths resolve against the sample root.
_env_override = os.environ.get("CONSENT_ENV_FILE", "").strip()
ENV_PATH = (
    (Path(_env_override) if Path(_env_override).is_absolute() else SAMPLE_ROOT / _env_override)
    if _env_override
    else SAMPLE_ROOT / ".env"
)

# Consent-portal operations (create/get/list/delete-consent-portal) landed in
# this botocore release. grantType=AUTHORIZATION_CODE on gateway targets and
# mcpServer target configurations are older, so this single floor covers
# everything the sample calls.
MIN_BOTO_VERSION = (1, 43, 88)

REQUIRED_PROFILE_KEYS = (
    "name",
    "displayName",
    "vendor",
    "providerConfigKey",
    "discoveryUrlMustContain",
    "resourcePrefix",
)


def check_boto_version() -> None:
    installed = tuple(int(x) for x in boto3.__version__.split(".")[:3])
    if installed < MIN_BOTO_VERSION:
        floor = ".".join(map(str, MIN_BOTO_VERSION))
        print(
            f"ERROR: boto3 {boto3.__version__} is too old for the consent-portal "
            f"APIs.\n"
            f"       They were added in boto3/botocore {floor}. Without it the "
            f"calls fail with\n"
            f"       \"object has no attribute 'create_consent_portal'\".\n"
            f"       Fix: pip install -U 'boto3>={floor}' 'botocore>={floor}'",
            file=sys.stderr,
        )
        sys.exit(1)


# --- .env state ---------------------------------------------------------------


def load_env() -> None:
    """Read the sample root's .env into the environment without clobbering exports.

    setdefault, not assignment: a value exported on the command line always
    wins over one saved by an earlier step.
    """
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value.strip().strip('"'))


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"ERROR: {name} is not set. Export it or add it to .env (see config.example.env).",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def save_env(**kwargs: str) -> None:
    """Upsert keys into the sample root's .env and export them for this run.

    Rewrites in place rather than regenerating the file, so config.example.env's
    comments and ordering survive every deploy step.
    """
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    remaining = dict(kwargs)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0]
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n")
    for key, value in kwargs.items():
        os.environ[key] = str(value)


# --- IdP profile selection ------------------------------------------------------


def available_idps() -> list[str]:
    if not IDPS_DIR.is_dir():
        return []
    return sorted(p.stem for p in IDPS_DIR.glob("*.json"))


def load_idp(name: str) -> dict:
    path = IDPS_DIR / f"{name}.json"
    if not path.exists():
        print(f"ERROR: no such IdP profile: {name}", file=sys.stderr)
        print(f"  available: {', '.join(available_idps()) or '(none)'}", file=sys.stderr)
        sys.exit(1)
    idp = json.loads(path.read_text())
    missing = [k for k in REQUIRED_PROFILE_KEYS if k not in idp]
    if missing:
        print(
            f"ERROR: profile {name}.json is missing required keys: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)
    return idp


def select_idp(description: str) -> dict:
    """Parse argv for a required, mutually exclusive --<idp> flag."""
    names = available_idps()
    if not names:
        print(f"ERROR: no IdP profiles found in {IDPS_DIR}", file=sys.stderr)
        sys.exit(1)
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    for name in names:
        group.add_argument(f"--{name}", dest=name, action="store_true", help=f"use the {name} IdP profile")
    args = parser.parse_args()
    chosen = next(name for name in names if getattr(args, name))
    return load_idp(chosen)


def select_idp_with_args(description: str, extra_args: list[tuple[list[str], dict]]):
    """Like select_idp, but also returns extra parsed args (used by teardown)."""
    names = available_idps()
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    for name in names:
        group.add_argument(f"--{name}", dest=name, action="store_true", help=f"use the {name} IdP profile")
    for flags, kwargs in extra_args:
        parser.add_argument(*flags, **kwargs)
    args = parser.parse_args()
    chosen = next(name for name in names if getattr(args, name))
    return load_idp(chosen), args


# --- Derived resource names ------------------------------------------------------
# Each name is needed by the script that creates the resource and again by
# teardown.py. Deriving both from the profile keeps the pair in agreement.


def gateway_name(idp: dict) -> str:
    # `or`, not a get() default: config.example.env ships these keys present
    # but empty, and a get() default would not fire for an empty string —
    # which would create a gateway with no name.
    return os.environ.get("GATEWAY_NAME") or f"{idp['resourcePrefix']}-consent-github-gw"


def gateway_role_name(idp: dict) -> str:
    return f"AmazonBedrockAgentCoreGatewayRole-{gateway_name(idp)}"


def portal_name(idp: dict) -> str:
    # See gateway_name for why this is `or` and not a get() default.
    return os.environ.get("PORTAL_NAME") or f"{idp['resourcePrefix']}-github-consent-portal"


def idp_provider_name(idp: dict) -> str:
    return f"{idp['resourcePrefix']}-consent-portal-idp"


def portal_role_name(idp: dict) -> str:
    return f"{idp['resourcePrefix'].capitalize()}GithubConsentPortalRole"


def github_provider_name(idp: dict) -> str:
    return f"{idp['resourcePrefix']}-consent-github-provider"


def github_target_name(idp: dict) -> str:
    # Shown to end users on the portal's Connections page — keep it readable.
    return "github-mcp-server"


def interceptor_lambda_name(idp: dict) -> str:
    # Env-overridable like the gateway and portal names, so an org with its own
    # naming convention does not have to edit the script.
    return os.environ.get("INTERCEPTOR_LAMBDA_NAME") or f"{idp['resourcePrefix']}-consent-jit-interceptor"


def interceptor_role_name(idp: dict) -> str:
    return os.environ.get("INTERCEPTOR_ROLE_NAME") or f"{idp['resourcePrefix'].capitalize()}ConsentInterceptorRole"


# --- Discovery URL / scope helpers ------------------------------------------------


def discovery_url(idp: dict) -> str:
    """Return IDP_DISCOVERY_URL, enforcing the profile's required substring.

    For Entra the substring is /v2.0/, and it is not cosmetic: the v1.0
    document advertises iss = https://sts.windows.net/<tenant>/, which does
    not match the iss in the v2.0 tokens Entra actually issues, so token
    validation fails later in a way that looks like a scope bug. Checking here
    turns it into an error at step one.
    """
    url = must_env("IDP_DISCOVERY_URL")
    required = idp["discoveryUrlMustContain"]
    if required not in url:
        print(f"ERROR: IDP_DISCOVERY_URL must contain {required}", file=sys.stderr)
        print(f"  got:      {url}", file=sys.stderr)
        example = idp.get("discoveryUrlExample")
        if example:
            print(f"  expected: {example}", file=sys.stderr)
        sys.exit(1)
    return url


def okta_domain() -> str:
    """OKTA_DOMAIN as a bare hostname.

    Tolerates the three forms people paste: with a scheme, with a trailing
    slash, and the `-admin` console hostname (the admin API works on the
    app-facing host). Every caller builds `https://{domain}/...`, so a scheme
    left in place would produce `https://https://...`.
    """
    raw = must_env("OKTA_DOMAIN").strip()
    host = raw.removeprefix("https://").removeprefix("http://").rstrip("/")
    if "-admin." in host:
        host = host.replace("-admin.", ".")
    return host


def portal_scopes() -> list[str]:
    """Return idpConfig.scopes with openid guaranteed present.

    A consent portal always requests openid on top of the scopes you
    configure, and every scope it requests — openid included — must be
    defined and permitted on the IdP application, or authorization fails
    with invalid_scope.
    """
    scopes = must_env("PORTAL_SCOPES").split()
    if "openid" not in scopes:
        scopes.insert(0, "openid")
    return scopes


# --- boto3 find helpers -----------------------------------------------------------


def find_gateway_by_name(control, name: str) -> dict | None:
    paginator = control.get_paginator("list_gateways")
    for page in paginator.paginate():
        for gw in page.get("items", []):
            if gw.get("name") == name:
                return gw
    return None


def find_target_by_name(control, gateway_id: str, name: str) -> dict | None:
    paginator = control.get_paginator("list_gateway_targets")
    for page in paginator.paginate(gatewayIdentifier=gateway_id):
        for target in page.get("items", []):
            if target.get("name") == name:
                return target
    return None


def find_provider(control, name: str) -> bool:
    paginator = control.get_paginator("list_oauth2_credential_providers")
    for page in paginator.paginate():
        for prov in page.get("credentialProviders", []):
            if prov.get("name") == name:
                return True
    return False


def find_portal_by_name(control, name: str) -> dict | None:
    """Paged, because a truncated scan would silently create a duplicate portal."""
    kwargs = {"maxResults": 50}
    while True:
        page = control.list_consent_portals(**kwargs)
        for item in page.get("consentPortals", []):
            if item.get("name") == name:
                return item
        token = page.get("nextToken")
        if not token:
            return None
        kwargs["nextToken"] = token


def control_client():
    region = os.environ.get("AWS_REGION") or boto3.Session().region_name
    if not region:
        print("ERROR: no AWS region. Set AWS_REGION in .env.", file=sys.stderr)
        sys.exit(1)
    return boto3.client("bedrock-agentcore-control", region_name=region), region
