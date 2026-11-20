"""Generate deterministic HR Assistant sessions for Continuous Insights."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import boto3
from utils.response_stream import decode_agentcore_response


@dataclass(frozen=True)
class Scenario:
    """One repeatable prompt used to create a telemetry category."""

    name: str
    prompt: str


SCENARIOS = {
    "success": Scenario("success", "Employee ID: EMP-001. What is my current PTO balance?"),
    "unknown_employee": Scenario("unknown_employee", "Employee ID: EMP-999. What is my current PTO balance?"),
    "unknown_pay_stub": Scenario(
        "unknown_pay_stub",
        "Employee ID: EMP-003. Retrieve my pay stub for period 2026-01.",
    ),
    "unsupported_policy": Scenario("unsupported_policy", "What is Acme Corp's sabbatical policy?"),
    "unsupported_benefit": Scenario(
        "unsupported_benefit",
        "What gym membership benefit does Acme Corp provide?",
    ),
}

PRESETS = {
    "quick": {
        "success": 4,
        "unknown_employee": 3,
        "unknown_pay_stub": 3,
        "unsupported_policy": 3,
        "unsupported_benefit": 3,
    },
    "insights": {
        "success": 4,
        "unknown_employee": 13,
        "unknown_pay_stub": 13,
        "unsupported_policy": 12,
        "unsupported_benefit": 12,
    },
}


def build_workload(
    preset: str,
    scenario_name: str = "all",
    session_count: int | None = None,
) -> list[Scenario]:
    """Build the selected deterministic workload."""
    names = list(SCENARIOS) if scenario_name == "all" else [scenario_name]
    workload = []
    for name in names:
        count = session_count if session_count is not None else PRESETS[preset][name]
        workload.extend([SCENARIOS[name]] * count)
    return workload


def load_runtime_state(runtime_name: str, fallback_region: str) -> dict:
    """Load identifiers written by deploy.py."""
    path = Path(f"agent_state_{runtime_name}.json")
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run deploy.py first.")
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("runtime_name", runtime_name)
    state.setdefault("region", fallback_region)
    return state


def invoke_session(client, state: dict, scenario: Scenario) -> dict:
    """Invoke one independent Runtime session and return a bounded record."""
    started = datetime.now(timezone.utc)  # noqa: UP017 - sample supports Python 3.10
    record = {
        "runtime_name": state["runtime_name"],
        "runtime_id": state["runtime_id"],
        "application_name": state["service_name"],
        "session_id": str(uuid.uuid4()),
        "timestamp": started.isoformat(),
        "scenario": scenario.name,
        "delivery_status": "delivered",
    }
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=state["runtime_arn"],
            runtimeSessionId=record["session_id"],
            payload=json.dumps({"prompt": scenario.prompt}).encode(),
            qualifier="DEFAULT",
        )
        record["response_summary"] = decode_agentcore_response(response)[:500]
    except Exception as error:  # noqa: BLE001 - every attempted session is recorded
        record["delivery_status"] = "failed"
        record["error_type"] = type(error).__name__
        record["error_summary"] = str(error)[:500]
    elapsed = (
        datetime.now(timezone.utc) - started  # noqa: UP017 - Python 3.10
    )
    record["duration_ms"] = round(elapsed.total_seconds() * 1000)
    return record


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--preset", choices=sorted(PRESETS), default="quick")
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    parser.add_argument("--sessions", type=int)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    parser.add_argument("--profile")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if args.sessions is not None and args.sessions < 1:
        parser.error("--sessions must be at least 1")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    return args


def run_generation(
    *,
    runtime_name: str,
    preset: str,
    scenario: str = "all",
    sessions: int | None = None,
    region: str = "us-west-2",
    profile: str | None = None,
    concurrency: int = 4,
    output: Path | None = None,
    assume_yes: bool = False,
) -> int:
    """Generate a workload from typed options shared by both entry points."""
    state = load_runtime_state(runtime_name, region)
    workload = build_workload(preset, scenario, sessions)
    output_path = output or Path(f"insight_sessions_{preset}.jsonl")
    print(f"Runtime     : {state['runtime_name']}")
    print(f"Application : {state['service_name']}")
    print(f"Sessions    : {len(workload)}")
    print(f"Output      : {output_path}")
    print("These calls use real AWS resources and can incur cost.")
    if not assume_yes and input("Proceed? Type yes: ").strip().lower() != "yes":
        return 1

    session = boto3.Session(profile_name=profile, region_name=state["region"])
    client = session.client("bedrock-agentcore")
    records = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(invoke_session, client, state, scenario) for scenario in workload]
        for completed, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            print(f"[{completed:02d}/{len(workload):02d}] {record['scenario']:<24} {record['delivery_status']}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        for record in sorted(records, key=lambda item: item["timestamp"]):
            output_file.write(json.dumps(record, sort_keys=True) + "\n")
    failures = sum(item["delivery_status"] == "failed" for item in records)
    print(f"\nGenerated {len(records)} sessions; {failures} delivery failures.")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """Parse CLI options and generate the selected workload."""
    args = parse_args(argv)
    return run_generation(
        runtime_name=args.runtime,
        preset=args.preset,
        scenario=args.scenario,
        sessions=args.sessions,
        region=args.region,
        profile=args.profile,
        concurrency=args.concurrency,
        output=args.output,
        assume_yes=args.yes,
    )


if __name__ == "__main__":
    sys.exit(main())
