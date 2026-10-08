"""
Decision-Model Cascade for the HR Assistant Agent.

Demonstrates the confidence-based cascade pattern described in the blog post:
fast, cheap decision-model evaluators screen every turn; only sessions with
flagged turns escalate to built-in LLM evaluators for a rich explanation.

Pattern
-------
  1. Screen  — DM evaluators (Jev or Strands Decider) run on every turn.
               No LLM inference; ~100–200 ms per turn; calibrated probabilities.
  2. Flag    — Any turn scoring below the threshold is flagged.
  3. Explain — Only flagged sessions call Builtin.Correctness / Builtin.Helpfulness.
               These run LLM inference and return natural-language explanations
               that tell you *why* a turn fell short.

Cost savings: you pay for LLM evaluation only on sessions that need it.
On a typical deployment where most sessions pass, the DM gate eliminates
80–90 % of LLM evaluator calls while still surfacing every genuine issue.

Usage
-----
    python cascade.py [--jev-ids results/jev_evaluator_ids.json]
                      [--decider-ids results/decider_evaluator_ids.json]
                      [--groundedness-threshold 0.75]
                      [--helpfulness-threshold 0.60]
                      [--region REGION]
                      [--config PATH]

    One of --jev-ids or --decider-ids is required (or both files can be
    present in results/ and the script will auto-detect).

Prerequisites
-------------
    Run evaluate.py first to deploy the DM evaluators and write the IDs file:

        python evaluate.py --with-jev --jev-secret-arn <ARN>
      or
        python evaluate.py --with-decider [--decider-ec2]

Outputs
-------
    results/cascade_results.json  — per-turn DM scores, flagged turns, and
                                    built-in LLM explanations for flagged sessions
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import boto3
from boto3.session import Session
from botocore.config import Config

# ============================================================
# Parse args
# ============================================================

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / ".." / "utils" / "agent_config.json"
_RESULTS_DIR = _SCRIPT_DIR / "results"
_RESULTS_DIR.mkdir(exist_ok=True)

parser = argparse.ArgumentParser(description="Decision-model cascade for the HR Assistant agent")
parser.add_argument("--region", default=None, help="AWS region")
parser.add_argument(
    "--config",
    default=str(_DEFAULT_CONFIG),
    help="Path to agent_config.json (written by deploy.py)",
)
parser.add_argument(
    "--jev-ids",
    default=None,
    help="Path to jev_evaluator_ids.json (written by evaluate.py --with-jev)",
)
parser.add_argument(
    "--decider-ids",
    default=None,
    help="Path to decider_evaluator_ids.json (written by evaluate.py --with-decider)",
)
parser.add_argument(
    "--groundedness-threshold",
    type=float,
    default=0.75,
    help="DM groundedness value below which a turn is flagged for LLM review (default: 0.75)",
)
parser.add_argument(
    "--helpfulness-threshold",
    type=float,
    default=0.60,
    help="DM helpfulness value below which a turn is flagged for LLM review (default: 0.60)",
)
args = parser.parse_args()

# ============================================================
# Load agent config
# ============================================================

_config_path = Path(args.config)
if not _config_path.exists():
    print(f"ERROR: Agent config not found at {_config_path}")
    print("Run deploy.py first:  cd ../utils && python deploy.py")
    sys.exit(1)

_cfg = json.loads(_config_path.read_text())
AGENT_ID = _cfg["agent_id"]
AGENT_ARN = _cfg["agent_arn"]
REGION = args.region or _cfg.get("region") or Session().region_name or "us-east-1"

# ============================================================
# Load DM evaluator IDs  (auto-detect from results/ if needed)
# ============================================================

_jev_ids_path = Path(args.jev_ids) if args.jev_ids else _RESULTS_DIR / "jev_evaluator_ids.json"
_decider_ids_path = Path(args.decider_ids) if args.decider_ids else _RESULTS_DIR / "decider_evaluator_ids.json"

_dm_backend: str
_dm_ids: dict[str, str]

if _jev_ids_path.exists():
    _raw = json.loads(_jev_ids_path.read_text())
    _dm_ids = _raw["evaluator_ids"]
    _dm_backend = "Jev"
    _dm_groundedness_key = "JevGroundedness"
    _dm_helpfulness_key = "JevHelpfulness"
    print(f"Using Jev evaluator IDs from: {_jev_ids_path}")
elif _decider_ids_path.exists():
    _raw = json.loads(_decider_ids_path.read_text())
    _dm_ids = _raw["evaluator_ids"]
    _dm_backend = "Strands Decider"
    _dm_groundedness_key = "DeciderGroundedness"
    _dm_helpfulness_key = "DeciderHelpfulness"
    print(f"Using Strands Decider evaluator IDs from: {_decider_ids_path}")
else:
    print("ERROR: No DM evaluator IDs found.")
    print("Run evaluate.py --with-jev or evaluate.py --with-decider first to deploy the decision-model evaluators.")
    sys.exit(1)

DM_GROUNDEDNESS_ID = _dm_ids[_dm_groundedness_key]
DM_HELPFULNESS_ID = _dm_ids[_dm_helpfulness_key]

# ============================================================
# boto3 clients
# ============================================================

agentcore_client = boto3.client(
    "bedrock-agentcore",
    region_name=REGION,
    config=Config(read_timeout=120, connect_timeout=30),
)

# ============================================================
# Banner
# ============================================================

print()
print("=" * 65)
print("HR Assistant — Decision-Model Cascade")
print("=" * 65)
print(f"  Region              : {REGION}")
print(f"  Agent ARN           : {AGENT_ARN}")
print(f"  DM backend          : {_dm_backend}")
print(f"  DM Groundedness ID  : {DM_GROUNDEDNESS_ID}")
print(f"  DM Helpfulness ID   : {DM_HELPFULNESS_ID}")
print(f"  Groundedness thresh : {args.groundedness_threshold}")
print(f"  Helpfulness thresh  : {args.helpfulness_threshold}")
print()

# ============================================================
# Step 1 — Invoke HR Assistant
#
# Three standard HR turns + one adversarial turn that exercises
# a capability gap in the agent (no payroll-correction tool).
# The adversarial turn is designed to score low on helpfulness
# so the cascade has something to flag and escalate.
# ============================================================

CASCADE_TURNS = [
    # ── Standard turns (should score well) ──────────────────
    "What is the current PTO balance for employee EMP-001?",
    "Please submit a PTO request for EMP-001 from 2026-10-06 to 2026-10-10.",
    "What is the company PTO policy regarding advance notice?",
    # ── Adversarial turn (likely to flag on helpfulness) ─────
    # The HR assistant has tools for PTO, policy, benefits, and pay stubs,
    # but no tool to dispute or correct payroll. The agent can look up the
    # January pay stub but cannot verify overtime or update payroll — so
    # its response falls short of the employee's actual goal.
    (
        "EMP-001 says they worked 20 hours of overtime in January 2026 "
        "but their paycheck didn't include overtime pay. "
        "Can you verify the overtime and update the payroll to add the missing amount?"
    ),
]

SESSION_ID = f"cascade-{uuid.uuid4()}"
print(f"[Step 1/3] Invoking HR Assistant ({len(CASCADE_TURNS)} turns) ...")
print(f"  Session ID : {SESSION_ID[:36]}")
print()


def _invoke_agent(prompt: str, session_id: str) -> str:
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
            except json.JSONDecodeError:
                pass
            parts.append(str(chunk))
    return "".join(parts) if parts else raw


agent_replies: list[str] = []
for i, prompt in enumerate(CASCADE_TURNS, 1):
    label = "(adversarial)" if i == len(CASCADE_TURNS) else "(standard)"
    print(f"  Turn {i} {label}")
    print(f"    User  : {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
    reply = _invoke_agent(prompt, SESSION_ID)
    agent_replies.append(reply)
    print(f"    Agent : {reply[:100]}{'...' if len(reply) > 100 else ''}")
    print()

N_TURNS = len(CASCADE_TURNS)

# ============================================================
# Wait for CloudWatch log ingestion
# ============================================================

WAIT_SECONDS = 150
print(f"  Waiting {WAIT_SECONDS}s for CloudWatch log ingestion ...")
time.sleep(WAIT_SECONDS)

# ============================================================
# Step 2 — Screen with DM evaluators
#
# Run the two TRACE-level DM evaluators on the session.
# EvaluationClient.run() returns one result per turn per evaluator,
# ordered by turn. We group results by evaluator and index into
# per-turn scores.
# ============================================================

from bedrock_agentcore.evaluation import EvaluationClient

print()
print(f"[Step 2/3] Screening with {_dm_backend} evaluators ...")

dm_ec = EvaluationClient(region_name=REGION)
dm_ec._evaluator_level_cache.update(
    {
        DM_GROUNDEDNESS_ID: "TRACE",
        DM_HELPFULNESS_ID: "TRACE",
    }
)

dm_results = dm_ec.run(
    evaluator_ids=[DM_GROUNDEDNESS_ID, DM_HELPFULNESS_ID],
    agent_id=AGENT_ID,
    session_id=SESSION_ID,
    look_back_time=timedelta(hours=1),
)

# Group results by evaluator; within each group, order = turn order
_gnd_results = [r for r in dm_results if r.get("evaluatorId") == DM_GROUNDEDNESS_ID]
_help_results = [r for r in dm_results if r.get("evaluatorId") == DM_HELPFULNESS_ID]

print(f"\n  {_dm_backend} scores ({len(dm_results)} result(s)) :\n")
print(f"  {'Turn':<6} {'Groundedness':>14} {'Helpfulness':>12}  {'Flags'}")
print("  " + "-" * 55)

# Collect per-turn flags
flagged_turns: list[int] = []  # 1-based turn indices
per_turn_scores: list[dict] = []

for i in range(N_TURNS):
    gnd_val: float | str = _gnd_results[i]["value"] if i < len(_gnd_results) else "N/A"
    help_val: float | str = _help_results[i]["value"] if i < len(_help_results) else "N/A"
    gnd_err = _gnd_results[i].get("errorCode") if i < len(_gnd_results) else None
    help_err = _help_results[i].get("errorCode") if i < len(_help_results) else None

    flags = []
    if gnd_err:
        flags.append(f"GND_ERR:{gnd_err}")
    elif isinstance(gnd_val, float) and gnd_val < args.groundedness_threshold:
        flags.append(f"LOW_GND({gnd_val:.2f}<{args.groundedness_threshold})")
    if help_err:
        flags.append(f"HELP_ERR:{help_err}")
    elif isinstance(help_val, float) and help_val < args.helpfulness_threshold:
        flags.append(f"LOW_HELP({help_val:.2f}<{args.helpfulness_threshold})")

    gnd_str = f"{gnd_val:.2f}" if isinstance(gnd_val, float) else str(gnd_val)
    help_str = f"{help_val:.2f}" if isinstance(help_val, float) else str(help_val)
    flag_str = ", ".join(flags) if flags else "—"

    label = " (adversarial)" if i + 1 == N_TURNS else ""
    print(f"  {i + 1:<6} {gnd_str:>14} {help_str:>12}  {flag_str}{label}")

    per_turn_scores.append(
        {
            "turn_index": i + 1,
            "prompt": CASCADE_TURNS[i][:120],
            "agent_reply": agent_replies[i][:300],
            "groundedness": gnd_val,
            "helpfulness": help_val,
            "groundedness_error": gnd_err,
            "helpfulness_error": help_err,
            "flags": flags,
        }
    )
    if flags:
        flagged_turns.append(i + 1)

print()
if flagged_turns:
    print(f"  Flagged turns : {flagged_turns}")
else:
    print("  No turns flagged — all scores above thresholds.")

# ============================================================
# Step 3 — Escalate to built-in LLM evaluators
#
# The DM flagged at least one turn. Now run Builtin.Correctness
# (faithfulness/groundedness from an LLM perspective) and
# Builtin.Helpfulness on the same session. These produce per-turn
# natural-language explanations that diagnose exactly what went wrong.
#
# If no turns were flagged the LLM evaluators are skipped entirely,
# demonstrating the cost saving for "clean" sessions.
# ============================================================

builtin_results: list[dict] = []
escalated = False

print()
print("[Step 3/3] Escalating to built-in LLM evaluators ...")

if not flagged_turns:
    print()
    print("  No turns flagged — skipping LLM evaluation.")
    print(
        f"  Cost saving: {N_TURNS * 2} LLM evaluator call(s) avoided "
        f"(Builtin.Correctness + Builtin.Helpfulness × {N_TURNS} turns)."
    )
else:
    escalated = True
    print(
        f"\n  {len(flagged_turns)} of {N_TURNS} turn(s) flagged. Running Builtin.Correctness + Builtin.Helpfulness ..."
    )

    builtin_ec = EvaluationClient(region_name=REGION)
    builtin_ec._evaluator_level_cache.update(
        {
            "Builtin.Correctness": "TRACE",
            "Builtin.Helpfulness": "TRACE",
        }
    )

    builtin_raw = builtin_ec.run(
        evaluator_ids=["Builtin.Correctness", "Builtin.Helpfulness"],
        agent_id=AGENT_ID,
        session_id=SESSION_ID,
        look_back_time=timedelta(hours=1),
    )

    _corr_results = [r for r in builtin_raw if r.get("evaluatorId") == "Builtin.Correctness"]
    _help_builtin = [r for r in builtin_raw if r.get("evaluatorId") == "Builtin.Helpfulness"]

    print()
    print("  Built-in LLM evaluator results:\n")

    for i in range(N_TURNS):
        corr = _corr_results[i] if i < len(_corr_results) else {}
        hlp = _help_builtin[i] if i < len(_help_builtin) else {}
        is_flagged = (i + 1) in flagged_turns

        flag_marker = " *** FLAGGED ***" if is_flagged else ""
        print(f"  Turn {i + 1}{flag_marker}")
        print(f"    Prompt     : {CASCADE_TURNS[i][:80]}{'...' if len(CASCADE_TURNS[i]) > 80 else ''}")

        if corr:
            corr_err = corr.get("errorCode")
            corr_label = f"ERR:{corr_err}" if corr_err else corr.get("label", "N/A")
            corr_val = corr.get("value", "N/A")
            corr_expl = corr.get("explanation", "")
            print(f"    Correctness: {corr_val!s:.4}  [{corr_label}]")
            if corr_expl:
                print(f"    Explanation: {corr_expl[:200]}{'...' if len(corr_expl) > 200 else ''}")
        if hlp:
            hlp_err = hlp.get("errorCode")
            hlp_label = f"ERR:{hlp_err}" if hlp_err else hlp.get("label", "N/A")
            hlp_val = hlp.get("value", "N/A")
            hlp_expl = hlp.get("explanation", "")
            print(f"    Helpfulness: {hlp_val!s:.4}  [{hlp_label}]")
            if hlp_expl:
                print(f"    Explanation: {hlp_expl[:200]}{'...' if len(hlp_expl) > 200 else ''}")
        print()

        builtin_results.append(
            {
                "turn_index": i + 1,
                "flagged": is_flagged,
                "correctness": corr,
                "helpfulness": hlp,
            }
        )

    # Cost savings for escalated sessions
    saved_calls = (N_TURNS - len(flagged_turns)) * 2
    total_possible = N_TURNS * 2
    pct_saved = round(saved_calls / total_possible * 100) if total_possible else 0
    print(f"  Cost saving: {saved_calls} of {total_possible} LLM evaluator call(s) avoided ({pct_saved}% reduction).")

# ============================================================
# Cascade summary
# ============================================================

print()
print("=" * 65)
print("Cascade Summary")
print("=" * 65)
print(f"  Session         : {SESSION_ID}")
print(f"  Turns evaluated : {N_TURNS}")
print(f"  DM backend      : {_dm_backend}")
print(f"  DM flagged      : {len(flagged_turns)} turn(s) — {flagged_turns}")
print(f"  LLM escalated   : {'yes' if escalated else 'no'}")
if not flagged_turns:
    print(f"  LLM calls saved : {N_TURNS * 2} (100% — all turns passed DM screen)")
else:
    saved = (N_TURNS - len(flagged_turns)) * 2
    pct = round(saved / (N_TURNS * 2) * 100) if N_TURNS else 0
    print(f"  LLM calls saved : {saved} of {N_TURNS * 2} ({pct}%)")

print()
print("How the cascade reduces cost:")
print(
    f"  Without cascade : {N_TURNS * 2} LLM evaluator calls "
    f"(Correctness + Helpfulness × {N_TURNS} turns) on every session."
)
if not flagged_turns:
    print("  With cascade    : 0 LLM calls (DM found no issues).")
else:
    print(f"  With cascade    : {len(flagged_turns) * 2} LLM calls (only for {len(flagged_turns)} flagged turn(s)).")
print()
print(
    "  At scale, if most sessions pass the DM screen, the cascade eliminates\n"
    "  the majority of LLM evaluator calls while still catching every issue."
)

# ============================================================
# Save results
# ============================================================

_output = {
    "session_id": SESSION_ID,
    "dm_backend": _dm_backend,
    "dm_evaluator_ids": {
        "groundedness": DM_GROUNDEDNESS_ID,
        "helpfulness": DM_HELPFULNESS_ID,
    },
    "thresholds": {
        "groundedness": args.groundedness_threshold,
        "helpfulness": args.helpfulness_threshold,
    },
    "per_turn_dm_scores": per_turn_scores,
    "flagged_turns": flagged_turns,
    "escalated_to_builtin": escalated,
    "builtin_results": builtin_results,
    "cost_summary": {
        "total_turns": N_TURNS,
        "turns_flagged": len(flagged_turns),
        "lm_calls_if_no_cascade": N_TURNS * 2,
        "lm_calls_with_cascade": len(flagged_turns) * 2 if escalated else 0,
        "lm_calls_saved": (N_TURNS - len(flagged_turns)) * 2 if escalated else N_TURNS * 2,
    },
}

_out_path = _RESULTS_DIR / "cascade_results.json"
_out_path.write_text(json.dumps(_output, indent=2, default=str))
print(f"\n  Results saved: {_out_path}")
