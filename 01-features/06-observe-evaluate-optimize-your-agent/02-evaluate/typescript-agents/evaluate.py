"""Evaluate the TypeScript HR Assistant with AgentCore Evaluations.

Walks through the full evaluation lifecycle for an agent written in TypeScript.
AgentCore Evaluations is language-agnostic: it scores the OpenTelemetry spans the
agent writes to CloudWatch, so the same Python SDK and APIs used for Python agents
work unchanged here.

  1. On-demand evaluation (EvaluationClient)
     Invoke the agent for a 5-turn session, wait for span ingestion, then score
     that one session with built-in and custom evaluators.

  2. Dataset evaluation (OnDemandEvaluationDatasetRunner)
     Run a ground-truth dataset of scenarios through the agent: the runner invokes
     every turn, waits for spans, and evaluates each scenario against its
     expected responses and assertions.

  3. Batch evaluation (StartBatchEvaluation)
     Score all recorded sessions in the runtime log group with an asynchronous,
     service-side job.

  4. Online evaluation (CreateOnlineEvaluationConfig)
     Continuously evaluate a sample of live production traffic.

Custom evaluators created:
  HRPolicyAccuracy (TRACE)   - judges whether answers match the expected HR facts
  HRResponseQuality (TRACE)  - judges helpfulness / specificity per turn

Built-in evaluators used:
  Builtin.GoalSuccessRate  (SESSION)
  Builtin.Correctness      (TRACE)
  Builtin.Helpfulness      (TRACE)

Usage:
    python evaluate.py [--region REGION] [--config PATH]
                       [--modes on-demand dataset batch online]
                       [--session-id SESSION_ID]

Prerequisites:
    python deploy.py    # deploys the TypeScript agent first
    pip install -r requirements.txt

Outputs:
    results/on_demand_results.json
    results/dataset_runner_results.json
    results/batch_eval_results.json
    results/online_eval_config.json
"""

# pylint: disable=duplicate-code
import argparse
import json
import sys
import time
import uuid
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import boto3
from boto3.session import Session
from bedrock_agentcore.evaluation import (
    AgentInvokerInput,
    AgentInvokerOutput,
    CloudWatchAgentSpanCollector,
    Dataset,
    EvaluationClient,
    EvaluationRunConfig,
    EvaluatorConfig,
    OnDemandEvaluationDatasetRunner,
    PredefinedScenario,
    ReferenceInputs,
    Turn,
)

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / "agent_config.json"
_RESULTS_DIR = _SCRIPT_DIR / "results"
_CLEANUP_STATE_PATH = _RESULTS_DIR / "cleanup_state.json"

ALL_MODES = ["on-demand", "dataset", "batch", "online"]

# Seconds to wait after invoking the agent before spans are queryable in CloudWatch.
CW_INGESTION_WAIT = 150

JUDGE_MODEL_ID = "us.amazon.nova-pro-v1:0"

# ============================================================
# Cleanup state — every resource created here is recorded so cleanup.py can remove it
# ============================================================


def _load_cleanup_state() -> dict:
    if not _CLEANUP_STATE_PATH.exists():
        return {}
    state = json.loads(_CLEANUP_STATE_PATH.read_text(encoding="utf-8"))
    return state if isinstance(state, dict) else {}


_cleanup_state = _load_cleanup_state()


def _remember(key: str, value: str) -> None:
    existing = _cleanup_state.get(key, [])
    values = list(existing) if isinstance(existing, list) else []
    if value not in values:
        values.append(value)
    _cleanup_state[key] = values
    _CLEANUP_STATE_PATH.write_text(json.dumps(_cleanup_state, indent=2), encoding="utf-8")


def _save_result(filename: str, payload: dict) -> Path:
    path = _RESULTS_DIR / filename
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"  Results saved: {path}")
    return path


# ============================================================
# Agent invocation
# ============================================================


def _parse_sse(raw: str) -> str:
    """Join the ``data:`` chunks of an SSE body; fall back to the raw body."""
    parts = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            chunk = line[len("data: ") :]
            try:
                chunk = json.loads(chunk)
            except json.JSONDecodeError:
                pass
            parts.append(str(chunk))
    return "".join(parts) if parts else raw


def invoke_agent(agentcore_client, agent_arn: str, prompt: str, session_id: str) -> str:
    """Send one turn to the TypeScript agent on AgentCore Runtime."""
    resp = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
    )
    return _parse_sse(resp["response"].read().decode("utf-8"))


def _print_scores(label: str, results: list) -> None:
    for res in results:
        if res.get("errorCode") or res.get("error"):
            print(f"    {label:<40} ERR: {res.get('errorMessage') or res.get('error')}"[:140])
        else:
            print(f"    {label:<40} {str(res.get('value')):<6} {res.get('label', '')}")


# ============================================================
# Custom LLM-as-a-judge evaluators
# ============================================================


def _numerical_judge(instructions: str, scale: list) -> dict:
    return {
        "llmAsAJudge": {
            "instructions": instructions,
            "ratingScale": {
                "numerical": [{"value": v, "label": lbl, "definition": d} for v, lbl, d in scale],
            },
            "modelConfig": {
                "bedrockEvaluatorModelConfig": {
                    "modelId": JUDGE_MODEL_ID,
                    "inferenceConfig": {"maxTokens": 512},
                }
            },
        }
    }


def create_custom_evaluators(control_client, suffix: str) -> dict:
    """Create the two HR-specific judges and return {display_name: evaluator_id}."""
    policy_accuracy = _numerical_judge(
        "You are evaluating an HR assistant for Acme Corp.\n\n"
        "Agent response: {assistant_turn}\n"
        "Expected answer: {expected_response}\n\n"
        "Judge whether the HR assistant's response is factually consistent with the "
        "expected answer about company policies, benefits, or pay information.\n"
        "Key facts (dollar amounts, day counts, percentages) must match the "
        "expected answer. Minor phrasing differences are acceptable.\n"
        "If no expected answer is provided, judge based on general HR accuracy.",
        [
            (0.0, "inaccurate", "Response contradicts or omits key facts from the expected answer."),
            (0.5, "partial", "Mostly correct but missing important details."),
            (1.0, "accurate", "Factually consistent with the expected answer."),
        ],
    )
    response_quality = _numerical_judge(
        "You are evaluating an HR assistant for Acme Corp.\n\n"
        "Agent response: {assistant_turn}\n"
        "Context: {context}\n\n"
        "Rate the quality of the HR assistant's response on these criteria:\n"
        "1. Did it provide specific, actionable HR information (not vague)?\n"
        "2. Was the tone professional and helpful?\n"
        "3. Did it cite specific numbers, percentages, or dates where relevant?\n"
        "4. Is it consistent with standard HR assistant behavior (uses tools, does not fabricate data)?\n\n"
        "If the response is a vague deflection or appears to make up data, rate it low.",
        [
            (0.0, "poor", "Response fails to address the question or is vague/unhelpful."),
            (0.5, "adequate", "Response partially addresses the question with some useful information."),
            (1.0, "excellent", "Response fully addresses the question with accurate, specific information."),
        ],
    )

    evaluator_ids = {}
    for name, config in (("HRPolicyAccuracy", policy_accuracy), ("HRResponseQuality", response_quality)):
        resp = control_client.create_evaluator(
            evaluatorName=f"{name}_{suffix}",
            level="TRACE",
            evaluatorConfig=config,
        )
        evaluator_ids[name] = resp["evaluatorId"]
        _remember("custom_evaluator_ids", resp["evaluatorId"])
        print(f"  {name:<18} (TRACE): {resp['evaluatorId']}")
    return evaluator_ids


# ============================================================
# 1. On-demand evaluation (EvaluationClient)
# ============================================================

ON_DEMAND_TURNS = [
    "What is the PTO balance for employee EMP-001?",
    "What is the company's remote work policy?",
    "Can you tell me about the health insurance benefit?",
    "What are the 401k contribution limits and company match?",
    "Please submit a PTO request for EMP-001 from 2026-09-01 to 2026-09-05.",
]

ON_DEMAND_ASSERTIONS = [
    "Agent reported EMP-001 has 10 remaining PTO days",
    "Agent described remote work up to 3 days per week with manager approval",
    "Agent described health insurance with 90% employee coverage",
    "Agent mentioned 401k company match of 100% up to 4% and the 2026 limit",
    "Agent confirmed PTO request was submitted and approved for EMP-001",
]

# A plain-string expected_response applies to the last trace of the session only.
# HRPolicyAccuracy needs an expected answer for every turn, so it runs in the
# dataset step (each Turn carries its own expected_response) instead of here.
ON_DEMAND_EXPECTED_LAST = "PTO request submitted and approved for EMP-001 from 2026-09-01 to 2026-09-05."


def run_on_demand(ctx: dict, session_id: str | None) -> dict:
    """Invoke a 5-turn session (or reuse one) and score it with EvaluationClient."""
    print("\n[On-demand] EvaluationClient — score a single session")

    if session_id:
        print(f"  Re-using existing session {session_id} (no invocation)")
    else:
        session_id = f"hr-ts-eval-{uuid.uuid4()}"
        _track_session(ctx, session_id)
        print(f"  Invoking agent for session {session_id}")
        for i, prompt in enumerate(ON_DEMAND_TURNS, 1):
            reply = invoke_agent(ctx["agentcore"], ctx["agent_arn"], prompt, session_id)
            print(f"    Turn {i}: {prompt[:70]}")
            print(f"         -> {reply[:100]}")
        print(f"  Waiting {CW_INGESTION_WAIT}s for CloudWatch span ingestion ...")
        time.sleep(CW_INGESTION_WAIT)

    evaluator_ids = ctx["builtin_ids"] + [ctx["custom_ids"]["HRResponseQuality"]]
    reference_inputs = ReferenceInputs(
        assertions=ON_DEMAND_ASSERTIONS,
        expected_response=ON_DEMAND_EXPECTED_LAST,
    )

    eval_client = EvaluationClient(region_name=ctx["region"])
    results = []
    # One call per evaluator so a single failing evaluator does not hide the others.
    for evaluator_id in evaluator_ids:
        try:
            res = eval_client.run(
                evaluator_ids=[evaluator_id],
                agent_id=ctx["agent_id"],
                session_id=session_id,
                look_back_time=timedelta(hours=1),
                reference_inputs=reference_inputs,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            res = [{"evaluatorId": evaluator_id, "error": str(exc)[:200]}]
        results.extend(res)
        _print_scores(ctx["display"].get(evaluator_id, evaluator_id), res)

    _save_result(
        "on_demand_results.json",
        {
            "session_id": session_id,
            "agent_id": ctx["agent_id"],
            "framework": "langgraph-typescript",
            "evaluators": evaluator_ids,
            "custom_evaluator_ids": ctx["custom_ids"],
            "results": results,
        },
    )
    return {"session_id": session_id, "evaluations": len(results)}


# ============================================================
# 2. Dataset evaluation (OnDemandEvaluationDatasetRunner)
# ============================================================
#
# Each PredefinedScenario becomes one runtime session. Turns carry their own
# expected_response, so TRACE-level evaluators (Correctness, HRPolicyAccuracy)
# score every turn against its own ground truth; assertions feed GoalSuccessRate.
#
# expected_trajectory is intentionally omitted: with LangGraph/OpenInference spans
# the service rejects trajectory reference inputs with a ValidationException.

HR_DATASET = Dataset(
    scenarios=[
        PredefinedScenario(
            scenario_id="pto-balance",
            turns=[
                Turn(
                    input="How many PTO days does employee EMP-002 have left?",
                    expected_response="EMP-002 has 3 remaining PTO days out of 15 total (12 used).",
                )
            ],
            assertions=["Agent reported that EMP-002 has 3 remaining PTO days"],
        ),
        PredefinedScenario(
            scenario_id="parental-leave-policy",
            turns=[
                Turn(
                    input="What is the parental leave policy?",
                    expected_response=(
                        "Primary caregivers receive 16 weeks of fully paid leave and secondary "
                        "caregivers receive 6 weeks. Leave may begin up to 2 weeks before the "
                        "expected birth or adoption date."
                    ),
                )
            ],
            assertions=[
                "Agent stated primary caregivers get 16 weeks of paid leave",
                "Agent stated secondary caregivers get 6 weeks of paid leave",
            ],
        ),
        PredefinedScenario(
            scenario_id="pay-stub",
            turns=[
                Turn(
                    input="Show me the January 2026 pay stub for EMP-042.",
                    expected_response=(
                        "For January 2026, EMP-042 had gross pay of $10,416.67 and net pay of $6,607.30."
                    ),
                )
            ],
            assertions=["Agent reported gross pay of $10,416.67 and net pay of $6,607.30"],
        ),
        PredefinedScenario(
            scenario_id="benefits-multi-turn",
            turns=[
                Turn(
                    input="What does the dental insurance cover?",
                    expected_response=(
                        "Dental covers 100% of preventive care, 80% of basic restorative care and "
                        "50% of major restorative care, with a $2,000 annual maximum per person."
                    ),
                ),
                Turn(
                    input="What vision benefits do employees get?",
                    expected_response=(
                        "The annual eye exam is covered in full, with a $200 yearly allowance for "
                        "frames or contacts and 15% off laser vision correction."
                    ),
                ),
            ],
            assertions=[
                "Agent described the dental coverage percentages and $2,000 annual maximum",
                "Agent described the $200 frames or contacts allowance",
            ],
        ),
    ]
)


def run_dataset(ctx: dict) -> dict:
    """Run HR_DATASET through the agent and score every scenario."""
    print("\n[Dataset] OnDemandEvaluationDatasetRunner — score a ground-truth dataset")
    print(f"  Dataset: {len(HR_DATASET.scenarios)} scenarios")

    def agent_invoker(invoker_input: AgentInvokerInput) -> AgentInvokerOutput:
        # Called once per turn; session_id is stable across a scenario's turns.
        _track_session(ctx, invoker_input.session_id)
        payload = invoker_input.payload
        prompt = payload if isinstance(payload, str) else payload.get("prompt", "")
        reply = invoke_agent(ctx["agentcore"], ctx["agent_arn"], prompt, invoker_input.session_id)
        return AgentInvokerOutput(agent_output=reply)

    evaluator_ids = [
        "Builtin.GoalSuccessRate",
        "Builtin.Correctness",
        ctx["custom_ids"]["HRPolicyAccuracy"],
    ]
    config = EvaluationRunConfig(
        evaluator_config=EvaluatorConfig(evaluator_ids=evaluator_ids),
        evaluation_delay_seconds=CW_INGESTION_WAIT,
        max_concurrent_scenarios=4,
    )
    span_collector = CloudWatchAgentSpanCollector(
        log_group_name=ctx["cw_log_group"],
        region=ctx["region"],
        max_wait_seconds=180,
        poll_interval_seconds=15,
    )

    print(f"  Invoking agent, then waiting {CW_INGESTION_WAIT}s before evaluating ...")
    eval_result = OnDemandEvaluationDatasetRunner(region=ctx["region"]).run(
        config=config,
        dataset=HR_DATASET,
        agent_invoker=agent_invoker,
        span_collector=span_collector,
    )

    scores = defaultdict(list)
    completed = 0
    for scenario in eval_result.scenario_results:
        if scenario.status != "COMPLETED":
            print(f"  Scenario '{scenario.scenario_id}': {scenario.status} — {scenario.error}")
            continue
        completed += 1
        print(f"  Scenario: {scenario.scenario_id}")
        for evaluator_result in scenario.evaluator_results:
            eid = evaluator_result.evaluator_id
            _print_scores(ctx["display"].get(eid, eid), evaluator_result.results)
            for res in evaluator_result.results:
                if res.get("value") is not None and not res.get("errorCode"):
                    scores[eid].append(float(res["value"]))

    print(f"\n  Completed {completed}/{len(eval_result.scenario_results)} scenarios. Average scores:")
    averages = {}
    for eid, values in scores.items():
        averages[eid] = round(sum(values) / len(values), 3)
        print(f"    {ctx['display'].get(eid, eid):<40} {averages[eid]:.2f}  (n={len(values)})")

    _save_result("dataset_runner_results.json", eval_result.model_dump())
    return {"scenarios_completed": completed, "averages": averages}


# ============================================================
# 3. Batch evaluation (StartBatchEvaluation)
# ============================================================


def _track_session(ctx: dict, session_id: str) -> None:
    """Record a session created by this run (for batch scoping and cleanup)."""
    if session_id not in ctx["run_session_ids"]:
        ctx["run_session_ids"].append(session_id)
    _remember("session_ids", session_id)


def _cloudwatch_source(ctx: dict, session_ids: list | None = None) -> dict:
    source = {
        "logGroupNames": [ctx["cw_log_group"]],
        "serviceNames": [ctx["otel_service_name"]],
    }
    if session_ids:
        source["filterConfig"] = {"sessionIds": session_ids[:500]}
    return {"cloudWatchLogs": source}


def _wait_for_batch(ctx: dict, batch_id: str) -> dict:
    """Poll a batch evaluation for up to 10 minutes and print its per-evaluator summary."""
    status_resp = {}
    status = "SUBMITTED"
    for elapsed in range(20, 620, 20):  # poll for up to 10 minutes
        time.sleep(20)
        status_resp = ctx["agentcore"].get_batch_evaluation(batchEvaluationId=batch_id)
        status = status_resp.get("status", "UNKNOWN")
        print(f"  [{elapsed:>4}s] status: {status}")
        if status in ("COMPLETED", "FAILED", "STOPPED"):
            break

    status_resp.pop("ResponseMetadata", None)
    summary = status_resp.get("evaluationResults", {})
    print(
        f"  Sessions completed: {summary.get('numberOfSessionsCompleted', 0)}/{summary.get('totalNumberOfSessions', 0)}"
    )
    for evaluator in summary.get("evaluatorSummaries", []):
        eid = evaluator["evaluatorId"]
        avg = evaluator.get("statistics", {}).get("averageScore")
        print(f"    {ctx['display'].get(eid, eid):<40} avg={avg}  (n={evaluator.get('totalEvaluated')})")
    return status_resp


def run_batch(ctx: dict, suffix: str) -> dict:
    """Start a service-side batch evaluation over the runtime log group and poll it."""
    print("\n[Batch] StartBatchEvaluation — score recorded sessions service-side")

    # Add one more session so the batch stage also works when run on its own.
    session_id = f"hr-ts-batch-{uuid.uuid4()}"
    _track_session(ctx, session_id)
    print(f"  Invoking agent for batch session {session_id}")
    for prompt in (
        "What is the PTO balance for employee EMP-042?",
        "What are the dental insurance benefits?",
        "What is EMP-042's pay stub for January 2026?",
    ):
        reply = invoke_agent(ctx["agentcore"], ctx["agent_arn"], prompt, session_id)
        print(f"    > {prompt[:70]}\n      -> {reply[:80]}")
    print(f"  Waiting {CW_INGESTION_WAIT}s for CloudWatch span ingestion ...")
    time.sleep(CW_INGESTION_WAIT)

    # The job discovers sessions by service name (stamped on every span by agent.ts);
    # filterConfig scopes it to the sessions this run created. Drop the filter to
    # score every session in the log group. Batch jobs carry no ground truth,
    # so only reference-free evaluators are used.
    batch_name = f"hr_ts_batch_{suffix}"
    evaluator_ids = [
        "Builtin.GoalSuccessRate",
        "Builtin.Helpfulness",
        ctx["custom_ids"]["HRResponseQuality"],
    ]
    print(f"  Scoring {len(ctx['run_session_ids'])} session(s) created by this run")
    try:
        resp = ctx["agentcore"].start_batch_evaluation(
            batchEvaluationName=batch_name,
            evaluators=[{"evaluatorId": eid} for eid in evaluator_ids],
            dataSourceConfig=_cloudwatch_source(ctx, ctx["run_session_ids"]),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  Warning: StartBatchEvaluation failed — {exc}")
        _save_result("batch_eval_results.json", {"error": str(exc)})
        return {"batch_evaluation_id": ""}

    batch_id = resp["batchEvaluationId"]
    _remember("batch_evaluation_ids", batch_id)
    print(f"  BatchEvaluationId: {batch_id}")

    status_resp = _wait_for_batch(ctx, batch_id)
    status = status_resp.get("status", "UNKNOWN")
    summary = status_resp.get("evaluationResults", {})
    _save_result(
        "batch_eval_results.json",
        {
            "batch_evaluation_id": batch_id,
            "batch_evaluation_name": batch_name,
            "session_ids": ctx["run_session_ids"],
            "final_status": status,
            "evaluators": evaluator_ids,
            "response": status_resp,
        },
    )
    return {
        "batch_evaluation_id": batch_id,
        "status": status,
        "sessions_completed": summary.get("numberOfSessionsCompleted", 0),
    }


# ============================================================
# 4. Online evaluation (CreateOnlineEvaluationConfig)
# ============================================================


def _ensure_online_eval_role(iam_client, role_name: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:FilterLogEvents",
                    "logs:GetLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                    "logs:StartQuery",
                    "logs:GetQueryResults",
                    "logs:StopQuery",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
            }
        ],
    }
    try:
        role_arn = iam_client.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"  Using existing IAM role: {role_arn}")
    except iam_client.exceptions.NoSuchEntityException:
        role_arn = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Execution role for AgentCore online evaluation of the TypeScript HR Assistant",
        )["Role"]["Arn"]
        print(f"  Created IAM role: {role_arn}")
    _remember("evaluation_role_names", role_name)
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="AgentCoreOnlineEvalPolicy",
        PolicyDocument=json.dumps(policy),
    )
    print("  Waiting 10s for IAM propagation ...")
    time.sleep(10)
    return role_arn


def run_online(ctx: dict, suffix: str) -> dict:
    """Create an online evaluation config that scores live traffic."""
    print("\n[Online] CreateOnlineEvaluationConfig — monitor live traffic")
    role_arn = _ensure_online_eval_role(ctx["iam"], f"AgentCoreOnlineEvalTS_{suffix}")

    config_name = f"hr_ts_online_{suffix}"
    try:
        resp = ctx["control"].create_online_evaluation_config(
            onlineEvaluationConfigName=config_name,
            rule={"samplingConfig": {"samplingPercentage": 100.0}},
            dataSourceConfig=_cloudwatch_source(ctx),
            evaluators=[
                {"evaluatorId": "Builtin.GoalSuccessRate"},
                {"evaluatorId": "Builtin.Helpfulness"},
                {"evaluatorId": ctx["custom_ids"]["HRResponseQuality"]},
            ],
            evaluationExecutionRoleArn=role_arn,
            enableOnCreate=True,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  Warning: online eval config failed — {exc}")
        _save_result("online_eval_config.json", {"error": str(exc)})
        return {"config_id": ""}

    config_id = resp["onlineEvaluationConfigId"]
    _remember("online_evaluation_config_ids", config_id)
    print(f"  Online eval config created: {config_id}")
    print("  Every new session is now scored automatically. Results are written to:")
    print(f"    /aws/bedrock-agentcore/evaluations/results/{config_id}")
    _save_result(
        "online_eval_config.json",
        {
            "config_name": config_name,
            "config_id": config_id,
            "config_arn": resp.get("onlineEvaluationConfigArn", ""),
            "evaluation_role_arn": role_arn,
            "results_log_group": f"/aws/bedrock-agentcore/evaluations/results/{config_id}",
        },
    )
    return {"config_id": config_id}


# ============================================================
# Main
# ============================================================


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Evaluate the TypeScript HR Assistant with AgentCore Evaluations")
    parser.add_argument("--region", default=None, help="AWS region (default: from agent_config.json)")
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG),
        help="Path to agent_config.json written by deploy.py",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=ALL_MODES,
        default=ALL_MODES,
        help="Evaluation modes to run (default: all four, in lifecycle order)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="On-demand only: evaluate an existing session instead of invoking the agent",
    )
    return parser.parse_args()


def main() -> None:
    """Create evaluators and run the selected evaluation modes."""
    args = parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"ERROR: Agent config not found at {config_path}")
        print("Run deploy.py first:  python deploy.py")
        sys.exit(1)
    _RESULTS_DIR.mkdir(exist_ok=True)

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    region = args.region or cfg.get("region") or Session().region_name or "us-east-1"

    print("=" * 65)
    print("TypeScript HR Assistant — AgentCore Evaluations lifecycle")
    print("=" * 65)
    print(f"  Region        : {region}")
    print(f"  Agent ID      : {cfg['agent_id']}")
    print(f"  CW Log Group  : {cfg['cw_log_group']}")
    print(f"  Modes         : {', '.join(args.modes)}")

    control_client = boto3.client("bedrock-agentcore-control", region_name=region)
    suffix = uuid.uuid4().hex[:8]

    print("\nCreating custom LLM-as-a-judge evaluators ...")
    custom_ids = create_custom_evaluators(control_client, suffix)

    ctx = {
        "region": region,
        "agent_id": cfg["agent_id"],
        "agent_arn": cfg["agent_arn"],
        "cw_log_group": cfg["cw_log_group"],
        "otel_service_name": cfg.get("otel_service_name", ""),
        "agentcore": boto3.client("bedrock-agentcore", region_name=region),
        "control": control_client,
        "iam": boto3.client("iam"),
        "builtin_ids": ["Builtin.GoalSuccessRate", "Builtin.Correctness", "Builtin.Helpfulness"],
        "custom_ids": custom_ids,
        "display": {eid: f"{name} (custom)" for name, eid in custom_ids.items()},
        "run_session_ids": [],
    }

    summary = {}
    if "on-demand" in args.modes:
        summary["on-demand"] = run_on_demand(ctx, args.session_id)
    if "dataset" in args.modes:
        summary["dataset"] = run_dataset(ctx)
    if "batch" in args.modes:
        summary["batch"] = run_batch(ctx, suffix)
    if "online" in args.modes:
        summary["online"] = run_online(ctx, suffix)

    print("\n" + "=" * 65)
    print("Summary")
    print("=" * 65)
    print("  Framework       : LangGraph TypeScript")
    print("  Instrumentation : @arizeai/openinference-instrumentation-langchain")
    for mode, details in summary.items():
        print(f"  {mode:<16}: {json.dumps(details, default=str)}")
    print(f"\n  Output files in: {_RESULTS_DIR}")
    print("  Run cleanup:  python cleanup.py")


if __name__ == "__main__":
    main()
