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
        "expected_trajectory": {"toolNames": ["execute_sql_query"]},
        "assertions": [
            {"text": "Agent generated a SQL query summing all regional sales columns"},
            {"text": "Agent ordered results by total sales descending with LIMIT 5"},
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
        "expected_trajectory": {"toolNames": ["execute_sql_query"]},
        "assertions": [
            {"text": "Agent used AVG(critic_score) in the query"},
            {"text": "Agent grouped results by genre"},
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
        "expected_trajectory": {"toolNames": ["execute_sql_query"]},
        "assertions": [
            {"text": "Agent filtered for year 2015 using release_date"},
            {"text": "Agent summed na_sales grouped by genre"},
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
        "expected_trajectory": {"toolNames": ["execute_sql_query"]},
        "assertions": [
            {"text": "Agent filtered for PS4 console"},
            {"text": "Agent counted titles grouped by publisher"},
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
        "expected_trajectory": {"toolNames": ["execute_sql_query"]},
        "assertions": [
            {"text": "Agent filtered for Nintendo publisher"},
            {"text": "Agent compared jp_sales and na_sales totals"},
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
        "expected_trajectory": {"toolNames": ["execute_sql_query"]},
        "assertions": [
            {"text": "Agent extracted year from release_date"},
            {"text": "Agent counted titles per year ordered chronologically"},
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
            {"text": "Agent politely declined the out-of-scope request"},
            {"text": "Agent did not attempt to generate a SQL query"},
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
        "expected_trajectory": {"toolNames": ["execute_sql_query"]},
        "assertions": [
            {"text": "Agent summed pal_sales (Europe/PAL region sales)"},
            {"text": "Agent grouped by console ordered descending"},
        ],
        "metadata": {"category": "aggregation"},
    },
]


def invoke_agent(agentcore_client, agent_runtime_arn: str, prompt: str, session_id: str) -> str:
    """Invoke the deployed agent and collect the full response."""
    payload = json.dumps(
        {
            "prompt": prompt,
            "session_id": session_id,
            "user_id": "evaluator",
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

    raw = response["response"].read().decode("utf-8")
    parts = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            chunk = line[len("data: ") :]
            try:
                chunk = json.loads(chunk)
            except Exception:
                pass
            parts.append(str(chunk))
    return "".join(parts) if parts else raw


def create_managed_dataset(region: str) -> tuple[str, str]:
    """Create a managed PREDEFINED dataset from the evaluation scenarios.

    Returns:
        Tuple of (dataset_id, dataset_version)
    """
    from bedrock_agentcore.evaluation import DatasetClient

    client = DatasetClient(region_name=region)

    ds_name = f"data_analyst_eval_{uuid.uuid4().hex[:8]}"
    logger.info("Creating managed dataset '%s' with %d scenarios ...", ds_name, len(DATASET_SCENARIOS))

    dataset = client.create_dataset_and_wait(
        datasetName=ds_name,
        schemaType="AGENTCORE_EVALUATION_PREDEFINED_V1",
        source={"inlineExamples": {"examples": DATASET_SCENARIOS}},
    )
    dataset_id = dataset["datasetId"]
    logger.info(
        "  datasetId: %s (status: %s, examples: %s)", dataset_id, dataset["status"], dataset.get("exampleCount", "?")
    )

    logger.info("  Publishing as version 1 ...")
    client.create_dataset_version_and_wait(datasetId=dataset_id)
    logger.info("  Dataset version 1 published (immutable snapshot)")

    return dataset_id, "1"


def run_batch_evaluation(
    region: str,
    agent_runtime_arn: str,
    dataset_id: str,
    dataset_version: str,
    cw_log_group: str,
    service_name: str,
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
        Dataset,
        DatasetClient,
        PredefinedScenario,
        Turn,
    )

    agentcore_client = boto3.client("bedrock-agentcore", region_name=region)

    # Load examples from the managed dataset and convert to Dataset object
    dc = DatasetClient(region_name=region)
    logger.info("Loading dataset %s version %s ...", dataset_id, dataset_version)
    examples_resp = dc.list_dataset_examples(datasetId=dataset_id, datasetVersion=dataset_version)
    examples = examples_resp.get("examples", [])

    scenarios = []
    for ex in examples:
        turns = [Turn(input=t["input"], expected_response=t.get("expected_response")) for t in ex.get("turns", [])]
        traj = (ex.get("expected_trajectory") or {}).get("toolNames", [])
        assertions = [a["text"] for a in ex.get("assertions", []) if isinstance(a, dict)]
        scenarios.append(
            PredefinedScenario(
                scenario_id=ex["scenario_id"],
                turns=turns,
                expected_trajectory=traj or None,
                assertions=assertions or None,
            )
        )
    dataset = Dataset(scenarios=scenarios)
    logger.info("  Loaded %d scenarios", len(dataset.scenarios))

    def agent_invoker(invoker_input: AgentInvokerInput) -> AgentInvokerOutput:
        payload = invoker_input.payload
        body = {"prompt": payload} if isinstance(payload, str) else payload
        body.setdefault("session_id", invoker_input.session_id)
        body.setdefault("user_id", "evaluator")
        body.setdefault("user_timezone", "UTC")
        body.setdefault("user_name", "Evaluator")
        resp = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=agent_runtime_arn,
            qualifier="DEFAULT",
            runtimeSessionId=invoker_input.session_id,
            payload=json.dumps(body).encode("utf-8"),
        )
        raw = resp["response"].read().decode("utf-8")
        parts = []
        for line in raw.splitlines():
            if line.startswith("data: "):
                chunk = line[len("data: ") :]
                try:
                    chunk = json.loads(chunk)
                except Exception:
                    pass
                parts.append(str(chunk))
        return AgentInvokerOutput(agent_output="".join(parts) if parts else raw)

    eval_name = f"data_analyst_eval_{uuid.uuid4().hex[:8]}"
    config = BatchEvaluationRunConfig(
        batch_evaluation_name=eval_name,
        evaluator_config=BatchEvaluatorConfig(
            evaluator_ids=[
                "Builtin.Correctness",
                "Builtin.GoalSuccessRate",
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
    sql_evaluator_arn: str = "",
    response_evaluator_arn: str = "",
) -> dict:
    """Run EvaluationClient against individual sessions using managed dataset ground truth.

    Invokes the agent for the first scenario, waits for CloudWatch ingestion,
    then evaluates using ground truth loaded from the managed dataset.
    """
    from bedrock_agentcore.evaluation import DatasetClient, EvaluationClient, ReferenceInputs

    agentcore_client = boto3.client("bedrock-agentcore", region_name=region)
    agent_id = agent_runtime_arn.split("/")[-1]

    dc = DatasetClient(region_name=region)
    examples_resp = dc.list_dataset_examples(datasetId=dataset_id, datasetVersion=dataset_version)
    examples = examples_resp.get("examples", [])

    if not examples:
        logger.warning("No examples found in dataset %s version %s", dataset_id, dataset_version)
        return {}

    scenario = examples[0]
    first_turn = scenario["turns"][0]

    session_id = str(uuid.uuid4())
    logger.info("Invoking agent for scenario '%s' ...", scenario["scenario_id"])
    invoke_agent(agentcore_client, agent_runtime_arn, first_turn["input"], session_id)

    logger.info("  Waiting 60s for CloudWatch log ingestion ...")
    time.sleep(60)

    evaluator_ids = ["Builtin.Correctness", "Builtin.GoalSuccessRate"]
    if sql_evaluator_arn:
        evaluator_ids.append(sql_evaluator_arn)
    if response_evaluator_arn:
        evaluator_ids.append(response_evaluator_arn)

    assertions = [a["text"] for a in scenario.get("assertions", []) if isinstance(a, dict)]

    ec = EvaluationClient(region_name=region)
    logger.info("Running evaluators: %s", evaluator_ids)
    results = ec.run(
        evaluator_ids=evaluator_ids,
        agent_id=agent_id,
        session_id=session_id,
        look_back_time=timedelta(hours=1),
        reference_inputs=ReferenceInputs(
            expected_response=first_turn.get("expected_response"),
            assertions=assertions or None,
        ),
    )

    logger.info("EvaluationClient results:")
    for r in results:
        evaluator = r.get("evaluatorId", "")[-40:]
        value = r.get("value", r.get("score", "N/A"))
        label = r.get("label", r.get("rating", ""))
        logger.info("  %s: value=%s label=%s", evaluator, value, label)

    return {"scenario_id": scenario["scenario_id"], "session_id": session_id, "results": results}


def cleanup_dataset(region: str, dataset_id: str):
    """Delete the managed dataset created during evaluation."""
    from bedrock_agentcore.evaluation import DatasetClient

    client = DatasetClient(region_name=region)
    try:
        client.delete_dataset_and_wait(datasetId=dataset_id)
        logger.info("Cleaned up dataset %s", dataset_id)
    except Exception as e:
        logger.warning("Failed to clean up dataset %s: %s", dataset_id, e)


def main():
    parser = argparse.ArgumentParser(description="Run evaluations for Data Analyst Conversational Assistant")
    parser.add_argument("--region", default=None, help="AWS region")
    parser.add_argument(
        "--agent-runtime-arn",
        required=True,
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

    args = parser.parse_args()

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

    # Step 1: Create managed dataset
    print("[1/3] Creating managed evaluation dataset ...")
    dataset_id, dataset_version = create_managed_dataset(region)
    print(f"  Dataset ID: {dataset_id} (version: {dataset_version})")

    results = {"dataset_id": dataset_id, "dataset_version": dataset_version}
    output_file = Path("evaluations/eval_results.json")

    try:
        # Step 2: Run batch evaluation
        print(f"\n[2/3] Running batch evaluation ({len(DATASET_SCENARIOS)} scenarios) ...")
        batch_results = run_batch_evaluation(
            region=region,
            agent_runtime_arn=args.agent_runtime_arn,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            cw_log_group=cw_log_group,
            service_name=service_name,
        )
        results["batch_evaluation"] = batch_results

        # Step 3: Optionally run EvaluationClient for individual sessions
        if args.use_agentcore_evals:
            print("\n[3/3] Running EvaluationClient on individual session ...")
            session_results = run_session_evaluation(
                region=region,
                agent_runtime_arn=args.agent_runtime_arn,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
            )
            results["session_evaluation"] = session_results
        else:
            print("\n[3/3] Skipped (pass --use-agentcore-evals to run EvaluationClient)")

    finally:
        # Save results
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(results, indent=2, default=str))
        logger.info("Results saved to: %s", output_file)

        # Cleanup
        if not args.keep_dataset:
            print("\n[Cleanup] Deleting managed dataset ...")
            cleanup_dataset(region, dataset_id)
        else:
            print(f"\n[Cleanup] Skipped — dataset {dataset_id} preserved (--keep-dataset)")

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
