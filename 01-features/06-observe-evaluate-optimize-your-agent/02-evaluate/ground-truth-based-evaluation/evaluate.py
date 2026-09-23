"""
Ground Truth Evaluation of the HR Assistant Agent.

Demonstrates four evaluation interfaces from the bedrock-agentcore SDK:

  1. EvaluationClient
       Evaluate specific existing CloudWatch sessions against ground-truth references.
       Use this when you already have sessions and want spot-check or CI evaluation.

  2. OnDemandEvaluationDatasetRunner
       Define a test dataset, invoke the agent once per scenario, wait for CloudWatch
       ingestion, then evaluate all results. Use this for regression testing and CI/CD.

  3. BatchEvaluationRunner
       Submit all sessions to the service in a single batch job and get aggregate scores
       per evaluator. Use this for baseline measurement and pre/post comparisons.

  4. DatasetClient (dataset management)
       Persist evaluation datasets to AgentCore's managed storage, publish immutable
       versions for reproducible CI/CD runs, and load ground truth directly from a
       versioned dataset into EvaluationClient or OnDemandEvaluationDatasetRunner.

Usage:
    python evaluate.py [--region REGION] [--config PATH]

Args:
    --region    AWS region (default: from boto3 session, fallback us-east-1)
    --config    Path to agent_config.json written by deploy.py
                (default: ../utils/agent_config.json)

Prerequisites:
    1. Deploy the HR Assistant agent:
           cd ../utils && python deploy.py [--region REGION]
    2. Install evaluation dependencies:
           pip install -r requirements.txt

Outputs:
    results/eval_client_results.json         - EvaluationClient scores
    results/dataset_runner_results.json      - OnDemandEvaluationDatasetRunner scores
    results/batch_runner_results.json        - BatchEvaluationRunner aggregate scores
    results/managed_dataset_eval_results.json - DatasetClient managed dataset scores
"""

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

# ============================================================
# 0. Parse args and load agent config
# ============================================================

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / ".." / "utils" / "agent_config.json"
_RESULTS_DIR = _SCRIPT_DIR / "results"
_RESULTS_DIR.mkdir(exist_ok=True)

parser = argparse.ArgumentParser(description="Evaluate the HR Assistant agent with ground truth")
parser.add_argument("--region", default=None, help="AWS region")
parser.add_argument(
    "--config",
    default=str(_DEFAULT_CONFIG),
    help="Path to agent_config.json (written by deploy.py)",
)
args = parser.parse_args()

_config_path = Path(args.config)
if not _config_path.exists():
    print(f"ERROR: Agent config not found at {_config_path}")
    print("Run deploy.py first:  cd ../utils && python deploy.py")
    sys.exit(1)

_cfg = json.loads(_config_path.read_text())
AGENT_ID = _cfg["agent_id"]
AGENT_ARN = _cfg["agent_arn"]
CW_LOG_GROUP = _cfg["cw_log_group"]
REGION = args.region or _cfg.get("region") or Session().region_name or "us-east-1"

print("=" * 60)
print("HR Assistant Agent — Ground Truth Evaluation")
print("=" * 60)
print(f"  Region       : {REGION}")
print(f"  Agent ID     : {AGENT_ID}")
print(f"  Agent ARN    : {AGENT_ARN}")
print(f"  CW Log Group : {CW_LOG_GROUP}")

agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)
_cp = boto3.client("bedrock-agentcore-control", region_name=REGION)

# ============================================================
# 1. Create custom (LLM-as-a-judge) evaluators
# ============================================================
#
# Custom evaluators let you define your own scoring criteria in natural language.
# They reference ground-truth placeholders that the service substitutes at eval time:
#
#   TRACE-level placeholders (one result per agent turn):
#     {assistant_turn}    - the agent's actual response for that turn
#     {expected_response} - the expectedResponse from ReferenceInputs
#     {context}           - conversation context preceding the turn
#
#   SESSION-level placeholders (one result per complete session):
#     {actual_tool_trajectory}   - tools the agent actually called
#     {expected_tool_trajectory} - expectedTrajectory from ReferenceInputs
#     {assertions}               - assertions list from ReferenceInputs
#     {available_tools}          - tools available to the agent

print("\n[1/6] Creating custom LLM-as-a-judge evaluators ...")

_SUFFIX = uuid.uuid4().hex[:8]

# Trace-level: compares agent response against expected_response
print("  Creating HRResponseSimilarity (TRACE) ...")
_resp_sim = _cp.create_evaluator(
    evaluatorName=f"HRResponseSimilarity_{_SUFFIX}",
    level="TRACE",
    evaluatorConfig={
        "llmAsAJudge": {
            "instructions": (
                "Compare the agent's response with the expected response.\n"
                "Agent response: {assistant_turn}\n"
                "Expected response: {expected_response}\n\n"
                "Rate how closely the agent's response matches the expected response. "
                "Focus on whether the key facts, numbers, and conclusions agree."
            ),
            "ratingScale": {
                "numerical": [
                    {
                        "value": 0.0,
                        "label": "not_similar",
                        "definition": "Response is factually different or missing key information.",
                    },
                    {
                        "value": 0.5,
                        "label": "partially_similar",
                        "definition": "Response captures some expected content but omits or misrepresents parts.",
                    },
                    {
                        "value": 1.0,
                        "label": "highly_similar",
                        "definition": "Response is semantically equivalent — all key facts match.",
                    },
                ]
            },
            "modelConfig": {
                "bedrockEvaluatorModelConfig": {
                    "modelId": "us.amazon.nova-lite-v1:0",
                    "inferenceConfig": {"maxTokens": 512},
                }
            },
        }
    },
)
CUSTOM_RESPONSE_SIMILARITY_ID = _resp_sim["evaluatorId"]
print(f"    evaluatorId: {CUSTOM_RESPONSE_SIMILARITY_ID}")

# Session-level: checks tool trajectory compliance and assertion satisfaction
print("  Creating HRAssertionChecker (SESSION) ...")
_assert_chk = _cp.create_evaluator(
    evaluatorName=f"HRAssertionChecker_{_SUFFIX}",
    level="SESSION",
    evaluatorConfig={
        "llmAsAJudge": {
            "instructions": (
                "Evaluate whether the agent fulfilled the session requirements.\n\n"
                "Expected tool trajectory: {expected_tool_trajectory}\n"
                "Actual tool trajectory: {actual_tool_trajectory}\n"
                "Assertions to verify: {assertions}\n\n"
                "Score the agent on how well it followed the expected tool trajectory "
                "and satisfied every listed assertion."
            ),
            "ratingScale": {
                "numerical": [
                    {
                        "value": 0.0,
                        "label": "failed",
                        "definition": "Agent did not follow the trajectory and failed most assertions.",
                    },
                    {
                        "value": 0.5,
                        "label": "partial",
                        "definition": "Agent partially followed the trajectory or satisfied only some assertions.",
                    },
                    {
                        "value": 1.0,
                        "label": "passed",
                        "definition": "Agent followed the expected trajectory and satisfied all assertions.",
                    },
                ]
            },
            "modelConfig": {
                "bedrockEvaluatorModelConfig": {
                    "modelId": "us.amazon.nova-lite-v1:0",
                    "inferenceConfig": {"maxTokens": 512},
                }
            },
        }
    },
)
CUSTOM_ASSERTION_CHECKER_ID = _assert_chk["evaluatorId"]
print(f"    evaluatorId: {CUSTOM_ASSERTION_CHECKER_ID}")
print("  Custom evaluators ready.")

# ============================================================
# 2. Invoke the agent to generate sessions for EvaluationClient
# ============================================================
#
# EvaluationClient evaluates sessions that already exist in CloudWatch.
# We create those sessions here by invoking the agent directly.

print("\n[2/6] Invoking agent to create sessions for EvaluationClient ...")


def invoke_agent(prompt: str, session_id: str) -> str:
    """Send a single prompt to the HR assistant and return its text response."""
    resp = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
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
    return "".join(parts) if parts else raw


def run_session(turns: list, session_prefix: str) -> str:
    """Invoke a multi-turn session and return its session ID."""
    session_id = f"{session_prefix}-{uuid.uuid4()}"
    print(f"  Session: {session_id}")
    for turn_input in turns:
        print(f"    > {turn_input[:70]}")
        response = invoke_agent(turn_input, session_id)
        print(f"    < {response[:100]}")
    return session_id


# Single-turn sessions
session_pto_balance = run_session(
    ["What is the current PTO balance for employee EMP-001?"],
    "pto-balance-check",
)
session_submit_pto = run_session(
    ["Please submit a PTO request for employee EMP-001 from 2026-04-14 to 2026-04-16 for a family vacation."],
    "submit-pto-request",
)
session_pay_stub = run_session(
    ["Can you pull up the January 2026 pay stub for employee EMP-001?"],
    "pay-stub-lookup",
)

# Multi-turn session: PTO planning (3 turns)
session_pto_planning = run_session(
    [
        "How many PTO days do I have left? My employee ID is EMP-001.",
        "Great. I'd like to take December 23 to December 25 off. Please submit a request.",
        "Remind me, what is the policy on rolling over unused PTO?",
    ],
    "pto-planning-session",
)

# Multi-turn session: new employee onboarding (4 turns)
session_onboarding = run_session(
    [
        "I just joined the company. What is the remote work policy?",
        "How much PTO do I get as a new employee?",
        "What life insurance benefit does the company provide?",
        "Can you check the current PTO balance for employee EMP-042?",
    ],
    "new-employee-onboarding",
)

print("\n  Waiting 60s for CloudWatch log ingestion ...")
time.sleep(60)
print("  Sessions ready.")

# ============================================================
# 3. EvaluationClient — evaluate specific existing sessions
# ============================================================
#
# EvaluationClient is the right tool when you already have sessions in CloudWatch
# and want to evaluate them against ground truth ad-hoc or in CI.
#
# ReferenceInputs carries the ground truth:
#   expected_response   → used by Builtin.Correctness and custom TRACE evaluators
#   expected_trajectory → used by Builtin.TrajectoryExactOrderMatch / InOrderMatch / AnyOrderMatch
#   assertions          → used by Builtin.GoalSuccessRate and custom SESSION evaluators

print("\n[3/6] EvaluationClient — evaluating existing sessions ...")

from bedrock_agentcore.evaluation import EvaluationClient, ReferenceInputs  # noqa: E402

eval_client = EvaluationClient(region_name=REGION)
all_ec_results = {}


def print_eval_results(label: str, results: list) -> None:
    """Print evaluation results as a simple table."""
    print(f"\n  --- {label} ---")
    print(f"  {'Evaluator':<45} {'Value':>6}  {'Label':<20} Explanation")
    print(f"  {'-' * 45} {'-' * 6}  {'-' * 20} {'-' * 40}")
    for r in results:
        evaluator = r.get("evaluatorId", "")[-40:]
        value = str(r.get("value", r.get("score", "N/A")))
        lbl = str(r.get("label", r.get("rating", "")))[:20]
        explanation = (r.get("explanation", "") or "")[:60].replace("\n", " ")
        error_code = r.get("errorCode")
        if error_code:
            lbl = f"ERR:{error_code}"[:20]
            explanation = (r.get("errorMessage", "") or "")[:60]
        print(f"  {evaluator:<45} {value:>6}  {lbl:<20} {explanation}")


# 3a. PTO Balance: Correctness + Helpfulness + ResponseRelevance + custom ResponseSimilarity
print("\n  3a. PTO Balance session ...")
pto_balance_results = eval_client.run(
    evaluator_ids=[
        "Builtin.Correctness",
        "Builtin.Helpfulness",
        "Builtin.ResponseRelevance",
        CUSTOM_RESPONSE_SIMILARITY_ID,
    ],
    session_id=session_pto_balance,
    agent_id=AGENT_ID,
    look_back_time=timedelta(hours=2),
    reference_inputs=ReferenceInputs(
        expected_response="Employee EMP-001 has 10 remaining PTO days out of 15 total (5 days used).",
    ),
)
print_eval_results(
    "PTO Balance — Correctness + Quality + Custom ResponseSimilarity",
    pto_balance_results,
)
all_ec_results["pto_balance"] = pto_balance_results

# 3b. PTO Submission: assertions + trajectory + custom evaluators
print("\n  3b. PTO Submission session ...")
submit_pto_results = eval_client.run(
    evaluator_ids=[
        "Builtin.GoalSuccessRate",
        "Builtin.TrajectoryExactOrderMatch",
        "Builtin.TrajectoryAnyOrderMatch",
        "Builtin.Correctness",
        CUSTOM_RESPONSE_SIMILARITY_ID,
    ],
    session_id=session_submit_pto,
    agent_id=AGENT_ID,
    look_back_time=timedelta(hours=2),
    reference_inputs=ReferenceInputs(
        expected_trajectory=["submit_pto_request"],
        assertions=[
            "Agent called submit_pto_request for employee EMP-001",
            "Agent confirmed the PTO request was approved",
            "Agent provided a request ID (e.g. PTO-2026-001)",
        ],
        expected_response="PTO request submitted and approved for EMP-001 from 2026-04-14 to 2026-04-16.",
    ),
)
print_eval_results("PTO Submission — Built-in + Custom ResponseSimilarity", submit_pto_results)
all_ec_results["submit_pto"] = submit_pto_results

# 3c. Pay Stub: Correctness + GoalSuccessRate
print("\n  3c. Pay Stub session ...")
pay_stub_results = eval_client.run(
    evaluator_ids=[
        "Builtin.Correctness",
        "Builtin.GoalSuccessRate",
    ],
    session_id=session_pay_stub,
    agent_id=AGENT_ID,
    look_back_time=timedelta(hours=2),
    reference_inputs=ReferenceInputs(
        expected_response="EMP-001 January 2026: gross pay $8,333.33, net pay $5,362.50.",
        assertions=[
            "Agent called get_pay_stub for EMP-001 period 2026-01",
            "Agent reported the correct gross pay of $8,333.33",
            "Agent reported the correct net pay of $5,362.50",
        ],
    ),
)
print_eval_results("Pay Stub — Correctness + GoalSuccessRate", pay_stub_results)
all_ec_results["pay_stub"] = pay_stub_results

# 3d. Multi-turn PTO Planning (3 turns) + custom AssertionChecker
print("\n  3d. PTO Planning multi-turn session ...")
pto_planning_results = eval_client.run(
    evaluator_ids=[
        "Builtin.GoalSuccessRate",
        "Builtin.TrajectoryExactOrderMatch",
        "Builtin.TrajectoryInOrderMatch",
        "Builtin.TrajectoryAnyOrderMatch",
        "Builtin.Helpfulness",
        CUSTOM_ASSERTION_CHECKER_ID,
    ],
    session_id=session_pto_planning,
    agent_id=AGENT_ID,
    look_back_time=timedelta(hours=2),
    reference_inputs=ReferenceInputs(
        expected_trajectory=[
            "get_pto_balance",
            "submit_pto_request",
            "lookup_hr_policy",
        ],
        assertions=[
            "Agent correctly reported 10 remaining PTO days for EMP-001 in turn 1",
            "Agent submitted a PTO request for December 23-25, 2026 in turn 2",
            "Agent correctly stated the 5-day PTO rollover limit in turn 3",
        ],
    ),
)
print_eval_results(
    "PTO Planning — Multi-Turn (3 turns) + Custom AssertionChecker",
    pto_planning_results,
)
all_ec_results["pto_planning"] = pto_planning_results

# Save EvaluationClient results
_ec_path = _RESULTS_DIR / "eval_client_results.json"
_ec_path.write_text(json.dumps(all_ec_results, indent=2, default=str))
print(f"\n  EvaluationClient results saved to: {_ec_path}")

# ============================================================
# 4. OnDemandEvaluationDatasetRunner — automated dataset evaluation
# ============================================================
#
# OnDemandEvaluationDatasetRunner is the right tool when you have a test dataset
# and want to:
#   1. Automatically invoke your agent for each scenario
#   2. Wait for CloudWatch spans to appear
#   3. Run evaluators against each scenario's results
#
# The runner manages session IDs, invocation, waiting, and evaluation for you.

print("\n[4/6] OnDemandEvaluationDatasetRunner — automated dataset evaluation ...")

from bedrock_agentcore.evaluation import (  # noqa: E402
    AgentInvokerInput,
    AgentInvokerOutput,
    CloudWatchAgentSpanCollector,
    Dataset,
    EvaluationRunConfig,
    EvaluatorConfig,
    OnDemandEvaluationDatasetRunner,
    PredefinedScenario,
    Turn,
)


def agent_invoker(invoker_input: AgentInvokerInput) -> AgentInvokerOutput:
    """
    Adapter called by OnDemandEvaluationDatasetRunner once per turn.
    AgentInvokerInput provides:
      - payload:    The turn input (str) from the dataset
      - session_id: Stable session ID for this scenario (for multi-turn continuity)
    """
    payload = invoker_input.payload
    body = {"prompt": payload} if isinstance(payload, str) else payload

    resp = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
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


# Define the evaluation dataset
# Each PredefinedScenario has:
#   - turns:               what the user asks (and the expected response for Correctness)
#   - expected_trajectory: which tools the agent should call, in order
#   - assertions:          session-level checks for GoalSuccessRate
dataset = Dataset(
    scenarios=[
        PredefinedScenario(
            scenario_id="pto-balance-check",
            turns=[
                Turn(
                    input="What is the current PTO balance for employee EMP-001?",
                    expected_response="Employee EMP-001 has 10 remaining PTO days out of 15 total (5 days used).",
                )
            ],
            expected_trajectory=["get_pto_balance"],
            assertions=[
                "Agent called get_pto_balance with employee_id=EMP-001",
                "Agent reported 10 remaining PTO days",
            ],
        ),
        PredefinedScenario(
            scenario_id="pto-policy-lookup",
            turns=[
                Turn(
                    input="What is the company PTO policy?",
                    expected_response=(
                        "Full-time employees accrue 15 days of PTO per year. "
                        "Requests must be submitted at least 2 business days in advance. "
                        "Up to 5 unused days roll over each year."
                    ),
                )
            ],
            expected_trajectory=["lookup_hr_policy"],
            assertions=[
                "Agent called lookup_hr_policy with topic=pto",
                "Agent mentioned the 15-day annual accrual for full-time employees",
                "Agent mentioned the 2 business day advance notice requirement",
            ],
        ),
        PredefinedScenario(
            scenario_id="401k-info",
            turns=[
                Turn(
                    input="How does the 401k match work?",
                    expected_response=(
                        "The company matches 100% of contributions up to 4% of salary, "
                        "plus 50% on the next 2%, for a total effective match of up to 5%. "
                        "The match vests over 3 years."
                    ),
                )
            ],
            expected_trajectory=["get_benefits_summary"],
            assertions=[
                "Agent called get_benefits_summary with benefit_type=401k",
                "Agent correctly described the 4% full match and 50% match on next 2%",
                "Agent mentioned the 3-year vesting schedule",
            ],
        ),
        PredefinedScenario(
            scenario_id="check-and-submit-pto",
            turns=[
                Turn(
                    input=(
                        "Check the PTO balance for EMP-002, and if they have at least 2 days, "
                        "submit a request for 2026-05-26 to 2026-05-27."
                    ),
                    expected_response=(
                        "EMP-002 has 3 remaining PTO days. "
                        "PTO request submitted and approved for 2026-05-26 to 2026-05-27."
                    ),
                )
            ],
            expected_trajectory=["get_pto_balance", "submit_pto_request"],
            assertions=[
                "Agent first called get_pto_balance for EMP-002",
                "Agent confirmed 3 remaining days is sufficient",
                "Agent then called submit_pto_request for the correct dates",
            ],
        ),
        PredefinedScenario(
            scenario_id="benefits-exploration",
            turns=[
                Turn(
                    input="Can you walk me through the health insurance options?",
                    expected_response=(
                        "The company covers 90% of premiums for employee-only coverage. "
                        "Three plans are available: Blue Shield PPO, Kaiser HMO, and HDHP with HSA."
                    ),
                ),
                Turn(
                    input="What about dental?",
                    expected_response=(
                        "The dental plan covers 100% of preventive care, 80% of basic restorative care, "
                        "and 50% of major work, with a $2,000 annual maximum."
                    ),
                ),
                Turn(
                    input="And how much does the company contribute to the 401k?",
                    expected_response=(
                        "The company matches 100% up to 4% of salary, plus 50% on the next 2%, "
                        "for a total effective match of up to 5%."
                    ),
                ),
            ],
            expected_trajectory=[
                "get_benefits_summary",
                "get_benefits_summary",
                "get_benefits_summary",
            ],
            assertions=[
                "Agent called get_benefits_summary three times across the conversation",
                "Agent correctly described health, dental, and 401k benefits in their respective turns",
                "Agent maintained conversational context across all three turns",
            ],
        ),
    ]
)

print(f"  Dataset: {len(dataset.scenarios)} scenarios")

# Span collector: polls CloudWatch for OTel spans emitted by the agent
span_collector = CloudWatchAgentSpanCollector(
    log_group_name=CW_LOG_GROUP,
    region=REGION,
    max_wait_seconds=180,
    poll_interval_seconds=15,
)

# Evaluator level cache — required for custom evaluators so the runner knows
# whether to apply them at TRACE (per-turn) or SESSION (per-conversation) level
EVALUATOR_LEVELS = {
    "Builtin.GoalSuccessRate": "SESSION",
    "Builtin.TrajectoryExactOrderMatch": "SESSION",
    "Builtin.TrajectoryInOrderMatch": "SESSION",
    "Builtin.TrajectoryAnyOrderMatch": "SESSION",
    "Builtin.Correctness": "TRACE",
    CUSTOM_RESPONSE_SIMILARITY_ID: "TRACE",
    CUSTOM_ASSERTION_CHECKER_ID: "SESSION",
}

config = EvaluationRunConfig(
    evaluator_config=EvaluatorConfig(
        evaluator_ids=[
            "Builtin.Correctness",
            "Builtin.GoalSuccessRate",
            "Builtin.TrajectoryExactOrderMatch",
            "Builtin.TrajectoryInOrderMatch",
            "Builtin.TrajectoryAnyOrderMatch",
            CUSTOM_RESPONSE_SIMILARITY_ID,
            CUSTOM_ASSERTION_CHECKER_ID,
        ]
    ),
    evaluation_delay_seconds=180,
    max_concurrent_scenarios=3,
)

runner = OnDemandEvaluationDatasetRunner(region=REGION)
runner._evaluator_level_cache.update(EVALUATOR_LEVELS)

print(f"  Evaluators: {len(config.evaluator_config.evaluator_ids)} (5 built-in + 2 custom)")
print("  Starting evaluation (invoking agent + waiting 180s for CloudWatch) ...")

eval_result = runner.run(
    config=config,
    dataset=dataset,
    agent_invoker=agent_invoker,
    span_collector=span_collector,
)

completed = sum(1 for sr in eval_result.scenario_results if sr.status == "COMPLETED")
failed = sum(1 for sr in eval_result.scenario_results if sr.status == "FAILED")
print(f"\n  Completed: {completed}/{len(eval_result.scenario_results)} scenarios  (failed: {failed})")

# Print per-scenario results
for sr in eval_result.scenario_results:
    if sr.status == "FAILED":
        print(f"\n  Scenario '{sr.scenario_id}': FAILED — {sr.error}")
        continue
    print(f"\n  Scenario: {sr.scenario_id}")
    for er in sr.evaluator_results:
        for res in er.results:
            value = res.get("value", res.get("score", "N/A"))
            lbl = res.get("label", res.get("rating", ""))
            error_code = res.get("errorCode")
            if error_code:
                print(f"    {er.evaluator_id[-40:]:<40}  ERR:{error_code}")
            else:
                print(f"    {er.evaluator_id[-40:]:<40}  {str(value):>5}  {str(lbl)}")

# Aggregate summary
scores_by_evaluator: dict = defaultdict(list)
for sr in eval_result.scenario_results:
    if sr.status != "COMPLETED":
        continue
    for er in sr.evaluator_results:
        for res in er.results:
            if "value" in res and res["value"] is not None and not res.get("errorCode"):
                scores_by_evaluator[er.evaluator_id].append(float(res["value"]))

print("\n  Summary (average score across all scenarios):")
print(f"  {'Evaluator':<45} {'avg':>5}  n")
print(f"  {'-' * 45} {'-' * 5}  -")
for eid, scores in sorted(scores_by_evaluator.items()):
    avg = sum(scores) / len(scores)
    print(f"  {eid[-45:]:<45} {avg:>5.2f}  {len(scores)}")

# Save results
_dr_path = _RESULTS_DIR / "dataset_runner_results.json"
_dr_path.write_text(json.dumps(eval_result.model_dump(), indent=2, default=str))
print(f"\n  DatasetRunner results saved to: {_dr_path}")

# ============================================================
# 4b. DatasetClient — persist, version, and reload the evaluation dataset
# ============================================================
#
# DatasetClient stores evaluation datasets in AgentCore's managed service.
# Benefits over an in-memory Dataset object:
#   - Version-pinned runs: publish version N and always evaluate against that exact
#     set of examples, regardless of future DRAFT changes.
#   - Shared ground truth: other teams and CI/CD pipelines load the same scenarios
#     by dataset ID + version, with no copy-paste of scenario definitions.
#   - Incremental curation: add, update, or remove examples over time; publish new
#     versions when the dataset is ready for use.
#   - Bulk download: GetDataset returns a presigned URL for dataset.jsonl (5 min TTL)
#     for use in external tools or offline analysis.
#
# Here we persist the same 5-scenario evaluation dataset defined above, publish it
# as version 1, then load version 1 back and run EvaluationClient with the ground
# truth loaded directly from the managed dataset.

print("\n[4b] DatasetClient — managed dataset with version-pinned evaluation ...")

from bedrock_agentcore.evaluation import DatasetClient  # noqa: E402

_dc = DatasetClient(region_name=REGION)
_ds_name = f"hr_gt_eval_{uuid.uuid4().hex[:8]}"

# Serialize the PredefinedScenario objects into PREDEFINED_V1 format so they can
# be stored in the managed dataset service as the canonical source of ground truth.
_inline_examples = []
for sc in dataset.scenarios:
    _example: dict = {
        "scenario_id": sc.scenario_id,
        "turns": [
            {
                "input": t.input,
                **({"expected_response": t.expected_response} if t.expected_response else {}),
            }
            for t in sc.turns
        ],
    }
    if sc.expected_trajectory:
        _example["expected_trajectory"] = {"toolNames": sc.expected_trajectory}
    if sc.assertions:
        _example["assertions"] = [{"text": a} for a in sc.assertions]
    _inline_examples.append(_example)

print(f"  Creating managed dataset '{_ds_name}' ({len(_inline_examples)} scenarios) ...")
_managed_ds = _dc.create_dataset_and_wait(
    datasetName=_ds_name,
    schemaType="AGENTCORE_EVALUATION_PREDEFINED_V1",
    source={"inlineExamples": {"examples": _inline_examples}},
)
_managed_ds_id = _managed_ds["datasetId"]
print(f"  datasetId    : {_managed_ds_id}")
print(f"  status       : {_managed_ds['status']}")
print(f"  exampleCount : {_managed_ds.get('exampleCount', '?')}")
print(f"  draftStatus  : {_managed_ds.get('draftStatus', '?')}  (MODIFIED = DRAFT has unpublished changes)")
if "downloadUrl" in _managed_ds:
    print("  downloadUrl  : <presigned URL for draft/dataset.jsonl — valid 5 min>")

# Publish version 1 — creates an immutable snapshot of the current DRAFT.
# The DRAFT is preserved unchanged; future mutations will change draftStatus back to MODIFIED.
# CI/CD pipelines pin to version 1 to guarantee reproducible evaluation results.
print("\n  Publishing version 1 (immutable CI/CD snapshot) ...")
_dc.create_dataset_version_and_wait(datasetId=_managed_ds_id)
_versions_resp = _dc.list_dataset_versions(datasetId=_managed_ds_id)
print(f"  Published versions : {len(_versions_resp.get('versions', []))}")
for _v in _versions_resp.get("versions", []):
    print(
        f"    version={_v.get('datasetVersion', '?')}  "
        f"examples={_v.get('exampleCount', '?')}  "
        f"createdAt={_v.get('createdAt', '?')}"
    )
_managed_ds_after = _dc.get_dataset(datasetId=_managed_ds_id)
print(f"  draftStatus after publish : {_managed_ds_after.get('draftStatus', '?')}  (UNMODIFIED = DRAFT matches v1)")

# Load examples back from version 1.
# Always load from a pinned version (not DRAFT) in CI/CD so evaluation is reproducible.
print("\n  Loading examples from version 1 ...")
_v1_resp = _dc.list_dataset_examples(datasetId=_managed_ds_id, datasetVersion="1")
_v1_examples = _v1_resp.get("examples", [])
print(f"  Loaded {len(_v1_examples)} examples  (datasetVersion={_v1_resp.get('datasetVersion', '?')})")

# Rebuild a Dataset object from the managed dataset's version 1 examples.
# This demonstrates the full round-trip: in-memory → managed service → in-memory.
# In production, the eval script would skip the "serialize" step and load directly
# from the dataset ID, enabling scenario definitions to be managed independently
# of the evaluation code.
_managed_scenarios = []
for _ex in _v1_examples:
    _turns = [
        Turn(
            input=_t["input"],
            expected_response=_t.get("expected_response"),
        )
        for _t in _ex.get("turns", [])
    ]
    _traj = (_ex.get("expected_trajectory") or {}).get("toolNames", [])
    _assertions = [_a["text"] for _a in _ex.get("assertions", []) if isinstance(_a, dict)]
    _managed_scenarios.append(
        PredefinedScenario(
            scenario_id=_ex["scenario_id"],
            turns=_turns,
            expected_trajectory=_traj or None,
            assertions=_assertions or None,
        )
    )
_dataset_from_managed = Dataset(scenarios=_managed_scenarios)
print(f"  Dataset rebuilt from managed v1 : {len(_dataset_from_managed.scenarios)} scenarios")

# Run EvaluationClient on one session using the ground truth loaded from the
# managed dataset. This confirms the round-trip is lossless — the same expected
# response and assertions that were stored in the service are used for evaluation.
print("\n  Running EvaluationClient with ground truth loaded from managed dataset v1 ...")
_managed_pto_scenario = next(
    (sc for sc in _dataset_from_managed.scenarios if sc.scenario_id == "pto-balance-check"),
    _dataset_from_managed.scenarios[0] if _dataset_from_managed.scenarios else None,
)
_managed_eval_results = []
if _managed_pto_scenario and session_pto_balance:
    _managed_ref = ReferenceInputs(
        expected_response=(_managed_pto_scenario.turns[0].expected_response if _managed_pto_scenario.turns else None),
        assertions=_managed_pto_scenario.assertions,
    )
    _managed_eval_results = eval_client.run(
        evaluator_ids=[
            "Builtin.Correctness",
            "Builtin.GoalSuccessRate",
            CUSTOM_RESPONSE_SIMILARITY_ID,
        ],
        session_id=session_pto_balance,
        agent_id=AGENT_ID,
        look_back_time=timedelta(hours=2),
        reference_inputs=_managed_ref,
    )
    print_eval_results(
        "PTO Balance — ground truth from managed dataset v1",
        _managed_eval_results,
    )

# Save results
_mds_path = _RESULTS_DIR / "managed_dataset_eval_results.json"
_mds_path.write_text(
    json.dumps(
        {
            "dataset_id": _managed_ds_id,
            "dataset_name": _ds_name,
            "dataset_version": "1",
            "example_count": len(_v1_examples),
            "eval_results": _managed_eval_results,
        },
        indent=2,
        default=str,
    )
)
print(f"\n  Managed dataset results saved to: {_mds_path}")

# Clean up — in a real workflow, keep the dataset and reference it by ID.
# Tip: add the dataset ID to your agent_config.json to share it across scripts.
_dc.delete_dataset_and_wait(datasetId=_managed_ds_id)
print(f"  Managed dataset {_managed_ds_id} cleaned up.")

# ============================================================
# 5. BatchEvaluationRunner — service-side batch evaluation
# ============================================================
#
# BatchEvaluationRunner submits all sessions to the service in a single job and
# returns aggregate scores per evaluator. Unlike OnDemandEvaluationDatasetRunner
# (which evaluates client-side), batch evaluation runs server-side and is ideal for:
#   - Measuring a baseline across many sessions
#   - Pre/post comparison after prompt changes
#   - Large-scale production monitoring

print("\n[5/6] BatchEvaluationRunner — service-side batch evaluation ...")

from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_models import (  # noqa: E402
    BatchEvaluationRunConfig,
    BatchEvaluatorConfig,
    CloudWatchDataSourceConfig,
)
from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_runner import (  # noqa: E402
    BatchEvaluationRunner,
)

SERVICE_NAME = f"{AGENT_ID}.DEFAULT"
SPANS_LOG_GROUP = "aws/spans"

batch_data_source = CloudWatchDataSourceConfig(
    service_names=[SERVICE_NAME],
    log_group_names=[SPANS_LOG_GROUP, CW_LOG_GROUP],
    ingestion_delay_seconds=180,
)

batch_config = BatchEvaluationRunConfig(
    batch_evaluation_name=f"gt_batch_{uuid.uuid4().hex[:8]}",
    evaluator_config=BatchEvaluatorConfig(
        evaluator_ids=[
            "Builtin.Correctness",
            "Builtin.GoalSuccessRate",
            "Builtin.TrajectoryExactOrderMatch",
        ]
    ),
    data_source=batch_data_source,
    polling_timeout_seconds=1800,
    polling_interval_seconds=30,
)

print(f"  Batch name: {batch_config.batch_evaluation_name}")
print(f"  Evaluators: {batch_config.evaluator_config.evaluator_ids}")
print(f"  Dataset   : {len(dataset.scenarios)} scenarios")
print("  Starting batch evaluation (may take several minutes) ...")

batch_runner = BatchEvaluationRunner(region=REGION)
batch_result = batch_runner.run_dataset_evaluation(
    config=batch_config,
    dataset=dataset,
    agent_invoker=agent_invoker,
)

print(f"\n  Batch ID : {batch_result.batch_evaluation_id}")
print(f"  Status   : {batch_result.status}")

if batch_result.evaluation_results:
    ev = batch_result.evaluation_results
    print(f"  Sessions : {ev.number_of_sessions_completed} completed, {ev.number_of_sessions_failed} failed")
    if ev.evaluator_summaries:
        print("\n  Per-evaluator aggregate scores:")
        for es in ev.evaluator_summaries:
            eid = es.evaluator_id or "unknown"
            score = (
                f"{es.statistics.average_score:.3f}"
                if es.statistics and es.statistics.average_score is not None
                else "N/A"
            )
            evaluated = es.total_evaluated or 0
            print(f"    {eid:<40} score={score}  (n={evaluated})")
else:
    print("  No aggregated results returned.")

# Fetch per-session detail from CloudWatch
if batch_result.output_data_config:
    events = batch_runner.fetch_evaluation_events(batch_result)
    print(f"\n  Per-session events: {len(events)}")
    for ev in events[:3]:
        attrs = ev.get("attributes", {})
        print(f"    session  : {attrs.get('session.id', '')[:45]}")
        print(f"    evaluator: {attrs.get('gen_ai.evaluation.name')}")
        print(f"    score    : {attrs.get('gen_ai.evaluation.score.value')}")
        print(f"    label    : {attrs.get('gen_ai.evaluation.score.label')}")
        print()

# Save results
_br_path = _RESULTS_DIR / "batch_runner_results.json"
_batch_data = {
    "batch_evaluation_id": batch_result.batch_evaluation_id,
    "status": batch_result.status,
    "created_at": str(batch_result.created_at),
}
if batch_result.evaluation_results:
    ev = batch_result.evaluation_results
    _batch_data["sessions_completed"] = ev.number_of_sessions_completed
    _batch_data["sessions_failed"] = ev.number_of_sessions_failed
    if ev.evaluator_summaries:
        _batch_data["evaluator_summaries"] = [
            {
                "evaluator_id": es.evaluator_id,
                "average_score": es.statistics.average_score if es.statistics else None,
                "total_evaluated": es.total_evaluated,
            }
            for es in ev.evaluator_summaries
        ]
_br_path.write_text(json.dumps(_batch_data, indent=2, default=str))
print(f"  BatchRunner results saved to: {_br_path}")

# ============================================================
# 6. Summary
# ============================================================

print("\n[6/6] All evaluations complete.")
print("\n  Results written to:")
print(f"    {_ec_path}")
print(f"    {_dr_path}")
print(f"    {_mds_path}")
print(f"    {_br_path}")
print(
    "\n  Interface comparison:\n"
    "    EvaluationClient         → per-session, ad-hoc, supports all evaluator types\n"
    "    OnDemandDatasetRunner    → per-scenario, client-side, good for CI/CD\n"
    "    DatasetClient            → managed dataset storage + versioning; load ground truth by dataset ID\n"
    "    BatchEvaluationRunner    → aggregate scores, service-side, good for baselines\n"
)
