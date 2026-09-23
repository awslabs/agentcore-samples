#!/usr/bin/env python3
"""
Market Trends Agent — Simulated Dataset Batch Evaluation
=========================================================

Uses the Amazon Bedrock AgentCore SDK (bedrock-agentcore>=1.7.0) to run a
batch evaluation in which an LLM-backed actor plays the role of an investment
broker interacting with the Market Trends Agent. No pre-scripted turn sequences
are needed: the actor generates realistic, varied conversations on its own.

Usage
-----
    # Install dependencies (from the market-trends-agent project root)
    uv sync

    # Set your deployed agent ARN (created by deploy.py)
    export AGENT_RUNTIME_ARN=$(cat .agent_arn)   # or set manually
    export AWS_REGION=us-west-2                   # match your deployment region

    uv run python optimization/user_simulated_dataset.py

Why simulated evaluation?
--------------------------
Hand-authored test scenarios tell you whether the agent handles *known* cases
correctly, but they miss edge cases and the natural variation of real user
language. Simulated scenarios let an LLM actor drive the conversation, producing
different phrasings, follow-up questions, and multi-turn paths each run. This
exposes gaps that scripted tests miss and scales scenario coverage without
authoring hundreds of fixed turn sequences.

Public documentation
--------------------
  Evaluating agents:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html

  Simulated datasets:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/simulation.html
"""

import json
import logging
import os
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

from bedrock_agentcore.evaluation import (
    ActorProfile,
    AgentInvokerInput,
    AgentInvokerOutput,
    BatchEvaluationRunConfig,
    BatchEvaluationRunner,
    BatchEvaluatorConfig,
    CloudWatchDataSourceConfig,
    Dataset,
    SimulatedScenario,
    SimulationConfig,
)

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------

REGION = os.environ.get("AWS_REGION", "us-west-2")


# Agent ARN: read from .agent_arn file or AGENT_RUNTIME_ARN env var
def _load_agent_arn() -> str:
    env_arn = os.environ.get("AGENT_RUNTIME_ARN", "")
    if env_arn:
        return env_arn
    arn_file = Path(__file__).parent.parent / ".agent_arn"
    if arn_file.exists():
        return arn_file.read_text().strip()
    raise RuntimeError(
        "Could not find agent ARN. Set AGENT_RUNTIME_ARN or deploy the agent first "
        "(uv run python deploy.py) to create .agent_arn."
    )


AGENT_ARN = _load_agent_arn()
RUNTIME_ID = AGENT_ARN.split("/")[-1]
AGENT_NAME = RUNTIME_ID.rsplit("-", 1)[0]
LOG_GROUP = f"/aws/bedrock-agentcore/runtimes/{RUNTIME_ID}-DEFAULT"
SERVICE_NAME = f"{AGENT_NAME}.DEFAULT"
SPANS_LOG_GROUP = "aws/spans"

# ---------------------------------------------------------------------------
# Evaluator IDs
# Built-in evaluators are always included. Custom LLM-as-a-judge evaluators
# are loaded from custom_evaluator_ids.json if it exists (created by
# running evaluators/custom_evaluators.py first).
# ---------------------------------------------------------------------------

_BUILTIN_EVALUATOR_IDS = [
    "Builtin.GoalSuccessRate",
    "Builtin.Helpfulness",
    "Builtin.Correctness",
]


def _load_evaluator_ids() -> list[str]:
    """Return built-in evaluator IDs plus any custom evaluators that have been created."""
    ids = list(_BUILTIN_EVALUATOR_IDS)
    custom_ids_file = Path(__file__).parent / "custom_evaluator_ids.json"
    if custom_ids_file.exists():
        custom = json.loads(custom_ids_file.read_text())
        if custom:
            ids.extend(custom.values())
            print(
                f"Loaded {len(custom)} custom evaluator(s) from {custom_ids_file.name}:\n"
                + "\n".join(f"  {name}: {eid}" for name, eid in custom.items())
            )
        else:
            print("custom_evaluator_ids.json is empty — using built-in evaluators only.")
    else:
        print(
            "No custom_evaluator_ids.json found. Using built-in evaluators only.\n"
            "To add custom LLM-as-a-judge evaluators, run:\n"
            "  uv run python evaluators/custom_evaluators.py"
        )
    return ids


EVALUATOR_IDS = _load_evaluator_ids()

# Actor model: drives the simulated broker persona
# Choose a model capable of following complex persona instructions.
ACTOR_MODEL_ID = os.environ.get("ACTOR_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# Seconds to wait for spans to land in CloudWatch before submitting the eval
INGESTION_DELAY_SECONDS = int(os.environ.get("INGESTION_DELAY_SECONDS", "180"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent invoker — one call per actor turn
# ---------------------------------------------------------------------------

_runtime_client = boto3.client(
    "bedrock-agentcore",
    region_name=REGION,
    config=Config(read_timeout=120, connect_timeout=30),
)


def agent_invoker(inp: AgentInvokerInput) -> AgentInvokerOutput:
    """Invoke one turn of the Market Trends Agent.

    The SDK calls this function once per conversation turn, providing:
    - ``inp.payload``: the actor's message (string or dict)
    - ``inp.session_id``: stable session ID managed by the runner framework

    The Market Trends Agent entrypoint returns a plain text response that
    ``BedrockAgentCoreApp`` serializes as a JSON string.
    """
    payload = inp.payload
    if isinstance(payload, str):
        prompt = payload
    elif isinstance(payload, dict):
        prompt = payload.get("prompt", str(payload))
    else:
        prompt = str(payload)

    session_id = inp.session_id or str(uuid.uuid4())

    resp = _runtime_client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode(),
        contentType="application/json",
        accept="application/json",
    )
    raw = resp["response"].read().decode("utf-8")
    try:
        parsed = json.loads(raw)
        agent_text = parsed if isinstance(parsed, str) else parsed.get("response", raw)
    except Exception:
        agent_text = raw

    logger.debug("Turn [session=%s] user=%r agent=%r", session_id, prompt[:80], agent_text[:80])
    return AgentInvokerOutput(agent_output=agent_text)


# ---------------------------------------------------------------------------
# Simulated scenarios
# ---------------------------------------------------------------------------
# Each scenario defines:
#   - actor_profile: who the broker is and what they want to accomplish
#   - input: the opening message that starts the conversation
#   - max_turns: safety backstop (conversations typically end earlier via
#     goal completion)
#   - assertions: natural-language expectations used by LLM evaluators
#
# The actor drives all subsequent turns autonomously based on the agent's
# responses. No fixed turn sequences are needed.
# ---------------------------------------------------------------------------

SCENARIOS = [
    # --- 1. Technology sector deep-dive ---
    SimulatedScenario(
        scenario_id="sim-tech-stock-deep-dive",
        scenario_description=(
            "A senior technology broker needs a comprehensive NVDA + MSFT briefing "
            "before a high-net-worth client meeting."
        ),
        actor_profile=ActorProfile(
            traits={
                "expertise": "senior",
                "focus": "technology sector",
                "style": "data-driven",
            },
            context=(
                "Senior technology sector broker preparing for a high-net-worth client "
                "meeting. Needs current prices, sector outlook, and recent catalysts for "
                "NVIDIA and Microsoft."
            ),
            goal=(
                "Obtain current stock data, sector analysis, and recent news for NVIDIA "
                "and Microsoft to prepare a client presentation."
            ),
        ),
        input=(
            "I have a client meeting in an hour and need a quick briefing on NVIDIA and "
            "Microsoft. What are the current prices and how is the tech sector looking?"
        ),
        max_turns=6,
        assertions=[
            "Agent retrieves current stock data for NVDA and MSFT",
            "Agent provides technology sector context or market overview",
            "Agent includes specific price and performance numbers in the response",
            "Agent searches for relevant technology news",
        ],
    ),
    # --- 2. New broker profile onboarding ---
    SimulatedScenario(
        scenario_id="sim-broker-profile-onboarding",
        scenario_description=(
            "A new ESG and healthcare-focused broker sets up their investment profile "
            "and requests personalized sector analysis."
        ),
        actor_profile=ActorProfile(
            traits={
                "expertise": "specialist",
                "focus": "ESG and healthcare",
                "investment_style": "long-term",
            },
            context=(
                "New broker Sarah Chen specializing in sustainable and healthcare "
                "investments. Manages $200M AUM with ESG-screened portfolios and "
                "GLP-1 pharma plays."
            ),
            goal=(
                "Register investment profile with the agent and receive personalized "
                "healthcare sector analysis aligned to ESG principles."
            ),
        ),
        input=(
            "Hi, I'm Sarah Chen, a new broker specializing in ESG and healthcare "
            "investing. I manage around $200M in AUM. Can I set up my profile with you?"
        ),
        max_turns=6,
        assertions=[
            "Agent stores or acknowledges the broker profile",
            "Agent confirms the profile was received",
            "Agent provides healthcare or ESG sector analysis",
            "Agent personalizes response to the broker's stated focus",
        ],
    ),
    # --- 3. Morning market briefing ---
    SimulatedScenario(
        scenario_id="sim-morning-market-brief",
        scenario_description=(
            "A portfolio manager needs a pre-market briefing covering major indices, "
            "sector performance, and macro headlines before a 9am investment committee call."
        ),
        actor_profile=ActorProfile(
            traits={
                "expertise": "senior",
                "focus": "macro and multi-asset",
                "time_pressure": "high",
            },
            context=(
                "Portfolio manager at a multi-asset fund needing a morning briefing on "
                "overall market conditions, top movers, and macro news to brief their "
                "investment committee."
            ),
            goal=(
                "Get a complete morning market snapshot including index levels, sector "
                "performance, and macro economic headlines."
            ),
        ),
        input=(
            "Good morning. I need a quick market briefing before my 9am investment "
            "committee call. What's the overall market looking like today?"
        ),
        max_turns=5,
        assertions=[
            "Agent provides market overview including index or sector performance",
            "Agent retrieves or summarizes macro or financial news",
            "Agent delivers a structured briefing with specific data points",
        ],
    ),
    # --- 4. Bank stock comparison ---
    SimulatedScenario(
        scenario_id="sim-financials-stock-comparison",
        scenario_description=(
            "A value-oriented financials specialist compares JPMorgan, Goldman Sachs, "
            "and Bank of America ahead of Q1 earnings season."
        ),
        actor_profile=ActorProfile(
            traits={
                "expertise": "specialist",
                "focus": "US banks and financials",
                "investment_style": "value and dividend",
            },
            context=(
                "Financials sector specialist looking for the best risk-adjusted "
                "opportunity among major US banks ahead of Q1 earnings. Prefers "
                "dividend-paying stocks with strong fundamentals."
            ),
            goal=(
                "Compare JPM, GS, and BAC on key metrics, understand the financial "
                "sector outlook, and identify the strongest investment opportunity."
            ),
        ),
        input=(
            "I'm looking at the big banks — JPMorgan, Goldman Sachs, and Bank of "
            "America. Can you compare them? I want to know which has the best value "
            "right now ahead of earnings."
        ),
        max_turns=6,
        assertions=[
            "Agent retrieves stock data for JPM, GS, and BAC",
            "Agent provides comparative analysis or sector context",
            "Agent includes specific metrics or current prices",
            "Agent gives a clear view on the opportunity or sector outlook",
        ],
    ),
    # --- 5. Portfolio risk review with memory recall ---
    SimulatedScenario(
        scenario_id="sim-portfolio-risk-review",
        scenario_description=(
            "An existing broker with energy sector holdings (XOM, CVX) requests a "
            "risk assessment given current oil price volatility."
        ),
        actor_profile=ActorProfile(
            traits={
                "expertise": "intermediate",
                "focus": "energy sector",
                "risk_attitude": "risk-aware",
            },
            context=(
                "Broker Marcus Rivera managing a conservative income portfolio with "
                "significant energy sector exposure. Concerned about oil price "
                "volatility and wants to reassess XOM and CVX positions."
            ),
            goal=(
                "Check current status of XOM and CVX, understand energy sector risks, "
                "and get recent energy news to decide whether to hold or reduce exposure."
            ),
        ),
        input=(
            "Hi, it's Marcus Rivera. I have significant exposure to XOM and Chevron "
            "in my portfolio and I'm getting nervous about the energy sector. What's "
            "the current situation?"
        ),
        max_turns=6,
        assertions=[
            "Agent identifies or acknowledges the broker Marcus Rivera",
            "Agent retrieves stock data for XOM and CVX",
            "Agent retrieves or summarizes energy sector analysis",
            "Agent searches for energy news",
            "Agent provides a risk assessment with specific data",
        ],
    ),
]

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _separator(title: str = "") -> None:
    width = 64
    if title:
        pad = max(0, width - len(title) - 2)
        print(f"\n{'=' * (pad // 2)} {title} {'=' * (pad - pad // 2)}")
    else:
        print("\n" + "=" * width)


def main() -> None:
    _separator("Market Trends Agent — Simulated Dataset Evaluation")
    print(f"Agent ARN   : {AGENT_ARN}")
    print(f"Runtime ID  : {RUNTIME_ID}")
    print(f"Region      : {REGION}")
    print(f"Scenarios   : {len(SCENARIOS)}")
    print(f"Evaluators  : {len(EVALUATOR_IDS)}")
    print(f"Actor model : {ACTOR_MODEL_ID}")
    print(f"Ingestion delay: {INGESTION_DELAY_SECONDS}s")

    dataset = Dataset(scenarios=SCENARIOS)

    data_source = CloudWatchDataSourceConfig(
        service_names=[SERVICE_NAME],
        log_group_names=[SPANS_LOG_GROUP, LOG_GROUP],
        ingestion_delay_seconds=INGESTION_DELAY_SECONDS,
    )

    simulation_config = SimulationConfig(model_id=ACTOR_MODEL_ID)

    run_name = f"mt_simulated_{uuid.uuid4().hex[:8]}"
    config = BatchEvaluationRunConfig(
        batch_evaluation_name=run_name,
        evaluator_config=BatchEvaluatorConfig(evaluator_ids=EVALUATOR_IDS),
        data_source=data_source,
        simulation_config=simulation_config,
        polling_timeout_seconds=1800,
        polling_interval_seconds=30,
    )

    print(f"\nEvaluation run name: {run_name}")
    print(
        "Starting simulation + evaluation. Each actor-driven conversation runs to\n"
        "goal completion or max_turns, then the evaluators score the session.\n"
        "Budget 15–25 minutes for invocation + ingestion + evaluation.\n"
    )

    runner = BatchEvaluationRunner(region=REGION)
    result = runner.run_dataset_evaluation(
        config=config,
        dataset=dataset,
        agent_invoker=agent_invoker,
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _separator("RESULTS")
    print(f"Batch eval ID : {result.batch_evaluation_id}")
    print(f"Status        : {result.status}")

    if result.agent_invocation_failures:
        print(f"\nAgent invocation failures ({len(result.agent_invocation_failures)}):")
        for fail in result.agent_invocation_failures:
            print(f"  [{fail.scenario_id}] {fail.error_message[:120]}")

    if result.evaluation_results:
        ev = result.evaluation_results
        completed = getattr(ev, "sessions_completed", None) or getattr(ev, "number_of_sessions_completed", "?")
        failed = getattr(ev, "sessions_failed", None) or getattr(ev, "number_of_sessions_failed", "?")
        print(f"\nSessions: completed={completed}, failed={failed}")

        summaries = getattr(ev, "evaluator_summaries", None) or []
        if summaries:
            print(f"\n{'Evaluator':<42} {'Avg Score':>10}  {'Evaluated':>10}")
            print("-" * 66)
            for es in summaries:
                name = getattr(es, "evaluator_name", None) or getattr(es, "evaluator_id", "unknown")
                stats = getattr(es, "statistics", None)
                avg = f"{stats.average_score:.3f}" if stats and stats.average_score is not None else "N/A"
                evaluated = getattr(es, "total_evaluated", 0) or 0
                print(f"{name:<42} {avg:>10}  {evaluated:>10}")
        else:
            print("\nNo evaluator summaries returned (evaluation may still be in progress).")
            print(
                "Re-run with the batch eval ID to check:\n"
                f"  aws bedrock-agentcore get-batch-evaluation "
                f"--batch-evaluation-id {result.batch_evaluation_id} --region {REGION}"
            )
    else:
        print("\nNo aggregated evaluation results returned.")

    if result.error_details:
        print(f"\nError details: {result.error_details}")

    # ------------------------------------------------------------------
    # Per-turn events from CloudWatch
    # ------------------------------------------------------------------
    if result.output_data_config:
        odc = result.output_data_config
        print(f"\nOutput log group  : {odc.log_group_name}")
        print(f"Output log stream : {odc.log_stream_name}")
        print("\nFetching per-turn evaluation events from CloudWatch...")
        try:
            events = runner.fetch_evaluation_events(result)
            print(f"Retrieved {len(events)} evaluation events")
            if events:
                print(f"\nSample events (first 10 of {len(events)}):")
                for ev_item in events[:10]:
                    attrs = ev_item.get("attributes", {})
                    name = attrs.get("gen_ai.evaluation.name", "?")
                    score = attrs.get("gen_ai.evaluation.score.value", "?")
                    label = attrs.get("gen_ai.evaluation.score.label", "?")
                    explanation = attrs.get("gen_ai.evaluation.explanation", "")
                    sid = attrs.get("session.id", "?")
                    print(f"  [{name}] score={score} label={label} session={str(sid)[:36]}")
                    if explanation:
                        print(f"    {str(explanation)[:140]}")
        except LookupError as exc:
            print(f"Per-turn events not ready yet (retry after a moment): {exc}")
    else:
        print("\nNo CloudWatch output config returned — per-turn events unavailable.")

    _separator()
    print("Done.")
    print(
        "\nNext steps:\n"
        "  - Run optimize_agent.py to generate improvement recommendations\n"
        "    based on these sessions and test them with A/B experiments.\n"
        f"  - View full results in CloudWatch:\n"
        f"    aws logs tail /aws/bedrock-agentcore/evaluations/batch-evaluations/results/default "
        f"--log-stream-names run-{result.batch_evaluation_id} --region {REGION}"
    )


if __name__ == "__main__":
    main()
