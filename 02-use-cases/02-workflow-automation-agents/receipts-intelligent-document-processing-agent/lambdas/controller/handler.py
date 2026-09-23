"""Account-level ladder controller (spec §6.3 path 2 — the control loop).

A CloudWatch alarm on sustained `503`-driven model step-downs fires an EventBridge
"CloudWatch Alarm State Change" event at this Lambda. On ALARM it steps `activeRung`
DOWN one rung (toward L4) via AppConfig, so EVERY new invocation starts on the safer
rung. On OK (recovery) it steps back UP one rung. One rung per event, with a cooldown
to prevent flapping.

Why a custom metric, not a Runtime System Error metric: a `503` the agent RECOVERS
from via the in-agent step-down (sub-step 2) is a *successful* Runtime invocation, so
it never shows up as a Runtime `System Error`. The honest account-level signal is the
custom `ModelStepDowns` metric the agent emits whenever it actually steps down
(see app/receiptsagent — `ModelStepDowns` in namespace `ReceiptsAgent/Ladder`). The
down-alarm watches that metric; this is the bridge from the reactive in-agent path to
the proactive account-level path.

Mechanics, all grounded against the appconfig control-plane API:
  - read the latest hosted config version's JSON (current activeRung + rungs map),
  - compute the next rung (pure `decide_next_rung`),
  - cooldown check: if the most recent deployment started < COOLDOWN_SECONDS ago, skip
    (anti-flap — stateless, no extra store; reads deployment history),
  - write a new hosted config version with activeRung repointed, start a deployment.

The agent re-reads activeRung from AppConfig (cached, TTL) at the start of each run, so
a rung change here takes effect on the next invocation without any stack redeploy.
"""

import json
import os
from datetime import datetime, timezone

import boto3

# Rung order: L0 (full) is the top, L4 (defer) the bottom. "Down" = toward L4.
RUNG_ORDER = ["L0", "L1", "L2", "L3", "L4"]

APP_ID = os.environ.get("APPCONFIG_APPLICATION", "")
ENV_ID = os.environ.get("APPCONFIG_ENVIRONMENT", "")
PROFILE_ID = os.environ.get("APPCONFIG_PROFILE", "")
STRATEGY_ID = os.environ.get("APPCONFIG_STRATEGY", "")
# The alarm whose state drives the ladder. Only this alarm moves the rung, so a
# stray alarm elsewhere in the account can't degrade the agent.
LADDER_ALARM = os.environ.get("LADDER_ALARM", "")
# Anti-flap: ignore a state change if a deployment started within this window.
COOLDOWN_SECONDS = int(os.environ.get("LADDER_COOLDOWN_SECONDS", "120"))

REGION = os.environ.get("AWS_REGION", "us-west-2")


def decide_next_rung(current: str, alarm_state: str) -> str | None:
    """Pure: given the current rung and the new alarm state, return the rung to move
    to, or None for no change. ALARM steps DOWN one rung (toward L4, clamped at L4);
    OK steps UP one rung (toward L0, clamped at L0); anything else is a no-op."""
    if current not in RUNG_ORDER:
        current = "L0"
    i = RUNG_ORDER.index(current)
    if alarm_state == "ALARM":
        nxt = min(i + 1, len(RUNG_ORDER) - 1)
    elif alarm_state == "OK":
        nxt = max(i - 1, 0)
    else:  # INSUFFICIENT_DATA or unknown -> do nothing
        return None
    if nxt == i:
        return None  # already clamped at an end
    return RUNG_ORDER[nxt]


def _latest_config(ac) -> tuple[dict, int]:
    """Read the most recent hosted config version's JSON content + its version number."""
    versions = ac.list_hosted_configuration_versions(
        ApplicationId=APP_ID, ConfigurationProfileId=PROFILE_ID, MaxResults=1
    )["Items"]
    if not versions:
        raise RuntimeError("no hosted configuration versions to read")
    vnum = versions[0]["VersionNumber"]
    got = ac.get_hosted_configuration_version(
        ApplicationId=APP_ID, ConfigurationProfileId=PROFILE_ID, VersionNumber=vnum
    )
    return json.loads(got["Content"].read()), vnum


def _in_cooldown(ac) -> bool:
    """True if the most recent deployment started within COOLDOWN_SECONDS (anti-flap).
    Stateless: the deployment history IS the cooldown clock."""
    deps = ac.list_deployments(ApplicationId=APP_ID, EnvironmentId=ENV_ID, MaxResults=1)["Items"]
    if not deps:
        return False
    started = deps[0].get("StartedAt")
    if not started:
        return False
    age = (datetime.now(timezone.utc) - started).total_seconds()
    return age < COOLDOWN_SECONDS


def handler(event, context):
    """EventBridge 'CloudWatch Alarm State Change' -> step activeRung one rung."""
    detail = event.get("detail", {}) if isinstance(event, dict) else {}
    alarm_name = detail.get("alarmName", "")
    state = (detail.get("state") or {}).get("value", "")

    # Only the ladder alarm drives the rung.
    if LADDER_ALARM and alarm_name != LADDER_ALARM:
        return {"skipped": "unrelated-alarm", "alarmName": alarm_name}

    ac = boto3.client("appconfig", region_name=REGION)
    config, _vnum = _latest_config(ac)
    current = config.get("activeRung", "L0")
    target = decide_next_rung(current, state)

    if target is None:
        return {"skipped": "no-change", "rung": current, "state": state}

    if _in_cooldown(ac):
        return {"skipped": "cooldown", "rung": current, "wanted": target, "state": state}

    config["activeRung"] = target
    ver = ac.create_hosted_configuration_version(
        ApplicationId=APP_ID,
        ConfigurationProfileId=PROFILE_ID,
        Content=json.dumps(config).encode(),
        ContentType="application/json",
        Description=f"controller: {current}->{target} on alarm {state}",
    )
    ac.start_deployment(
        ApplicationId=APP_ID,
        EnvironmentId=ENV_ID,
        ConfigurationProfileId=PROFILE_ID,
        ConfigurationVersion=str(ver["VersionNumber"]),
        DeploymentStrategyId=STRATEGY_ID,
        Description=f"ladder {current}->{target} ({state})",
    )
    return {
        "stepped": True,
        "from": current,
        "to": target,
        "state": state,
        "version": ver["VersionNumber"],
    }
