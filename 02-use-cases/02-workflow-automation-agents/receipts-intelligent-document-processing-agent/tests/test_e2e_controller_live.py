"""Phase 6 sub-step 3 end-to-end: the account-level control loop (spec §6.3 path 2).

UNLIKE the deferred 503-step-down sim (sub-step 2), this loop is faithfully
live-testable WITHOUT a real Bedrock outage. Two real, no-mock paths together cover
the whole loop:

  A) FULL WIRING — `cloudwatch.set_alarm_state(ALARM)` fires the REAL EventBridge
     "Alarm State Change" event on demand (AWS SetAlarmState docs: "the action
     configured for the appropriate state is invoked"). We assert the controller,
     reached via EventBridge, steps activeRung L0 -> L1. This proves the alarm ->
     EventBridge -> controller -> AppConfig hop end to end.

  B) BRANCH COVERAGE — a forced metric-alarm state is TEMPORARY (the docs: "returns
     to its actual state quickly, often within seconds"), and EventBridge only fires
     on a state TRANSITION, so a held OK edge after the cooldown can't be driven
     deterministically. For the cooldown-skip and step-up branches we invoke the REAL
     deployed controller Lambda directly with the REAL CloudWatch-alarm-state-change
     event shape (grounded from the AWS docs). Still no mocks: the Lambda, the
     AppConfig read/write, and the cooldown clock are all real — only the already-
     proven EventBridge hop is bypassed for the two auto-reverting branches.

Always restores L0 in `finally` so it never poisons shared AppConfig state
(the test-isolation lesson from sub-step 1).

Requires a deployed stack (run via `make e2e`); skips cleanly otherwise.
"""

import json
import os
import time

import boto3
import pytest

REGION = os.environ.get("AWS_REGION", "us-west-2")
STACK = os.environ.get("RECEIPTS_STACK", "AgentCore-ReceiptsAgent-dev")
ALARM = "ReceiptsAgent-LadderStepDowns"
CONTROLLER_FN = "ReceiptsAgent-Controller"
# Must match the controller's LADDER_COOLDOWN_SECONDS (CDK env). The test waits
# past this to prove the cooldown both blocks (branch B1) and releases (B2).
COOLDOWN_SECONDS = 60

pytestmark = pytest.mark.e2e


def _alarm_event(state: str) -> dict:
    """The real EventBridge 'CloudWatch Alarm State Change' detail shape (grounded
    from the AWS CloudWatch+EventBridge docs) — what the controller receives."""
    return {
        "source": "aws.cloudwatch",
        "detail-type": "CloudWatch Alarm State Change",
        "detail": {"alarmName": ALARM, "state": {"value": state, "reason": "e2e"}},
    }


def _invoke_controller(state: str) -> dict:
    """Invoke the REAL deployed controller Lambda with a real alarm-state event."""
    lam = boto3.client("lambda", region_name=REGION)
    resp = lam.invoke(
        FunctionName=CONTROLLER_FN,
        Payload=json.dumps(_alarm_event(state)).encode(),
    )
    return json.loads(resp["Payload"].read())


def _appconfig_ids():
    ac = boto3.client("appconfig", region_name=REGION)
    app = next((a for a in ac.list_applications()["Items"] if a["Name"] == "ReceiptsAgent-Ladder"), None)
    if not app:
        pytest.skip("AppConfig application not found")
    env = ac.list_environments(ApplicationId=app["Id"])["Items"][0]
    prof = ac.list_configuration_profiles(ApplicationId=app["Id"])["Items"][0]
    strat = next((s for s in ac.list_deployment_strategies()["Items"] if s["Name"] == "ReceiptsAgent-AllAtOnce"), None)
    return ac, app["Id"], env["Id"], prof["Id"], (strat["Id"] if strat else None)


def _active_rung(app_id, env_id, prof_id) -> str:
    """Read the DEPLOYED activeRung the way the agent does — via the appconfigdata
    data API (StartConfigurationSession -> GetLatestConfiguration). This reflects what
    has actually been DEPLOYED, not merely the latest created hosted version (a created-
    but-not-deployed version would mislead the assertion — caught when StartDeployment
    failed yet the version existed)."""
    data = boto3.client("appconfigdata", region_name=REGION)
    token = data.start_configuration_session(
        ApplicationIdentifier=app_id,
        EnvironmentIdentifier=env_id,
        ConfigurationProfileIdentifier=prof_id,
    )["InitialConfigurationToken"]
    raw = data.get_latest_configuration(ConfigurationToken=token)["Configuration"].read()
    if not raw:
        return "?"
    return json.loads(raw).get("activeRung", "?")


def _set_rung_l0(ac, app_id, env_id, prof_id, strat_id):
    """Restore activeRung=L0 directly (control plane), independent of the controller."""
    config = {
        "activeRung": "L0",
        "rungs": {
            "L0": {
                "model": "global.anthropic.claude-opus-4-8",
                "features": {
                    "validator": True,
                    "memoryRead": True,
                    "memoryWrite": True,
                    "merchantLookup": True,
                    "categoryInference": True,
                    "dedup": True,
                    "forceReview": False,
                },
            },
            "L1": {
                "model": "global.anthropic.claude-opus-4-7",
                "features": {
                    "validator": True,
                    "memoryRead": True,
                    "memoryWrite": False,
                    "merchantLookup": False,
                    "categoryInference": True,
                    "dedup": True,
                    "forceReview": False,
                },
            },
            "L2": {
                "model": "global.anthropic.claude-opus-4-6-v1",
                "features": {
                    "validator": False,
                    "memoryRead": False,
                    "memoryWrite": False,
                    "merchantLookup": False,
                    "categoryInference": False,
                    "dedup": True,
                    "forceReview": True,
                },
            },
            "L3": {
                "model": "global.anthropic.claude-sonnet-4-6",
                "features": {
                    "validator": False,
                    "memoryRead": False,
                    "memoryWrite": False,
                    "merchantLookup": False,
                    "categoryInference": False,
                    "dedup": True,
                    "forceReview": True,
                },
            },
            "L4": {"features": {"forceReview": True}},
        },
    }
    ver = ac.create_hosted_configuration_version(
        ApplicationId=app_id,
        ConfigurationProfileId=prof_id,
        Content=json.dumps(config).encode(),
        ContentType="application/json",
        Description="test cleanup: restore L0",
    )
    ac.start_deployment(
        ApplicationId=app_id,
        EnvironmentId=env_id,
        ConfigurationProfileId=prof_id,
        ConfigurationVersion=str(ver["VersionNumber"]),
        DeploymentStrategyId=strat_id,
    )
    _wait_complete(ac, app_id, env_id)


def _wait_complete(ac, app_id, env_id, tries=30):
    for _ in range(tries):
        deps = ac.list_deployments(ApplicationId=app_id, EnvironmentId=env_id, MaxResults=1)["Items"]
        if deps and deps[0]["State"] == "COMPLETE":
            return
        time.sleep(5)


def _force_alarm(state: str):
    boto3.client("cloudwatch", region_name=REGION).set_alarm_state(
        AlarmName=ALARM, StateValue=state, StateReason=f"e2e control-loop test: {state}"
    )


def _wait_for_rung(app_id, env_id, prof_id, want: str, tries=18, label="") -> str:
    """Poll the DEPLOYED activeRung until it equals `want` (the controller writes
    async via EventBridge). Returns the last seen rung."""
    seen = "?"
    for _ in range(tries):
        seen = _active_rung(app_id, env_id, prof_id)
        if seen == want:
            return seen
        time.sleep(5)
    return seen


def test_control_loop_full_wiring_steps_down_via_eventbridge():
    """Path A: the real alarm -> EventBridge -> controller -> AppConfig hop. Forcing
    the alarm to ALARM is a clean OK->ALARM edge, so EventBridge fires reliably."""
    ac, app_id, env_id, prof_id, strat_id = _appconfig_ids()
    assert strat_id, "deployment strategy not found"

    _set_rung_l0(ac, app_id, env_id, prof_id, strat_id)
    assert _active_rung(app_id, env_id, prof_id) == "L0"
    # The L0-restore deployment starts the cooldown clock; wait it out so the first
    # real step isn't wrongly skipped as cooldown.
    time.sleep(COOLDOWN_SECONDS + 5)

    try:
        _force_alarm("ALARM")
        rung = _wait_for_rung(app_id, env_id, prof_id, "L1", label="down-step")
        assert rung == "L1", f"alarm->EventBridge->controller should step L0->L1, got {rung}"
    finally:
        _set_rung_l0(ac, app_id, env_id, prof_id, strat_id)
        try:
            _force_alarm("OK")
        except Exception:
            pass


def test_controller_respects_cooldown_then_steps_up():
    """Path B: cooldown-skip + step-up branches, by invoking the REAL controller
    Lambda directly (auto-reverting forced states can't drive these via EventBridge).
    The Lambda, AppConfig I/O, and cooldown clock are all real."""
    ac, app_id, env_id, prof_id, strat_id = _appconfig_ids()
    assert strat_id, "deployment strategy not found"

    _set_rung_l0(ac, app_id, env_id, prof_id, strat_id)
    assert _active_rung(app_id, env_id, prof_id) == "L0"
    # The L0-restore deployment starts the cooldown clock; wait it out so the first
    # real step isn't wrongly skipped as cooldown.
    time.sleep(COOLDOWN_SECONDS + 5)

    try:
        # Step L0 -> L1 (this also starts the cooldown clock: a fresh deployment).
        out = _invoke_controller("ALARM")
        assert out.get("stepped") and out.get("to") == "L1", f"expected L0->L1, got {out}"
        _wait_complete(ac, app_id, env_id)
        assert _active_rung(app_id, env_id, prof_id) == "L1"

        # Immediately recover: the L0->L1 deployment is younger than the cooldown, so
        # the step-up must be SKIPPED (anti-flap).
        out = _invoke_controller("OK")
        assert out.get("skipped") == "cooldown", f"cooldown should block step-up, got {out}"
        assert _active_rung(app_id, env_id, prof_id) == "L1"

        # Wait out the cooldown, recover again -> step L1 -> L0.
        time.sleep(COOLDOWN_SECONDS + 5)
        out = _invoke_controller("OK")
        assert out.get("stepped") and out.get("to") == "L0", f"expected L1->L0 after cooldown, got {out}"
        _wait_complete(ac, app_id, env_id)
        assert _active_rung(app_id, env_id, prof_id) == "L0"
    finally:
        _set_rung_l0(ac, app_id, env_id, prof_id, strat_id)
