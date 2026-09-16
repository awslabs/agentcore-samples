#!/usr/bin/env python3
"""
IdP provider flag helper for the consolidated lakehouse-agent tutorial.

This tutorial runs on either Amazon Cognito or Okta, selected by a single
top-level flag ``IDP_PROVIDER`` with allowed values ``"cognito"`` or ``"okta"``.

Flag persistence (see design "Flag-Persistence Mechanism", DR-12):
    1. The reader chooses the IdP **in notebook 01's Step-0 cell** by passing an
       explicit value: ``set_idp_provider(ssm_client, value="cognito")`` (or
       ``"okta"``). The notebook cell is the canonical knob — NOT ``.env``.
    2. ``set_idp_provider`` validates the value and mirrors it into SSM at
       ``/app/lakehouse-agent/idp-provider``.
    3. Every downstream notebook / script calls ``get_idp_provider(...)`` to read
       the flag back from SSM (the demo's existing cross-cell coordination
       substrate) and fails fast if it is missing.

The ``IDP_PROVIDER`` environment variable is only a **fallback** in the
resolution order (used when the notebook passes no explicit ``value=``); it is
NOT the documented way to choose the IdP. No new dependency is introduced: this
reuses the ``/app/lakehouse-agent/*`` SSM contract already used throughout the tutorial.

Usage in notebook 01 (choose here, in the Step-0 cell):
    from utils.idp_config import set_idp_provider
    IDP_PROVIDER = set_idp_provider(ssm_client, value="cognito")  # change to "okta"

Usage in every downstream notebook (read the persisted flag):
    from utils.idp_config import get_idp_provider
    IDP_PROVIDER = get_idp_provider(ssm_client)
    if IDP_PROVIDER == "okta":
        ...
"""

import os
from typing import Optional

# The flag contract. Cognito is the default so an unmodified checkout reproduces
# the upstream (Cognito-only) tutorial behavior.
FLAG_NAME = "IDP_PROVIDER"
ALLOWED_VALUES = ("cognito", "okta")
DEFAULT_VALUE = "cognito"
SSM_PARAM_NAME = "/app/lakehouse-agent/idp-provider"


def validate_idp_provider(value: str | None) -> str:
    """
    Validate an IDP_PROVIDER value and return it normalized (lower-case).

    Fails fast (ValueError) on a missing or invalid value, naming the flag and
    its allowed values so the tutorial reader knows exactly what to fix.

    Args:
        value: The candidate flag value (e.g. from .env or SSM).

    Returns:
        The normalized value, one of ALLOWED_VALUES.

    Raises:
        ValueError: If value is None/empty or not one of ALLOWED_VALUES.
    """
    if value is None or str(value).strip() == "":
        raise ValueError(
            f"{FLAG_NAME} is not set. Set {FLAG_NAME} to one of "
            f"{list(ALLOWED_VALUES)} (e.g. in your .env file: {FLAG_NAME}={DEFAULT_VALUE})."
        )

    normalized = str(value).strip().lower()
    if normalized not in ALLOWED_VALUES:
        raise ValueError(f"{FLAG_NAME}={value!r} is invalid. Allowed values are {list(ALLOWED_VALUES)}.")
    return normalized


def set_idp_provider(ssm_client, value: str | None = None, verbose: bool = True) -> str:
    """
    Resolve, validate, and persist the IDP_PROVIDER flag to SSM (notebook 01).

    Resolution order:
        1. Explicit ``value`` argument — the canonical knob; notebook 01's Step-0
           cell passes this (e.g. ``value="cognito"``).
        2. Fallback only: the ``IDP_PROVIDER`` environment variable, if set. This
           is NOT the documented way to choose the IdP (DR-12) — it exists so the
           resolver has a sensible middle rung when no explicit value is passed.
        3. The default (``"cognito"``) so an unmodified checkout reproduces the
           upstream tutorial.

    The resolved value is validated, then written to SSM at
    ``/app/lakehouse-agent/idp-provider`` so every downstream cell can read it.

    Args:
        ssm_client: A boto3 SSM client.
        value: Optional explicit flag value; overrides the .env / default lookup.
        verbose: If True, print a short confirmation.

    Returns:
        The normalized flag value that was persisted.

    Raises:
        ValueError: If the resolved value is invalid.
    """
    if value is None:
        value = os.environ.get(FLAG_NAME)
    if value is None or str(value).strip() == "":
        # Unset -> fall back to the Cognito default (upstream behavior preserved).
        value = DEFAULT_VALUE

    normalized = validate_idp_provider(value)

    ssm_client.put_parameter(
        Name=SSM_PARAM_NAME,
        Value=normalized,
        Type="String",
        Overwrite=True,
    )

    if verbose:
        print(f"✅ {FLAG_NAME} = '{normalized}' (persisted to SSM {SSM_PARAM_NAME})")

    return normalized


def get_idp_provider(ssm_client) -> str:
    """
    Read the persisted IDP_PROVIDER flag from SSM (every downstream notebook).

    Fails fast if the flag has not been set yet (i.e. notebook 01 was not run)
    or if the stored value is invalid.

    Args:
        ssm_client: A boto3 SSM client.

    Returns:
        The normalized flag value, one of ALLOWED_VALUES.

    Raises:
        ValueError: If the flag is missing from SSM or holds an invalid value.
    """
    try:
        value = ssm_client.get_parameter(Name=SSM_PARAM_NAME)["Parameter"]["Value"]
    except ssm_client.exceptions.ParameterNotFound as e:
        raise ValueError(
            f"{FLAG_NAME} not found in SSM ({SSM_PARAM_NAME}). "
            f"Run notebook 01 first to set {FLAG_NAME} to one of {list(ALLOWED_VALUES)}."
        ) from e

    return validate_idp_provider(value)


# ─────────────────────────────────────────────────────────────────────────
# DR-11 pre-flight IdP-mismatch guard
# ─────────────────────────────────────────────────────────────────────────


def detect_gateway_idp(live_gateway) -> str:
    """
    Infer a live gateway's IdP from its JWT authorizer configuration.

    Used by the DR-11 pre-flight guard to catch a flag-switch-without-teardown
    before an in-place converge/reuse mutates the gateway into the other IdP.

    Detection (from ``authorizerConfiguration.customJWTAuthorizer``):
      - **cognito**: a ``discoveryUrl`` on ``cognito-idp.<region>.amazonaws.com``
        with ``allowedClients`` present (Cognito access tokens carry no ``aud``).
      - **okta**: an Okta-tenant ``discoveryUrl`` with ``allowedAudience`` present.

    Args:
        live_gateway: A get_gateway / list_gateways item (dict) for the live gateway.

    Returns:
        "cognito" or "okta".

    Raises:
        ValueError: If the authorizer is missing or the signals are ambiguous.
    """
    authz = ((live_gateway or {}).get("authorizerConfiguration") or {}).get("customJWTAuthorizer") or {}
    discovery_url = authz.get("discoveryUrl") or ""
    has_clients = bool(authz.get("allowedClients"))
    has_audience = bool(authz.get("allowedAudience"))

    # Primary signal: the discovery URL host.
    if "cognito-idp." in discovery_url and has_clients:
        return "cognito"
    if discovery_url and "cognito-idp." not in discovery_url and has_audience:
        return "okta"
    # Fallback: the credential-shape signal when the URL is absent/ambiguous.
    if has_clients and not has_audience:
        return "cognito"
    if has_audience and not has_clients:
        return "okta"

    raise ValueError(
        "Cannot determine gateway IdP from its authorizer configuration "
        f"(discoveryUrl={discovery_url!r}, allowedClients={has_clients}, "
        f"allowedAudience={has_audience}). Expected a Cognito "
        "(cognito-idp.* discoveryUrl + allowedClients) or Okta "
        "(tenant discoveryUrl + allowedAudience) authorizer."
    )


def assert_gateway_idp_matches(live_gateway, flag: str, gateway_name: str) -> None:
    """
    DR-11 pre-flight guard: fail fast if a live gateway's IdP != the current flag.

    A gateway's IdP is baked into its JWT authorizer; an in-place converge (GW1)
    or reuse (GW2) against a gateway deployed for the *other* IdP would silently
    mutate / mis-wire it. Detect the mismatch and refuse, pointing at teardown.

    Raises:
        RuntimeError: On IdP mismatch, with teardown guidance.
        ValueError:   If the live gateway's IdP cannot be determined.
    """
    live = detect_gateway_idp(live_gateway)
    if live != flag:
        raise RuntimeError(
            f"❌ IdP mismatch on gateway '{gateway_name}': deployed for "
            f"IDP_PROVIDER='{live}', current flag='{flag}'. A gateway cannot "
            "switch IdPs in place. Run teardown first "
            "(GW1: deployment/5a-gateway-setup/cleanup_gateway.py or notebook 09; "
            "GW2: deployment/5b-obo-gateway-setup/06_cleanup_obo_gateway.py), then "
            f"set IDP_PROVIDER='{flag}' and re-run."
        )


# ─────────────────────────────────────────────────────────────────────────
# CLI entrypoint (Option-B / DevOps path — DR-12-consistent)
# ─────────────────────────────────────────────────────────────────────────
# The notebook path chooses the IdP in notebook 01's Step-0 cell. The CLI path
# has no notebook, so this entrypoint is the equivalent explicit knob:
#     python -m utils.idp_config cognito     # or: okta
# It persists an EXPLICIT value to SSM (never reads .env for the choice) — the
# same canonical mechanism as the notebook cell, just from the shell.


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m utils.idp_config",
        description="Persist the IDP_PROVIDER flag to SSM (Option-B/CLI equivalent of notebook 01's Step-0 cell).",
    )
    parser.add_argument(
        "value",
        choices=ALLOWED_VALUES,
        help="Identity provider to select (cognito | okta).",
    )
    args = parser.parse_args(argv)

    import boto3  # imported after arg-parse so `--help` works without boto3 installed

    ssm_client = boto3.client("ssm")
    set_idp_provider(ssm_client, value=args.value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
