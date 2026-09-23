"""
Simulated Dataset Construction and Batch Evaluation for HR Assistant.

This script demonstrates how to build an evaluation dataset by simulating
realistic multi-turn employee conversations using an LLM actor. The actor
plays the role of an employee with a specific HR request, driving the
conversation across multiple turns against the deployed HR Assistant agent.

The process:
  1. For each scenario, the actor LLM generates employee messages turn-by-turn
     based on a persona profile and goal.
  2. Each employee message is sent to the HR Assistant agent, which responds
     using its real tools (get_pto_balance, submit_pto_request, etc.).
  3. After all scenarios complete, sessions are submitted to AgentCore for
     batch evaluation using built-in evaluators.
  4. Evaluation results are saved to results/simulation_results.json.

Why simulate?
  Recorded real user sessions often lack ground truth (you don't know what the
  employee expected). Simulation lets you:
  - Control the conversation goal and assertions
  - Generate reproducible test cases at scale
  - Attach precise ground truth for evaluators that need it

Usage:
    python simulate.py [--region REGION] [--config PATH] [--dry-run]

Args:
    --region    AWS region (default: from agent_config.json or boto3 session)
    --config    Path to agent_config.json written by deploy.py
                (default: ../../utils/agent_config.json)
    --dry-run   Print scenario definitions without invoking the agent

Prerequisites:
    1. Deploy the HR Assistant agent:
           cd ../../utils && python deploy.py [--region REGION]
    2. Install dependencies:
           pip install -r requirements.txt

Outputs:
    results/simulation_results.json   - Simulated session transcripts
    results/batch_eval_results.json   - Batch evaluation aggregate scores
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3
from boto3.session import Session
from botocore.config import Config

# ============================================================
# 0. Parse args and load agent config
# ============================================================

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / ".." / ".." / "utils" / "agent_config.json"
_RESULTS_DIR = _SCRIPT_DIR / "results"
_RESULTS_DIR.mkdir(exist_ok=True)

parser = argparse.ArgumentParser(description="Simulate HR conversations and run batch evaluation")
parser.add_argument("--region", default=None, help="AWS region")
parser.add_argument(
    "--config",
    default=str(_DEFAULT_CONFIG),
    help="Path to agent_config.json (written by deploy.py)",
)
parser.add_argument("--dry-run", action="store_true", help="Print scenarios without invoking")
args = parser.parse_args()

_config_path = Path(args.config)
if not _config_path.exists():
    print(f"ERROR: Agent config not found at {_config_path}")
    print("Run deploy.py first:  cd ../../utils && python deploy.py")
    sys.exit(1)

_cfg = json.loads(_config_path.read_text())
AGENT_ID = _cfg["agent_id"]
AGENT_ARN = _cfg["agent_arn"]
CW_LOG_GROUP = _cfg["cw_log_group"]
OTEL_SERVICE_NAME = _cfg.get("otel_service_name", "")
REGION = args.region or _cfg.get("region") or Session().region_name or "us-east-1"

print("=" * 60)
print("HR Assistant — Simulated Dataset Evaluation")
print("=" * 60)
print(f"  Region       : {REGION}")
print(f"  Agent ARN    : {AGENT_ARN}")
print(f"  CW Log Group : {CW_LOG_GROUP}")
print(f"  OTel Service : {OTEL_SERVICE_NAME}")

bac = boto3.client(
    "bedrock-agentcore",
    region_name=REGION,
    config=Config(read_timeout=120, connect_timeout=30),
)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)

# ============================================================
# 1. Scenario Definitions
# ============================================================
#
# Each scenario has:
#   actor_profile  - the persona the LLM uses to drive the conversation
#   first_input    - opening message to the HR Assistant agent
#   max_turns      - conversation cap to prevent infinite loops
#   assertions     - ground truth for evaluation
#
# Assertions are attached to the batch evaluation as sessionMetadata,
# so evaluators like GoalSuccessRate know what success looks like.

SCENARIOS = [
    {
        "scenario_id": "sim-pto-balance",
        "scenario_description": ("An employee wants to check their remaining PTO balance."),
        "actor_profile": {
            "traits": {"communication_style": "direct", "planning_ahead": True},
            "context": "Employee EMP-001 wants to know how many PTO days they have left before booking a vacation.",
            "goal": "Find out the remaining PTO balance for employee EMP-001.",
        },
        "first_input": "Hi, I'd like to check my PTO balance. My employee ID is EMP-001.",
        "max_turns": 4,
        "assertions": [
            "Agent calls get_pto_balance for EMP-001",
            "Agent reports the remaining PTO days (10 days)",
            "Agent provides a clear and accurate balance summary",
        ],
    },
    {
        "scenario_id": "sim-pto-request",
        "scenario_description": "An employee wants to submit a PTO request for a specific date range.",
        "actor_profile": {
            "traits": {"organized": True, "detail_oriented": True},
            "context": "Employee EMP-001 wants to take a week off in July for a family vacation.",
            "goal": "Submit a PTO request for EMP-001 from 2026-07-14 to 2026-07-18.",
        },
        "first_input": "I need to submit a PTO request. My ID is EMP-001 and I'd like to take off July 14th through July 18th, 2026 for a family vacation.",
        "max_turns": 4,
        "assertions": [
            "Agent calls submit_pto_request for EMP-001",
            "Agent confirms the PTO request was approved",
            "Agent provides a request ID or confirmation number",
        ],
    },
    {
        "scenario_id": "sim-remote-work-policy",
        "scenario_description": "An employee wants to understand the company's remote work policy.",
        "actor_profile": {
            "traits": {"curious": True, "new_employee": True},
            "context": "A recently joined employee wants to know how many days per week they can work from home.",
            "goal": "Understand the remote work policy including the maximum days allowed per week.",
        },
        "first_input": "I'm a new employee and I was wondering about the remote work policy. How many days a week can I work from home?",
        "max_turns": 4,
        "assertions": [
            "Agent calls lookup_hr_policy for remote_work",
            "Agent reports the maximum remote days (3 days per week)",
            "Agent mentions manager approval requirement",
        ],
    },
    {
        "scenario_id": "sim-benefits-inquiry",
        "scenario_description": "An employee wants to know about the 401k matching benefit.",
        "actor_profile": {
            "traits": {"financially_savvy": True, "benefit_conscious": True},
            "context": "Employee planning for retirement wants to understand 401k matching details.",
            "goal": "Get details about the 401k plan including the company match percentage.",
        },
        "first_input": "Can you tell me about our 401k benefits? Specifically how much the company matches?",
        "max_turns": 4,
        "assertions": [
            "Agent calls get_benefits_summary for 401k",
            "Agent reports the company match (100% up to 4% of salary)",
            "Agent mentions the vesting schedule",
        ],
    },
    {
        "scenario_id": "sim-pay-stub",
        "scenario_description": "An employee wants to retrieve their pay stub for a specific month.",
        "actor_profile": {
            "traits": {"detail_oriented": True, "record_keeping": True},
            "context": "Employee EMP-001 needs their January 2026 pay stub for a loan application.",
            "goal": "Retrieve the pay stub for EMP-001 for January 2026 including net pay.",
        },
        "first_input": "I need my pay stub for January 2026. My employee ID is EMP-001.",
        "max_turns": 4,
        "assertions": [
            "Agent calls get_pay_stub for EMP-001 period 2026-01",
            "Agent reports the gross pay and net pay amounts",
            "Agent provides the full pay stub breakdown",
        ],
    },
]

# ============================================================
# 2. Actor Simulator
# ============================================================
#
# The actor LLM (Claude Haiku) plays the employee role. It receives
# the full conversation transcript and generates the next employee
# message. When the actor determines the goal is complete, it ends
# the conversation naturally.

ACTOR_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

_FAREWELL_MARKERS = (
    "thanks, bye",
    "thanks bye",
    "all set",
    "goodbye",
    "that's all",
    "thats all",
    "i'm done",
    "im done",
    "end the conversation",
)


def _invoke_agent(prompt: str, session_id: str) -> str:
    """Send one prompt to the HR Assistant agent and return the response text."""
    resp = bac.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
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


def _build_actor_system_prompt(scenario: dict) -> str:
    p = scenario["actor_profile"]
    return (
        f"You are role-playing an employee interacting with a company HR Assistant chatbot.\n\n"
        f"Your profile:\n"
        f"  Context: {p['context']}\n"
        f"  Goal:    {p['goal']}\n"
        f"  Traits:  {p['traits']}\n\n"
        f"Rules for your messages:\n"
        f"- Stay in character. Be realistic and consistent with your traits.\n"
        f"- Respond naturally to what the HR assistant says. Provide info when requested.\n"
        f"- Keep each message short (1-2 sentences typically).\n"
        f"- When your goal is complete (e.g., balance confirmed, request submitted, policy explained), "
        f"end the conversation naturally.\n"
        f"- Do NOT pretend to be the HR assistant. ONLY speak as the employee.\n"
    )


def _actor_next_message(system_prompt: str, transcript: list) -> tuple:
    """Generate the next employee message using the actor LLM.

    transcript: list of (speaker, text) tuples.
    Returns (text, conversation_complete).
    """
    messages = []
    for speaker, text in transcript:
        role = "user" if speaker == "employee" else "assistant"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"][0]["text"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": [{"text": text}]})

    # Ensure last message is from the agent (assistant) before asking actor to respond
    if not messages or messages[-1]["role"] != "assistant":
        messages.append({"role": "assistant", "content": [{"text": "(waiting for your response)"}]})

    resp = bedrock_runtime.converse(
        modelId=ACTOR_MODEL_ID,
        system=[{"text": system_prompt}],
        messages=messages,
        inferenceConfig={"maxTokens": 256, "temperature": 0.7},
    )
    content_blocks = resp["output"]["message"].get("content", [])
    text = ""
    for block in content_blocks:
        if "text" in block:
            text = block["text"].strip()
            break
    if not text:
        return "Thanks, that's all I needed!", True
    is_farewell = any(marker in text.lower() for marker in _FAREWELL_MARKERS)
    return text, is_farewell


def _run_scenario(scenario: dict) -> dict:
    """Run one scenario and return its session record."""
    scenario_id = scenario["scenario_id"]
    session_id = str(uuid.uuid4())
    system_prompt = _build_actor_system_prompt(scenario)
    transcript = []

    current_message = scenario["first_input"]
    transcript.append(("employee", current_message))
    print(f"\n  [{scenario_id}] session={session_id[:20]}...")
    print(f"    turn 1  employee > {current_message[:80]}")

    turns_taken = 0
    for turn_idx in range(scenario["max_turns"]):
        turns_taken = turn_idx + 1
        agent_response = _invoke_agent(current_message, session_id)
        transcript.append(("agent", agent_response))
        print(f"    turn {turns_taken}  agent    < {agent_response[:80]}")

        if turn_idx == scenario["max_turns"] - 1:
            break

        next_message, complete = _actor_next_message(system_prompt, transcript)
        transcript.append(("employee", next_message))
        print(f"    turn {turns_taken + 1}  employee > {next_message[:80]}")
        if complete:
            print(f"    [{scenario_id}] Actor signalled goal complete.")
            turns_taken += 1
            break
        current_message = next_message

    return {
        "scenario_id": scenario_id,
        "session_id": session_id,
        "num_turns": turns_taken,
        "transcript": transcript,
    }


# ============================================================
# 3. Run All Scenarios
# ============================================================

if args.dry_run:
    print("\n[DRY RUN] Scenarios defined (not invoking agent):")
    for s in SCENARIOS:
        print(f"  {s['scenario_id']}: {s['scenario_description']}")
    print(f"\nTotal scenarios: {len(SCENARIOS)}")
    sys.exit(0)

INGESTION_DELAY_SECONDS = 180

print(f"\n[1/3] Running {len(SCENARIOS)} simulated scenarios ...")

simulation_results = []
for scenario in SCENARIOS:
    result = _run_scenario(scenario)
    simulation_results.append(result)

# Save transcripts
_sim_path = _RESULTS_DIR / "simulation_results.json"
_sim_path.write_text(
    json.dumps(
        [
            {
                "scenario_id": r["scenario_id"],
                "session_id": r["session_id"],
                "num_turns": r["num_turns"],
                "transcript": r["transcript"],
            }
            for r in simulation_results
        ],
        indent=2,
    )
)
print(f"\n  Transcripts saved: {_sim_path}")

print(f"\n  Waiting {INGESTION_DELAY_SECONDS}s for CloudWatch span ingestion ...")
time.sleep(INGESTION_DELAY_SECONDS)
print("  Ready for batch evaluation.")

# ============================================================
# 4. Batch Evaluation
# ============================================================
#
# All simulated sessions are submitted in one start_batch_evaluation call.
# The sessionMetadata attaches assertions as ground truth per session,
# so GoalSuccessRate and Correctness evaluators know what to check.
#
# The OTel spans log group ("aws/spans") and the runtime log group are both
# included because AgentCore evaluations needs both to reconstruct sessions.

print("\n[2/3] Submitting batch evaluation ...")

session_ids = [r["session_id"] for r in simulation_results]
session_metadata = []
for result in simulation_results:
    scenario = next(s for s in SCENARIOS if s["scenario_id"] == result["scenario_id"])
    session_metadata.append(
        {
            "sessionId": result["session_id"],
            "testScenarioId": scenario["scenario_id"],
            "groundTruth": {
                "inline": {
                    "assertions": [{"text": a} for a in scenario["assertions"]],
                },
            },
        }
    )

EVALUATOR_IDS = [
    "Builtin.GoalSuccessRate",
    "Builtin.Helpfulness",
    "Builtin.Correctness",
]

_batch_name = f"hr_simulated_{uuid.uuid4().hex[:8]}"
_client_token = str(uuid.uuid4())

print(f"  Batch name    : {_batch_name}")
print(f"  Sessions      : {len(session_ids)}")
print(f"  Evaluators    : {EVALUATOR_IDS}")

_cw_config = {
    "logGroupNames": ["aws/spans", CW_LOG_GROUP],
    "filterConfig": {"sessionIds": session_ids},
}
if OTEL_SERVICE_NAME:
    _cw_config["serviceNames"] = [OTEL_SERVICE_NAME]

_start_resp = bac.start_batch_evaluation(
    batchEvaluationName=_batch_name,
    evaluators=[{"evaluatorId": e} for e in EVALUATOR_IDS],
    dataSourceConfig={"cloudWatchLogs": _cw_config},
    evaluationMetadata={"sessionMetadata": session_metadata},
    clientToken=_client_token,
)

batch_id = _start_resp["batchEvaluationId"]
print(f"  batchEvaluationId: {batch_id}")

# ============================================================
# 5. Poll and Display Results
# ============================================================

print("\n[3/3] Polling batch evaluation status ...")

TERMINAL_STATUSES = {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "STOPPED"}
POLL_SECONDS = 30

while True:
    resp = bac.get_batch_evaluation(batchEvaluationId=batch_id)
    status = resp["status"]
    print(f"  status = {status}")
    if status in TERMINAL_STATUSES:
        break
    time.sleep(POLL_SECONDS)

batch_final = resp
print(f"\n  Batch evaluation finished: {batch_final['status']}")

results = batch_final.get("evaluationResults", {})
summaries = results.get("evaluatorSummaries", [])
sessions_completed = results.get("numberOfSessionsCompleted", "N/A")
sessions_failed = results.get("numberOfSessionsFailed", "N/A")

print(f"\n  Sessions completed : {sessions_completed}")
print(f"  Sessions failed    : {sessions_failed}")

if summaries:
    print(f"\n  {'Evaluator':<35} {'Avg Score':<12} {'Evaluated':<12} {'Failed'}")
    print("  " + "-" * 70)
    for s in summaries:
        eid = s.get("evaluatorId", "")
        stats = s.get("statistics", {})
        avg = stats.get("averageScore", "N/A")
        total_eval = s.get("totalEvaluated", "N/A")
        total_failed = s.get("totalFailed", "N/A")
        print(f"  {eid:<35} {str(avg):<12} {str(total_eval):<12} {str(total_failed)}")
else:
    print("  No evaluator summaries returned yet. Try querying again after a few minutes.")

_batch_path = _RESULTS_DIR / "batch_eval_results.json"
_batch_path.write_text(
    json.dumps(
        {
            "batch_id": batch_id,
            "batch_name": _batch_name,
            "status": batch_final["status"],
            "evaluator_summaries": summaries,
            "sessions_completed": sessions_completed,
            "sessions_failed": sessions_failed,
            "session_ids": session_ids,
        },
        indent=2,
        default=str,
    )
)
print(f"\n  Results saved: {_batch_path}")

print("\n" + "=" * 60)
print("Summary")
print("=" * 60)
print(f"  Scenarios simulated : {len(simulation_results)}")
print(f"  Batch evaluation    : {_batch_name} ({batch_final['status']})")
print("  Transcripts         : results/simulation_results.json")
print("  Batch results       : results/batch_eval_results.json")
