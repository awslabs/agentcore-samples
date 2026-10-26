"""AWS resource lifecycle — create or reuse Gateway, Harness, Guardrail."""

import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from utils.iam import create_harness_role, delete_harness_role
from utils.client import get_agentcore_control_client

STATE_FILE = Path(__file__).parent.parent / "resource_info.json"
REGION = os.environ.get("AWS_DEFAULT_REGION") or boto3.session.Session().region_name or "us-east-1"


def _poll(get_fn, extract_fn, target="READY", timeout=300, interval=5):
    deadline = time.monotonic() + timeout
    while True:
        resp = get_fn()
        status = extract_fn(resp)
        if status == target:
            return resp
        if status in ("FAILED", "CREATE_FAILED", "DELETE_FAILED"):
            raise RuntimeError(f"Resource failed: {status}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Not {target} after {timeout}s")
        time.sleep(interval)


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
        if g["status"] != "READY":
            return False
        return True
    except Exception:
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

    # IAM role
    role_arn = create_harness_role()
    time.sleep(10)

    # Gateway
    gateway_name = f"WeatherGW-{uuid.uuid4().hex[:8]}"
    resp = gw_control.create_gateway(
        name=gateway_name, roleArn=role_arn, protocolType="MCP", authorizerType="NONE"
    )
    gateway_id = resp["gatewayId"]
    gateway_arn = resp["gatewayArn"]
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
    _poll(
        lambda: gw_control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id),
        lambda r: r["status"],
    )

    # Harness
    harness_name = f"WeatherAgent_{uuid.uuid4().hex[:8]}"
    resp = control.create_harness(
        harnessName=harness_name,
        executionRoleArn=role_arn,
        systemPrompt=[
            {
                "text": (
                    "You are a weather assistant. You ONLY answer questions about weather, "
                    "climate, and atmospheric conditions (temperature, wind, humidity, UV index, "
                    "sunrise, sunset, moon phase, forecasts, air quality, precipitation). "
                    "If the user asks about anything unrelated to weather, politely redirect them. "
                    "For example: 'I'm a weather assistant — I can help with forecasts, current conditions, "
                    "UV index, wind, sunrise/sunset, and more. What location would you like weather for?' "
                    "When answering weather questions: always search for real-time data using your tools, "
                    "include specific numbers with units (temperature in F/C, wind in km/h or mph), "
                    "mention the city name in your response, and keep responses concise and well-structured."
                )
            }
        ],
    )
    harness_id = resp["harness"]["harnessId"]
    harness_arn = resp["harness"]["arn"]
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
        gr = bedrock.create_guardrail(
            name=guardrail_name,
            description="Anonymize PII in weather agent responses",
            sensitiveInformationPolicyConfig={
                "piiEntitiesConfig": [
                    {"type": "EMAIL", "action": "ANONYMIZE"},
                    {"type": "PHONE", "action": "ANONYMIZE"},
                    {"type": "ADDRESS", "action": "ANONYMIZE"},
                    {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZE"},
                ]
            },
            blockedInputMessaging="Content blocked.",
            blockedOutputsMessaging="Content blocked.",
        )
        guardrail_id = gr["guardrailId"]
        gv = bedrock.create_guardrail_version(guardrailIdentifier=guardrail_id, description="v1")
        guardrail_version = gv["version"]
    except Exception as e:
        print(f"[resources] Guardrail creation failed (non-critical): {e}")

    state = {
        "gateway_id": gateway_id,
        "gateway_arn": gateway_arn,
        "gateway_name": gateway_name,
        "target_id": target_id,
        "harness_id": harness_id,
        "harness_arn": harness_arn,
        "harness_name": harness_name,
        "guardrail_id": guardrail_id,
        "guardrail_name": guardrail_name,
        "guardrail_version": guardrail_version,
        "role_arn": role_arn,
        "region": REGION,
    }
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

    if state.get("harness_id"):
        try:
            control.delete_harness(harnessId=state["harness_id"])
            print(f"  Deleted harness: {state['harness_id']}")
        except Exception as e:
            print(f"  Warning: {e}")

    if state.get("gateway_id") and state.get("target_id"):
        try:
            gw_control.delete_gateway_target(
                gatewayIdentifier=state["gateway_id"], targetId=state["target_id"]
            )
            print(f"  Deleted target: {state['target_id']}")
            time.sleep(10)
        except Exception as e:
            print(f"  Warning: {e}")

    if state.get("gateway_id"):
        try:
            gw_control.delete_gateway(gatewayIdentifier=state["gateway_id"])
            print(f"  Deleted gateway: {state['gateway_id']}")
        except Exception as e:
            print(f"  Warning: {e}")

    if state.get("guardrail_id"):
        try:
            bedrock.delete_guardrail(guardrailIdentifier=state["guardrail_id"])
            print(f"  Deleted guardrail: {state['guardrail_id']}")
        except Exception as e:
            print(f"  Warning: {e}")

    # Delete batch evaluations created by this app
    dp_client = boto3.client("bedrock-agentcore", region_name=REGION)
    try:
        evals = dp_client.list_batch_evaluations()
        for ev in evals.get("batchEvaluations", evals.get("items", [])):
            ev_name = ev.get("batchEvaluationName", ev.get("name", ""))
            ev_id = ev.get("batchEvaluationId", "")
            if ev_name.startswith("weather_eval_"):
                try:
                    dp_client.delete_batch_evaluation(batchEvaluationId=ev_id)
                    print(f"  Deleted batch evaluation: {ev_name}")
                except Exception:
                    pass
    except Exception as e:
        print(f"  Warning (batch evals): {e}")

    # Delete recommendations created by this app
    try:
        recs = dp_client.list_recommendations()
        for rec in recs.get("recommendationSummaries", recs.get("recommendations", recs.get("items", []))):
            rec_name = rec.get("name", "")
            rec_id = rec.get("recommendationId", "")
            if rec_name.startswith("weather_rec_"):
                try:
                    dp_client.delete_recommendation(recommendationId=rec_id)
                    print(f"  Deleted recommendation: {rec_name}")
                except Exception:
                    pass
    except Exception as e:
        print(f"  Warning (recommendations): {e}")

    delete_harness_role()
    STATE_FILE.unlink(missing_ok=True)
    print("[resources] Cleanup complete")
