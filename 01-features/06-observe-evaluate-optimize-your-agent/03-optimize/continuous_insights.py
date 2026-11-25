"""Generate Continuous Insights traffic and export coding-assistant handoffs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import boto3
import generate_insight_sessions

MAX_EXPORT_INSIGHTS = 10
INSIGHT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def runtime_state_path(runtime_name: str) -> Path:
    """Return the state path written by deploy.py."""
    return Path(f"agent_state_{runtime_name}.json")


def load_runtime_state(runtime_name: str) -> dict[str, Any]:
    """Load one Runtime identity."""
    path = runtime_state_path(runtime_name)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run deploy.py first.")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_export_insight_ids(insight_ids: list[str]) -> list[str]:
    """Deduplicate Insight IDs and reject unsafe filename components."""
    normalized = list(dict.fromkeys(item.strip() for item in insight_ids if item.strip()))
    if not normalized:
        raise ValueError("Provide at least one Agent Insight ID")
    if len(normalized) > MAX_EXPORT_INSIGHTS:
        raise ValueError(f"At most {MAX_EXPORT_INSIGHTS} Agent Insight IDs can be exported")
    if any(not INSIGHT_ID_PATTERN.fullmatch(item) for item in normalized):
        raise ValueError("Insight IDs may contain only letters, numbers, underscores, and hyphens")
    return normalized


def _text(value: Any, fallback: str = "Not provided.") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def render_coding_assistant_task(*, application_name: str, insights: list[dict[str, Any]]) -> str:
    """Render a vendor-neutral, evidence-backed coding task."""
    sections = []
    for insight in insights:
        causes = []
        for index, cause in enumerate(insight.get("rootCauses", []), start=1):
            findings = []
            for session in cause.get("sampleSessions", []):
                for finding in session.get("findings", []):
                    findings.append(
                        f"- Session `{session.get('sessionId', 'unknown')}`; "
                        f"{finding.get('targetType', 'target')} "
                        f"`{finding.get('targetId', 'unknown')}`: "
                        f"{_text(finding.get('description'))}"
                    )
            causes.append(
                f"""### Root cause {index}: {_text(cause.get("name"), "Unnamed root cause")}

{_text(cause.get("explanation"))}

Service suggested fix:

> {_text(cause.get("suggestedFix"))}

Evidence:

{chr(10).join(findings) or "- No span-level findings were returned."}"""
            )
        rendered_causes = "\n\n".join(causes) or (
            "No root causes were returned. Inspect the listed sessions before changing code."
        )
        sections.append(
            f"""## Insight `{insight["agentInsightId"]}`: {_text(insight.get("name"), "Unnamed Insight")}

Affected sessions: {insight.get("affectedSessionCount", 0)}

{_text(insight.get("description"))}

{rendered_causes}"""
        )
    return f"""# Coding-assistant task from Continuous Insights

## Objective

Investigate and fix recurring failure modes reported for `{application_name}`.
Validate every suggested fix against the repository, tool contracts, and
telemetry evidence before changing source code.

{chr(10).join(sections)}

## Constraints

- Preserve successful HR workflows and public tool contracts.
- Do not invent backend behavior, supported values, or data availability.
- Do not expose hidden reasoning or internal control tags.
- Make only changes supported by exported evidence.
- Add deterministic regression tests.
- Produce a reviewable diff; do not deploy automatically.

After review, deploy the edited checkout as a separate Runtime and replay the
same Continuous Insights workload.
"""


def generate(args: argparse.Namespace) -> int:
    """Generate traffic through the shared typed driver."""
    return generate_insight_sessions.run_generation(
        runtime_name=args.name,
        dataset_id=args.dataset_id,
        dataset_version=args.dataset_version,
        preset=args.preset,
        scenario=args.scenario,
        sessions=args.sessions,
        region=args.region or "us-west-2",
        profile=args.profile,
        concurrency=args.concurrency,
        output=args.output,
        assume_yes=args.yes,
    )


def export_insights(args: argparse.Namespace) -> int:
    """Export complete Insight responses and a coding-assistant task."""
    state = load_runtime_state(args.name)
    region = args.region or state.get("region")
    application_name = args.application_name or state.get("service_name")
    if not region or not application_name:
        raise ValueError("Runtime state must provide region and service_name")
    insight_ids = validate_export_insight_ids(args.insight_ids)
    client = boto3.Session(profile_name=args.profile, region_name=region).client("bedrock-agentcore")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    insights = []
    for insight_id in insight_ids:
        response = client.get_agent_insight(
            agentInsightId=insight_id,
            applicationName=application_name,
        )
        insights.append(response)
        path = args.output_dir / f"insight_{insight_id}.json"
        path.write_text(json.dumps(response, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"Insight saved: {path}")
    task = render_coding_assistant_task(application_name=application_name, insights=insights)
    task_path = args.output_dir / "coding-assistant-task.md"
    task_path.write_text(task, encoding="utf-8")
    print(f"Coding-assistant task saved: {task_path}")
    return 0


def parse_args() -> argparse.Namespace:
    """Parse the Continuous Insights command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--dataset-id", required=True)
    generate_parser.add_argument("--dataset-version", required=True)
    generate_parser.add_argument("--preset", choices=["quick", "insights"], default="insights")
    generate_parser.add_argument("--scenario", default="all")
    generate_parser.add_argument("--sessions", type=int)
    generate_parser.add_argument("--concurrency", type=int, default=4)
    generate_parser.add_argument("--output", type=Path)
    generate_parser.add_argument("--yes", action="store_true")
    generate_parser.set_defaults(handler=generate)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--insight-id", action="append", required=True, dest="insight_ids")
    export_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("continuous-insights-output"),
    )
    export_parser.add_argument("--application-name")
    export_parser.set_defaults(handler=export_insights)
    return parser.parse_args()


def main() -> int:
    """Dispatch the selected operation."""
    args = parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
