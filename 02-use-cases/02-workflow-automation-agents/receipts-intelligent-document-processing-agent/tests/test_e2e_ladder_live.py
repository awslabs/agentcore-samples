"""Phase 6 sub-step 1 end-to-end: config-driven model swap with NO redeploy.

Proves the degradation ladder's core promise (spec §6): the agent's model is set
by `activeRung` in AppConfig, and flipping it changes the model on the next
invocation without touching the stack. No mocks — real AppConfig + real Runtime.

Requires a deployed stack (run via `make e2e`); skips cleanly otherwise.
"""

import json
import os
import time
import uuid

import boto3
import pytest

REGION = os.environ.get("AWS_REGION", "us-west-2")
STACK = os.environ.get("RECEIPTS_STACK", "AgentCore-ReceiptsAgent-dev")

pytestmark = pytest.mark.e2e


def _runtime_arn():
    cfn = boto3.client("cloudformation", region_name=REGION)
    try:
        outs = cfn.describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", [])
    except Exception:
        pytest.skip(f"stack {STACK} not deployed")
    for o in outs:
        if o["OutputKey"].startswith("RuntimeArn"):
            return o["OutputValue"]
    pytest.skip("RuntimeArn output not present")


def _appconfig_ids():
    ac = boto3.client("appconfig", region_name=REGION)
    app = next((a for a in ac.list_applications()["Items"] if a["Name"] == "ReceiptsAgent-Ladder"), None)
    if not app:
        pytest.skip("AppConfig application not found")
    env = ac.list_environments(ApplicationId=app["Id"])["Items"][0]
    prof = ac.list_configuration_profiles(ApplicationId=app["Id"])["Items"][0]
    strat = next((s for s in ac.list_deployment_strategies()["Items"] if s["Name"] == "ReceiptsAgent-AllAtOnce"), None)
    return ac, app["Id"], env["Id"], prof["Id"], (strat["Id"] if strat else None)


def _invoke(arn) -> dict:
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    s3 = f"s3://receipts-inbox-{boto3.client('sts', region_name=REGION).get_caller_identity()['Account']}-{REGION}/nope.png"
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"ladder-{uuid.uuid4().hex}",
        payload=json.dumps({"s3_uri": s3, "user_id": "user-001"}).encode(),
    )
    raw = resp["response"]
    body = raw.read().decode() if hasattr(raw, "read") else raw
    return json.loads(body) if isinstance(body, str) else body


def _set_active_rung(ac, app_id, env_id, prof_id, strat_id, rung: str):
    """Flip activeRung by deploying a new hosted config version — control-plane
    only, NO CloudFormation/stack redeploy."""
    config = {
        "activeRung": rung,
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
    )
    ac.start_deployment(
        ApplicationId=app_id,
        EnvironmentId=env_id,
        ConfigurationProfileId=prof_id,
        ConfigurationVersion=str(ver["VersionNumber"]),
        DeploymentStrategyId=strat_id,
    )
    # Wait for the deployment to complete.
    for _ in range(30):
        deps = ac.list_deployments(ApplicationId=app_id, EnvironmentId=env_id)["Items"]
        if deps and deps[0]["State"] == "COMPLETE":
            return
        time.sleep(5)


def test_active_rung_drives_model_without_redeploy():
    arn = _runtime_arn()
    ac, app_id, env_id, prof_id, strat_id = _appconfig_ids()
    assert strat_id, "deployment strategy not found"

    try:
        # Default deploy is L0 -> Opus 4.8. (s3 is bogus, but the rung resolves first.)
        first = _invoke(arn)
        assert first.get("rung") == "L0", f"expected L0 default, got: {first}"

        # Flip to L3 in AppConfig — control plane only, no stack redeploy.
        _set_active_rung(ac, app_id, env_id, prof_id, strat_id, "L3")

        # The agent re-polls AppConfig (cache TTL); give it a few tries.
        swapped = None
        for _ in range(6):
            swapped = _invoke(arn)
            if swapped.get("rung") == "L3":
                break
            time.sleep(10)
        assert swapped.get("rung") == "L3", f"activeRung flip to L3 should change the rung; got: {swapped}"
    finally:
        # Restore L0 so this test doesn't poison shared AppConfig state for others.
        _set_active_rung(ac, app_id, env_id, prof_id, strat_id, "L0")
