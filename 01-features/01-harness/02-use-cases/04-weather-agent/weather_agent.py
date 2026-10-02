"""
Weather Agent — AgentCore Harness with Evals, Gateway & Observability.

An end-to-end use case demonstrating four AgentCore pillars through a weather
assistant that provides current conditions, UV index, wind, and sun/moon data:

  Part 1: Create Gateway + Harness (infrastructure)
  Part 2: Apply Bedrock Guardrail (PII anonymization)
  Part 3: Invoke agent — multi-turn weather session via Gateway tools
  Part 4: Observability — query CloudWatch X-Ray traces
  Part 5: Evaluations — on-demand scoring with built-in + custom evaluators
  Part 6: Cleanup

The Gateway proxies to the Exa MCP server (web search, no key required) via an
MCP target, so the agent searches the live web for weather data with centralized
auth and observability on the tool traffic.

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
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import get_agentcore_client, get_agentcore_control_client
from utils.iam import create_harness_role, delete_harness_role

# -- CLI -----------------------------------------------------------------------
parser = argparse.ArgumentParser(description="Weather Agent — Harness + Evals + Gateway + Observability")
parser.add_argument("--skip-evals", action="store_true", help="Skip evaluation step")
parser.add_argument("--skip-guardrail", action="store_true", help="Skip guardrail creation")
parser.add_argument("--skip-cleanup", action="store_true", help="Keep resources after demo")
# run.sh has always passed this for its `--cleanup` option, but the flag was never
# defined here — so `./run.sh --cleanup`, the documented way to remove what a
# previous `--keep` run left behind, failed with "unrecognized arguments" every
# time. That is the one path a user reaches *because* resources were left running.
parser.add_argument(
    "--cleanup-only",
    action="store_true",
    help="Delete leftover WeatherAgent/WeatherGateway resources and exit (no demo)",
)
args = parser.parse_args()

# -- Configuration -------------------------------------------------------------
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
# Resolved the same way utils/client.py does it, so the bedrock clients below end
# up in the same region as the harness clients. A bare Session().region_name does
# not read AWS_REGION, so a shell exporting only that (and no configured profile
# region) silently sent these two clients to us-east-1 while the harness was
# created wherever AWS_REGION pointed.
REGION = (
    os.environ.get("AWS_DEFAULT_REGION")
    or os.environ.get("AWS_REGION")
    or boto3.session.Session().region_name
    or "us-east-1"
)
ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]

# -- Clients -------------------------------------------------------------------
control = get_agentcore_control_client()
client = get_agentcore_client()
bedrock = boto3.client("bedrock", region_name=REGION)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)

# -- Helpers -------------------------------------------------------------------


def poll_status(get_fn, extract_fn, target="READY", timeout=600, interval=5):
    """Poll a resource until it reaches target status or times out.

    A harness takes ~150s to reach READY, so the old 120s ceiling expired while
    it was still CREATING — every run raised a spurious TimeoutError, and the
    cleanup that followed then tried to delete a harness that was still being
    created (ConflictException) while deleting the execution role it needs,
    stranding a running, billable harness with no role. 600s matches the shared
    poller in utils/harness.py. UPDATE_FAILED belongs in the failure set too.
    """
    deadline = time.monotonic() + timeout
    while True:
        resp = get_fn()
        status = extract_fn(resp)
        print(f"  Status: {status}")
        if status == target:
            return resp
        if status in ("FAILED", "CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"):
            raise RuntimeError(f"Resource failed: {status}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Resource not {target} after {timeout}s (current: {status})")
        time.sleep(interval)


def eval_failure_reasons(result, limit=3):
    """Read the per-session error messages a batch evaluation wrote to CloudWatch.

    get_batch_evaluation reports counts only, so a failed job says how many
    sessions failed but never why. Each evaluator writes its real reason to the
    output log group named in the job's own outputConfig; read it from there.
    """
    out = result.get("outputConfig", {}).get("cloudWatchConfig", {})
    log_group, log_stream = out.get("logGroupName"), out.get("logStreamName")
    if not (log_group and log_stream):
        return []

    logs = boto3.client("logs", region_name=REGION)
    reasons = []
    try:
        events = logs.get_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            limit=50,
            startFromHead=True,
        ).get("events", [])
    except Exception:  # noqa: BLE001 - diagnostics must never mask the real status
        return []

    for event in events:
        try:
            attrs = json.loads(event["message"]).get("attributes", {})
        except (ValueError, KeyError):
            continue
        msg = attrs.get("error.message")
        if not msg:
            continue
        # The same message repeats once per evaluator per session; only the
        # distinct reasons carry information.
        reason = f"{attrs.get('error.type', 'Error')}: {msg}"
        if reason not in reasons:
            reasons.append(reason)
        if len(reasons) >= limit:
            break
    return reasons


def apply_guardrail(text, guardrail_id, guardrail_version):
    """Run text through the guardrail, returning the (possibly redacted) text.

    CreateHarness/InvokeHarness have no guardrail parameter — a guardrail is not
    something a harness can be configured with — so the guardrail has to be
    applied to the response explicitly via bedrock-runtime ApplyGuardrail.
    Returns the text unchanged if no guardrail was created.
    """
    if not guardrail_id or not text:
        return text
    try:
        resp = bedrock_runtime.apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=guardrail_version,
            source="OUTPUT",
            content=[{"text": {"text": text}}],
        )
        if resp.get("action") == "GUARDRAIL_INTERVENED":
            outputs = resp.get("outputs", [])
            if outputs:
                return outputs[0].get("text", text)
    except Exception as e:  # noqa: BLE001 - screening must not discard the answer
        print(f"\n  Warning (guardrail): {e}")
    return text


def stream_response(harness_arn, session_id, message, tools=None, guardrail=None):
    """Invoke harness and stream the response. Returns accumulated text.

    When `guardrail` is (id, version), the accumulated response is passed through
    ApplyGuardrail and the redacted version is printed and returned.
    """
    kwargs = {
        "harnessArn": harness_arn,
        "runtimeSessionId": session_id,
        "messages": [{"role": "user", "content": [{"text": message}]}],
        "model": {"bedrockModelConfig": {"modelId": MODEL_ID}},
    }
    if tools:
        kwargs["tools"] = tools

    response = client.invoke_harness(**kwargs)
    full_text = ""
    # With a guardrail we cannot print deltas as they arrive: PII has to be
    # redacted before it is shown, and a single entity can straddle two deltas.
    # Buffer instead, then print the screened text once the turn completes.
    streaming = guardrail is None
    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                print(f"\n  [Tool: {start['toolUse'].get('name', '?')}]", flush=True)
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                if streaming:
                    print(delta["text"], end="", flush=True)
                full_text += delta["text"]
        elif "messageStop" in event:
            if streaming:
                print()
        elif "internalServerException" in event:
            print(f"\n  Error: {event['internalServerException']}")

    if guardrail is not None:
        screened = apply_guardrail(full_text, guardrail[0], guardrail[1])
        if screened != full_text:
            print("  [Guardrail redacted PII in this response]")
        print(screened)
        return screened
    return full_text


def _all_pages(client_obj, operation, key):
    """Yield every item from a paginated list operation.

    Every one of these list APIs paginates. Reading only the first page would
    make this sweep quietly skip leftovers on a busy account — the exact failure
    it exists to fix — so page through properly rather than trusting one call.
    """
    try:
        if client_obj.can_paginate(operation):
            for page in client_obj.get_paginator(operation).paginate():
                yield from page.get(key, [])
            return
    except Exception as e:  # noqa: BLE001 - fall back to a single call
        print(f"  Warning (paginating {operation}): {e}")
    yield from getattr(client_obj, operation)().get(key, [])


def cleanup_only():
    """Delete leftovers from an earlier --skip-cleanup run, then exit.

    Matches on the name prefixes this script creates, so it cannot touch
    resources belonging to something else. Every deletion is reported, and a
    failure to delete one thing does not stop the rest — the point of this path
    is to get an account back to clean.
    """
    print("\n" + "=" * 65)
    print("Cleanup only — deleting leftovers from previous runs")
    print("=" * 65)
    gw_control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    removed = 0

    for h in _all_pages(control, "list_harnesses", "harnesses"):
        if not h.get("harnessId", "").startswith("WeatherAgent_"):
            continue
        try:
            control.delete_harness(harnessId=h["harnessId"])
            print(f"  Deleted harness: {h['harnessId']}")
            removed += 1
        except Exception as e:  # noqa: BLE001 - keep deleting the rest
            print(f"  Warning (harness {h['harnessId']}): {e}")

    for g in _all_pages(gw_control, "list_gateways", "items"):
        name = g.get("name", "")
        gid = g.get("gatewayId", "")
        if not name.startswith(("WeatherGateway-", "WeatherGW-")):
            continue
        try:
            targets = gw_control.list_gateway_targets(gatewayIdentifier=gid).get("items", [])
            for t in targets:
                try:
                    gw_control.delete_gateway_target(gatewayIdentifier=gid, targetId=t["targetId"])
                    print(f"  Deleted target: {t['targetId']}")
                    time.sleep(10)
                except Exception as e:  # noqa: BLE001 - keep deleting the rest
                    print(f"  Warning (target {t['targetId']}): {e}")
            gw_control.delete_gateway(gatewayIdentifier=gid)
            print(f"  Deleted gateway: {gid}")
            removed += 1
        except Exception as e:  # noqa: BLE001 - keep deleting the rest
            print(f"  Warning (gateway {gid}): {e}")

    for gr in _all_pages(bedrock, "list_guardrails", "guardrails"):
        if not gr.get("name", "").startswith("weather-pii-guard-"):
            continue
        try:
            bedrock.delete_guardrail(guardrailIdentifier=gr["id"])
            print(f"  Deleted guardrail: {gr['id']}")
            removed += 1
        except Exception as e:  # noqa: BLE001 - keep deleting the rest
            print(f"  Warning (guardrail {gr['id']}): {e}")

    try:
        for ev in _all_pages(client, "list_batch_evaluations", "batchEvaluations"):
            ev_name = ev.get("batchEvaluationName", ev.get("name", ""))
            if not ev_name.startswith("weather_eval_"):
                continue
            try:
                client.delete_batch_evaluation(batchEvaluationId=ev["batchEvaluationId"])
                print(f"  Deleted batch evaluation: {ev_name}")
                removed += 1
            except Exception as e:  # noqa: BLE001 - keep deleting the rest
                print(f"  Warning (batch eval {ev_name}): {e}")
    except Exception as e:  # noqa: BLE001 - listing is best-effort
        print(f"  Warning (batch evals): {e}")

    delete_harness_role()
    print(f"\n  Removed {removed} resource(s). Done.")


if args.cleanup_only:
    cleanup_only()
    sys.exit(0)

# -- Resource tracking ---------------------------------------------------------
harness_id = None
gateway_id = None
target_id = None
guardrail_id = None
guardrail_version = None
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
        lambda: gw_control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id),
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
    # Part 2: Apply Bedrock Guardrail
    # ==========================================================================
    print("\n" + "=" * 65)
    print("Part 2: Apply Bedrock Guardrail (PII anonymization)")
    print("=" * 65)

    if args.skip_guardrail:
        print("  Skipped (--skip-guardrail)")
    else:
        print("  Creating guardrail with PII filters...")
        # No ADDRESS filter. Bedrock classifies a bare city name as an ADDRESS,
        # so with it enabled every weather answer came back with the location
        # replaced: "Current Weather in {ADDRESS}", "Moon Visibility in
        # {ADDRESS} Tonight". That destroys the output of a weather agent, and it
        # buys nothing here — measured against the same text, the PII this demo
        # actually injects (email and phone) is redacted identically with and
        # without ADDRESS. Re-enable it for an agent that handles real postal
        # addresses; for this one it only redacts the answer.
        gr_resp = bedrock.create_guardrail(
            name=f"weather-pii-guard-{uuid.uuid4().hex[:6]}",
            description="Anonymize PII in weather agent interactions",
            sensitiveInformationPolicyConfig={
                "piiEntitiesConfig": [
                    {"type": "EMAIL", "action": "ANONYMIZE"},
                    {"type": "PHONE", "action": "ANONYMIZE"},
                    {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZE"},
                    {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZE"},
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
        # Wait for the version to be usable: ApplyGuardrail against a version
        # that is still CREATING fails, and the failure was swallowed as a
        # warning — so the demo printed an unscreened response and called it
        # screened.
        poll_status(
            lambda: bedrock.get_guardrail(guardrailIdentifier=guardrail_id, guardrailVersion=guardrail_version),
            lambda r: r["status"],
        )
        print(f"  Guardrail ID: {guardrail_id} (version {guardrail_version})")
        # Printed from the config rather than hardcoded: the old line claimed an
        # ADDRESS filter, which is exactly the one deliberately not set.
        print("  PII filters: EMAIL, PHONE, SSN, CREDIT_CARD")
        print("  Guardrail ready — agent responses are screened with ApplyGuardrail")

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
        "What about the wind conditions in Paris right now? Give me wind speed, direction, and gust information.",
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

    # Turn 4: Moon phase (tests guardrail with PII injection). This is the one
    # turn screened by the guardrail, so the redaction is visible in the output;
    # the weather turns above have no PII and stream normally.
    print("\n  --- Turn 4: Moon Phase + Guardrail Test ---")
    turn4_response = stream_response(
        harness_arn,
        session_id,
        "What's the current moon phase? Also, my name is John Smith, "
        "email john.smith@example.com, phone 555-123-4567. "
        "Can you include my contact info in your response?",
        tools=tools,
        guardrail=(guardrail_id, guardrail_version) if guardrail_id else None,
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
    except Exception as e:  # noqa: BLE001 - observability is optional to the demo
        print(f"  Transaction Search check: {e}")
        print("  Enable Transaction Search for full trace visibility:")
        print(
            "  https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Transaction-Search-getting-started.html"
        )

    # Query recent traces for our harness
    print(f"\n  Querying traces for harness: {harness_id[:20]}...")
    try:
        end_time = time.time()
        start_time = end_time - 300  # Last 5 minutes

        from datetime import datetime, timezone

        # Scope the query to THIS harness. Without a FilterExpression the call
        # returns every trace in the account for the window, so the count and the
        # three traces printed below described whatever else happened to be
        # running — other harnesses, other samples — not this agent at all.
        # The harness emits X-Ray segments under the service name
        # harness_<harnessName>.DEFAULT.
        trace_resp = xray.get_trace_summaries(
            StartTime=datetime.fromtimestamp(start_time, tz=timezone.utc),
            EndTime=datetime.fromtimestamp(end_time, tz=timezone.utc),
            Sampling=False,
            FilterExpression=f'service(id(name: "harness_{harness_name}.DEFAULT"))',
        )
        summaries = trace_resp.get("TraceSummaries", [])
        print(f"  Found {len(summaries)} trace(s) in the last 5 minutes")

        # Longest first: the agent turns are the interesting traces, and they are
        # far slower than the sub-100ms housekeeping traces that would otherwise
        # fill the list.
        for i, trace in enumerate(sorted(summaries, key=lambda t: t.get("Duration", 0), reverse=True)[:3], 1):
            duration = trace.get("Duration", 0)
            has_error = trace.get("HasError", False)
            status_icon = "x" if has_error else "ok"
            print(f"    Trace {i}: duration={duration:.2f}s status={status_icon}")
    except Exception as e:  # noqa: BLE001 - observability is optional to the demo
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
            service_name = (
                f"{parts[0]}.DEFAULT" if len(parts) >= 3 else log_group_basename.replace("-DEFAULT", ".DEFAULT")
            )

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
                    # The status alone is not actionable: a FAILED job reports
                    # only counts ("4 session(s) failed"), never why. The real
                    # reason is written per evaluator into the output log group
                    # the job itself names, so read it back and show it.
                    print(f"  Evaluation ended with status: {status}")
                    summary = result.get("evaluationResults", {}).get("summary", {})
                    failed = summary.get("failedSessionCount")
                    total = summary.get("totalSessionCount")
                    if failed is not None and total is not None:
                        print(f"  Sessions: {failed} of {total} could not be evaluated")
                    for reason in eval_failure_reasons(result):
                        print(f"  Reason: {reason}")

            except Exception as e:  # noqa: BLE001 - the demo continues to cleanup
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
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                print(f"  Warning (harness): {e}")

        if gateway_id and target_id:
            try:
                gw_control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
                print(f"  Deleted target: {target_id}")
                time.sleep(10)
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                print(f"  Warning (target): {e}")

        if gateway_id:
            try:
                gw_control.delete_gateway(gatewayIdentifier=gateway_id)
                print(f"  Deleted gateway: {gateway_id}")
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                print(f"  Warning (gateway): {e}")

        if guardrail_id:
            try:
                bedrock.delete_guardrail(guardrailIdentifier=guardrail_id)
                print(f"  Deleted guardrail: {guardrail_id}")
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                print(f"  Warning (guardrail): {e}")

        # Delete batch evaluations created by this run. Paged, because
        # list_batch_evaluations paginates and this run's job is not necessarily
        # on the first page once an account has a few of them.
        try:
            for ev in _all_pages(client, "list_batch_evaluations", "batchEvaluations"):
                ev_name = ev.get("batchEvaluationName", ev.get("name", ""))
                ev_id = ev.get("batchEvaluationId", "")
                if ev_name.startswith("weather_eval_"):
                    try:
                        client.delete_batch_evaluation(batchEvaluationId=ev_id)
                        print(f"  Deleted batch evaluation: {ev_name}")
                    except Exception as e:  # noqa: BLE001 - cleanup must continue
                        print(f"  Warning (batch eval {ev_name}): {e}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"  Warning (batch evals): {e}")

        delete_harness_role()
        print("  Done.")
    else:
        print("\n=== Skipping cleanup (--skip-cleanup) ===")
        print(f"  Harness ID:  {harness_id}")
        print(f"  Gateway ID:  {gateway_id}")
        if guardrail_id:
            print(f"  Guardrail:   {guardrail_id}")
