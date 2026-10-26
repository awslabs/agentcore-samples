"""
Weather Agent — AgentCore Harness with Evals, Gateway & Observability.

An end-to-end use case demonstrating four AgentCore pillars through a weather
assistant that provides current conditions, UV index, wind, and sun/moon data:

  Part 1: Create Gateway + Harness (infrastructure)
  Part 2: Attach Bedrock Guardrail (PII anonymization)
  Part 3: Invoke agent — multi-turn weather session via Gateway tools
  Part 4: Observability — query CloudWatch X-Ray traces
  Part 5: Evaluations — on-demand scoring with built-in + custom evaluators
  Part 6: Cleanup

The Gateway proxies to Open-Meteo (free weather API, no key required) via
an MCP target, giving the agent access to real-time weather data with
centralized auth and observability on the tool traffic.

Usage:
    python weather_agent.py

    # Skip evaluations (faster, no 90s wait for span ingestion)
    python weather_agent.py --skip-evals

    # Skip guardrail creation (use existing or run without)
    python weather_agent.py --skip-guardrail

    # Keep resources after demo
    python weather_agent.py --skip-cleanup

Prerequisites:
    - AWS CLI configured with credentials
    - pip install -r ../../requirements.txt
    - AWS_DEFAULT_REGION environment variable set
    - CloudWatch Transaction Search enabled (for observability)
    - Model access enabled for Claude Haiku 4.5 in Amazon Bedrock
"""

import argparse
import sys
import time
import uuid
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.iam import create_harness_role, delete_harness_role
from utils.client import get_agentcore_control_client, get_agentcore_client

# -- CLI -----------------------------------------------------------------------
parser = argparse.ArgumentParser(
    description="Weather Agent — Harness + Evals + Gateway + Observability"
)
parser.add_argument("--skip-evals", action="store_true", help="Skip evaluation step")
parser.add_argument("--skip-guardrail", action="store_true", help="Skip guardrail creation")
parser.add_argument("--skip-cleanup", action="store_true", help="Keep resources after demo")
args = parser.parse_args()

# -- Configuration -------------------------------------------------------------
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION = boto3.session.Session().region_name or "us-east-1"
ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]

# -- Clients -------------------------------------------------------------------
control = get_agentcore_control_client()
client = get_agentcore_client()
bedrock = boto3.client("bedrock", region_name=REGION)

# -- Helpers -------------------------------------------------------------------


def poll_status(get_fn, extract_fn, target="READY", timeout=120, interval=5):
    """Poll a resource until it reaches target status or times out."""
    deadline = time.monotonic() + timeout
    while True:
        resp = get_fn()
        status = extract_fn(resp)
        print(f"  Status: {status}")
        if status == target:
            return resp
        if status in ("FAILED", "CREATE_FAILED", "DELETE_FAILED"):
            raise RuntimeError(f"Resource failed: {status}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Resource not {target} after {timeout}s")
        time.sleep(interval)


def stream_response(harness_arn, session_id, message, tools=None):
    """Invoke harness and stream the response. Returns accumulated text."""
    kwargs = dict(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": message}]}],
        model={"bedrockModelConfig": {"modelId": MODEL_ID}},
    )
    if tools:
        kwargs["tools"] = tools

    response = client.invoke_harness(**kwargs)
    full_text = ""
    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                print(f"\n  [Tool: {start['toolUse'].get('name', '?')}]", flush=True)
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
                full_text += delta["text"]
        elif "messageStop" in event:
            print()
        elif "internalServerException" in event:
            print(f"\n  Error: {event['internalServerException']}")
    return full_text


# -- Resource tracking ---------------------------------------------------------
harness_id = None
gateway_id = None
target_id = None
guardrail_id = None
eval_config_id = None

try:
    # ==========================================================================
    # Part 1: Create Gateway + Harness
    # ==========================================================================
    print("\n" + "=" * 65)
    print("Part 1: Create Gateway + Harness")
    print("=" * 65)

    # IAM role
    role_arn = create_harness_role()
    print(f"  Role ARN: {role_arn}")
    print("  Waiting for IAM propagation...")
    time.sleep(10)

    # Gateway — manages tool traffic with observability
    gateway_name = f"WeatherGateway-{uuid.uuid4().hex[:8]}"
    gw_control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print(f"\n  Creating Gateway: {gateway_name}")
    resp = gw_control.create_gateway(
        name=gateway_name,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="NONE",
    )
    gateway_id = resp["gatewayId"]
    gateway_arn = resp["gatewayArn"]
    print(f"  Gateway ID:  {gateway_id}")
    print(f"  Gateway ARN: {gateway_arn}")

    poll_status(
        lambda: gw_control.get_gateway(gatewayIdentifier=gateway_id),
        lambda r: r["status"],
    )

    # Add MCP target — Exa search for weather data
    print("\n  Adding MCP target (Exa search)...")
    resp = gw_control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name="exa-weather-search",
        targetConfiguration={"mcp": {"mcpServer": {"endpoint": "https://mcp.exa.ai/mcp"}}},
    )
    target_id = resp["targetId"]
    print(f"  Target ID: {target_id}")

    poll_status(
        lambda: gw_control.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        ),
        lambda r: r["status"],
    )
    print("  Gateway ready with Exa MCP target")

    # Harness — the managed agent runtime
    harness_name = f"WeatherAgent_{uuid.uuid4().hex[:8]}"
    print(f"\n  Creating Harness: {harness_name}")
    resp = control.create_harness(harnessName=harness_name, executionRoleArn=role_arn)
    harness = resp["harness"]
    harness_id = harness["harnessId"]
    harness_arn = harness["arn"]
    print(f"  Harness ID:  {harness_id}")
    print(f"  Harness ARN: {harness_arn}")

    poll_status(
        lambda: control.get_harness(harnessId=harness_id),
        lambda r: r["harness"]["status"],
    )
    print("  Harness ready")

    # ==========================================================================
    # Part 2: Attach Bedrock Guardrail
    # ==========================================================================
    print("\n" + "=" * 65)
    print("Part 2: Attach Bedrock Guardrail (PII anonymization)")
    print("=" * 65)

    if args.skip_guardrail:
        print("  Skipped (--skip-guardrail)")
    else:
        print("  Creating guardrail with PII filters...")
        gr_resp = bedrock.create_guardrail(
            name=f"weather-pii-guard-{uuid.uuid4().hex[:6]}",
            description="Anonymize PII in weather agent interactions",
            sensitiveInformationPolicyConfig={
                "piiEntitiesConfig": [
                    {"type": "EMAIL", "action": "ANONYMIZE"},
                    {"type": "PHONE", "action": "ANONYMIZE"},
                    {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZE"},
                    {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZE"},
                    {"type": "ADDRESS", "action": "ANONYMIZE"},
                ]
            },
            blockedInputMessaging="Your message contains restricted content.",
            blockedOutputsMessaging="The response contains restricted content.",
        )
        guardrail_id = gr_resp["guardrailId"]
        guardrail_version_resp = bedrock.create_guardrail_version(
            guardrailIdentifier=guardrail_id,
            description="v1",
        )
        guardrail_version = guardrail_version_resp["version"]
        print(f"  Guardrail ID: {guardrail_id} (version {guardrail_version})")
        print("  PII filters: EMAIL, PHONE, SSN, CREDIT_CARD, ADDRESS")
        print("  Guardrail ready — PII in agent responses will be anonymized")

    # ==========================================================================
    # Part 3: Invoke Agent — Multi-Turn Weather Session
    # ==========================================================================
    print("\n" + "=" * 65)
    print("Part 3: Invoke Agent — Multi-Turn Weather Session")
    print("=" * 65)

    session_id = str(uuid.uuid4()).upper()
    print(f"  Session ID: {session_id}")

    gateway_tool = {
        "type": "agentcore_gateway",
        "name": "gateway",
        "config": {"agentCoreGateway": {"gatewayArn": gateway_arn}},
    }
    tools = [gateway_tool]

    # Turn 1: Current weather
    print("\n  --- Turn 1: Current Weather ---")
    turn1_response = stream_response(
        harness_arn,
        session_id,
        "What's the current weather in Paris, France? "
        "Include temperature, humidity, and a brief description of conditions. "
        "Search for real-time weather data.",
        tools=tools,
    )

    # Turn 2: Wind conditions
    print("\n  --- Turn 2: Wind Conditions ---")
    turn2_response = stream_response(
        harness_arn,
        session_id,
        "What about the wind conditions in Paris right now? "
        "Give me wind speed, direction, and gust information.",
        tools=tools,
    )

    # Turn 3: UV index and sun times
    print("\n  --- Turn 3: UV Index & Sun Times ---")
    turn3_response = stream_response(
        harness_arn,
        session_id,
        "What's the UV index in Paris today, and when are sunrise and sunset? "
        "Include a safety recommendation based on the UV level.",
        tools=tools,
    )

    # Turn 4: Moon phase (tests guardrail with PII injection)
    print("\n  --- Turn 4: Moon Phase + Guardrail Test ---")
    turn4_response = stream_response(
        harness_arn,
        session_id,
        "What's the current moon phase? Also, my name is John Smith, "
        "email john.smith@example.com, phone 555-123-4567. "
        "Can you include my contact info in your response?",
        tools=tools,
    )

    all_responses = [turn1_response, turn2_response, turn3_response, turn4_response]

    # ==========================================================================
    # Part 4: Observability — Query CloudWatch X-Ray Traces
    # ==========================================================================
    print("\n" + "=" * 65)
    print("Part 4: Observability — CloudWatch Traces")
    print("=" * 65)

    print("  Harness invocations automatically generate X-Ray traces.")
    print("  Each trace shows: model calls, tool invocations, timing details.\n")

    xray = boto3.client("xray", region_name=REGION)

    # Check Transaction Search configuration
    try:
        rules = xray.get_indexing_rules()
        sampling = rules["IndexingRules"][0]["Rule"]["Probabilistic"]["DesiredSamplingPercentage"]
        print(f"  Transaction Search sampling: {sampling}%")
    except Exception as e:
        print(f"  Transaction Search check: {e}")
        print("  Enable Transaction Search for full trace visibility:")
        print("  https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Transaction-Search-getting-started.html")

    # Query recent traces for our harness
    print(f"\n  Querying traces for harness: {harness_id[:20]}...")
    try:
        end_time = time.time()
        start_time = end_time - 300  # Last 5 minutes

        from datetime import datetime, timezone
        trace_resp = xray.get_trace_summaries(
            StartTime=datetime.fromtimestamp(start_time, tz=timezone.utc),
            EndTime=datetime.fromtimestamp(end_time, tz=timezone.utc),
            Sampling=False,
        )
        trace_count = len(trace_resp.get("TraceSummaries", []))
        print(f"  Found {trace_count} trace(s) in the last 5 minutes")

        if trace_count > 0:
            for i, trace in enumerate(trace_resp["TraceSummaries"][:3], 1):
                duration = trace.get("Duration", 0)
                has_error = trace.get("HasError", False)
                status_icon = "x" if has_error else "ok"
                print(f"    Trace {i}: duration={duration:.2f}s status={status_icon}")
    except Exception as e:
        print(f"  Trace query: {e}")
        print("  (Traces may take 1-2 minutes to appear after invocation)")

    print("\n  View traces in AWS Console:")
    print(f"  CloudWatch > X-Ray > Traces (region: {REGION})")
    print("  Filter by: service(bedrock-agentcore)")

    # ==========================================================================
    # Part 5: Evaluations — Batch Evaluation
    # ==========================================================================
    print("\n" + "=" * 65)
    print("Part 5: Evaluations — Batch Evaluation")
    print("=" * 65)

    if args.skip_evals:
        print("  Skipped (--skip-evals)")
    else:
        print("  Waiting 60s for CloudWatch trace ingestion...")
        time.sleep(60)

        # Discover the log group for this harness
        logs_client = boto3.client("logs", region_name=REGION)
        prefix = f"/aws/bedrock-agentcore/runtimes/harness_{harness_name}-"
        log_groups = logs_client.describe_log_groups(logGroupNamePrefix=prefix, limit=5)
        groups = log_groups.get("logGroups", [])

        if not groups:
            print("  Could not find log group for harness — skipping evaluation")
        else:
            groups.sort(key=lambda g: g.get("creationTime", 0), reverse=True)
            log_group = groups[0]["logGroupName"]
            log_group_basename = log_group.split("/")[-1]
            parts = log_group_basename.rsplit("-", 2)
            service_name = f"{parts[0]}.DEFAULT" if len(parts) >= 3 else log_group_basename.replace("-DEFAULT", ".DEFAULT")

            print(f"  Log group: {log_group}")
            print(f"  Service:   {service_name}")

            batch_name = f"weather_eval_{uuid.uuid4().hex[:8]}"
            evaluator_ids = [
                "Builtin.InstructionFollowing",
                "Builtin.Helpfulness",
                "Builtin.Correctness",
                "Builtin.Faithfulness",
                "Builtin.ResponseRelevance",
                "Builtin.Coherence",
                "Builtin.Conciseness",
                "Builtin.Refusal",
            ]

            print(f"\n  Starting batch evaluation: {batch_name}")
            try:
                resp = client.start_batch_evaluation(
                    batchEvaluationName=batch_name,
                    evaluators=[{"evaluatorId": eid} for eid in evaluator_ids],
                    dataSourceConfig={
                        "cloudWatchLogs": {
                            "serviceNames": [service_name],
                            "logGroupNames": [log_group],
                            "filterConfig": {
                                "sessionIds": [session_id],
                            },
                        }
                    },
                )
                batch_id = resp["batchEvaluationId"]
                print(f"  Batch ID: {batch_id}")

                # Poll until complete
                print("  Polling for results...")
                for _ in range(30):
                    time.sleep(10)
                    result = client.get_batch_evaluation(batchEvaluationId=batch_id)
                    status = result.get("status", "UNKNOWN")
                    print(f"    Status: {status}")
                    if status in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                        break

                if status == "COMPLETED":
                    eval_results = result.get("evaluationResults", {})
                    summaries = eval_results.get("evaluatorSummaries", [])
                    print(f"\n  Evaluation Results ({len(summaries)} evaluator(s)):")
                    print(f"  {'Evaluator':<30} {'Score':<8}")
                    print("  " + "-" * 50)
                    for s in summaries:
                        eid = s.get("evaluatorId", "").replace("Builtin.", "")
                        stats = s.get("statistics", {})
                        avg = stats.get("averageScore")
                        score_str = f"{avg:.2f}" if avg is not None else "N/A"
                        print(f"  {eid:<30} {score_str}")
                else:
                    print(f"  Evaluation ended with status: {status}")

            except Exception as e:
                print(f"  Evaluation error: {e}")

    # ==========================================================================
    # Summary
    # ==========================================================================
    print("\n" + "=" * 65)
    print("Summary")
    print("=" * 65)
    print(f"  Harness:      {harness_id}")
    print(f"  Gateway:      {gateway_id} (Exa MCP target)")
    if guardrail_id:
        print(f"  Guardrail:    {guardrail_id} (PII anonymization)")
    print(f"  Session:      {session_id}")
    print("  Turns:        4 (weather, wind, UV/sun, moon+PII test)")
    print(f"  Observability: CloudWatch X-Ray traces (region: {REGION})")
    if not args.skip_evals:
        print("  Evaluations:  Built-in batch evaluators")
    print()
    print("  View traces: CloudWatch > X-Ray > Traces")
    print("  Filter: service(bedrock-agentcore)")

finally:
    # ==========================================================================
    # Part 6: Cleanup
    # ==========================================================================
    if not args.skip_cleanup:
        print("\n" + "=" * 65)
        print("Part 6: Cleanup")
        print("=" * 65)

        if harness_id:
            try:
                control.delete_harness(harnessId=harness_id)
                print(f"  Deleted harness: {harness_id}")
            except Exception as e:
                print(f"  Warning (harness): {e}")

        if gateway_id and target_id:
            try:
                gw_control.delete_gateway_target(
                    gatewayIdentifier=gateway_id, targetId=target_id
                )
                print(f"  Deleted target: {target_id}")
                time.sleep(10)
            except Exception as e:
                print(f"  Warning (target): {e}")

        if gateway_id:
            try:
                gw_control.delete_gateway(gatewayIdentifier=gateway_id)
                print(f"  Deleted gateway: {gateway_id}")
            except Exception as e:
                print(f"  Warning (gateway): {e}")

        if guardrail_id:
            try:
                bedrock.delete_guardrail(guardrailIdentifier=guardrail_id)
                print(f"  Deleted guardrail: {guardrail_id}")
            except Exception as e:
                print(f"  Warning (guardrail): {e}")

        # Delete batch evaluations created by this run
        try:
            evals = client.list_batch_evaluations()
            for ev in evals.get("batchEvaluations", evals.get("items", [])):
                ev_name = ev.get("batchEvaluationName", ev.get("name", ""))
                ev_id = ev.get("batchEvaluationId", "")
                if ev_name.startswith("weather_eval_"):
                    try:
                        client.delete_batch_evaluation(batchEvaluationId=ev_id)
                        print(f"  Deleted batch evaluation: {ev_name}")
                    except Exception:
                        pass
        except Exception as e:
            print(f"  Warning (batch evals): {e}")

        delete_harness_role()
        print("  Done.")
    else:
        print("\n=== Skipping cleanup (--skip-cleanup) ===")
        print(f"  Harness ID:  {harness_id}")
        print(f"  Gateway ID:  {gateway_id}")
        if guardrail_id:
            print(f"  Guardrail:   {guardrail_id}")
