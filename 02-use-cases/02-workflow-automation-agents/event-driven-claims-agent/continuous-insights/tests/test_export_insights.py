"""Tests for turning Insight findings (which point at span ids) into a brief a coding agent can use."""

import json
import sys
from pathlib import Path

# Fake test fixtures, not credentials.
FAKE_SPAN_ID = "465f2a6763610961"  # pragma: allowlist secret
FAKE_LOG_GROUP = "/aws/bedrock-agentcore/runtimes/x-DEFAULT"  # pragma: allowlist secret

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_insights as ex

STACKTRACE = (
    "Traceback (most recent call last):\n"
    '  File "/var/task/strands/tools/decorator.py", line 654, in stream\n'
    "    result = await asyncio.to_thread(self._tool_func, **validated_input)\n"
    '  File "/opt/aws/agentcore-runtime/python/versions/3.13.9/lib/python3.13/asyncio/threads.py", line 25, in to_thread\n'
    '  File "/var/task/opentelemetry/instrumentation/threading/__init__.py", line 162, in wrapped_func\n'
    '  File "/var/task/tools.py", line 40, in get_quote\n'
    "    quote = _quote_for(symbol)\n"
    '  File "/var/task/tools.py", line 30, in _quote_for\n'
    "    return QUOTES[symbol]\n"
    "KeyError: 'LEN-B'\n"
)

# Shape of a real span record from a failed tool call (trimmed to the fields the exporter reads).
SPAN = {
    "spanId": FAKE_SPAN_ID,
    "name": "execute_tool get_quote",
    "attributes": {"gen_ai.tool.name": "get_quote", "gen_ai.tool.status": "error"},
    "events": [
        {
            "name": "exception",
            "attributes": {
                "exception.type": "KeyError",
                "exception.message": "'LEN-B'",
                "exception.stacktrace": STACKTRACE,
            },
        }
    ],
    "status": {"code": "ERROR", "message": "'LEN-B'"},
}


class FakeLogs:
    """Stands in for the CloudWatch Logs client: records the query and returns canned span records."""

    def __init__(self, spans):
        self.spans, self.queries = spans, []

    def start_query(self, **kwargs):
        self.queries.append(kwargs)
        return {"queryId": "q-1"}

    def get_query_results(self, queryId):
        rows = [[{"field": "@message", "value": json.dumps(s)}] for s in self.spans]
        return {"status": "Complete", "results": rows}


def insight(finding):
    return {
        "agentInsightId": "abc123",
        "name": "Quote lookups fail",
        "description": "Quotes raise KeyError.",
        "affectedSessionCount": 7,
        "lastSeenAt": "2026-11-10T19:00:00Z",
        "rootCauses": [
            {
                "name": "No ticker normalisation",
                "explanation": "Exact-match lookup.",
                "suggestedFix": "Normalise tickers.",
                "sampleSessions": [{"sessionId": "s1", "findings": [finding]}],
            }
        ],
        "sampleSessions": [{"sessionId": "s1"}],
    }


def test_last_code_frame_skips_framework_frames():
    assert ex.last_code_frame(STACKTRACE) == "tools.py:30 in _quote_for"


def test_last_code_frame_without_user_code_is_none():
    assert ex.last_code_frame('File "/var/task/strands/x.py", line 1, in f') is None
    assert ex.last_code_frame("") is None


def test_summarize_span_extracts_tool_and_exception():
    summary = ex.summarize_span(SPAN)
    assert summary == {
        "span_name": "execute_tool get_quote",
        "tool": "get_quote",
        "status": "ERROR",
        "exception_type": "KeyError",
        "exception_message": "'LEN-B'",
        "code_location": "tools.py:30 in _quote_for",
    }


def test_collect_span_ids_keeps_only_well_formed_ids():
    findings = [
        {"targetType": "SPAN_ID", "targetId": FAKE_SPAN_ID, "affectedSpanIds": ["0123456789abcdef"]},
        {"targetType": "SPAN_ID", "targetId": 'x"] | fields @message #'},  # would break out of the query
        {"targetType": "TRACE_ID", "targetId": "6af36bcd210254570f0af2680f14a63c"},
    ]
    ids = ex.collect_span_ids([insight(f) for f in findings])
    assert ids == {FAKE_SPAN_ID, "0123456789abcdef"}


def test_resolve_spans_queries_both_log_groups_and_returns_summaries():
    logs = FakeLogs([SPAN])
    found = ex.resolve_spans(
        logs,
        "ClaimsAgent_claimsagent.DEFAULT",
        FAKE_LOG_GROUP,
        {FAKE_SPAN_ID},
        lookback_days=7,
    )
    assert found[FAKE_SPAN_ID]["tool"] == "get_quote"
    query = logs.queries[0]
    assert query["logGroupNames"] == ["aws/spans", FAKE_LOG_GROUP]
    assert 'resource.attributes.service.name = "ClaimsAgent_claimsagent.DEFAULT"' in query["queryString"]
    assert f'spanId in ["{FAKE_SPAN_ID}"]' in query["queryString"]


def test_resolve_spans_refuses_an_unsafe_application_name():
    logs = FakeLogs([SPAN])
    assert ex.resolve_spans(logs, 'bad" or 1=1', "lg", {FAKE_SPAN_ID}, 7) == {}
    assert logs.queries == []


def test_markdown_names_the_tool_exception_and_code_location_for_span_id_findings():
    finding = {
        "targetType": "SPAN_ID",
        "targetId": FAKE_SPAN_ID,
        "category": "execution-error",
        "description": "The quote tool raised a KeyError.",
    }
    spans = {FAKE_SPAN_ID: ex.summarize_span(SPAN)}
    text = ex.render_markdown("ClaimsAgent_claimsagent.DEFAULT", [insight(finding)], spans)
    assert "`get_quote` raised `KeyError: 'LEN-B'` at `tools.py:30 in _quote_for`" in text
    assert "**Suggested fix:** Normalise tickers." in text


def test_markdown_falls_back_to_the_span_id_when_the_span_is_not_found():
    finding = {
        "targetType": "SPAN_ID",
        "targetId": FAKE_SPAN_ID,
        "category": "execution-error",
        "description": "The quote tool raised a KeyError.",
    }
    text = ex.render_markdown("ClaimsAgent_claimsagent.DEFAULT", [insight(finding)], {})
    assert "SPAN_ID `465f2a6763610961`" in text


def test_markdown_treats_a_span_name_target_as_the_tool_name():
    finding = {"targetType": "SPAN_NAME", "targetId": "get_quote", "category": "execution-error", "description": "d"}
    assert "`get_quote` (execution-error)" in ex.render_markdown("app", [insight(finding)], {})


def test_repeated_findings_collapse_into_one_bullet():
    finding = {"targetType": "SPAN_ID", "targetId": FAKE_SPAN_ID, "category": "c", "description": "d"}
    spans = {FAKE_SPAN_ID: ex.summarize_span(SPAN)}
    one = insight(finding)
    one["rootCauses"][0]["sampleSessions"] *= 3
    assert ex.render_markdown("app", [one], spans).count("raised `KeyError") == 1
