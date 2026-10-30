"""Evaluate Market Trends Agent Skills with AgentCore Evaluations.

Prerequisite:
    uv run python deploy_skill_agent.py --region us-west-2

Usage:
    uv run python evaluators/scripts/evaluate_skills.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
from bedrock_agentcore.evaluation import (
    AgentInvokerInput,
    AgentInvokerOutput,
    Dataset,
    EvaluationClient,
    PredefinedScenario,
    Turn,
)
from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_models import (
    BatchEvaluationRunConfig,
    BatchEvaluatorConfig,
    CloudWatchDataSourceConfig,
)
from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_runner import (
    BatchEvaluationRunner,
)
from boto3.session import Session  # type: ignore[import-untyped]

_PROJECT_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _PROJECT_DIR / "skill_agent_config.json"
_RESULTS_PATH = _PROJECT_DIR / "evaluators/results/skill_evaluation_results.json"
_EVALUATOR_IDS = [
    "Builtin.SkillSelectionAccuracy",
    "Builtin.SkillInstructionFollowing",
]
_SCENARIOS = [
    (
        "trend-analysis",
        ("Use the trend-analysis skill to analyze NVDA's price trend, momentum, support, resistance, and confidence."),
    ),
    (
        "sector-rotation",
        ("Use the sector-rotation skill to recommend sectors to overweight and underweight in the current market."),
    ),
    (
        "earnings-snapshot",
        (
            "Use the earnings-snapshot skill to assess LLY's earnings outlook, "
            "valuation, dividend yield, and relevant earnings news."
        ),
    ),
    (
        "portfolio-risk",
        (
            "Use the portfolio-risk skill to evaluate a portfolio of NVDA, TSLA, "
            "and JPM for concentration and volatility risk."
        ),
    ),
]
_NO_SKILL_SCENARIO = (
    "no-skill",
    "Give me a concise general market overview with major indices and top movers.",
)
_EXPECTED_LABELS = {
    "Builtin.SkillSelectionAccuracy": {"Yes": 1.0},
    "Builtin.SkillInstructionFollowing": {
        "Fully Followed": 1.0,
        "Mostly Followed": 0.75,
    },
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AgentCore's built-in skill evaluators")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--region", default=None)
    parser.add_argument(
        "--wait",
        type=int,
        default=180,
        help="Seconds to allow unified telemetry ingestion (default: 180)",
    )
    args = parser.parse_args()
    if args.wait < 0:
        parser.error("--wait must be zero or greater")
    return args


def _load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Deployment config not found: {path}. Run deploy_skill_agent.py first.")
    raw_config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_config, dict):
        raise TypeError("Deployment config must contain a JSON object")
    config: dict[str, Any] = raw_config
    required = {"agent_id", "agent_arn", "cw_log_group", "service_name", "region"}
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"Deployment config is missing: {', '.join(missing)}")
    return config


def _response_text(raw: str) -> str:
    """Return text from AgentCore's JSON, SSE, or plain-text response."""
    parts: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        value = line.removeprefix("data:").strip()
        if value == "[DONE]":
            continue
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
        parts.append(str(value))
    if parts:
        return "".join(parts)
    try:
        return str(json.loads(raw))
    except json.JSONDecodeError:
        return raw


def _invoke(client: Any, agent_arn: str, session_id: str, prompt: Any) -> str:
    payload = {"prompt": prompt} if isinstance(prompt, str) else prompt
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode("utf-8"),
    )
    raw = response["response"].read().decode("utf-8", errors="replace")
    return _response_text(raw)


def _evaluator_id(result: dict[str, Any]) -> str:
    value = str(result.get("evaluatorId", ""))
    for evaluator_id in _EVALUATOR_IDS:
        if value == evaluator_id or value.endswith(f"/{evaluator_id}"):
            return evaluator_id
    return value


def _validate_session_results(
    scenario_id: str,
    results: list[dict[str, Any]],
    *,
    expects_skill: bool,
) -> list[str]:
    if not expects_skill:
        return [] if not results else [f"{scenario_id}: expected no skill results"]

    failures: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {evaluator_id: [] for evaluator_id in _EVALUATOR_IDS}
    for result in results:
        evaluator_id = _evaluator_id(result)
        if evaluator_id in grouped:
            grouped[evaluator_id].append(result)

    for evaluator_id, evaluator_results in grouped.items():
        if len(evaluator_results) != 1:
            failures.append(f"{scenario_id}: expected one {evaluator_id} result, received {len(evaluator_results)}")
            continue
        result = evaluator_results[0]
        if result.get("errorCode"):
            failures.append(f"{scenario_id} / {evaluator_id}: {result['errorCode']} - {result.get('errorMessage', '')}")
            continue
        label = result.get("label")
        value = result.get("value")
        expected = _EXPECTED_LABELS[evaluator_id]
        if label not in expected or value != expected.get(label):
            failures.append(f"{scenario_id} / {evaluator_id}: unexpected label/value {label!r}/{value!r}")
    return failures


def _print_results(scenario_id: str, results: list[dict[str, Any]]) -> None:
    if not results:
        print(f"  {scenario_id:<24} no skill evaluation results")
        return
    for result in results:
        evaluator_id = _evaluator_id(result)
        label = result.get("label", result.get("errorCode", "N/A"))
        value = result.get("value", "N/A")
        print(f"  {scenario_id:<24} {evaluator_id:<38} {value!s:<5} {label}")


def _dataset() -> Dataset:
    return Dataset(
        scenarios=[
            PredefinedScenario(
                scenario_id=skill_name,
                turns=[Turn(input=prompt)],
            )
            for skill_name, prompt in _SCENARIOS
        ]
    )


def main() -> int:
    args = _parse_args()
    try:
        config = _load_config(args.config)
    except (OSError, TypeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    region = args.region or config["region"] or Session().region_name or "us-east-1"
    if region != config["region"]:
        print("ERROR: --region must match the deployed runtime region", file=sys.stderr)
        return 1

    runtime_client = boto3.client("bedrock-agentcore", region_name=region)
    evaluation_client = EvaluationClient(region_name=region)
    agent_arn = str(config["agent_arn"])

    print("Market Trends Agent Skills Evaluation")
    print(f"  Runtime log group: {config['cw_log_group']}")
    print("  Telemetry source: unified runtime log group only")

    sessions: list[tuple[str, str, bool]] = []
    for scenario_id, prompt in [*_SCENARIOS, _NO_SKILL_SCENARIO]:
        session_id = f"skill-eval-{uuid.uuid4()}"
        response = _invoke(runtime_client, agent_arn, session_id, prompt)
        print(f"\n[Invoke] {scenario_id}: {response[:160]}")
        sessions.append((scenario_id, session_id, scenario_id != "no-skill"))

    if args.wait:
        print(f"\nWaiting {args.wait}s for unified telemetry ingestion ...")
        time.sleep(args.wait)

    failures: list[str] = []
    on_demand_results: dict[str, Any] = {}
    print("\nEvaluationClient results:")
    for scenario_id, session_id, expects_skill in sessions:
        results = evaluation_client.run(
            evaluator_ids=_EVALUATOR_IDS,
            session_id=session_id,
            agent_id=str(config["agent_id"]),
            log_group_name=str(config["cw_log_group"]),
            look_back_time=timedelta(hours=2),
        )
        _print_results(scenario_id, results)
        failures.extend(
            _validate_session_results(
                scenario_id,
                results,
                expects_skill=expects_skill,
            )
        )
        on_demand_results[scenario_id] = {
            "session_id": session_id,
            "results": results,
        }

    def agent_invoker(invoker_input: AgentInvokerInput) -> AgentInvokerOutput:
        if not invoker_input.session_id:
            raise ValueError("Batch evaluation did not provide a session ID")
        output = _invoke(
            runtime_client,
            agent_arn,
            invoker_input.session_id,
            invoker_input.payload,
        )
        return AgentInvokerOutput(agent_output=output)

    batch_config = BatchEvaluationRunConfig(
        batch_evaluation_name=f"market_trends_skills_{uuid.uuid4().hex[:8]}",
        evaluator_config=BatchEvaluatorConfig(evaluator_ids=_EVALUATOR_IDS),
        data_source=CloudWatchDataSourceConfig(
            service_names=[str(config["service_name"])],
            log_group_names=[str(config["cw_log_group"])],
            ingestion_delay_seconds=args.wait,
        ),
    )
    print("\nStarting BatchEvaluationRunner ...")
    batch_runner = BatchEvaluationRunner(region=region)
    batch_result = batch_runner.run_dataset_evaluation(
        config=batch_config,
        dataset=_dataset(),
        agent_invoker=agent_invoker,
    )
    print(f"  Batch ID: {batch_result.batch_evaluation_id}")
    print(f"  Status: {batch_result.status}")
    if batch_result.status != "COMPLETED":
        failures.append(f"batch evaluation ended with status {batch_result.status}")

    output = {
        "evaluation_client": on_demand_results,
        "batch_evaluation": batch_result.model_dump(),
        "validation_failures": failures,
    }
    _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RESULTS_PATH.write_text(
        json.dumps(output, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"\nResults saved to {_RESULTS_PATH}")

    if failures:
        print("\nValidation failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nAll four skills passed both built-in evaluators; no-skill control passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
