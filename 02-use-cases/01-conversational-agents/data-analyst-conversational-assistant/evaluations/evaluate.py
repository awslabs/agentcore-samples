#!/usr/bin/env python3
"""
Data Analyst Conversational Assistant — Evaluation Harness

Measures SQL generation accuracy and response quality using Amazon Bedrock AgentCore
Evaluations with managed datasets and batch evaluation.

This script demonstrates:
  1. DatasetClient — Create and manage a PREDEFINED evaluation dataset
  2. BatchEvaluationRunner — Invoke agent, submit server-side batch evaluation job,
     poll for aggregate scores (the native AgentCore batch evaluation API)
  3. EvaluationClient — Evaluate individual sessions against ground truth (on-demand)

Custom evaluators (deployed via CDK):
  - SqlAccuracy: Compares generated SQL results against ground-truth expected outputs
  - ResponseQuality: Checks that natural language answers are relevant and complete

Built-in evaluators:
  - Builtin.Correctness: General answer correctness
  - Builtin.GoalSuccessRate: Whether the agent achieved the user's goal

Usage:
  python evaluations/evaluate.py --region us-east-1 --agent-runtime-arn <ARN>

Prerequisites:
  - Agent deployed to Amazon Bedrock AgentCore Runtime
  - CloudWatch Transaction Search enabled
  - pip install bedrock-agentcore boto3
"""

import argparse
import json
import logging
import os
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path

import boto3
from boto3.session import Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATASET_SCENARIOS = [
    {
        "scenario_id": "top-5-best-selling",
        "turns": [
            {
                "input": "What are the top 5 best-selling games globally?",
                "expected_response": "The query should sum na_sales + jp_sales + pal_sales + other_sales and ORDER BY total DESC LIMIT 5",
            }
        ],
        "expected_trajectory": ["DatabaseToolsTarget___execute_sql_query"],
        "assertions": [
            "Agent generated a SQL query summing all regional sales columns",
            "Agent ordered results by total sales descending with LIMIT 5",
        ],
        "metadata": {"category": "aggregation"},
    },
    {
        "scenario_id": "highest-avg-critic-score",
        "turns": [
            {
                "input": "Which genre has the highest average critic score?",
                "expected_response": "The query should use AVG(critic_score) grouped by genre, ordered descending, limit 1",
            }
        ],
        "expected_trajectory": ["DatabaseToolsTarget___execute_sql_query"],
        "assertions": [
            "Agent used AVG(critic_score) in the query",
            "Agent grouped results by genre",
        ],
        "metadata": {"category": "aggregation"},
    },
    {
        "scenario_id": "na-sales-2015-by-genre",
        "turns": [
            {
                "input": "Total North America sales for games released in 2015 by genre",
                "expected_response": "The query should filter release_date for year 2015, sum na_sales, group by genre",
            }
        ],
        "expected_trajectory": ["DatabaseToolsTarget___execute_sql_query"],
        "assertions": [
            "Agent filtered for year 2015 using release_date",
            "Agent summed na_sales grouped by genre",
        ],
        "metadata": {"category": "filtering"},
    },
    {
        "scenario_id": "most-ps4-games-publisher",
        "turns": [
            {
                "input": "Which publisher released the most games on PS4?",
                "expected_response": "The query should filter console='PS4', count titles grouped by publisher, order descending, limit 1",
            }
        ],
        "expected_trajectory": ["DatabaseToolsTarget___execute_sql_query"],
        "assertions": [
            "Agent filtered for PS4 console",
            "Agent counted titles grouped by publisher",
        ],
        "metadata": {"category": "filtering"},
    },
    {
        "scenario_id": "nintendo-jp-vs-na-sales",
        "turns": [
            {
                "input": "Compare Japanese sales vs North American sales for Nintendo games",
                "expected_response": "The query should filter publisher='Nintendo', sum jp_sales and na_sales",
            }
        ],
        "expected_trajectory": ["DatabaseToolsTarget___execute_sql_query"],
        "assertions": [
            "Agent filtered for Nintendo publisher",
            "Agent compared jp_sales and na_sales totals",
        ],
        "metadata": {"category": "comparison"},
    },
    {
        "scenario_id": "releases-by-year-trend",
        "turns": [
            {
                "input": "What is the trend in game releases by year?",
                "expected_response": "The query should extract year from release_date, count titles per year, order by year",
            }
        ],
        "expected_trajectory": ["DatabaseToolsTarget___execute_sql_query"],
        "assertions": [
            "Agent extracted year from release_date",
            "Agent counted titles per year ordered chronologically",
        ],
        "metadata": {"category": "trend"},
    },
    {
        "scenario_id": "out-of-scope-weather",
        "turns": [
            {
                "input": "Tell me about the weather today",
                "expected_response": "The agent should politely decline since this is outside the data analysis domain",
            }
        ],
        "assertions": [
            "Agent politely declined the out-of-scope request",
            "Agent did not attempt to generate a SQL query",
        ],
        "metadata": {"category": "out_of_scope"},
    },
    {
        "scenario_id": "europe-sales-by-console",
        "turns": [
            {
                "input": "What consoles have the highest total sales in Europe?",
                "expected_response": "The query should sum pal_sales grouped by console, ordered descending",
            }
        ],
        "expected_trajectory": ["DatabaseToolsTarget___execute_sql_query"],
        "assertions": [
            "Agent summed pal_sales (Europe/PAL region sales)",
            "Agent grouped by console ordered descending",
        ],
        "metadata": {"category": "aggregation"},
    },
]


def decode_runtime_response(raw: str) -> str:
    parts = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        event = json.loads(line[5:].strip())
        if isinstance(event, str):
            event = json.loads(event)
        if event.get("error"):
            raise RuntimeError("Runtime rejected the evaluation request")
        if isinstance(event.get("data"), str):
            parts.append(event["data"])
    if not parts:
        raise RuntimeError("Runtime returned no assistant text")
    return "".join(parts)


def invoke_agent(agentcore_client, agent_runtime_arn: str, prompt: str, session_id: str, id_token: str) -> str:
    """Invoke the deployed agent and collect the full response."""
    payload = json.dumps(
        {
            "prompt": prompt,
            "session_id": session_id,
            "id_token": id_token,
            "user_timezone": "UTC",
            "user_name": "Evaluator",
            "prompt_uuid": str(uuid.uuid4()),
        }
    ).encode()

    response = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=agent_runtime_arn,
        runtimeSessionId=session_id,
        payload=payload,
        qualifier="DEFAULT",
    )

    return decode_runtime_response(response["response"].read().decode("utf-8"))


def save_results(path: Path, results: dict):
    """Durable, private replacement; ownership is recorded before any wait."""
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(results, stream, indent=2, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def wait_for_dataset(client, dataset_id, *, deleting=False, timeout=120, interval=2):
    """Bounded polling. Only the modeled ResourceNotFoundException proves deletion."""
    from botocore.exceptions import ClientError

    deadline = time.monotonic() + timeout
    while True:
        try:
            response = client.get_dataset(datasetId=dataset_id)
        except ClientError as error:
            if deleting and error.response["Error"]["Code"] == "ResourceNotFoundException":
                return
            raise
        if response.get("datasetId") != dataset_id:
            raise RuntimeError("Dataset response identity mismatch")
        status = response.get("status")
        if not deleting and status == "ACTIVE":
            return response
        if status not in ({"DELETING"} if deleting else {"CREATING", "UPDATING"}):
            raise RuntimeError(f"Dataset {dataset_id} unexpected/failed status: {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Dataset {dataset_id} did not reach the required terminal state")
        time.sleep(interval)


def create_managed_dataset(region: str, results: dict, output_file: Path) -> tuple[str, str]:
    from bedrock_agentcore.evaluation import DatasetClient, PredefinedScenario

    client = DatasetClient(region_name=region)
    # SDK PREDEFINED documents use list[str], not API ReferenceInputs objects.
    examples = [
        PredefinedScenario.model_validate(s).model_dump(mode="json", exclude_none=True) for s in DATASET_SCENARIOS
    ]
    response = client.create_dataset(
        datasetName=results["dataset_name"],
        schemaType="AGENTCORE_EVALUATION_PREDEFINED_V1",
        source={"inlineExamples": {"examples": examples}},
    )
    dataset_id = response["datasetId"]
    results.update(dataset_id=dataset_id, dataset_arn=response.get("datasetArn"), phase="created")
    save_results(output_file, results)  # before ACTIVE wait or publication can fail
    wait_for_dataset(client, dataset_id)
    results["phase"] = "publishing"
    save_results(output_file, results)
    client.create_dataset_version(datasetId=dataset_id)
    wait_for_dataset(client, dataset_id)
    results.update(dataset_version="1", phase="published")  # first version of this newly created dataset
    save_results(output_file, results)
    return dataset_id, "1"


def load_dataset(client, dataset_id: str, dataset_version: str):
    from bedrock_agentcore.evaluation import Dataset, PredefinedScenario

    scenarios = []
    token = None
    seen = set()
    while True:
        response = client.list_dataset_examples(
            datasetId=dataset_id, datasetVersion=dataset_version, **({"nextToken": token} if token else {})
        )
        scenarios.extend(PredefinedScenario.model_validate(ex) for ex in response.get("examples", []))
        token = response.get("nextToken")
        if not token:
            break
        if token in seen:
            raise RuntimeError("Repeated dataset pagination token")
        seen.add(token)
    dataset = Dataset(scenarios=scenarios)
    if {s.scenario_id for s in dataset.scenarios} != {s["scenario_id"] for s in DATASET_SCENARIOS}:
        raise RuntimeError("Dataset must contain all eight expected scenarios, not a successful subset")
    return dataset


def require_complete_batch(result: dict):
    """Fail closed on absent counters, partial invocations or evaluator coverage."""
    expected = len(DATASET_SCENARIOS)
    raw = result.get("runner_result", {})
    summary = raw.get("evaluation_results") or {}
    counters = {
        "number_of_sessions_completed": expected,
        "total_number_of_sessions": expected,
        "number_of_sessions_failed": 0,
        "number_of_sessions_ignored": 0,
        "number_of_sessions_in_progress": 0,
    }
    evaluators = summary.get("evaluator_summaries") or []
    ids = [e.get("evaluator_id") for e in evaluators]
    wanted = result.get("expected_evaluators", [])
    if (
        result.get("expected_scenario_ids") != [s["scenario_id"] for s in DATASET_SCENARIOS]
        or raw.get("status") != "COMPLETED"
        or raw.get("agent_invocation_failures") != []
        or raw.get("error_details")
        or any(summary.get(k) != v for k, v in counters.items())
        or len(wanted) != 4
        or len(set(wanted)) != 4
        or sorted(ids, key=str) != sorted(wanted)
        or any(e.get("total_evaluated") != expected or e.get("total_failed") != 0 for e in evaluators)
    ):
        raise RuntimeError(
            "Evaluation incomplete: require all eight one-turn scenarios and all four evaluators; inspect saved diagnostics"
        )


def run_batch_evaluation(
    region: str,
    agent_runtime_arn: str,
    dataset_id: str,
    dataset_version: str,
    cw_log_group: str,
    service_name: str,
    id_token: str,
    sql_evaluator_arn: str,
    response_evaluator_arn: str,
) -> dict:
    """Run evaluation using BatchEvaluationRunner (server-side batch API).

    Uses the native AgentCore Batch Evaluation pipeline:
      1. Loads scenarios from the managed dataset
      2. Invokes the agent for each scenario (parallel)
      3. Submits a StartBatchEvaluation job pointing at the CloudWatch spans
      4. Polls until the server-side evaluation completes
      5. Returns aggregate scores per evaluator
    """
    from bedrock_agentcore.evaluation import (
        AgentInvokerInput,
        AgentInvokerOutput,
        BatchEvaluationRunConfig,
        BatchEvaluationRunner,
        BatchEvaluatorConfig,
        CloudWatchDataSourceConfig,
        DatasetClient,
    )

    agentcore_client = boto3.client("bedrock-agentcore", region_name=region)

    # Load examples from the managed dataset and convert to Dataset object
    dc = DatasetClient(region_name=region)
    logger.info("Loading dataset %s version %s ...", dataset_id, dataset_version)
    dataset = load_dataset(dc, dataset_id, dataset_version)
    logger.info("  Loaded %d scenarios", len(dataset.scenarios))

    def agent_invoker(invoker_input: AgentInvokerInput) -> AgentInvokerOutput:
        payload = invoker_input.payload
        body = {"prompt": payload} if isinstance(payload, str) else dict(payload)
        body["session_id"] = invoker_input.session_id
        body["id_token"] = id_token
        body["prompt_uuid"] = str(uuid.uuid4())
        body.setdefault("user_timezone", "UTC")
        body.setdefault("user_name", "Evaluator")
        resp = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=agent_runtime_arn,
            qualifier="DEFAULT",
            runtimeSessionId=invoker_input.session_id,
            payload=json.dumps(body).encode("utf-8"),
        )
        return AgentInvokerOutput(agent_output=decode_runtime_response(resp["response"].read().decode("utf-8")))

    eval_name = f"data_analyst_eval_{uuid.uuid4().hex[:8]}"
    config = BatchEvaluationRunConfig(
        batch_evaluation_name=eval_name,
        evaluator_config=BatchEvaluatorConfig(
            evaluator_ids=[
                "Builtin.Correctness",
                "Builtin.GoalSuccessRate",
                sql_evaluator_arn,
                response_evaluator_arn,
            ]
        ),
        data_source=CloudWatchDataSourceConfig(
            service_names=[service_name],
            log_group_names=[cw_log_group],
            ingestion_delay_seconds=180,
        ),
        max_concurrent_scenarios=3,
        polling_timeout_seconds=600,
        polling_interval_seconds=30,
    )

    logger.info("Starting batch evaluation '%s' ...", eval_name)
    logger.info("  Service name: %s", service_name)
    logger.info("  Log group: %s", cw_log_group)
    logger.info("  Evaluators: %s", config.evaluator_config.evaluator_ids)
    logger.info("  Scenarios: %d", len(dataset.scenarios))

    runner = BatchEvaluationRunner(region=region)
    result = runner.run_dataset_evaluation(
        config=config,
        dataset=dataset,
        agent_invoker=agent_invoker,
    )

    logger.info("  Batch evaluation status: %s", result.status)
    logger.info("  Batch evaluation ID: %s", result.batch_evaluation_id)

    result_data = {
        "batch_evaluation_id": result.batch_evaluation_id,
        "batch_evaluation_name": result.batch_evaluation_name,
        "status": result.status,
        "runner_result": result.model_dump(mode="json"),
        "expected_scenario_ids": [s["scenario_id"] for s in DATASET_SCENARIOS],
        "expected_evaluators": config.evaluator_config.evaluator_ids,
    }

    if result.agent_invocation_failures:
        result_data["invocation_failures"] = [
            {"scenario_id": f.scenario_id, "error": f.error_message} for f in result.agent_invocation_failures
        ]
        for f in result.agent_invocation_failures:
            logger.warning("  Invocation failure: %s — %s", f.scenario_id, f.error_message)

    if result.evaluation_results:
        summary = result.evaluation_results
        result_data["total_scenarios"] = len(dataset.scenarios)
        result_data["completed"] = summary.number_of_sessions_completed
        result_data["failed"] = summary.number_of_sessions_failed
        result_data["evaluator_summaries"] = []

        for es in summary.evaluator_summaries or []:
            avg = es.statistics.average_score if es.statistics else None
            entry = {
                "evaluator_id": es.evaluator_id,
                "average_score": avg,
                "total_evaluated": es.total_evaluated,
            }
            result_data["evaluator_summaries"].append(entry)
            logger.info("  %s: avg=%.3f (n=%d)", es.evaluator_id, avg or 0, es.total_evaluated or 0)
    else:
        result_data["total_scenarios"] = len(dataset.scenarios)
        result_data["completed"] = 0
        result_data["failed"] = len(dataset.scenarios)
        logger.warning("  No evaluation results returned (status: %s)", result.status)
        if result.error_details:
            logger.warning("  Error: %s", result.error_details)
            result_data["error"] = str(result.error_details)

    if result.output_data_config:
        result_data["output_log_group"] = result.output_data_config.log_group_name
        result_data["output_log_stream"] = result.output_data_config.log_stream_name
        logger.info(
            "  Detailed results: %s / %s",
            result.output_data_config.log_group_name,
            result.output_data_config.log_stream_name,
        )

    return result_data


def run_session_evaluation(
    region: str,
    agent_runtime_arn: str,
    dataset_id: str,
    dataset_version: str,
    id_token: str,
    sql_evaluator_arn: str,
    response_evaluator_arn: str,
) -> dict:
    """Run EvaluationClient against individual sessions using managed dataset ground truth.

    Invokes the agent for the first scenario, waits for CloudWatch ingestion,
    then evaluates using ground truth loaded from the managed dataset.
    """
    from bedrock_agentcore.evaluation import DatasetClient, EvaluationClient, ReferenceInputs

    agentcore_client = boto3.client("bedrock-agentcore", region_name=region)
    agent_id = agent_runtime_arn.split("/")[-1]

    dc = DatasetClient(region_name=region)
    scenario = load_dataset(dc, dataset_id, dataset_version).scenarios[0]
    first_turn = scenario.turns[0]

    session_id = str(uuid.uuid4())
    logger.info("Invoking agent for scenario '%s' ...", scenario.scenario_id)
    invoke_agent(agentcore_client, agent_runtime_arn, first_turn.input, session_id, id_token)

    logger.info("  Waiting 60s for CloudWatch log ingestion ...")
    time.sleep(60)

    evaluator_ids = ["Builtin.Correctness", "Builtin.GoalSuccessRate"]
    if sql_evaluator_arn:
        evaluator_ids.append(sql_evaluator_arn)
    if response_evaluator_arn:
        evaluator_ids.append(response_evaluator_arn)

    assertions = scenario.assertions

    ec = EvaluationClient(region_name=region)
    logger.info("Running evaluators: %s", evaluator_ids)
    results = ec.run(
        evaluator_ids=evaluator_ids,
        agent_id=agent_id,
        session_id=session_id,
        look_back_time=timedelta(hours=1),
        reference_inputs=ReferenceInputs(
            expected_response=first_turn.expected_response,
            assertions=assertions,
            expected_trajectory=scenario.expected_trajectory,
        ),
    )

    logger.info("EvaluationClient results:")
    for r in results:
        evaluator = r.get("evaluatorId", "")[-40:]
        value = r.get("value", r.get("score", "N/A"))
        label = r.get("label", r.get("rating", ""))
        logger.info("  %s: value=%s label=%s", evaluator, value, label)

    return {"scenario_id": scenario.scenario_id, "session_id": session_id, "results": results}


def require_complete_session(session_result: dict, evaluator_ids: list[str]):
    """Validate returned results, not merely that four Evaluate calls were issued.

    Custom responses carry the exact full evaluatorArn and its resource ID in
    evaluatorId. Builtin.Name requests return
    arn:<partition>:bedrock-agentcore:::evaluator/Builtin.Name plus the plain ID.
    TRACE evaluators
    may produce multiple distinct target results. Exact duplicate targets cannot
    stand in for missing evaluator coverage. Zero is a valid numerical score.
    """
    import math

    # Custom ARN ownership stays exact. Built-ins are partition-scoped service
    # resources, with empty region/account components in the response model.
    partitions = {i.split(":", 2)[1] for i in evaluator_ids if i.startswith("arn:")}
    if len(partitions) != 1:
        raise RuntimeError("Session evaluation requires custom ARNs in one AWS partition")
    partition = next(iter(partitions))
    expected = {
        f"arn:{partition}:bedrock-agentcore:::evaluator/{i}" if i.startswith("Builtin.") else i for i in evaluator_ids
    }
    rows = session_result.get("results")
    if len(evaluator_ids) != 4 or len(expected) != 4 or not isinstance(rows, list) or not rows:
        raise RuntimeError("Session evaluation incomplete: no complete four-evaluator result set")
    covered, targets = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Session evaluation returned an invalid result")  # noqa: TRY004 -- invalid remote result, not a caller argument
        arn, identifier = row.get("evaluatorArn"), row.get("evaluatorId")
        if arn not in expected or identifier != arn.rsplit("/", 1)[-1]:
            raise RuntimeError("Session evaluation returned an unexpected or missing evaluator identity")
        if row.get("errorCode") or row.get("errorMessage"):
            raise RuntimeError("Session evaluation contains evaluator errors; inspect saved diagnostics")
        value, label = row.get("value"), row.get("label")
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        categorical = value is None and isinstance(label, str) and bool(label.strip())
        if not numeric and not categorical:
            raise RuntimeError("Session evaluation returned no valid numerical or categorical score")
        target = (arn, json.dumps(row.get("context"), sort_keys=True), row.get("evaluationDimensionName"))
        if target in targets:
            raise RuntimeError("Session evaluation returned a duplicate evaluator/target result")
        targets.add(target)
        covered.add(arn)
    if covered != expected:
        raise RuntimeError("Session evaluation incomplete: missing requested evaluator coverage")


def cleanup_dataset(region: str, dataset_id: str, expected_name: str):
    """Delete only the durably recorded dataset, verifying identity and absence."""
    from bedrock_agentcore.evaluation import DatasetClient
    from botocore.exceptions import ClientError

    client = DatasetClient(region_name=region)
    try:
        current = client.get_dataset(datasetId=dataset_id)
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceNotFoundException":
            return
        raise
    if current.get("datasetId") != dataset_id or current.get("datasetName") != expected_name:
        raise RuntimeError("Refusing cleanup: dataset does not match this run journal")
    response = client.delete_dataset(datasetId=dataset_id)
    if response.get("datasetId") != dataset_id:
        raise RuntimeError("Deletion response identity mismatch")
    wait_for_dataset(client, dataset_id, deleting=True)


def finish_run(results, output_file, keep_dataset, primary_error=None):
    errors = [primary_error] if primary_error is not None else []
    if primary_error is not None:
        results["failure"] = {"type": type(primary_error).__name__, "message": str(primary_error)}
    if results.get("dataset_id"):
        if keep_dataset:
            results["cleanup"] = "kept"
        else:
            try:
                cleanup_dataset(results["region"], results["dataset_id"], results["dataset_name"])
                results["cleanup"] = "verified_absent"
            except Exception as error:
                results["cleanup"] = {"status": "incomplete", "type": type(error).__name__, "message": str(error)}
                errors.append(error)
    try:
        save_results(output_file, results)
    except Exception as error:
        errors.append(error)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise ExceptionGroup("Evaluation and/or cleanup did not complete; journal retains exact ownership", errors)


def recover_dataset_cleanup(output_file: Path):
    results = json.loads(output_file.read_text())
    if (
        results.get("format") != "data-analyst-evaluation-v1"
        or not results.get("dataset_id")
        or results.get("dataset_name") != "data_analyst_eval_" + results.get("run_id", "")
    ):
        raise ValueError("Not an owned evaluation journal; no name-based adoption is supported")
    finish_run(results, output_file, keep_dataset=False)


def main():
    parser = argparse.ArgumentParser(description="Run evaluations for Data Analyst Conversational Assistant")
    parser.add_argument("--region", default=None, help="AWS region")
    parser.add_argument(
        "--agent-runtime-arn",
        help="AgentCore Runtime ARN (from deploy outputs)",
    )
    parser.add_argument(
        "--cw-log-group",
        default=None,
        help="CloudWatch log group for agent traces (default: auto-derived from runtime ARN)",
    )
    parser.add_argument(
        "--use-agentcore-evals",
        action="store_true",
        help="Also run EvaluationClient for individual session scoring",
    )
    parser.add_argument(
        "--keep-dataset",
        action="store_true",
        help="Keep the managed dataset after evaluation (do not delete)",
    )

    parser.add_argument(
        "--results-file", type=Path, help="New private journal path; existing files are never overwritten by a new run"
    )
    parser.add_argument(
        "--cleanup-results-file", type=Path, help="Recover cleanup for exactly the dataset recorded by an earlier run"
    )
    parser.add_argument(
        "--id-token-file", type=Path, help="Short-lived Cognito ID token file; never put tokens in command arguments"
    )
    parser.add_argument("--sql-evaluator-arn")
    parser.add_argument("--response-evaluator-arn")
    args = parser.parse_args()
    if args.cleanup_results_file:
        if args.keep_dataset:
            parser.error("--keep-dataset cannot be combined with explicit cleanup")
        recover_dataset_cleanup(args.cleanup_results_file)
        return
    if not all((args.agent_runtime_arn, args.id_token_file, args.sql_evaluator_arn, args.response_evaluator_arn)):
        parser.error("Runtime ARN, ID token file and both custom evaluator ARNs are required")
    id_token = args.id_token_file.read_text().strip()
    if not id_token or not all(
        a.startswith("arn:aws:bedrock-agentcore:") and ":evaluator/" in a
        for a in (args.sql_evaluator_arn, args.response_evaluator_arn)
    ):
        parser.error("ID token and both custom evaluator ARNs are required")

    region = args.region or Session().region_name or "us-east-1"
    agent_id = args.agent_runtime_arn.split("/")[-1]
    cw_log_group = args.cw_log_group or f"/aws/bedrock-agentcore/runtimes/{agent_id}-DEFAULT"

    # Derive the OTel service name from the runtime name (not ID).
    # The service name in CloudWatch spans is "{runtimeName}.DEFAULT".
    ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
    runtime_info = ctrl.get_agent_runtime(agentRuntimeId=agent_id)
    service_name = f"{runtime_info['agentRuntimeName']}.DEFAULT"

    print("=" * 60)
    print("Data Analyst Assistant — Evaluation Harness")
    print("=" * 60)
    print(f"  Region           : {region}")
    print(f"  Agent Runtime ARN: {args.agent_runtime_arn}")
    print(f"  CW Log Group     : {cw_log_group}")
    print(f"  Service Name     : {service_name}")
    print()

    run_id = uuid.uuid4().hex[:16]
    output_file = args.results_file or Path(f"evaluations/eval_results_{run_id}.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.touch(mode=0o600, exist_ok=False)
    results = {
        "format": "data-analyst-evaluation-v1",
        "run_id": run_id,
        "region": region,
        "dataset_name": "data_analyst_eval_" + run_id,
        "phase": "starting",
    }
    save_results(output_file, results)
    primary_error = None
    try:
        # Creation and publication are inside the same durable cleanup scope.
        dataset_id, dataset_version = create_managed_dataset(region, results, output_file)
        # Step 2: Run batch evaluation
        print(f"\n[2/3] Running batch evaluation ({len(DATASET_SCENARIOS)} scenarios) ...")
        batch_results = run_batch_evaluation(
            region=region,
            agent_runtime_arn=args.agent_runtime_arn,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            cw_log_group=cw_log_group,
            service_name=service_name,
            id_token=id_token,
            sql_evaluator_arn=args.sql_evaluator_arn,
            response_evaluator_arn=args.response_evaluator_arn,
        )
        results["batch_evaluation"] = batch_results
        save_results(output_file, results)
        require_complete_batch(batch_results)

        # Step 3: Optionally run EvaluationClient for individual sessions
        if args.use_agentcore_evals:
            print("\n[3/3] Running EvaluationClient on individual session ...")
            session_results = run_session_evaluation(
                region=region,
                agent_runtime_arn=args.agent_runtime_arn,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                id_token=id_token,
                sql_evaluator_arn=args.sql_evaluator_arn,
                response_evaluator_arn=args.response_evaluator_arn,
            )
            results["session_evaluation"] = session_results
            save_results(output_file, results)  # retain raw SDK results before any rejection
            require_complete_session(
                session_results,
                [
                    "Builtin.Correctness",
                    "Builtin.GoalSuccessRate",
                    args.sql_evaluator_arn,
                    args.response_evaluator_arn,
                ],
            )
        else:
            print("\n[3/3] Skipped (pass --use-agentcore-evals to run EvaluationClient)")

    except Exception as error:
        primary_error = error
    finally:
        # Preserve both original and cleanup failures, and the exact returned ID.
        finish_run(results, output_file, args.keep_dataset, primary_error)

    print("\n" + "=" * 60)
    print("EVALUATION COMPLETE")
    print("=" * 60)
    eval_data = results.get("batch_evaluation", {})
    print(f"  Scenarios      : {eval_data.get('total_scenarios', 'N/A')}")
    print(f"  Completed      : {eval_data.get('completed', 'N/A')}")
    print(f"  Failed         : {eval_data.get('failed', 'N/A')}")
    if eval_data.get("evaluator_summaries"):
        print("  Evaluator scores:")
        for es in eval_data["evaluator_summaries"]:
            avg = es.get("average_score")
            print(
                f"    {es['evaluator_id']}: {avg:.3f} (n={es.get('total_evaluated', 0)})"
                if avg
                else f"    {es['evaluator_id']}: N/A"
            )
    print(f"  Results file   : {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
