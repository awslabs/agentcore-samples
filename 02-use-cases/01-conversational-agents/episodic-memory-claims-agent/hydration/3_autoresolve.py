"""
Auto-resolve OPEN review tasks via an LLM "adjuster simulant".

Mirror of the customer-simulant seeder (2_autoseed.py), for the human side:
authenticates as the demo adjuster, lists OPEN review tasks, and for each one an
LLM playing an experienced claims adjuster decides APPROVE / DENY / ESCALATE with
a short, grounded note. It then calls the reviews API to resolve the task — which
(Phase 5) writes the human decision back into AgentCore Memory.

Blind grounding: the adjuster simulant sees ONLY the factual signals on the task
(coverage determination, fraud assessment, policy, claims history, claim summary)
— never an AI recommendation or memory reflections — so its decisions are
independent human judgment.

Usage:
    python hydration/3_autoresolve.py                # resolve all OPEN tasks
    python hydration/3_autoresolve.py --dry-run      # show decisions, don't post
    python hydration/3_autoresolve.py --task <id>    # only this task
"""

import argparse
import json
import os
import sys
import time

import boto3
import requests
from strands import Agent

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "src"))

from memory.config import load_config

MODEL_ID = "global.anthropic.claude-sonnet-4-6"

ADJUSTER_SYSTEM = """\
You are an experienced insurance claims adjuster making the FINAL decision on a
claim that has been prepared for human review. You see only the factual signals
below — coverage determination, fraud assessment, policy status, and claims
history — plus the claim summary. You do NOT see any AI recommendation, and you
decide independently using your professional judgment.

Choose exactly one decision:
- DENY    — coverage determination is EXCLUDED, the policy status is explicitly
            inactive/lapsed/expired, OR fraud indicators are strong enough that
            the claim should not be paid (e.g. HIGH fraud risk, or a clear
            repeat-claim / delayed-reporting / staging pattern).
- APPROVE — coverage applies, the policy is active, and fraud risk is acceptable;
            the documentation and circumstances support a legitimate claim.

For MEDIUM fraud risk, use judgment: APPROVE if the documentation and
circumstances support legitimacy; DENY if there is a strong repeat-claim or
delayed-reporting fraud pattern.

Treat the provided Policy status as authoritative. Do NOT assume the policy is
expired or lapsed from the policy number, the policy-number year, or the incident
date — only treat the policy as inactive if the status field explicitly says so.

Write 1-3 sentences of adjuster notes that justify the decision, citing the
concrete factors (coverage, fraud level + flags, documentation, prior claims).
Be specific and professional.

Respond with ONLY a JSON object, no prose, no code fences:
{"decision": "APPROVE|DENY", "notes": "<your notes>"}
"""


def login_adjuster(cognito, client_id: str, username: str, password: str) -> str:
    r = cognito.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=client_id,
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return r["AuthenticationResult"]["IdToken"]


def format_task(t: dict) -> str:
    c = t.get("claim", {}) or {}
    s = t.get("signals", {}) or {}
    cov = s.get("coverage", {}) or {}
    fr = s.get("fraud", {}) or {}
    pol = s.get("policy", {}) or {}
    hist = s.get("claims_history", {}) or {}
    priors = [(x.get("type"), x.get("outcome")) for x in hist.get("claims", [])]
    return "\n".join(
        [
            f"Claim: {c.get('incident_type')} | policy {c.get('policy_number')} ({c.get('policy_type')})",
            f"Incident date: {c.get('incident_date')} | Filed: {c.get('filing_date')} | Amount: {c.get('claimed_amount')}",
            f"Description: {c.get('description')}",
            f"Coverage determination: {cov.get('determination')} (matched: {cov.get('matched_term')})",
            f"Fraud: {fr.get('risk_level')} (score {fr.get('risk_score')}/100), reporting delay {fr.get('delay_days')} day(s)",
            f"Fraud flags: {fr.get('flags') or 'none'}",
            f"Policy: status {pol.get('status')}, deductible {pol.get('deductible')}, exclusions {pol.get('exclusions')}",
            f"Prior claims ({hist.get('prior_count')}): {priors or 'none'}",
        ]
    )


def _rule_fallback(t: dict) -> dict:
    """Deterministic decision if the LLM output can't be parsed (binary)."""
    s = t.get("signals", {}) or {}
    cov = (s.get("coverage", {}) or {}).get("determination")
    fraud = (s.get("fraud", {}) or {}).get("risk_level")
    if cov == "EXCLUDED":
        return {
            "decision": "DENY",
            "notes": "Coverage determination is EXCLUDED; the loss is not covered under the policy.",
        }
    if fraud == "HIGH":
        return {
            "decision": "DENY",
            "notes": "Fraud risk is HIGH (repeat-claim/delayed-reporting pattern); claim denied pending the policyholder's appeal.",
        }
    return {
        "decision": "APPROVE",
        "notes": "Coverage confirmed and fraud risk acceptable; documentation supports the claim.",
    }


def decide(task: dict) -> dict:
    """Ask the adjuster simulant for a decision + notes (with a rule fallback)."""
    adjuster = Agent(model=MODEL_ID, system_prompt=ADJUSTER_SYSTEM)
    raw = str(adjuster(format_task(task))).strip()
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        d = json.loads(text)
        decision = (d.get("decision") or "").strip().upper()
        notes = (d.get("notes") or "").strip()
        if decision in ("APPROVE", "DENY") and notes:
            return {"decision": decision, "notes": notes}
    except (ValueError, TypeError):
        pass
    print(f"   (could not parse adjuster output; using rule fallback) raw: {raw[:120]}")
    return _rule_fallback(task)


def main():
    parser = argparse.ArgumentParser(description="Auto-resolve OPEN review tasks via an adjuster simulant")
    parser.add_argument("--task", nargs="*", help="Only resolve these task_id(s)")
    parser.add_argument("--dry-run", action="store_true", help="Show decisions without posting")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--reviews-api", default=None, help="Override reviews API URL")
    args = parser.parse_args()

    config = load_config()
    region = config["region"]
    client_id = config["cognito"]["client_id"]
    reviews_api = args.reviews_api or config.get("reviews_backend", {}).get("api_url")
    if not reviews_api:
        print("ERROR: reviews API URL not in config.json; pass --reviews-api")
        sys.exit(1)

    # Adjuster credentials — find the user in the adjuster group. For the demo,
    # that's dana-adjuster (not in the policyholder `users` list), so use known creds.
    adj_user, adj_pass = "dana-adjuster", "AdjustPass1!"

    cognito = boto3.client("cognito-idp", region_name=region)
    id_token = login_adjuster(cognito, client_id, adj_user, adj_pass)
    auth = {"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"}

    # Fetch OPEN tasks.
    resp = requests.get(f"{reviews_api}/reviews?status=OPEN", headers=auth, timeout=30)
    resp.raise_for_status()
    tasks = resp.json().get("tasks", [])
    if args.task:
        wanted = set(args.task)
        tasks = [t for t in tasks if t["task_id"] in wanted]

    if not tasks:
        print("No OPEN tasks to resolve.")
        return

    print(f"Resolving {len(tasks)} OPEN task(s) as {adj_user}{' (dry-run)' if args.dry_run else ''}\n")
    results = []
    for i, t in enumerate(tasks):
        tid = t["task_id"]
        incident = (t.get("claim", {}) or {}).get("incident_type", "?")
        d = decide(t)
        print(f"• {tid}\n  {incident}\n  → {d['decision']}: {d['notes']}")

        if args.dry_run:
            results.append((tid, d["decision"], "dry-run"))
        else:
            r = requests.post(
                f"{reviews_api}/reviews/{tid}/resolve",
                headers=auth,
                json={"decision": d["decision"], "notes": d["notes"]},
                timeout=30,
            )
            ok = r.status_code == 200
            mem = r.json().get("memory_recorded") if ok else None
            print(f"  resolved: HTTP {r.status_code} | memory_recorded={mem}")
            results.append((tid, d["decision"], f"http {r.status_code}"))
        print()
        if i < len(tasks) - 1:
            time.sleep(args.delay)

    print(f"{'=' * 70}\nRESOLVE SUMMARY\n{'=' * 70}")
    for tid, decision, status in results:
        print(f"  {tid:30} {decision:9} {status}")


if __name__ == "__main__":
    main()
