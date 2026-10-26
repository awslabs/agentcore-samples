"""Degradation ladder (spec §6) — resolve the active rung from AppConfig.

The agent runs in a CONTAINER (not a Lambda), so it reads AppConfig via the
`appconfigdata` data API directly (StartConfigurationSession -> GetLatestConfiguration),
NOT the Lambda Agent extension (which is a Lambda layer). Config is cached in-process
and only re-polled after the server-provided NextPollInterval.

Config shape (a freeform JSON profile):
{
  "activeRung": "L0",
  "rungs": {
    "L0": {"model": "global.anthropic.claude-opus-4-8",
           "features": {"validator": true, "memoryRead": true, "memoryWrite": true,
                        "merchantLookup": true, "categoryInference": true, "dedup": true,
                        "forceReview": false}},
    "L1": {...}, "L2": {...}, "L3": {...}, "L4": {...}
  }
}

Safety: if AppConfig is unset/unreachable/malformed, fall back to L0_DEFAULT — the
agent NEVER hard-fails because it couldn't read the ladder.
"""

import json
import time
from typing import Any

from config import (
    APPCONFIG_APPLICATION,
    APPCONFIG_ENVIRONMENT,
    APPCONFIG_PROFILE,
    DEFAULT_MODEL_ID,
    REGION,
)

# The L0 rung — used as the default when AppConfig is unavailable, and as the
# template for any feature flag a rung doesn't specify.
L0_DEFAULT: dict[str, Any] = {
    "rung": "L0",
    "model": DEFAULT_MODEL_ID,
    "features": {
        "validator": True,
        "memoryRead": True,
        "memoryWrite": True,
        "merchantLookup": True,
        "categoryInference": True,
        "dedup": True,
        "forceReview": False,
    },
}

# Rung ids that perform NO model call (defer to SQS, spec §6.1 L4).
NO_MODEL_RUNGS = {"L4"}

# The ladder order — used to find the next rung on a 503 step-down (spec §6.3).
RUNG_ORDER = ["L0", "L1", "L2", "L3", "L4"]


def next_rung(rung_id: str) -> str | None:
    """The next rung down, or None if already at the bottom (L4 = defer)."""
    try:
        i = RUNG_ORDER.index(rung_id)
    except ValueError:
        return None
    return RUNG_ORDER[i + 1] if i + 1 < len(RUNG_ORDER) else None


def classify_model_error(exc: Exception) -> str:
    """Map a model-call exception to a ladder action (spec §6.3 — the error->response
    mapping is the whole point):
      "step"    -> 503 ServiceUnavailable (model capacity): step the ladder
      "backoff" -> 429 ThrottlingException (quota) / 500 InternalServerException
                   (transient): retry the SAME model
      "raise"   -> anything else: not a ladder concern, propagate
    Works whether the error is a botocore ClientError (with response.Error.Code) or a
    Strands wrapper (ModelThrottledException). Stringy fallback for safety.
    """
    code = ""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = (resp.get("Error") or {}).get("Code", "")
    name = type(exc).__name__
    blob = f"{code} {name} {exc}".lower()

    if "serviceunavailable" in blob or "503" in blob:
        return "step"
    if "throttl" in blob or "modelthrottled" in blob or "429" in blob:
        return "backoff"
    if "internalserver" in blob or "internalfailure" in blob or "500" in blob:
        return "backoff"
    return "raise"


# Module-level cache: (resolved_dict, token, expires_at).
_cache: dict[str, Any] = {"value": None, "token": None, "expires_at": 0.0}


def resolve_rung(config: dict[str, Any], rung_id: str | None = None) -> dict[str, Any]:
    """Pure: given a ladder config dict, return the resolved active rung.

    Merges the rung's features over L0 defaults so a partial rung is safe. Returns
    a dict {rung, model, features, defer}. Falls back to L0_DEFAULT on anything
    missing/malformed.
    """
    if not isinstance(config, dict):
        return {**L0_DEFAULT, "defer": False}
    active = rung_id or config.get("activeRung") or "L0"
    rungs = config.get("rungs") or {}
    spec = rungs.get(active)
    if not isinstance(spec, dict):
        # Unknown rung -> safe default.
        return {**L0_DEFAULT, "defer": False}
    features = {**L0_DEFAULT["features"], **(spec.get("features") or {})}
    model = spec.get("model") or DEFAULT_MODEL_ID
    return {
        "rung": active,
        "model": model,
        "features": features,
        "defer": active in NO_MODEL_RUNGS or not spec.get("model"),
    }


def _appconfig_configured() -> bool:
    return bool(APPCONFIG_APPLICATION and APPCONFIG_ENVIRONMENT and APPCONFIG_PROFILE)


def _fetch_config() -> dict[str, Any] | None:
    """Read the latest ladder config via the appconfigdata data API. Returns the
    parsed dict, or None on any failure (caller falls back to L0)."""
    import boto3

    client = boto3.client("appconfigdata", region_name=REGION)
    token = _cache.get("token")
    if not token:
        token = client.start_configuration_session(
            ApplicationIdentifier=APPCONFIG_APPLICATION,
            EnvironmentIdentifier=APPCONFIG_ENVIRONMENT,
            ConfigurationProfileIdentifier=APPCONFIG_PROFILE,
        )["InitialConfigurationToken"]

    resp = client.get_latest_configuration(ConfigurationToken=token)
    _cache["token"] = resp["NextPollConfigurationToken"]
    _cache["expires_at"] = time.monotonic() + int(resp.get("NextPollIntervalInSeconds", 60))

    raw = resp["Configuration"].read()
    if raw:  # empty body => unchanged since last poll; keep cached value
        _cache["value"] = json.loads(raw)
    return _cache["value"]


def get_active_rung() -> dict[str, Any]:
    """Return the resolved active rung, reading AppConfig (cached) when configured,
    else the L0 default. Never raises — degrades to L0 on any error."""
    if not _appconfig_configured():
        return {**L0_DEFAULT, "defer": False}
    try:
        if _cache["value"] is None or time.monotonic() >= _cache["expires_at"]:
            cfg = _fetch_config()
        else:
            cfg = _cache["value"]
        return resolve_rung(cfg or {})
    except Exception:
        return {**L0_DEFAULT, "defer": False}


def rung_for(rung_id: str) -> dict[str, Any]:
    """Resolve a SPECIFIC rung by id from the already-cached config (no new fetch),
    for the in-agent step-down loop. Falls back to L0 if config/rung is unavailable."""
    cfg = _cache.get("value")
    if not isinstance(cfg, dict):
        return {**L0_DEFAULT, "defer": False}
    return resolve_rung(cfg, rung_id)
