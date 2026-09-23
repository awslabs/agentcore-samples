#!/usr/bin/env python3
"""
Market Trends Agent — Optimization Cycle
=========================================

This script runs the full AgentCore optimization loop for the Market Trends
Agent. It covers every stage of the cycle:

  Phase 1  Baseline batch evaluation — measure where the agent stands today
  Phase 2  Generate traffic — run representative broker sessions
  Phase 3  System prompt recommendation — AI-generated prompt improvement
  Phase 4  Tool description recommendation — AI-improved tool descriptions
  Phase 5  Configuration bundles — create control and treatment variants,
           read back to verify, compare versions, and (after A/B) promote
  Phase 6  A/B test (config-bundle routing) — compare original vs recommended
           config without any code deployment
  Phase 7  A/B test (target-based routing) — phased canary rollout of a new
           model/code version (requires a second deployed runtime; see below)
  Phase 8  Cleanup — remove all created AWS resources

Usage
-----
    # Install dependencies
    uv sync

    # Set your deployed agent ARN
    export AGENT_RUNTIME_ARN=$(cat .agent_arn)   # or set manually
    export AWS_REGION=us-west-2

    # Run full cycle (all phases)
    uv run python optimization/optimize_agent.py

    # Run specific phases only
    uv run python optimization/optimize_agent.py --phases 1 2 3 4
    uv run python optimization/optimize_agent.py --phases 5 6
    uv run python optimization/optimize_agent.py --phases 7 --v2-arn <v2-runtime-arn>

    # Clean up all resources created by a previous run
    uv run python optimization/optimize_agent.py --cleanup --state-file optimization/state.json

Phase 6 — Configuration Bundle Routing (Config-Only Changes)
-------------------------------------------------------------
Use configuration bundle routing when the change you are testing is purely
configuration — a different system prompt, model ID, or tool descriptions.
Both variants run on the same runtime with different configuration bundle
versions. The gateway injects the correct bundle reference into each request
via W3C baggage headers, and the agent reads it at runtime. This means you
deploy one runtime and one online evaluation config.

Session assignment is sticky: once a session ID is assigned to a variant, all
subsequent requests with that session ID route to the same variant.

Phase 7 — Target-Based Routing (Code Changes / Runtime Upgrade)
----------------------------------------------------------------
Use target-based routing when the change you are testing involves code changes,
a framework upgrade, or an entirely different agent implementation. It deploys
multiple versions of the same runtime as named endpoints (gateway targets) and
routes traffic between them. Because each endpoint has its own log group, one
online evaluation config per variant is required
(perVariantOnlineEvaluationConfig).

To scope routing to a specific variant, use gatewayFilter.targetPaths to
restrict which requests the A/B rule matches. Pass enableOnCreate=True to
start the test immediately on creation — no separate update call needed.

Session assignment is sticky: once a session ID is assigned to a variant, all
subsequent requests with that session ID route to the same variant.

Use configuration bundle routing (Phase 6) when the change is purely
configuration — a different system prompt, model ID, or tool descriptions —
where both variants can run on the same runtime.

To run Phase 7 you need a second deployed runtime (v2). Deploy it with:

    uv run python deploy.py --agent-name market_trends_agent_v2 --region us-west-2

Then pass the v2 ARN:

    uv run python optimization/optimize_agent.py --phases 7 \
        --v2-arn arn:aws:bedrock-agentcore:us-west-2:<account>:runtime/<v2-id> \
        --state-file optimization/state.json   # reuses gateway from a prior run

Config Bundle Integration
-------------------------
Configuration bundles let you swap the system prompt and tool descriptions at
invocation time via a baggage header — no redeployment needed. The agent reads
the bundle directly inside its invocation handler:

    bundle = BedrockAgentCoreContext.get_config_bundle()
    system_prompt = DEFAULT_SYSTEM_PROMPT
    tool_descs = {}
    if bundle:
        system_prompt = bundle.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        tool_descs = bundle.get("tool_descriptions", {})

    # Apply system prompt
    agent.system_prompt = system_prompt

    # Apply tool description overrides
    if tool_descs:
        for t in agent.tools:
            name = getattr(t, "tool_name", None)
            if name and name in tool_descs and hasattr(t, "tool_spec"):
                t.tool_spec["description"] = tool_descs[name]

IAM Role Requirements (MarketTrendsAgentRole)
---------------------------------------------
The role passed to the gateway, online eval config, and A/B test must trust
bedrock-agentcore.amazonaws.com with a condition scoped to A/B test resources:

    "Condition": {
        "StringEquals": {"aws:SourceAccount": "<account-id>"},
        "ArnLike":      {"aws:SourceArn": "arn:aws:bedrock-agentcore:*:<account-id>:ab-test/*"}
    }

The permissions policy requires the following statements (see deploy.py for
the full policy document):

  ABTestAgentCoreResources   — GetGateway, GetGatewayTarget, ListGatewayTargets,
                               CreateGatewayRule, UpdateGatewayRule, GetGatewayRule,
                               DeleteGatewayRule, ListGatewayRules,
                               GetOnlineEvaluationConfig, GetEvaluator,
                               GetConfigurationBundle, GetConfigurationBundleVersion,
                               ListConfigurationBundleVersions
                               scoped to account ARNs with aws:ResourceAccount condition
  ABTestCloudWatchLogs       — DescribeLogGroups, DescribeIndexPolicies, PutIndexPolicy,
                               StartQuery, GetQueryResults, StopQuery,
                               FilterLogEvents, GetLogEvents
                               scoped to evaluations/* and aws/spans log groups

Public documentation
--------------------
  Optimization overview:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/optimization.html

  Configuration bundles:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/configuration-bundles.html

  A/B testing:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ab-testing.html

  Recommendations:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/optimization-recommendations.html

  Batch evaluation:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations.html
"""

import argparse
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import requests as http_requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REGION = os.environ.get("AWS_REGION", "us-west-2")


def _load_agent_arn() -> str:
    env_arn = os.environ.get("AGENT_RUNTIME_ARN", "")
    if env_arn:
        return env_arn
    arn_file = Path(__file__).parent.parent / ".agent_arn"
    if arn_file.exists():
        return arn_file.read_text().strip()
    raise RuntimeError(
        "Could not find agent ARN. Set AGENT_RUNTIME_ARN or deploy the agent "
        "first (uv run python deploy.py) to create .agent_arn."
    )


AGENT_ARN = _load_agent_arn()
RUNTIME_ID = AGENT_ARN.split("/")[-1]
AGENT_NAME = RUNTIME_ID.rsplit("-", 1)[0]
LOG_GROUP = f"/aws/bedrock-agentcore/runtimes/{RUNTIME_ID}-DEFAULT"
SERVICE_NAME = f"{AGENT_NAME}.DEFAULT"
SPANS_LOG_GROUP = "aws/spans"
LOG_GROUP_ARN = f"arn:aws:logs:{REGION}:{{account}}:log-group:{LOG_GROUP}"
SPANS_LOG_ARN = f"arn:aws:logs:{REGION}:{{account}}:log-group:{SPANS_LOG_GROUP}"

# Current system prompt (abbreviated; matches the intent of the agent's full prompt)
CURRENT_SYSTEM_PROMPT = """\
You are a financial market intelligence analyst working with investment brokers.

CAPABILITIES:
- Provide real-time stock prices and market data via get_stock_data
- Search financial news from Bloomberg, Reuters, CNBC, WSJ, and FT via search_news
- Maintain persistent broker profiles using AgentCore Memory tools
- Deliver personalized market analysis tailored to each broker's preferences

BROKER IDENTITY:
- When a user introduces themselves, IMMEDIATELY call identify_broker(user_message)
- After identification, call get_broker_financial_profile to retrieve stored preferences
- Use update_broker_financial_interests to store new preferences or profile updates
- Always pass the identified actor_id to all memory operations

MARKET ANALYSIS WORKFLOW:
1. Identify the broker (if identity markers present in the message)
2. Retrieve their stored profile to personalize the response
3. Fetch live stock data and sector information relevant to their query
4. Search for recent news aligned to their interests
5. Synthesize a professional, data-driven response

Always use tools to retrieve live data. Do not fabricate prices, news, or profile details.\
"""

# Tool descriptions for TD recommendation
CURRENT_TOOL_DESCRIPTIONS = {
    "get_stock_data": ("Get current stock price and key market data for a given ticker symbol."),
    "search_news": ("Search for recent news articles from financial news sources about a topic."),
    "parse_broker_profile_from_message": (
        "Parse a structured broker profile from a user message containing broker card "
        "information such as name, company, role, risk tolerance, and investment style."
    ),
    "generate_market_summary_for_broker": (
        "Generate a personalized market summary tailored to a broker's investment profile."
    ),
    "get_broker_card_template": ("Provide the broker card template format for structured profile collection."),
    "collect_broker_preferences_interactively": ("Guide the collection of specific broker preferences interactively."),
    "identify_broker": (
        "Identify a broker from their message using LLM analysis and retrieve or "
        "initialize their actor ID for memory operations."
    ),
    "get_broker_financial_profile": ("Retrieve the stored financial profile and investment preferences for a broker."),
    "update_broker_financial_interests": (
        "Store or update a broker's financial interests, preferences, and investment profile."
    ),
    "list_conversation_history": ("Retrieve recent conversation history for the current session."),
}

# Representative broker sessions used for baseline traffic and recommendations
BASELINE_SESSIONS = [
    (
        "Marcus Rivera",
        "I have exposure to XOM and CVX — what's the current situation in energy?",
    ),
    (
        "Sarah Chen",
        "Give me a quick briefing on the healthcare sector. Any GLP-1 news?",
    ),
    ("Yuval Bing", "I need NVDA and MSFT prices with a quick tech sector overview."),
    ("Marcus Rivera", "How is the financials sector looking? Thinking about JPM."),
    ("Sarah Chen", "What are the ESG trends in Europe right now? Any relevant ETFs?"),
    ("Yuval Bing", "Search for latest AI semiconductor news from Bloomberg."),
    ("Marcus Rivera", "Is it a good time to add energy exposure or reduce?"),
    (
        "Sarah Chen",
        "I want to set up my broker profile: ESG focus, $200M AUM, healthcare specialist.",
    ),
    ("Yuval Bing", "Compare NVDA and AMD — which is better positioned right now?"),
    ("Marcus Rivera", "What's happening with crude oil prices today?"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# boto3 clients
# ---------------------------------------------------------------------------

ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
dp = boto3.client(
    "bedrock-agentcore",
    region_name=REGION,
    config=Config(read_timeout=120, connect_timeout=30),
)
iam = boto3.client("iam", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
xray = boto3.client("xray", region_name=REGION)

sts = boto3.client("sts", region_name=REGION)
ACCOUNT_ID = sts.get_caller_identity()["Account"]

# Fix ARN templates with account
LOG_GROUP_ARN = LOG_GROUP_ARN.format(account=ACCOUNT_ID)
SPANS_LOG_ARN = SPANS_LOG_ARN.format(account=ACCOUNT_ID)

# ---------------------------------------------------------------------------
# State persistence — save IDs so cleanup can run after the fact
# ---------------------------------------------------------------------------

DEFAULT_STATE_FILE = Path(__file__).parent / "state.json"
_state: dict = {}


def _save_state(state_file: Path) -> None:
    state_file.write_text(json.dumps(_state, indent=2, default=str))
    logger.info("State saved to %s", state_file)


def _load_state(state_file: Path) -> None:
    global _state
    if state_file.exists():
        _state = json.loads(state_file.read_text())
        logger.info("Loaded existing state from %s", state_file)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TERMINAL_EVAL = {"COMPLETED", "FAILED", "STOPPED", "COMPLETED_WITH_ERRORS"}
TERMINAL_REC = {"COMPLETED", "FAILED"}


def _sep(title: str = "") -> None:
    width = 64
    if title:
        pad = max(0, width - len(title) - 2)
        print(f"\n{'=' * (pad // 2)} {title} {'=' * (pad - pad // 2)}")
    else:
        print("\n" + "=" * width)


def _poll(fn, terminal: set, interval: int = 30, timeout: int = 600, label: str = ""):
    """Poll ``fn()`` every ``interval`` seconds until status in ``terminal``."""
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        result = fn()
        status = result.get("status", "UNKNOWN")
        attempt += 1
        print(f"  [{attempt:3d}] {label} status={status}")
        if status in terminal:
            return result
        time.sleep(interval)
    raise TimeoutError(f"Timed out after {timeout}s waiting for {label}")


def _fetch_eval_scores(batch_eval_id: str) -> dict:
    """Read per-session evaluation scores from the batch eval CloudWatch stream."""
    log_group = "/aws/bedrock-agentcore/evaluations/batch-evaluations/results/default"
    log_stream = f"run-{batch_eval_id}"
    try:
        events = logs.get_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            startFromHead=True,
        ).get("events", [])
        by_eval: dict = defaultdict(list)
        for e in events:
            try:
                rec = json.loads(e["message"])
                attrs = rec.get("attributes") or rec
                eid = attrs.get("gen_ai.evaluation.name")
                score = attrs.get("gen_ai.evaluation.score.value")
                if eid and score is not None and rec.get("name") == "gen_ai.evaluation.result":
                    by_eval[eid].append(float(score))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return {eid: round(sum(v) / len(v), 4) for eid, v in by_eval.items() if v}
    except Exception as exc:
        logger.warning("CW score fetch failed: %s", exc)
        return {}


def _invoke_agent(prompt: str, session_id: str, baggage: str = "") -> str:
    """Invoke the Market Trends Agent runtime directly."""
    kwargs = dict(
        agentRuntimeArn=AGENT_ARN,
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode(),
        contentType="application/json",
        accept="application/json",
    )
    if baggage:
        kwargs["baggage"] = baggage
    resp = dp.invoke_agent_runtime(**kwargs)
    raw = resp["response"].read().decode("utf-8")
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, str) else parsed.get("response", raw)
    except Exception:
        return raw


# ---------------------------------------------------------------------------
# Phase 1 — Baseline Batch Evaluation
# ---------------------------------------------------------------------------


def phase1_baseline_eval(session_ids: list[str]) -> dict:
    """Start a batch evaluation over the provided session IDs."""
    _sep("Phase 1: Baseline Batch Evaluation")
    print(f"Measuring the agent's current performance across {len(session_ids)} sessions...\n")

    eval_resp = dp.start_batch_evaluation(
        batchEvaluationName=f"mt_baseline_{uuid.uuid4().hex[:6]}",
        evaluators=[
            {"evaluatorId": "Builtin.GoalSuccessRate"},
            {"evaluatorId": "Builtin.Helpfulness"},
            {"evaluatorId": "Builtin.Correctness"},
            {"evaluatorId": "Builtin.ToolSelectionAccuracy"},
        ],
        dataSourceConfig={
            "cloudWatchLogs": {
                "serviceNames": [SERVICE_NAME],
                "logGroupNames": [SPANS_LOG_GROUP, LOG_GROUP],
                "filterConfig": {"sessionIds": session_ids},
            }
        },
        clientToken=str(uuid.uuid4()),
    )
    eval_id = eval_resp["batchEvaluationId"]
    _state["baseline_eval_id"] = eval_id
    print(f"Batch evaluation started: {eval_id}")
    print("Polling for completion (typically 3–8 minutes)...\n")

    result = _poll(
        lambda: dp.get_batch_evaluation(batchEvaluationId=eval_id),
        terminal=TERMINAL_EVAL,
        interval=30,
        timeout=900,
        label="baseline eval",
    )

    # Read scores — try API response first, fall back to CloudWatch
    scores: dict = {}
    er = result.get("evaluationResults", {})
    for s in er.get("evaluatorSummaries", []):
        avg = s.get("statistics", {}).get("averageScore")
        if avg is not None:
            scores[s["evaluatorId"]] = avg

    if not scores:
        print("  Reading scores from CloudWatch logs...")
        scores = _fetch_eval_scores(eval_id)

    print(f"\n{'Evaluator':<45} {'Score':>8}")
    print("-" * 55)
    for eid, score in sorted(scores.items()):
        print(f"{eid:<45} {score:>8.4f}")
    print("\nBaseline scores recorded. These are your targets to beat.")
    _state["baseline_scores"] = scores
    return scores


# ---------------------------------------------------------------------------
# Phase 2 — Generate Traffic
# ---------------------------------------------------------------------------


def phase2_generate_traffic(baggage: str = "") -> list[str]:
    """Invoke the agent with representative broker sessions and return session IDs."""
    _sep("Phase 2: Generate Traffic for Recommendations")
    print(
        f"Sending {len(BASELINE_SESSIONS)} sessions to the agent.\n"
        "These traces will be used for recommendation analysis...\n"
    )

    session_ids: list[str] = []
    for broker_name, prompt in BASELINE_SESSIONS:
        sid = str(uuid.uuid4())
        session_ids.append(sid)
        full_prompt = f"I'm {broker_name}. {prompt}"
        try:
            response = _invoke_agent(full_prompt, sid, baggage=baggage)
            print(f"  [{sid[:8]}] [{broker_name}] {prompt[:60]}")
            print(f"          => {response[:100]}...\n")
        except Exception as exc:
            logger.warning("Session %s failed: %s", sid, exc)

    print(f"Sent {len(session_ids)} sessions. Waiting 3 minutes for CloudWatch ingestion...")
    for remaining in range(180, 0, -30):
        print(f"  {remaining}s remaining...")
        time.sleep(30)
    print("Ingestion wait complete.\n")
    _state["traffic_session_ids"] = session_ids
    return session_ids


# ---------------------------------------------------------------------------
# Phase 3 — System Prompt Recommendation
# ---------------------------------------------------------------------------


def phase3_sp_recommendation() -> str:
    """Request an AI-generated system prompt improvement focused on GoalSuccessRate."""
    _sep("Phase 3: System Prompt Recommendation")
    print(
        "Requesting a system prompt improvement from AgentCore.\n"
        "The service analyzes production traces and rewrites your prompt to "
        "improve the target evaluator metric...\n"
    )

    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=7)

    rec_resp = dp.start_recommendation(
        name=f"mt_sp_rec_{uuid.uuid4().hex[:6]}",
        type="SYSTEM_PROMPT_RECOMMENDATION",
        recommendationConfig={
            "systemPromptRecommendationConfig": {
                "systemPrompt": {"text": CURRENT_SYSTEM_PROMPT},
                "agentTraces": {
                    "cloudwatchLogs": {
                        "logGroupArns": [SPANS_LOG_ARN, LOG_GROUP_ARN],
                        "serviceNames": [SERVICE_NAME],
                        "startTime": start_dt,
                        "endTime": now,
                    }
                },
                "evaluationConfig": {
                    "evaluators": [{"evaluatorArn": ("arn:aws:bedrock-agentcore:::evaluator/Builtin.GoalSuccessRate")}]
                },
            }
        },
        clientToken=str(uuid.uuid4()),
    )
    rec_id = rec_resp["recommendationId"]
    _state["sp_rec_id"] = rec_id
    print(f"Recommendation ID: {rec_id}")
    print("Polling for completion (typically 2–7 minutes)...\n")

    result = _poll(
        lambda: dp.get_recommendation(recommendationId=rec_id),
        terminal=TERMINAL_REC,
        interval=30,
        timeout=900,
        label="SP recommendation",
    )

    if result.get("status") == "FAILED":
        reason = result.get("failureReason", "unknown")
        print(f"\nRecommendation failed: {reason}")
        print("Using manually-crafted fallback additions to baseline prompt.\n")
        recommended = (
            CURRENT_SYSTEM_PROMPT
            + "\n\n# Optimization additions:\n"
            + "- Before providing stock analysis, retrieve the broker's stored profile "
            "with get_broker_financial_profile to personalize sector focus.\n"
            "- When a broker asks about multiple stocks, retrieve data for all of them "
            "before composing the response to give a complete comparative view.\n"
            "- After answering, confirm whether the broker wants the response stored "
            "in their profile for future personalization."
        )
    else:
        rec_data = result.get("recommendationResult", {})
        sp_result = rec_data.get("systemPromptRecommendationResult", {})
        raw = sp_result.get("recommendedSystemPrompt")
        if isinstance(raw, dict):
            raw = raw.get("text", str(raw))
        recommended = raw or CURRENT_SYSTEM_PROMPT
        explanation = sp_result.get("explanation", "")

        _sep("RECOMMENDED SYSTEM PROMPT")
        print(recommended)
        if explanation:
            print(f"\n--- Explanation ---\n{explanation[:600]}")

    _state["recommended_system_prompt"] = recommended
    return recommended


# ---------------------------------------------------------------------------
# Phase 4 — Tool Description Recommendation
# ---------------------------------------------------------------------------


def phase4_td_recommendation() -> dict:
    """Request AI-improved tool descriptions to boost ToolSelectionAccuracy."""
    _sep("Phase 4: Tool Description Recommendation")
    print(
        "Requesting improved tool descriptions from AgentCore.\n"
        "The service analyzes how the agent uses each tool and generates more "
        "precise descriptions that help the LLM pick the right tool...\n"
    )

    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=7)

    tools_list = [
        {"toolName": name, "toolDescription": {"text": desc}} for name, desc in CURRENT_TOOL_DESCRIPTIONS.items()
    ]

    rec_resp = dp.start_recommendation(
        name=f"mt_td_rec_{uuid.uuid4().hex[:6]}",
        type="TOOL_DESCRIPTION_RECOMMENDATION",
        recommendationConfig={
            "toolDescriptionRecommendationConfig": {
                "toolDescription": {"toolDescriptionText": {"tools": tools_list}},
                "agentTraces": {
                    "cloudwatchLogs": {
                        "logGroupArns": [SPANS_LOG_ARN, LOG_GROUP_ARN],
                        "serviceNames": [SERVICE_NAME],
                        "startTime": start_dt,
                        "endTime": now,
                    }
                },
            }
        },
        clientToken=str(uuid.uuid4()),
    )
    rec_id = rec_resp["recommendationId"]
    _state["td_rec_id"] = rec_id
    print(f"Recommendation ID: {rec_id}")
    print("Polling for completion (typically 2–7 minutes)...\n")

    result = _poll(
        lambda: dp.get_recommendation(recommendationId=rec_id),
        terminal=TERMINAL_REC,
        interval=30,
        timeout=900,
        label="TD recommendation",
    )

    recommended_tools = dict(CURRENT_TOOL_DESCRIPTIONS)

    if result.get("status") == "COMPLETED":
        td_data = result.get("recommendationResult", {}).get("toolDescriptionRecommendationResult", {})
        returned_tools = td_data.get("tools", [])
        tool_keys = list(CURRENT_TOOL_DESCRIPTIONS.keys())

        _sep("RECOMMENDED TOOL DESCRIPTIONS")
        for i, item in enumerate(returned_tools):
            new_desc = item.get("recommendedToolDescription", "")
            tool_name = item.get("toolName") or (tool_keys[i] if i < len(tool_keys) else f"tool_{i}")
            recommended_tools[tool_name] = new_desc
            print(f"\n[{tool_name}]")
            print(f"  Before: {CURRENT_TOOL_DESCRIPTIONS.get(tool_name, '(unknown)')}")
            print(f"  After : {new_desc}")
    else:
        print(f"Tool description recommendation status: {result.get('status')}. Using current descriptions.")

    _state["recommended_tool_descriptions"] = recommended_tools
    return recommended_tools


# ---------------------------------------------------------------------------
# Phase 5 — Configuration Bundles
# ---------------------------------------------------------------------------


def phase5_create_bundles(
    recommended_prompt: str,
    recommended_tool_descs: dict | None = None,
) -> tuple[str, str, str, str]:
    """Create control (original) and treatment (recommended) configuration bundles.

    Configuration bundles let you inject a different system prompt and tool
    descriptions at invocation time via a baggage header — no redeployment needed.
    The agent reads both keys directly inside its invocation handler.

    After Phase 6 confirms the treatment wins, call phase5_promote_bundle() to
    promote the winning config. That function reads the current versionId from the
    API immediately before updating — rather than relying on the version stored here
    at creation time, which may be stale if the bundle was updated since then. The
    fresh version is passed as parentVersionIds, which enforces optimistic locking:
    the service rejects the update if the bundle was modified concurrently.

    See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/configuration-bundles.html
    """
    _sep("Phase 5: Configuration Bundles")
    print(
        "Creating control (original config) and treatment (recommended config) "
        "configuration bundles.\n"
        "Bundles carry both system prompt and tool descriptions — no agent "
        "redeployment needed.\n"
    )

    if recommended_tool_descs is None:
        recommended_tool_descs = CURRENT_TOOL_DESCRIPTIONS

    # --- 5a. Create control bundle (original config) ---
    ctrl_resp = ctrl.create_configuration_bundle(
        bundleName=f"mt_control_{uuid.uuid4().hex[:6]}",
        description="Market Trends Agent control variant — original system prompt and tool descriptions",
        components={
            AGENT_ARN: {
                "configuration": {
                    "system_prompt": CURRENT_SYSTEM_PROMPT,
                    "tool_descriptions": CURRENT_TOOL_DESCRIPTIONS,
                }
            }
        },
        commitMessage="Control: original system prompt and tool descriptions (baseline)",
        clientToken=str(uuid.uuid4()),
    )
    control_bundle_arn = ctrl_resp["bundleArn"]
    control_bundle_version = ctrl_resp["versionId"]
    control_bundle_id = ctrl_resp["bundleId"]
    print(f"Control bundle ID      : {control_bundle_id}")
    print(f"Control bundle version : {control_bundle_version}")

    # --- 5b. Create treatment bundle (recommended config) ---
    treat_resp = ctrl.create_configuration_bundle(
        bundleName=f"mt_treatment_{uuid.uuid4().hex[:6]}",
        description="Market Trends Agent treatment variant — recommended system prompt and tool descriptions",
        components={
            AGENT_ARN: {
                "configuration": {
                    "system_prompt": recommended_prompt,
                    "tool_descriptions": recommended_tool_descs,
                }
            }
        },
        commitMessage="Treatment: AI-recommended system prompt + improved tool descriptions (Phase 3/4)",
        clientToken=str(uuid.uuid4()),
    )
    treatment_bundle_arn = treat_resp["bundleArn"]
    treatment_bundle_version = treat_resp["versionId"]
    treatment_bundle_id = treat_resp["bundleId"]
    print(f"Treatment bundle ID      : {treatment_bundle_id}")
    print(f"Treatment bundle version : {treatment_bundle_version}")

    _state.update(
        {
            "control_bundle_id": control_bundle_id,
            "control_bundle_arn": control_bundle_arn,
            "control_bundle_version": control_bundle_version,
            "treatment_bundle_id": treatment_bundle_id,
            "treatment_bundle_arn": treatment_bundle_arn,
            "treatment_bundle_version": treatment_bundle_version,
        }
    )

    # --- 5c. Read back the treatment bundle to verify ---
    print("\n--- Reading treatment bundle to verify ---")
    read_resp = ctrl.get_configuration_bundle(bundleId=treatment_bundle_id)
    cfg = read_resp["components"][AGENT_ARN]["configuration"]
    print(f"Version    : {read_resp['versionId']}")
    print(f"Prompt     : {cfg['system_prompt'][:120]}...")
    print(f"Tools      : {list(cfg.get('tool_descriptions', {}).keys())}")

    # --- 5d. Compare control vs treatment versions ---
    _sep("Control vs Treatment — Configuration Diff")
    v_ctrl = ctrl.get_configuration_bundle_version(bundleId=control_bundle_id, versionId=control_bundle_version)
    v_treat = ctrl.get_configuration_bundle_version(bundleId=treatment_bundle_id, versionId=treatment_bundle_version)
    cfg_c = v_ctrl["components"][AGENT_ARN]["configuration"]
    cfg_t = v_treat["components"][AGENT_ARN]["configuration"]

    for key in sorted(set(cfg_c) | set(cfg_t)):
        val_c, val_t = cfg_c.get(key), cfg_t.get(key)
        if val_c == val_t:
            continue
        print(f"\n[{key}]")
        if isinstance(val_c, dict) and isinstance(val_t, dict):
            for tool in sorted(set(val_c) | set(val_t)):
                if val_c.get(tool) != val_t.get(tool):
                    print(f"  {tool}:")
                    print(f"    Before: {str(val_c.get(tool, ''))[:100]}")
                    print(f"    After : {str(val_t.get(tool, ''))[:100]}")
        else:
            c_str, t_str = str(val_c or ""), str(val_t or "")
            print(f"  Before ({len(c_str)} chars): {c_str[:200]}")
            print(f"  After  ({len(t_str)} chars): {t_str[:200]}")

    print(f"\nControl   commitMessage: {v_ctrl.get('commitMessage', 'n/a')}")
    print(f"Treatment commitMessage: {v_treat.get('commitMessage', 'n/a')}")

    # --- 5e. Spot-check: invoke both bundles ---
    print("\n--- Spot-checking both bundles ---")
    test_prompt = (
        "I'm Yuval Bing from HSBC. I manage tech-focused portfolios. Give me a quick take on NVDA and MSFT right now."
    )
    ctrl_baggage = (
        f"aws.agentcore.configbundle_arn={control_bundle_arn},"
        f"aws.agentcore.configbundle_version={control_bundle_version}"
    )
    treat_baggage = (
        f"aws.agentcore.configbundle_arn={treatment_bundle_arn},"
        f"aws.agentcore.configbundle_version={treatment_bundle_version}"
    )

    ctrl_text = _invoke_agent(test_prompt, str(uuid.uuid4()), baggage=ctrl_baggage)
    treat_text = _invoke_agent(test_prompt, str(uuid.uuid4()), baggage=treat_baggage)

    print(f"\nPrompt: {test_prompt}\n")
    print(f"[Control   ] {ctrl_text[:300]}")
    print()
    print(f"[Treatment ] {treat_text[:300]}")

    return (
        control_bundle_arn,
        control_bundle_version,
        treatment_bundle_arn,
        treatment_bundle_version,
    )


# ---------------------------------------------------------------------------
# Phase 5 (promote) — Promote winning config into the control bundle
# ---------------------------------------------------------------------------


def phase5_promote_bundle(
    recommended_prompt: str,
    recommended_tool_descs: dict,
) -> str:
    """Promote the treatment config into the control bundle after A/B validation.

    Call this after Phase 6 confirms the treatment variant wins. It updates the
    control bundle with the recommended config, producing a new immutable version.
    `parentVersionIds` enforces optimistic locking — the call fails if the bundle
    was updated concurrently, preventing accidental overwrites.

    Returns the new control bundle version ID.
    """
    _sep("Phase 5 (Promote): Update Control Bundle with Winning Config")

    control_bundle_id = _state.get("control_bundle_id")
    control_bundle_arn = _state.get("control_bundle_arn")

    if not control_bundle_id:
        print("No control bundle found in state. Run Phase 5 first.")
        return ""

    # Always read the current version immediately before updating — the stored
    # version may be stale if the bundle was updated since Phase 5 ran.
    current = ctrl.get_configuration_bundle(bundleId=control_bundle_id)
    control_bundle_version = current["versionId"]
    print(f"Current control bundle version : {control_bundle_version}")

    print(
        "Promoting treatment config into the control bundle.\n"
        "parentVersionIds enforces that we're updating from the correct parent,\n"
        "preventing overwrites if the bundle was updated concurrently.\n"
    )

    promote_resp = ctrl.update_configuration_bundle(
        bundleId=control_bundle_id,
        components={
            AGENT_ARN: {
                "configuration": {
                    "system_prompt": recommended_prompt,
                    "tool_descriptions": recommended_tool_descs,
                }
            }
        },
        parentVersionIds=[control_bundle_version],
        commitMessage="Promote treatment: AI-recommended config validated by A/B test (Phase 6)",
        clientToken=str(uuid.uuid4()),
    )
    new_version = promote_resp["versionId"]
    print(f"Control bundle promoted to version : {new_version}")
    print(f"Previous version was               : {control_bundle_version}")
    print()
    print("All new sessions using this bundle will receive the improved prompt")
    print("and tool descriptions — without any code redeployment.")

    _state["control_bundle_version"] = new_version
    _state["control_bundle_version_previous"] = control_bundle_version

    new_baggage = (
        f"aws.agentcore.configbundle_arn={control_bundle_arn},aws.agentcore.configbundle_version={new_version}"
    )
    print(f"\nUpdated baggage:\n  {new_baggage}")
    return new_version


# ---------------------------------------------------------------------------
# Phase 6 — A/B Test: Config-Bundle Routing (Prompt Comparison)
# ---------------------------------------------------------------------------


def phase6_ab_bundle_test(
    role_arn: str,
    control_bundle_arn: str,
    control_bundle_version: str,
    treatment_bundle_arn: str,
    treatment_bundle_version: str,
) -> str:
    """A/B test: split traffic between the original and recommended configuration bundles.

    Use configuration bundle routing when the change you are testing is purely
    configuration — a different system prompt, a different model ID, or different
    tool descriptions. Both variants run on the same runtime with different
    configuration bundle versions. The gateway injects the correct bundle reference
    into each request via W3C baggage headers, and the agent reads it at runtime.
    This means you deploy one runtime and one online evaluation config.

    If the change involves code changes, a framework upgrade, or an entirely different
    agent implementation, use target-based routing (Phase 7) instead, which sends
    traffic to two separate runtimes.

    Architecture:
        User request
             |
             v
        [Gateway] --50%--> [Control bundle C]   \
             |                                    +--> [Market Trends Runtime] --> CloudWatch
             +-50%--> [Treatment bundle T1]      /                                     |
                                                          [Online Eval Config] <--------+
                                                                   |
                                                          [A/B Test Results]

    Session assignment is sticky: once a session ID is assigned to a variant, all
    subsequent requests with that session ID route to the same variant, ensuring a
    consistent experience within a session while distributing new sessions according
    to the configured traffic weights.

    See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ab-testing.html
    """
    _sep("Phase 6: A/B Test — Config-Bundle Routing")
    print(
        "Setting up a gateway and 50/50 A/B test between the original and "
        "recommended system prompts.\n"
        "This requires no agent redeployment — the bundle is injected via "
        "a baggage header at invocation time.\n"
    )

    # --- 6a. Gateway ---
    gw_resp = ctrl.create_gateway(
        name=f"mt-gw-{uuid.uuid4().hex[:6]}",
        description="Market Trends Agent A/B test gateway",
        authorizerType="AWS_IAM",
        roleArn=role_arn,
        clientToken=str(uuid.uuid4()),
    )
    gw_id = gw_resp["gatewayId"]
    _state["gateway_id"] = gw_id
    print(f"Gateway created: {gw_id}. Polling for READY...")

    for i in range(60):
        gw = ctrl.get_gateway(gatewayIdentifier=gw_id)
        if gw.get("status") == "READY":
            break
        print(f"  Poll {i + 1}: {gw.get('status')}")
        time.sleep(5)

    gateway_arn = gw.get("gatewayArn", "")
    gateway_url = gw.get("gatewayUrl", "")
    _state.update({"gateway_arn": gateway_arn, "gateway_url": gateway_url})
    print(f"Gateway ready: {gateway_url}")

    # --- 6b. Gateway target ---
    target_name = "MarketTrendsV1"
    tgt_resp = ctrl.create_gateway_target(
        gatewayIdentifier=gw_id,
        name=target_name,
        description="Market Trends Agent v1 runtime target",
        targetConfiguration={
            "http": {
                "agentcoreRuntime": {
                    "arn": AGENT_ARN,
                    "qualifier": "DEFAULT",
                }
            }
        },
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        clientToken=str(uuid.uuid4()),
    )
    target_id = tgt_resp["targetId"]
    _state["gateway_target_id"] = target_id
    for i in range(30):
        tgt = ctrl.get_gateway_target(gatewayIdentifier=gw_id, targetId=target_id)
        if tgt.get("status") == "READY":
            break
        print(f"  Target poll {i + 1}: {tgt.get('status')}")
        time.sleep(5)
    print(f"Gateway target ready: {target_id}")

    # --- 6c. Configure X-Ray → CloudWatch Logs tracing ---
    try:
        dest = xray.get_trace_segment_destination()
        if dest.get("Destination") != "CloudWatchLogs":
            xray.update_trace_segment_destination(Destination="CloudWatchLogs")
            print("X-Ray trace destination set to CloudWatchLogs")
        else:
            print("X-Ray trace destination already set to CloudWatchLogs")
    except Exception as exc:
        print(f"X-Ray config skipped: {exc}")

    delivery_source_name = f"mt_gw_traces_{uuid.uuid4().hex[:6]}"
    _state["delivery_source_name"] = delivery_source_name
    delivery_id = None

    try:
        logs.put_delivery_source(
            name=delivery_source_name,
            resourceArn=gateway_arn,
            logType="TRACES",
        )
        print(f"Delivery source created: {delivery_source_name}")
    except Exception as exc:
        print(f"Delivery source (may already exist): {exc}")

    try:
        destinations = logs.describe_delivery_destinations().get("deliveryDestinations", [])
        xray_dest = next(
            (d for d in destinations if d.get("deliveryDestinationType") == "XRAY"),
            None,
        )
        if not xray_dest:
            logs.put_delivery_destination(
                name="xray-destination",
                deliveryDestinationType="XRAY",
                deliveryDestinationConfiguration={
                    "destinationResourceArn": (f"arn:aws:xray:{REGION}:{ACCOUNT_ID}:group/Default")
                },
            )
            destinations = logs.describe_delivery_destinations().get("deliveryDestinations", [])
            xray_dest = next(
                (d for d in destinations if d.get("deliveryDestinationType") == "XRAY"),
                None,
            )
        if xray_dest:
            xray_dest_arn = xray_dest["arn"]
            delivery = logs.create_delivery(
                deliverySourceName=delivery_source_name,
                deliveryDestinationArn=xray_dest_arn,
            )
            delivery_id = delivery["delivery"]["id"]
            _state["delivery_id"] = delivery_id
            print(f"Delivery created: {delivery_id}")
    except Exception as exc:
        print(f"Tracing delivery setup: {exc}")

    # --- 6d. Online evaluation config ---
    online_eval_resp = ctrl.create_online_evaluation_config(
        onlineEvaluationConfigName=f"mt_online_eval_{uuid.uuid4().hex[:6]}",
        description="Market Trends Agent online evaluation for A/B test",
        dataSourceConfig={
            "cloudWatchLogs": {
                "logGroupNames": [LOG_GROUP],
                "serviceNames": [SERVICE_NAME],
            }
        },
        evaluators=[
            {"evaluatorId": "Builtin.GoalSuccessRate"},
            {"evaluatorId": "Builtin.Helpfulness"},
        ],
        rule={
            "samplingConfig": {"samplingPercentage": 100.0},
            "sessionConfig": {"sessionTimeoutMinutes": 2},
        },
        evaluationExecutionRoleArn=role_arn,
        enableOnCreate=True,
        clientToken=str(uuid.uuid4()),
    )
    online_eval_id = online_eval_resp["onlineEvaluationConfigId"]
    online_eval_arn = online_eval_resp["onlineEvaluationConfigArn"]
    _state.update({"online_eval_id": online_eval_id, "online_eval_arn": online_eval_arn})
    print(f"Online eval config: {online_eval_id}")

    # --- 6e. Create the A/B test ---
    ab_resp = dp.create_ab_test(
        name=f"mt_bundle_ab_{uuid.uuid4().hex[:6]}",
        description=("Market Trends Agent: compare original vs recommended system prompt (50/50)"),
        gatewayArn=gateway_arn,
        roleArn=role_arn,
        enableOnCreate=True,
        evaluationConfig={"onlineEvaluationConfigArn": online_eval_arn},
        variants=[
            {
                "name": "C",
                "weight": 50,
                "variantConfiguration": {
                    "configurationBundle": {
                        "bundleArn": control_bundle_arn,
                        "bundleVersion": control_bundle_version,
                    }
                },
            },
            {
                "name": "T1",
                "weight": 50,
                "variantConfiguration": {
                    "configurationBundle": {
                        "bundleArn": treatment_bundle_arn,
                        "bundleVersion": treatment_bundle_version,
                    }
                },
            },
        ],
        clientToken=str(uuid.uuid4()),
    )
    ab_test_id = ab_resp["abTestId"]
    _state["bundle_ab_test_id"] = ab_test_id
    print(f"A/B test created: {ab_test_id}. Polling for ACTIVE/RUNNING...")

    for i in range(30):
        ab = dp.get_ab_test(abTestId=ab_test_id)
        s, es = ab.get("status", ""), ab.get("executionStatus", "")
        print(f"  Poll {i + 1}: status={s}  executionStatus={es}")
        if s == "ACTIVE" and es == "RUNNING":
            break
        if "FAILED" in s:
            print(f"  Error: {ab.get('errorDetails')}")
            break
        time.sleep(5)

    print(
        f"\nA/B test LIVE. Gateway is splitting traffic 50/50 between variants C and T1.\n"
        f"Gateway URL: {gateway_url}/{target_name}/invocations"
    )

    # --- 6f. Send traffic through the gateway ---
    _send_gateway_traffic(gateway_url, target_name, n_sessions=20)

    # --- 6g. Poll for results ---
    _monitor_ab_test(ab_test_id, label="Config-Bundle A/B Test")

    return ab_test_id


def _send_gateway_traffic(gateway_url: str, target_name: str, n_sessions: int = 20) -> list[str]:
    """Send SigV4-signed requests through the gateway to generate A/B test traffic."""
    gw_invoke_url = f"{gateway_url}/{target_name}/invocations"
    session = boto3.Session()
    creds = session.get_credentials().get_frozen_credentials()

    prompts = [
        "I'm Marcus Rivera. What's the current energy sector situation? Give me XOM and CVX prices.",
        "It's Sarah Chen. Any ESG news from Bloomberg today? Healthcare sector update?",
        "Yuval Bing here. Give me NVDA vs AMD comparison and latest chip sector news.",
        "Marcus Rivera — I need a quick risk check on my energy positions given recent oil moves.",
        "Sarah Chen. I want to add to my healthcare position. What's the GLP-1 landscape?",
        "This is Yuval Bing. MSFT earnings coming up. What's the consensus and current price?",
        "Marcus Rivera here. Compare XOM and CVX dividends — which is more attractive?",
        "Sarah Chen. ESG screening question: any controversy flags on big pharma stocks lately?",
        "Yuval Bing. AI infrastructure play — compare NVDA, AMD, and INTC right now.",
        "Marcus Rivera. Energy sector macro question: OPEC outlook and oil demand forecast?",
        "Sarah Chen. I'm looking at UNH and CVS for my healthcare sleeve. Current prices?",
        "Yuval Bing. Search for latest semiconductor supply chain news from Reuters.",
        "Marcus Rivera. Natural gas vs crude — which sector has better near-term momentum?",
        "Sarah Chen. What are the latest ESG regulation updates in Europe affecting portfolios?",
        "Yuval Bing. Quick briefing on cloud stocks: AMZN, MSFT, GOOG performance this week.",
        "Marcus Rivera. Is the energy sector overbought right now based on recent price action?",
        "Sarah Chen. I need to rebalance. What's the healthcare sector weighting vs S&P?",
        "Yuval Bing. Give me a bull/bear case for NVDA at current levels.",
        "Marcus Rivera. Any news on Middle East geopolitics affecting oil markets?",
        "Sarah Chen. Long-term pharma play: LLY vs NVO — which has better pipeline?",
    ]

    session_ids: list[str] = []
    success = 0

    for i, prompt in enumerate(prompts[:n_sessions]):
        sid = str(uuid.uuid4())
        session_ids.append(sid)
        body = json.dumps({"prompt": prompt, "sessionId": sid})
        req = AWSRequest(
            method="POST",
            url=gw_invoke_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sid,
            },
        )
        SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(req)
        try:
            resp = http_requests.post(gw_invoke_url, data=body, headers=dict(req.headers), timeout=120)
            if resp.status_code == 200:
                print(f"  [{i + 1:2d}/{n_sessions}] OK  {sid[:8]}...  {resp.text[:80]}")
                success += 1
            else:
                print(f"  [{i + 1:2d}/{n_sessions}] ERR status={resp.status_code}: {resp.text[:80]}")
        except Exception as exc:
            print(f"  [{i + 1:2d}/{n_sessions}] ERR {exc}")
        time.sleep(1)

    _state.setdefault("gateway_session_ids", []).extend(session_ids)
    print(
        f"\nSent {n_sessions} sessions through gateway (success={success}). Waiting for online evaluation pipeline..."
    )
    return session_ids


def _monitor_ab_test(ab_test_id: str, label: str = "A/B Test", polls: int = 20) -> dict:
    """Poll GetAbTest until analysisTimestamp is populated or timeout."""
    print(f"\nPolling for {label} results (up to {polls} minutes)...\n")
    ab_results = {}

    for poll in range(polls):
        ab = dp.get_ab_test(abTestId=ab_test_id)
        results = ab.get("results", {})
        metrics = results.get("evaluatorMetrics", [])
        print(f"--- Poll {poll + 1}/{polls} -- {time.strftime('%H:%M:%S')} ---")
        print(f"  analysisTimestamp: {results.get('analysisTimestamp', 'none')}")

        for m in metrics:
            name = m.get("evaluatorArn", "").split("/")[-1]
            cs = m.get("controlStats", {})
            print(f"  {name}:")
            print(f"    C  (control,   50%): mean={cs.get('mean', '-')}  n={cs.get('sampleSize', '-')}")
            for vr in m.get("variantResults", []):
                # Prefer API-provided percentChange; fall back to manual calculation
                pct_change = vr.get("percentChange")
                if pct_change is None:
                    cs_mean, vr_mean = cs.get("mean"), vr.get("mean")
                    if cs_mean and vr_mean and float(cs_mean) != 0:
                        pct_change = (float(vr_mean) - float(cs_mean)) / float(cs_mean) * 100
                delta = f"  change={pct_change:+.1f}%" if pct_change is not None else ""
                sig = vr.get("isSignificant", "-")
                print(
                    f"    T1 (treatment, 50%): mean={vr.get('mean', '-')}  "
                    f"n={vr.get('sampleSize', '-')}  p={vr.get('pValue', 'N/A')}  "
                    f"significant={sig}{delta}"
                )

        if results.get("analysisTimestamp") and metrics:
            ab_results = results
            print("\nResults are available!")
            _print_ab_interpretation(ab_results, label)
            break
        print()
        time.sleep(60)

    if not ab_results:
        print(
            f"\n{label} results not yet available after {polls} minutes.\n"
            "Results typically appear 10–15 minutes after your last request.\n"
            "To check later:\n"
            f"  aws bedrock-agentcore get-ab-test --ab-test-id {ab_test_id} "
            f"--region {REGION}"
        )
    return ab_results


def _print_ab_interpretation(results: dict, label: str) -> None:
    print(f"\n{'=' * 64}")
    print(f"{label} — INTERPRETATION")
    print("=" * 64)
    metrics = results.get("evaluatorMetrics", [])
    for m in metrics:
        name = m.get("evaluatorArn", "").split("/")[-1]
        cs_mean = m.get("controlStats", {}).get("mean")
        for vr in m.get("variantResults", []):
            sig = vr.get("isSignificant")
            t1_mean = vr.get("mean")
            p_value = vr.get("pValue", "N/A")
            # Prefer API-provided percentChange; fall back to manual calculation
            pct_change = vr.get("percentChange")
            if pct_change is None and cs_mean and t1_mean and float(cs_mean) != 0:
                pct_change = (float(t1_mean) - float(cs_mean)) / float(cs_mean) * 100
            pct_str = f"{pct_change:+.1f}%" if pct_change is not None else "N/A"
            print(f"\n  {name}:")
            print(f"    p-value={p_value}  change={pct_str}  significant={sig}")
            # p < 0.05 + positive change → treatment wins
            if sig and pct_change is not None and pct_change > 0:
                print("    RESULT: T1 wins (statistically significant improvement)")
                print("    ACTION: Run Phase 5p to promote treatment bundle as new default")
            # p < 0.05 + negative change → treatment regressed
            elif sig and pct_change is not None and pct_change < 0:
                print("    RESULT: T1 regressed (statistically significant decline)")
                print("    ACTION: Keep control; investigate recommendation quality")
            else:
                print("    RESULT: Inconclusive (p >= 0.05 or insufficient samples)")
                print("    ACTION: Continue sending traffic to accumulate sample size")


# ---------------------------------------------------------------------------
# Phase 7 — A/B Test: Target-Based Routing (Code Changes / Runtime Upgrade)
# ---------------------------------------------------------------------------


def phase7_ab_target_routing(
    role_arn: str,
    v2_agent_arn: str,
    gateway_id: str,
    gateway_arn: str,
    gateway_url: str,
    online_eval_arn: str,
) -> str:
    """Canary rollout of a v2 agent using target-based A/B routing (90/10 split).

    Use target-based routing when the change involves code changes, a framework
    upgrade, or an entirely different agent implementation. Target-based routing
    deploys multiple versions of the same runtime as named endpoints. The gateway
    registers each endpoint as a separate target and routes each session to one
    endpoint or the other based on the A/B test's traffic weights.

    Because each endpoint has its own log group, one online evaluation config per
    variant is required (perVariantOnlineEvaluationConfig).

    Use configuration bundle routing (Phase 6) when the change is purely
    configuration — a different system prompt, model ID, or tool descriptions —
    where both variants can run on the same runtime.

    Session assignment is sticky: once a session ID is assigned to a variant, all
    subsequent requests with that session ID route to the same target runtime,
    ensuring consistent behavior within a session while distributing new sessions
    according to the configured traffic weights.

    gatewayFilter.targetPaths scopes the A/B test routing rule to requests that
    match the control target's path. enableOnCreate=True starts the test immediately
    after creation — no separate update_ab_test call needed.

    Decision framework:
      T1 wins (isSignificant=True, positive percentChange)  -> ramp to 50% then 100%
      T1 loses (isSignificant=True, negative percentChange) -> halt rollout, investigate v2
      Inconclusive (p >= 0.05)                              -> send more traffic
      Ramp to 100%: update_ab_test(variants=[{name: C, weight: 0}, {name: T1, weight: 100}])

    See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ab-testing.html
    """
    _sep("Phase 7: A/B Test — Target-Based Routing (90/10 Canary)")

    v2_runtime_id = v2_agent_arn.split("/")[-1]
    v2_agent_name = v2_runtime_id.rsplit("-", 1)[0]
    v2_log_group = f"/aws/bedrock-agentcore/runtimes/{v2_runtime_id}-DEFAULT"
    v2_service_name = f"{v2_agent_name}.DEFAULT"

    print(
        f"v1 (stable, 90%): {AGENT_ARN}\n"
        f"v2 (canary, 10%): {v2_agent_arn}\n\n"
        "This routing type is used for code-level changes — new model, new tools,\n"
        "or refactored logic — where you want to validate the new version against\n"
        "real traffic before a full cutover.\n"
    )

    # Pause the config-bundle A/B test if it's running
    bundle_ab_id = _state.get("bundle_ab_test_id")
    if bundle_ab_id:
        try:
            ab = dp.get_ab_test(abTestId=bundle_ab_id)
            if ab.get("executionStatus") == "RUNNING":
                print("Pausing config-bundle A/B test to allow target routing to start...")
                dp.update_ab_test(abTestId=bundle_ab_id, executionStatus="PAUSED")
                time.sleep(5)
        except Exception as exc:
            print(f"  Could not pause bundle A/B test: {exc}")

    # Create v2 online eval config (separate log group)
    oe_v2_resp = ctrl.create_online_evaluation_config(
        onlineEvaluationConfigName=f"mt_online_eval_v2_{uuid.uuid4().hex[:6]}",
        description="Market Trends Agent v2 online evaluation (target-based routing)",
        dataSourceConfig={
            "cloudWatchLogs": {
                "logGroupNames": [v2_log_group],
                "serviceNames": [v2_service_name],
            }
        },
        evaluators=[
            {"evaluatorId": "Builtin.GoalSuccessRate"},
            {"evaluatorId": "Builtin.Helpfulness"},
        ],
        rule={
            "samplingConfig": {"samplingPercentage": 100.0},
            "sessionConfig": {"sessionTimeoutMinutes": 2},
        },
        evaluationExecutionRoleArn=role_arn,
        enableOnCreate=True,
        clientToken=str(uuid.uuid4()),
    )
    oe_v2_id = oe_v2_resp["onlineEvaluationConfigId"]
    oe_v2_arn = oe_v2_resp["onlineEvaluationConfigArn"]
    _state["online_eval_v2_id"] = oe_v2_id
    print(f"v2 online eval config: {oe_v2_id}")

    # Add v2 gateway target
    target_name_v1 = "MarketTrendsV1"
    target_name_v2 = "MarketTrendsV2"
    tgt_v2_resp = ctrl.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name_v2,
        description="Market Trends Agent v2 (model upgrade / new version)",
        targetConfiguration={
            "http": {
                "agentcoreRuntime": {
                    "arn": v2_agent_arn,
                    "qualifier": "DEFAULT",
                }
            }
        },
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        clientToken=str(uuid.uuid4()),
    )
    target_id_v2 = tgt_v2_resp["targetId"]
    _state["gateway_target_v2_id"] = target_id_v2
    for i in range(30):
        tgt = ctrl.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id_v2)
        if tgt.get("status") == "READY":
            break
        print(f"  v2 target poll {i + 1}: {tgt.get('status')}")
        time.sleep(5)
    print(f"v2 gateway target ready: {target_id_v2}")

    # Create the target-based A/B test (90% v1, 10% v2 canary).
    # gatewayFilter.targetPaths scopes the routing rule to the control target path.
    # enableOnCreate=True starts the test immediately — no separate update_ab_test needed.
    ab_resp = dp.create_ab_test(
        name=f"mt_target_ab_{uuid.uuid4().hex[:6]}",
        description=("Market Trends Agent: phased rollout — v1 stable (90%) vs v2 canary (10%)"),
        gatewayArn=gateway_arn,
        roleArn=role_arn,
        enableOnCreate=True,
        evaluationConfig={
            "perVariantOnlineEvaluationConfig": [
                {"name": "C", "onlineEvaluationConfigArn": online_eval_arn},
                {"name": "T1", "onlineEvaluationConfigArn": oe_v2_arn},
            ]
        },
        gatewayFilter={"targetPaths": [f"/{target_name_v1}/*"]},
        variants=[
            {
                "name": "C",
                "weight": 90,
                "variantConfiguration": {"target": {"name": target_name_v1}},
            },
            {
                "name": "T1",
                "weight": 10,
                "variantConfiguration": {"target": {"name": target_name_v2}},
            },
        ],
        clientToken=str(uuid.uuid4()),
    )
    target_ab_id = ab_resp["abTestId"]
    _state["target_ab_test_id"] = target_ab_id
    print(f"Target A/B test created: {target_ab_id}. Polling for ACTIVE/RUNNING...")

    for i in range(30):
        ab = dp.get_ab_test(abTestId=target_ab_id)
        s, es = ab.get("status", ""), ab.get("executionStatus", "")
        print(f"  Poll {i + 1}: status={s}  executionStatus={es}")
        if s == "ACTIVE" and es == "RUNNING":
            break
        if "FAILED" in s:
            raise RuntimeError(f"Target A/B test failed: {ab.get('errorDetails')}")
        time.sleep(5)

    print(
        f"\nTarget-based A/B test LIVE (90/10 canary split):\n"
        f"  90% -> {target_name_v1} (v1 stable)\n"
        f"  10% -> {target_name_v2} (v2 canary)\n"
    )

    # Send traffic
    _send_gateway_traffic(gateway_url, target_name_v1, n_sessions=10)

    # Monitor results
    _monitor_ab_test(target_ab_id, label="Target-Based A/B Test (Canary)")

    print(
        "\nPhased rollout workflow:\n"
        "  Canary (10%)  : validate no regressions\n"
        "  Ramp  (50%)   : gather statistical significance\n"
        "  Promote (100%): full cutover to v2\n"
        "\nTo update the traffic split:\n"
        f"  aws bedrock-agentcore update-ab-test --ab-test-id {target_ab_id} \\\n"
        '    --variants \'[{"name":"C","weight":50},{"name":"T1","weight":50}]\' \\\n'
        f"    --region {REGION}"
    )

    return target_ab_id


# ---------------------------------------------------------------------------
# Phase 8 — Cleanup
# ---------------------------------------------------------------------------


def phase8_cleanup(state: dict) -> None:
    """Remove all AWS resources created during the optimization cycle."""
    _sep("Phase 8: Cleanup")
    print("Removing all resources created by this optimization run...\n")

    # Stop and delete A/B tests
    for ab_key, label in [
        ("bundle_ab_test_id", "config-bundle"),
        ("target_ab_test_id", "target-based"),
    ]:
        ab_id = state.get(ab_key)
        if not ab_id:
            continue
        print(f"Deleting {label} A/B test: {ab_id}")
        try:
            ab = dp.get_ab_test(abTestId=ab_id)
            if ab.get("executionStatus") in ("RUNNING", "PAUSED"):
                dp.update_ab_test(abTestId=ab_id, executionStatus="STOPPED")
                time.sleep(3)
            dp.delete_ab_test(abTestId=ab_id)
            print(f"  Deleted: {ab_id}")
        except Exception as exc:
            print(f"  Skipped: {exc}")

    # Delete online eval configs
    for oe_key, label in [
        ("online_eval_id", "v1"),
        ("online_eval_v2_id", "v2"),
    ]:
        oe_id = state.get(oe_key)
        if not oe_id:
            continue
        print(f"Deleting online eval config ({label}): {oe_id}")
        try:
            ctrl.update_online_evaluation_config(onlineEvaluationConfigId=oe_id, executionStatus="DISABLED")
            time.sleep(2)
            ctrl.delete_online_evaluation_config(onlineEvaluationConfigId=oe_id)
            print(f"  Deleted: {oe_id}")
        except Exception as exc:
            print(f"  Skipped: {exc}")

    # Delete configuration bundles
    for b_key, label in [
        ("control_bundle_id", "control"),
        ("treatment_bundle_id", "treatment"),
    ]:
        b_id = state.get(b_key)
        if not b_id:
            continue
        print(f"Deleting bundle ({label}): {b_id}")
        try:
            ctrl.delete_configuration_bundle(bundleId=b_id)
            print(f"  Deleted: {b_id}")
        except Exception as exc:
            print(f"  Skipped: {exc}")

    # Delete gateway tracing
    delivery_id = state.get("delivery_id")
    if delivery_id:
        try:
            logs.delete_delivery(id=delivery_id)
            print(f"Deleted delivery: {delivery_id}")
        except Exception as exc:
            print(f"Delivery delete skipped: {exc}")

    delivery_source = state.get("delivery_source_name")
    if delivery_source:
        try:
            logs.delete_delivery_source(name=delivery_source)
            print(f"Deleted delivery source: {delivery_source}")
        except Exception as exc:
            print(f"Delivery source delete skipped: {exc}")

    # Delete gateway targets + gateway
    gw_id = state.get("gateway_id")
    if gw_id:
        for t_key, t_label in [
            ("gateway_target_v2_id", "v2"),
            ("gateway_target_id", "v1"),
        ]:
            t_id = state.get(t_key)
            if t_id:
                try:
                    ctrl.delete_gateway_target(gatewayIdentifier=gw_id, targetId=t_id)
                    time.sleep(3)
                    print(f"Deleted gateway target ({t_label}): {t_id}")
                except Exception as exc:
                    print(f"Gateway target ({t_label}) delete skipped: {exc}")

        try:
            ctrl.delete_gateway(gatewayIdentifier=gw_id)
            print(f"Deleted gateway: {gw_id}")
        except Exception as exc:
            print(f"Gateway delete skipped: {exc}")

    print("\nCleanup complete.")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _get_role_arn() -> str:
    """Retrieve the MarketTrendsAgentRole ARN for gateway / online eval."""
    role_name = os.environ.get("AGENT_ROLE_NAME", "MarketTrendsAgentRole")
    try:
        resp = iam.get_role(RoleName=role_name)
        return resp["Role"]["Arn"]
    except Exception as exc:
        raise RuntimeError(
            f"Could not retrieve role ARN for {role_name}: {exc}\nSet AGENT_ROLE_NAME or deploy the agent first."
        ) from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Market Trends Agent optimization cycle. "
            "By default runs all phases except 7 (target-based routing)."
        )
    )
    parser.add_argument(
        "--phases",
        nargs="+",
        default=["1", "2", "3", "4", "5", "6"],
        help=(
            "Phases to run. Default: 1 2 3 4 5 6. "
            "Use '5p' to promote the winning treatment config into the control bundle "
            "after Phase 6 confirms the treatment wins. "
            "Phase 7 requires --v2-arn. Phase 8 runs cleanup."
        ),
    )
    parser.add_argument(
        "--v2-arn",
        dest="v2_arn",
        default="",
        help=(
            "ARN of the v2 agent runtime for target-based routing (Phase 7). "
            "Deploy v2 with: uv run python deploy.py --agent-name market_trends_agent_v2"
        ),
    )
    parser.add_argument(
        "--state-file",
        dest="state_file",
        default=str(DEFAULT_STATE_FILE),
        help="Path to the state JSON file for persisting resource IDs.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help=(
            "Clean up all resources recorded in --state-file and exit. Useful for cleaning up after an interrupted run."
        ),
    )
    args = parser.parse_args()

    state_file = Path(args.state_file)
    _load_state(state_file)

    if args.cleanup:
        phase8_cleanup(_state)
        _save_state(state_file)
        return

    # Normalise phases to strings so "5p" works alongside integers
    phases = {str(p) for p in args.phases}

    _sep("Market Trends Agent — Optimization Cycle")
    print(f"Agent ARN   : {AGENT_ARN}")
    print(f"Runtime ID  : {RUNTIME_ID}")
    print(f"Region      : {REGION}")
    print(f"Account     : {ACCOUNT_ID}")
    print(f"Phases      : {sorted(phases)}")
    print(f"State file  : {state_file}")

    role_arn = _get_role_arn()
    print(f"Role ARN    : {role_arn}\n")

    session_ids: list[str] = _state.get("traffic_session_ids", [])
    baseline_scores: dict = _state.get("baseline_scores", {})
    recommended_prompt: str = _state.get("recommended_system_prompt", CURRENT_SYSTEM_PROMPT)
    recommended_tool_descs: dict = _state.get("recommended_tool_descriptions", CURRENT_TOOL_DESCRIPTIONS)

    # Phase 2 must run before Phase 1 to have session IDs
    if "2" in phases:
        session_ids = phase2_generate_traffic()
        _save_state(state_file)

    if "1" in phases:
        if not session_ids:
            print("Phase 1 requires session IDs. Run Phase 2 first to generate traffic.")
        else:
            baseline_scores = phase1_baseline_eval(session_ids)
            _save_state(state_file)

    if "3" in phases:
        recommended_prompt = phase3_sp_recommendation()
        _save_state(state_file)

    if "4" in phases:
        phase4_td_recommendation()
        _save_state(state_file)

    if "5" in phases:
        if not recommended_prompt:
            recommended_prompt = CURRENT_SYSTEM_PROMPT
        ctrl_arn, ctrl_ver, treat_arn, treat_ver = phase5_create_bundles(recommended_prompt, recommended_tool_descs)
        _save_state(state_file)
    else:
        ctrl_arn = _state.get("control_bundle_arn", "")
        ctrl_ver = _state.get("control_bundle_version", "")
        treat_arn = _state.get("treatment_bundle_arn", "")
        treat_ver = _state.get("treatment_bundle_version", "")

    if "6" in phases:
        if not (ctrl_arn and treat_arn):
            print("Phase 6 requires configuration bundles. Run Phase 5 first.")
        else:
            phase6_ab_bundle_test(
                role_arn=role_arn,
                control_bundle_arn=ctrl_arn,
                control_bundle_version=ctrl_ver,
                treatment_bundle_arn=treat_arn,
                treatment_bundle_version=treat_ver,
            )
            _save_state(state_file)

    if "5p" in phases:
        # Promote the winning treatment config into the control bundle.
        # Run this after Phase 6 confirms the treatment variant wins.
        phase5_promote_bundle(recommended_prompt, recommended_tool_descs)
        _save_state(state_file)

    if "7" in phases:
        if not args.v2_arn:
            print(
                "\nPhase 7 (target-based routing) requires a v2 agent runtime.\n"
                "Deploy v2 first:\n"
                "  uv run python deploy.py --agent-name market_trends_agent_v2 "
                f"--region {REGION}\n"
                "Then re-run with:\n"
                "  uv run python optimization/optimize_agent.py --phases 7 "
                "--v2-arn <v2-runtime-arn> --state-file optimization/state.json"
            )
        else:
            gw_id = _state.get("gateway_id", "")
            gw_arn = _state.get("gateway_arn", "")
            gw_url = _state.get("gateway_url", "")
            oe_arn = _state.get("online_eval_arn", "")
            if not gw_id:
                print("Phase 7 requires a gateway from Phase 6. Run Phase 6 first or provide a state file.")
            else:
                phase7_ab_target_routing(
                    role_arn=role_arn,
                    v2_agent_arn=args.v2_arn,
                    gateway_id=gw_id,
                    gateway_arn=gw_arn,
                    gateway_url=gw_url,
                    online_eval_arn=oe_arn,
                )
                _save_state(state_file)

    if "8" in phases:
        phase8_cleanup(_state)
        _save_state(state_file)

    _sep("Optimization Cycle Complete")
    if baseline_scores:
        print("\nBaseline scores recorded:")
        for eid, score in sorted(baseline_scores.items()):
            print(f"  {eid:<45} {score:.4f}")
    print(
        "\nRun A/B test results are stored in the state file:\n"
        f"  {state_file}\n"
        "\nTo run cleanup at any time:\n"
        f"  uv run python optimization/optimize_agent.py --cleanup "
        f"--state-file {state_file}"
    )


if __name__ == "__main__":
    main()
