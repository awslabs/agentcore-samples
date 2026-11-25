"""Export the agent's Insights to local files a coding agent can read.

An Insight's findings point at spans by id (for example `465f2a6763610961`). An id alone tells a
coding agent nothing, so this script looks each span up in CloudWatch Logs and adds the tool name,
the exception, and the line of your code that raised it.

Writes, under insights/<timestamp>/:
  insight-<id>.json   the full GetAgentInsight response for each Insight
  FAILURES.md         a condensed brief: per Insight, the root causes, the suggested fix,
                      the failing tool and exception, and example session ids
  spans.json          the span details that were looked up (id -> tool, exception, code location)
  summary.json        id -> {name, affectedSessionCount, lastSeenAt}, used by verify_fix.py

Usage:
    python scripts/export_insights.py --name ClaimsAgent [--archive-status ACTIVE|ARCHIVED] [--lookback-days 7]
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from common import ROOT, client, evaluation_clients, load_state, poll, validate_name

SPANS_LOG_GROUP = "aws/spans"  # CloudWatch Transaction Search writes span records here
SPAN_ID = re.compile(r"^[0-9a-f]{16}$")
SERVICE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,256}$")
STACK_FRAME = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
FRAMEWORK_PATHS = ("/strands/", "/opentelemetry/", "/asyncio/", "/concurrent/", "/opt/aws/", "/site-packages/")
MAX_IDS_PER_QUERY = 100


def list_all(data, application: str, archive_status: str = "ACTIVE") -> list[dict]:
    items, token = [], None
    while True:
        page = data.list_agent_insights(
            applicationName=application,
            archiveStatus=archive_status,
            maxResults=50,
            **({"nextToken": token} if token else {}),
        )
        items += page["agentInsights"]
        token = page.get("nextToken")
        if not token:
            return items


# ---------------------------------------------------------------- span lookup


def last_code_frame(stacktrace: str) -> str | None:
    """The last stack frame that is your code, as 'tools.py:30 in _quote_for' (framework frames skipped)."""
    own = [m for m in STACK_FRAME.finditer(stacktrace or "") if not any(p in m.group(1) for p in FRAMEWORK_PATHS)]
    if not own:
        return None
    path, line, function = own[-1].groups()
    return f"{Path(path).name}:{line} in {function}"


def summarize_span(span: dict) -> dict:
    """Pick out what a person needs from one span record."""
    attributes = span.get("attributes") or {}
    exception = next((e.get("attributes") or {} for e in span.get("events", []) if e.get("name") == "exception"), {})
    status = span.get("status") or {}
    return {
        "span_name": span.get("name"),
        "tool": attributes.get("gen_ai.tool.name"),
        "status": status.get("code"),
        "exception_type": exception.get("exception.type"),
        "exception_message": exception.get("exception.message") or status.get("message"),
        "code_location": last_code_frame(exception.get("exception.stacktrace", "")),
    }


def collect_span_ids(insights: list[dict]) -> set[str]:
    """Every span id the Insights' findings point at."""
    ids: set[str] = set()
    for insight in insights:
        for cause in insight.get("rootCauses", []):
            for session in cause.get("sampleSessions", []):
                for finding in session.get("findings", []):
                    if finding.get("targetType") == "SPAN_ID" and finding.get("targetId"):
                        ids.add(finding["targetId"])
                    ids.update(finding.get("affectedSpanIds", []))
    return {i for i in ids if SPAN_ID.match(i)}  # only well-formed ids are ever put into a query


def resolve_spans(
    logs, application: str, runtime_log_group: str, span_ids: set[str], lookback_days: int
) -> dict[str, dict]:
    """Look span ids up in aws/spans and the runtime log group. Returns {span id: summary}."""
    if not span_ids or not SERVICE_NAME.match(application):
        return {}
    ordered = sorted(span_ids)
    found: dict[str, dict] = {}
    now = int(time.time())
    for start in range(0, len(ordered), MAX_IDS_PER_QUERY):
        id_list = ", ".join(f'"{span_id}"' for span_id in ordered[start : start + MAX_IDS_PER_QUERY])
        query = (
            f'filter resource.attributes.service.name = "{application}" and ispresent(name) '
            f"and spanId in [{id_list}] | fields @message"
        )
        query_id = logs.start_query(
            logGroupNames=[SPANS_LOG_GROUP, runtime_log_group],
            startTime=now - lookback_days * 86400,
            endTime=now,
            queryString=query,
            limit=10000,
        )["queryId"]

        def finished(query_id=query_id) -> dict | None:
            result = logs.get_query_results(queryId=query_id)
            return result if result["status"] in ("Complete", "Failed", "Cancelled", "Timeout") else None

        result = poll(finished, timeout_s=180, interval_s=3, what="the span lookup in CloudWatch Logs")
        if result["status"] != "Complete":
            print(f"  warning: span lookup {result['status'].lower()}; findings will show span ids only")
            continue
        for row in result["results"]:
            for field in row:
                if field["field"] == "@message":
                    span = json.loads(field["value"])
                    found.setdefault(span["spanId"], summarize_span(span))
    return found


# ---------------------------------------------------------------- rendering


def evidence_line(finding: dict, spans: dict[str, dict]) -> tuple[tuple, str]:
    """One bullet for a finding, plus a key so repeated findings collapse into one bullet."""
    candidates = [finding["targetId"]] if finding.get("targetType") == "SPAN_ID" and finding.get("targetId") else []
    candidates += finding.get("affectedSpanIds", [])
    span = next((spans[c] for c in candidates if c in spans), None)
    category, description = finding.get("category", "?"), finding.get("description", "")

    if span:
        tool = span["tool"] or span["span_name"] or "unknown span"
        what = f"`{tool}`"
        if span["exception_type"]:
            message = (span["exception_message"] or "")[:120]
            what += f" raised `{span['exception_type']}: {message}`"
        if span["code_location"]:
            what += f" at `{span['code_location']}`"
        return (tool, span["exception_type"], category), f"  - {what} ({category}): {description}"

    target = f"{finding.get('targetType', '?')} `{finding.get('targetId', '?')}`"
    if finding.get("targetType") == "SPAN_NAME":
        target = f"`{finding.get('targetId', '?')}`"  # a span name is the tool name
    return (target, None, category), f"  - {target} ({category}): {description}"


def finding_lines(root_cause: dict, spans: dict[str, dict]) -> list[str]:
    seen: dict[tuple, str] = {}
    for session in root_cause.get("sampleSessions", []):
        for finding in session.get("findings", []):
            key, line = evidence_line(finding, spans)
            seen.setdefault(key, line)
    return list(seen.values())


def render_markdown(application: str, insights: list[dict], spans: dict[str, dict] | None = None) -> str:
    spans = spans or {}
    intro = f"{len(insights)} active Insight(s), most affected sessions first. Source: AgentCore Continuous Insights."
    lines = [f"# Failure insights for `{application}`", "", intro, ""]
    for insight in sorted(insights, key=lambda i: -i.get("affectedSessionCount", 0)):
        lines += [
            f"## {insight['name']}",
            "",
            f"- id: `{insight['agentInsightId']}`",
            f"- affected sessions: {insight.get('affectedSessionCount', 0)}",
            f"- last seen: {insight.get('lastSeenAt', 'n/a')}",
            "",
            insight["description"],
            "",
        ]
        for cause in insight.get("rootCauses", []):
            lines += [f"### Root cause: {cause['name']}", "", cause["explanation"], ""]
            if cause.get("suggestedFix"):
                lines += [f"**Suggested fix:** {cause['suggestedFix']}", ""]
            evidence = finding_lines(cause, spans)
            if evidence:
                lines += ["Evidence from sampled sessions:", *evidence, ""]
        sessions = insight.get("sampleSessions", [])
        if sessions:
            lines += ["Example sessions: " + ", ".join(f"`{s['sessionId']}`" for s in sessions[:3]), ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", help="output directory (default: insights/<timestamp>)")
    parser.add_argument(
        "--archive-status",
        choices=["ACTIVE", "ARCHIVED"],
        default="ACTIVE",
        help="export active Insights (default) or archived ones",
    )
    parser.add_argument("--lookback-days", type=int, default=7, help="how far back to search for the failing spans")
    args = parser.parse_args()
    state = load_state(validate_name(args.name))
    _, data = evaluation_clients(state["region"])
    application = state["service_name"]

    summaries = list_all(data, application, args.archive_status)
    out = Path(args.out) if args.out else ROOT / "insights" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out.mkdir(parents=True, exist_ok=True)

    details = []
    for item in summaries:
        detail = data.get_agent_insight(applicationName=application, agentInsightId=item["agentInsightId"])
        detail.pop("ResponseMetadata", None)
        details.append(detail)
        (out / f"insight-{item['agentInsightId']}.json").write_text(
            json.dumps(detail, indent=2, default=str) + "\n", encoding="utf-8"
        )

    span_ids = collect_span_ids(details)
    spans = resolve_spans(
        client(state["region"], "logs"), application, state["log_group"], span_ids, args.lookback_days
    )
    print(f"Resolved {len(spans)} of {len(span_ids)} failing span(s) to tool and exception details")

    (out / "spans.json").write_text(json.dumps(spans, indent=2) + "\n", encoding="utf-8")
    (out / "FAILURES.md").write_text(render_markdown(application, details, spans), encoding="utf-8")
    (out / "summary.json").write_text(
        json.dumps(
            {
                d["agentInsightId"]: {
                    "name": d["name"],
                    "affectedSessionCount": d.get("affectedSessionCount", 0),
                    "lastSeenAt": str(d.get("lastSeenAt", "")),
                }
                for d in details
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Exported {len(details)} Insight(s) to {out}")
    print(f"Read first: {out / 'FAILURES.md'}")


if __name__ == "__main__":
    main()
