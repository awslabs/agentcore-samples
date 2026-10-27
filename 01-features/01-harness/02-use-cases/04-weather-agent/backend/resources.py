"""AWS resource lifecycle — create or reuse Gateway, Harness, Guardrail."""

import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from agent import SYSTEM_PROMPT
from utils.client import get_agentcore_control_client
from utils.iam import create_harness_role, delete_harness_role

STATE_FILE = Path(__file__).parent.parent / "resource_info.json"
# Honour both region variables, in the same order utils/client.py uses. Reading
# only AWS_DEFAULT_REGION meant a shell that exported just AWS_REGION fell
# through to the us-east-1 default, so these boto3 clients created the gateway,
# harness and guardrail in a different region from the ones utils/client.py
# builds — which do read AWS_REGION. The resources existed, in the wrong place,
# and every later lookup came back empty.
REGION = (
    os.environ.get("AWS_DEFAULT_REGION")
    or os.environ.get("AWS_REGION")
    or boto3.session.Session().region_name
    or "us-east-1"
)


def _poll(get_fn, extract_fn, target="READY", timeout=600, interval=5):
    """Poll until `target`, matching the ceiling used by utils/harness.py.

    UPDATE_FAILED is in the failure set as well — without it an update that
    failed would be polled as "not ready yet" until the timeout, replacing the
    real status with a misleading TimeoutError.
    """
    deadline = time.monotonic() + timeout
    while True:
        resp = get_fn()
        status = extract_fn(resp)
        if status == target:
            return resp
        if status in ("FAILED", "CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"):
            raise RuntimeError(f"Resource failed: {status}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Not {target} after {timeout}s (current: {status})")
        time.sleep(interval)


def _all_pages(client_obj, operation, key):
    """Yield every item from a paginated list operation.

    These list APIs paginate. Reading only the first page meant the cleanup below
    silently skipped this app's own resources once the account held more than a
    page of them — which is the one thing a cleanup path must not do.
    """
    try:
        if client_obj.can_paginate(operation):
            for page in client_obj.get_paginator(operation).paginate():
                yield from page.get(key, [])
            return
    except Exception as e:  # noqa: BLE001 - fall back to a single call
        print(f"  Warning (paginating {operation}): {e}")
    yield from getattr(client_obj, operation)().get(key, [])


def _load_state() -> dict | None:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _resources_alive(state: dict) -> bool:
    try:
        control = get_agentcore_control_client()
        h = control.get_harness(harnessId=state["harness_id"])
        if h["harness"]["status"] != "READY":
            return False
        gw = boto3.client("bedrock-agentcore-control", region_name=REGION)
        g = gw.get_gateway(gatewayIdentifier=state["gateway_id"])
        return g["status"] == "READY"
    except Exception:  # noqa: BLE001 - any failure here means 'not reusable'
        return False


def ensure_resources() -> dict:
    """Create or reuse all AWS resources. Returns resource dict."""
    existing = _load_state()
    if existing and _resources_alive(existing):
        print("[resources] Reusing existing resources")
        return existing

    print("[resources] Provisioning new resources...")
    control = get_agentcore_control_client()
    gw_control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    bedrock = boto3.client("bedrock", region_name=REGION)

    # A stale state file describes resources that are gone or unhealthy, but it
    # may still name ones that are alive (e.g. the harness died and the gateway
    # did not). The new ids below overwrite the old ones, so remember the old
    # resources under `orphans` — destroy_resources deletes those too. Without
    # this, every re-provision silently stranded the previous run's gateway,
    # harness and guardrail, all of which keep billing.
    state = {"region": REGION, "orphans": list((existing or {}).get("orphans", []))}
    if existing:
        for key, kind in (
            ("harness_id", "harness"),
            ("gateway_id", "gateway"),
            ("guardrail_id", "guardrail"),
        ):
            if existing.get(key):
                orphan = {"kind": kind, "id": existing[key]}
                if kind == "gateway" and existing.get("target_id"):
                    orphan["target_id"] = existing["target_id"]
                if orphan not in state["orphans"]:
                    state["orphans"].append(orphan)
    # Saved after every create, so a failure part-way through still leaves a
    # state file naming what already exists. Previously state was written only at
    # the very end, so a failure after the gateway was created left a live,
    # billable gateway (and harness) that cleanup.sh could not see.
    _save_state(state)

    # IAM role
    role_arn = create_harness_role()
    state["role_arn"] = role_arn
    _save_state(state)
    time.sleep(10)

    # Gateway
    gateway_name = f"WeatherGW-{uuid.uuid4().hex[:8]}"
    resp = gw_control.create_gateway(name=gateway_name, roleArn=role_arn, protocolType="MCP", authorizerType="NONE")
    gateway_id = resp["gatewayId"]
    gateway_arn = resp["gatewayArn"]
    state.update(gateway_id=gateway_id, gateway_arn=gateway_arn, gateway_name=gateway_name)
    _save_state(state)
    _poll(
        lambda: gw_control.get_gateway(gatewayIdentifier=gateway_id),
        lambda r: r["status"],
    )

    # MCP target (Exa search)
    resp = gw_control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name="exa-weather",
        targetConfiguration={"mcp": {"mcpServer": {"endpoint": "https://mcp.exa.ai/mcp"}}},
    )
    target_id = resp["targetId"]
    state["target_id"] = target_id
    _save_state(state)
    _poll(
        lambda: gw_control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id),
        lambda r: r["status"],
    )

    # Harness
    harness_name = f"WeatherAgent_{uuid.uuid4().hex[:8]}"
    resp = control.create_harness(
        harnessName=harness_name,
        executionRoleArn=role_arn,
        systemPrompt=[{"text": SYSTEM_PROMPT}],
    )
    harness_id = resp["harness"]["harnessId"]
    harness_arn = resp["harness"]["arn"]
    state.update(harness_id=harness_id, harness_arn=harness_arn, harness_name=harness_name)
    _save_state(state)
    _poll(
        lambda: control.get_harness(harnessId=harness_id),
        lambda r: r["harness"]["status"],
    )

    # Guardrail
    guardrail_id = None
    guardrail_version = None
    guardrail_name = None
    try:
        guardrail_name = f"weather-pii-{uuid.uuid4().hex[:6]}"
        # No ADDRESS filter. Bedrock classifies a bare city name as an ADDRESS,
        # so with it enabled every weather answer came back with its location
        # replaced by "{ADDRESS}" — measured directly: "Current Weather in
        # {ADDRESS}". It also buys nothing for this demo, because the PII the app
        # is meant to catch (email, phone) is redacted identically without it.
        gr = bedrock.create_guardrail(
            name=guardrail_name,
            description="Anonymize PII in weather agent responses",
            sensitiveInformationPolicyConfig={
                "piiEntitiesConfig": [
                    {"type": "EMAIL", "action": "ANONYMIZE"},
                    {"type": "PHONE", "action": "ANONYMIZE"},
                    {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZE"},
                    {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZE"},
                ]
            },
            blockedInputMessaging="Content blocked.",
            blockedOutputsMessaging="Content blocked.",
        )
        guardrail_id = gr["guardrailId"]
        state.update(guardrail_id=guardrail_id, guardrail_name=guardrail_name)
        _save_state(state)
        gv = bedrock.create_guardrail_version(guardrailIdentifier=guardrail_id, description="v1")
        guardrail_version = gv["version"]
        # Wait for the version before anything tries to use it: ApplyGuardrail
        # against a version that is still CREATING fails, and agent.py treats a
        # screening failure as "pass the text through" — so the first few
        # responses would have gone out unscreened.
        _poll(
            lambda: bedrock.get_guardrail(guardrailIdentifier=guardrail_id, guardrailVersion=guardrail_version),
            lambda r: r["status"],
        )
    except Exception as e:  # noqa: BLE001 - guardrail is optional; the run continues without it
        print(f"[resources] Guardrail creation failed (non-critical): {e}")

    state.update(
        guardrail_id=guardrail_id,
        guardrail_name=guardrail_name,
        guardrail_version=guardrail_version,
    )
    _save_state(state)
    print("[resources] All resources ready")
    return state


def destroy_resources():
    """Delete all resources and remove state file."""
    state = _load_state()
    if not state:
        print("[resources] No state file found")
        return

    control = get_agentcore_control_client()
    gw_control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    bedrock = boto3.client("bedrock", region_name=REGION)

    # Resources a previous re-provision replaced. They are still live and still
    # billing, so delete them before the current set.
    for orphan in state.get("orphans", []):
        kind, oid = orphan.get("kind"), orphan.get("id")
        try:
            if kind == "harness":
                control.delete_harness(harnessId=oid)
            elif kind == "gateway":
                if orphan.get("target_id"):
                    try:
                        gw_control.delete_gateway_target(gatewayIdentifier=oid, targetId=orphan["target_id"])
                        time.sleep(10)
                    except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                        print(f"  Warning (orphan target {orphan['target_id']}): {e}")
                gw_control.delete_gateway(gatewayIdentifier=oid)
            elif kind == "guardrail":
                bedrock.delete_guardrail(guardrailIdentifier=oid)
            else:
                continue
            print(f"  Deleted orphaned {kind}: {oid}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"  Warning (orphan {kind} {oid}): {e}")

    if state.get("harness_id"):
        try:
            control.delete_harness(harnessId=state["harness_id"])
            print(f"  Deleted harness: {state['harness_id']}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"  Warning: {e}")

    if state.get("gateway_id") and state.get("target_id"):
        try:
            gw_control.delete_gateway_target(gatewayIdentifier=state["gateway_id"], targetId=state["target_id"])
            print(f"  Deleted target: {state['target_id']}")
            time.sleep(10)
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"  Warning: {e}")

    if state.get("gateway_id"):
        try:
            gw_control.delete_gateway(gatewayIdentifier=state["gateway_id"])
            print(f"  Deleted gateway: {state['gateway_id']}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"  Warning: {e}")

    if state.get("guardrail_id"):
        try:
            bedrock.delete_guardrail(guardrailIdentifier=state["guardrail_id"])
            print(f"  Deleted guardrail: {state['guardrail_id']}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"  Warning: {e}")

    # Delete batch evaluations created by this app. Both of these list calls
    # paginate, and reading only the first page meant that once an account had a
    # few jobs or recommendations, this app's own were quietly left behind — the
    # deletes below also swallowed their errors with a bare `pass`, so nothing
    # said so. Page through, and report what fails.
    dp_client = boto3.client("bedrock-agentcore", region_name=REGION)
    try:
        for ev in _all_pages(dp_client, "list_batch_evaluations", "batchEvaluations"):
            ev_name = ev.get("batchEvaluationName", ev.get("name", ""))
            ev_id = ev.get("batchEvaluationId", "")
            if ev_name.startswith("weather_eval_"):
                try:
                    dp_client.delete_batch_evaluation(batchEvaluationId=ev_id)
                    print(f"  Deleted batch evaluation: {ev_name}")
                except Exception as e:  # noqa: BLE001 - cleanup must continue
                    print(f"  Warning (batch eval {ev_name}): {e}")
    except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
        print(f"  Warning (batch evals): {e}")

    # Delete recommendations created by this app
    try:
        for rec in _all_pages(dp_client, "list_recommendations", "recommendationSummaries"):
            rec_name = rec.get("name", "")
            rec_id = rec.get("recommendationId", "")
            if rec_name.startswith("weather_rec_"):
                try:
                    dp_client.delete_recommendation(recommendationId=rec_id)
                    print(f"  Deleted recommendation: {rec_name}")
                except Exception as e:  # noqa: BLE001 - cleanup must continue
                    print(f"  Warning (recommendation {rec_name}): {e}")
    except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
        print(f"  Warning (recommendations): {e}")

    delete_harness_role()
    STATE_FILE.unlink(missing_ok=True)
    print("[resources] Cleanup complete")
