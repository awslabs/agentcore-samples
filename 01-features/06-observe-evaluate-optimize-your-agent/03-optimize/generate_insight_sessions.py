"""Generate HR Assistant sessions from a published AgentCore dataset."""

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
from typing import Any

import boto3
import requests
from utils.response_stream import decode_agentcore_response

SUPPORTED_PRESETS = ("quick", "insights")


@dataclass(frozen=True)
class ManagedScenario:
    """One versioned managed-dataset scenario and its replay counts."""

    name: str
    turns: tuple[str, ...]
    session_counts: dict[str, int]


def _validate_scenario(example: dict[str, Any], line_number: int) -> ManagedScenario:
    """Validate and normalize one predefined managed-dataset example."""
    name = example.get("scenario_id")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Dataset line {line_number} must have a non-empty scenario_id")

    raw_turns = example.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ValueError(f"Scenario {name!r} must have at least one turn")
    turns = []
    for turn_number, turn in enumerate(raw_turns, start=1):
        prompt = turn.get("input") if isinstance(turn, dict) else None
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(
                f"Scenario {name!r} turn {turn_number} must have a non-empty string input"
            )
        turns.append(prompt)

    metadata = example.get("metadata", {})
    counts = metadata.get("session_counts", {}) if isinstance(metadata, dict) else {}
    session_counts = {}
    for preset in SUPPORTED_PRESETS:
        count = counts.get(preset) if isinstance(counts, dict) else None
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(
                f"Scenario {name!r} metadata.session_counts.{preset} must be a positive integer"
            )
        session_counts[preset] = count
    return ManagedScenario(name=name, turns=tuple(turns), session_counts=session_counts)


def parse_dataset_jsonl(content: str) -> dict[str, ManagedScenario]:
    """Parse managed-dataset JSONL into scenarios keyed by scenario_id."""
    scenarios = {}
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            example = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Dataset line {line_number} is not valid JSON") from error
        if not isinstance(example, dict):
            raise TypeError(f"Dataset line {line_number} must contain a JSON object")
        scenario = _validate_scenario(example, line_number)
        if scenario.name in scenarios:
            raise ValueError(f"Duplicate scenario_id in dataset: {scenario.name}")
        scenarios[scenario.name] = scenario
    if not scenarios:
        raise ValueError("The published dataset contains no scenarios")
    return scenarios


def load_published_scenarios(
    control_client,
    *,
    dataset_id: str,
    dataset_version: str,
) -> dict[str, ManagedScenario]:
    """Download and validate one immutable managed-dataset version."""
    if dataset_version.upper() == "DRAFT":
        raise ValueError("Use a published dataset version, not DRAFT")
    dataset = control_client.get_dataset(
        datasetId=dataset_id,
        datasetVersion=dataset_version,
    )
    if dataset.get("status") != "ACTIVE":
        raise ValueError(
            f"Dataset {dataset_id} version {dataset_version} is not ACTIVE: "
            f"{dataset.get('status', 'UNKNOWN')}"
        )
    if dataset.get("schemaType") != "AGENTCORE_EVALUATION_PREDEFINED_V1":
        raise ValueError("Continuous Insights traffic requires a predefined managed dataset")
    download_url = dataset.get("downloadUrl")
    if not download_url:
        raise ValueError("GetDataset did not return a downloadUrl")
    response = requests.get(download_url, timeout=30)
    response.raise_for_status()
    return parse_dataset_jsonl(response.text)


def build_workload(
    scenarios: dict[str, ManagedScenario],
    preset: str,
    scenario_name: str = "all",
    session_count: int | None = None,
) -> list[ManagedScenario]:
    """Build a deterministic workload from managed-dataset metadata."""
    if preset not in SUPPORTED_PRESETS:
        raise ValueError(f"Unsupported preset: {preset}")
    if scenario_name == "all":
        selected = list(scenarios.values())
    else:
        try:
            selected = [scenarios[scenario_name]]
        except KeyError as error:
            available = ", ".join(sorted(scenarios))
            raise ValueError(
                f"Scenario {scenario_name!r} is not in the dataset. Available: {available}"
            ) from error

    workload = []
    for scenario in selected:
        count = session_count if session_count is not None else scenario.session_counts[preset]
        workload.extend([scenario] * count)
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


def invoke_session(client, state: dict, scenario: ManagedScenario) -> dict:
    """Invoke all turns in one independent Runtime session."""
    started = datetime.now(timezone.utc)  # noqa: UP017 - sample supports Python 3.10
    record = {
        "runtime_name": state["runtime_name"],
        "runtime_id": state["runtime_id"],
        "application_name": state["service_name"],
        "session_id": str(uuid.uuid4()),
        "timestamp": started.isoformat(),
        "scenario": scenario.name,
        "delivery_status": "delivered",
        "turn_results": [],
    }
    try:
        for prompt in scenario.turns:
            response = client.invoke_agent_runtime(
                agentRuntimeArn=state["runtime_arn"],
                runtimeSessionId=record["session_id"],
                payload=json.dumps({"prompt": prompt}).encode(),
                qualifier="DEFAULT",
            )
            summary = decode_agentcore_response(response)[:500]
            record["turn_results"].append(
                {"input": prompt, "response_summary": summary}
            )
        record["response_summary"] = record["turn_results"][-1]["response_summary"]
    except Exception as error:  # noqa: BLE001 - every attempted session is recorded
        record["delivery_status"] = "failed"
        record["error_type"] = type(error).__name__
        record["error_summary"] = str(error)[:500]
    elapsed = datetime.now(timezone.utc) - started  # noqa: UP017 - Python 3.10
    record["duration_ms"] = round(elapsed.total_seconds() * 1000)
    return record


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--preset", choices=SUPPORTED_PRESETS, default="quick")
    parser.add_argument("--scenario", default="all")
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
    dataset_id: str,
    dataset_version: str,
    preset: str,
    scenario: str = "all",
    sessions: int | None = None,
    region: str = "us-west-2",
    profile: str | None = None,
    concurrency: int = 4,
    output: Path | None = None,
    assume_yes: bool = False,
) -> int:
    """Generate sessions from one published managed-dataset version."""
    state = load_runtime_state(runtime_name, region)
    aws_session = boto3.Session(profile_name=profile, region_name=state["region"])
    scenarios = load_published_scenarios(
        aws_session.client("bedrock-agentcore-control"),
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )
    workload = build_workload(scenarios, preset, scenario, sessions)
    output_path = output or Path(f"insight_sessions_{preset}.jsonl")
    print(f"Runtime     : {state['runtime_name']}")
    print(f"Application : {state['service_name']}")
    print(f"Dataset     : {dataset_id} version {dataset_version}")
    print(f"Sessions    : {len(workload)}")
    print(f"Output      : {output_path}")
    print("These calls use real AWS resources and can incur cost.")
    if not assume_yes and input("Proceed? Type yes: ").strip().lower() != "yes":
        return 1

    client = aws_session.client("bedrock-agentcore")
    records = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(invoke_session, client, state, item) for item in workload]
        for completed, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            record["dataset_id"] = dataset_id
            record["dataset_version"] = dataset_version
            records.append(record)
            print(
                f"[{completed:02d}/{len(workload):02d}] "
                f"{record['scenario']:<24} {record['delivery_status']}"
            )

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
        dataset_id=args.dataset_id,
        dataset_version=args.dataset_version,
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
