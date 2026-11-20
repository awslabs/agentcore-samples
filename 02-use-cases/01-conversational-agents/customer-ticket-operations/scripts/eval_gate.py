"""Release gate: run the eval suite on one build and exit non-zero unless it passes.

    make eval BUILD=v5 PROFILE=<aws-profile>
    make eval-k10 BUILD=v5 PROFILE=<aws-profile>   # variance check: 10 samples per attack

Starts the suite runner Lambda (deployed by the evals root) and follows its progress in the state
table, then prints the per-case matrix. The suite resets the demo when it finishes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
# Standalone `python /path/to/scripts/eval_gate.py` has only scripts/ on sys.path;
# bootstrap both source directories before importing the shared pure release contract.
sys.path.insert(0, str(ROOT / "functions/api"))
sys.path.insert(0, str(ROOT / "functions/evals"))
import suite as gate_rules  # noqa: E402  (the same gate the runner computes)

GREEN, PINK, DIM, RESET = "\033[38;2;110;231;80m", "\033[38;2;255;77;139m", "\033[2m", "\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="v5", choices=[f"v{i}" for i in range(6)])
    ap.add_argument("--project", default="ticketops")
    ap.add_argument("--env", default="demo")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--dataset", default="default", choices=["default", "k10"])
    a = ap.parse_args()
    ssm = boto3.client("ssm", region_name=a.region)
    param = lambda k: ssm.get_parameter(Name=f"/{a.project}/{a.env}/{k}")["Parameter"]["Value"]  # noqa: E731
    function, table = param("evals/runner_function"), param("app/state_table")
    state = boto3.resource("dynamodb", region_name=a.region).Table(table)
    lam = boto3.client("lambda", region_name=a.region)
    source = json.loads((ROOT / "evals/dataset.json").read_text())
    if a.dataset == "default":
        suite = run_one(lam, state, function, f"evals#{a.build}", {"build": a.build}, a.timeout)
        if not suite or not compatible(suite, source, a.build):
            print(f"{PINK}incomplete or historical dataset; no current release evidence{RESET}")
            return 2
        return report(suite)
    # k10 is 53 live runs (57 samples), more than one Lambda invocation allows: one case per invocation,
    # stored apart from the console's matrix, then the same gate over all of them
    study = json.loads((ROOT / "evals/dataset-k10.json").read_text())
    if study.get("source_version") != source["version"]:
        print(f"{PINK}k10 source revision does not match default{RESET}")
        return 2
    cases = study["cases"]
    done = []
    for case in cases:
        key = f"evals#{a.build}#k10#{case['id']}"
        payload = {"build": a.build, "dataset": "k10", "case": case["id"]}
        suite = run_one(lam, state, function, key, payload, a.timeout)
        if (
            not suite
            or suite.get("build") != a.build
            or suite.get("dataset") != study["version"]
            or suite.get("dataset_name") != "k10"
            or suite.get("source_version") != source["version"]
            or len(suite.get("cases", [])) != 1
            or suite["cases"][0].get("id") != case["id"]
            or suite["cases"][0].get("kind") != case["kind"]
            or suite["cases"][0].get("family") != case.get("family")
            or len(suite["cases"][0].get("samples", [])) != case["samples"]
            or suite.get("gate") != gate_rules.gate(suite["cases"])
        ):
            print(f"{PINK}incomplete or mismatched k10 case: {case['id']}{RESET}")
            return 2
        done += suite["cases"]
    combined = {
        "build": a.build,
        "dataset": study["version"],
        "cases": done,
        "gate": gate_rules.gate(done),
    }
    return report(combined)


def compatible(record: dict, dataset: dict, build: str) -> bool:
    """CLI and API use the same full release-compatibility predicate."""
    return (
        record.get("status") == "DONE"
        and gate_rules.classify_default(record, dataset, build) == "current"
    )


def run_one(lam, state, function: str, key: str, payload: dict, timeout: int) -> dict | None:
    """Start one suite invocation and follow it in the state table until it's DONE."""
    suite_id = str(uuid.uuid4())
    lam.invoke(
        FunctionName=function,
        InvocationType="Event",
        Payload=json.dumps({**payload, "suite_id": suite_id}).encode(),
    )
    what = payload.get("case") or "all cases"
    print(f"eval suite {suite_id} on {payload['build'].upper()} ({what}) started ({function})")
    deadline, last = time.time() + timeout, ""
    while time.time() < deadline:
        item = state.get_item(Key={"customer_id": key}, ConsistentRead=True).get("Item")
        suite = json.loads(item["suite"]) if item else {}
        if suite.get("suite_id") == suite_id:
            line = f"{suite.get('status')} {suite.get('progress', '')}".strip()
            if line != last:
                print(f"  {DIM}{line}{RESET}")
                last = line
            if suite.get("status") == "ERROR":
                print(f"{PINK}suite error: {suite.get('error')}{RESET}")
                return None
            if suite.get("status") == "DONE":
                return suite
        time.sleep(10)
    print(f"{PINK}timed out waiting for the suite{RESET}")
    return None


def report(suite: dict) -> int:
    gate = suite["gate"]
    rows = {c["id"]: c for c in gate["cases"]}
    print()
    for case in suite["cases"]:
        r = rows[case["id"]]
        ok_case = r["safety"] == "PASS" and r.get("control", "PASS") == "PASS"
        mark = f"{GREEN}PASS{RESET}" if ok_case else f"{PINK}FAIL{RESET}"
        judge = [
            (s.get("scores") or {}).get("ticket_text_as_data", {}).get("label", "-")
            for s in case["samples"]
        ]
        verdicts = ", ".join(s.get("verdict", "?") for s in case["samples"])
        print(
            f"  {mark}  {case['title']:<46} safety {r['passed']}/{r['k']} "
            f"control {r.get('control', '-')}: {r.get('control_reason', '-')} "
            f"runs: {verdicts:<24} judge: {'/'.join(judge)}"
        )
    q = gate["quality"]
    print(
        f"\n  safety: {gate['safety']}; controls: {gate.get('controls', {}).get('status', 'not evaluated')}"
    )
    print(f"  quality: mean Helpfulness {q['helpfulness_mean']} (floor {q['floor']})")
    ok = gate["passed"]
    print(
        f"\n{GREEN if ok else PINK}{suite['build'].upper()} release gate: {'PASS' if ok else 'FAIL'}{RESET}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
