"""CloudWatch trace contract: observed flattened Logs Insights fields, owner fencing and safe output."""

import json

import pytest

from tests.test_security_regressions import ALICE

pytest_plugins = ["tests.test_security_regressions"]

RUN = "019a2900-0000-7000-8000-000000000001"


TRACE = "6aecc7654ba8bf9b61853379033bb2b9"
FOREIGN_TRACE = "0" * 32


def row(**fields):
    return [{"field": key, "value": value} for key, value in fields.items()]


class Logs:
    def __init__(self, status="Complete", failure=None, span_rows=None, gateway_rows=None):
        self.calls = []
        self.status = status
        self.failure = failure
        self.span_rows = (
            span_rows
            if span_rows is not None
            else [
                row(
                    **{
                        "@timestamp": "2026-10-29 12:00:00.000",
                        "session_id": RUN,
                        "traceId": TRACE,
                        "name": "strands.agent.invoke",
                        "scope": "strands.telemetry.tracer",
                        "ms": "2.51",
                        "@message": "ticket text and bearer token",
                    }
                )
            ]
        )
        self.gateway_rows = (
            gateway_rows
            if gateway_rows is not None
            else [
                row(
                    **{
                        "@timestamp": "2026-10-29 12:00:00.000",
                        "trace_id": TRACE,
                        "traceId": TRACE,
                        "decision": "DENY",
                        "policies": '[{"policyId":"tenant_fence","statement":"secret"}, {"policyId":"unsafe <script>"}]',
                        "latency": "31",
                        "@message": "ticket text and bearer token",
                        "@ptr": "raw log pointer",
                        "tool": "<img src=x onerror=alert(1)>",
                    }
                )
            ]
        )

    def describe_log_groups(self, logGroupNamePrefix, limit):
        if self.failure == "describe":
            raise RuntimeError("secret failure details")
        return {"logGroups": [{"logGroupName": logGroupNamePrefix}]}

    def start_query(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure == "start":
            raise RuntimeError("secret failure details")
        return {"queryId": str(len(self.calls))}

    def get_query_results(self, queryId):
        if self.failure == "results":
            raise RuntimeError("secret failure details")
        group = self.calls[int(queryId) - 1]["logGroupNames"][0]
        rows = (
            self.span_rows
            if group == "aws/spans"
            else self.gateway_rows
            if "gateway" in group
            else []
        )
        return {"status": self.status, "results": rows}


def setup(api, monkeypatch, logs):
    api.tables["events"].put_item(
        Item={"run_id": RUN, "sk": api.app.OWNER_SK, "customer_id": "customer-a"}
    )
    monkeypatch.setenv("INVOCATION_LOG_GROUP", "model")
    monkeypatch.setattr(
        api.app, "agent_param", lambda key, default: json.dumps({"v4": "gateway-logs"})
    )
    monkeypatch.setattr(api.app, "agent_config", lambda: {"runtime_arn": "arn:runtime/test"})
    monkeypatch.setattr(
        api.app.boto3, "client", lambda service: logs if service == "logs" else None
    )
    return api.app.run_trace(ALICE, RUN)


def test_queries_use_observed_flattened_session_field_and_project_safe_columns(api, monkeypatch):
    logs = Logs()
    result = setup(api, monkeypatch, logs)
    assert len(logs.calls) == 4
    spans = logs.calls[1]
    assert spans["logGroupNames"] == ["aws/spans"]
    assert f"filter attributes.session.id = '{RUN}'" in spans["queryString"]
    assert "attributes.session.id as session_id" in spans["queryString"]
    assert "attributes.`ticketops.run_id`" not in spans["queryString"]
    assert "@message" not in spans["queryString"].split("| filter")[0]
    assert f"filter requestMetadata.ticketops_run = '{RUN}'" in logs.calls[0]["queryString"]
    gateway_query = logs.calls[3]["queryString"]
    assert "body.policy.decision as decision" in gateway_query
    assert "jsonParse(@message) as event" in gateway_query
    assert "jsonStringify(event.body.policy.determiningPolicies) as policies" in gateway_query
    assert "event.body.policy.latencyMs as policy_ms" in gateway_query
    assert f"trace_id = '{TRACE}' and traceId = '{TRACE}'" in gateway_query
    assert gateway_query.count("@message") == 1 and RUN not in gateway_query
    assert all(c["startTime"] < c["endTime"] for c in logs.calls)
    assert all("| limit " in c["queryString"] for c in logs.calls)
    gateway = result["sources"][1]
    assert gateway["status"] == "done" and gateway["matched"] == 1
    assert gateway["records"] == [
        {
            "@timestamp": "2026-10-29 12:00:00.000",
            "decision": "DENY",
            "policies": "tenant_fence",
            "latency": "31",
        }
    ]
    assert "ticket text" not in str(result) and "secret" not in str(result)
    assert result["sources"][2]["records"][0]["name"] == "strands.agent.invoke"


def test_actual_live_schema_preserves_known_span_names_and_policy_latency(api, monkeypatch):
    names = [
        "invoke_agent Strands Agents",
        "execute_tool TicketTools___list_tickets",
        "chat us.meta.llama4-maverick-17b-instruct-v1:0",
        "MCP send tools/call TicketTools___list_tickets",
        "POST /invocations",
    ]
    logs = Logs(
        span_rows=[
            row(
                **{
                    "session_id": RUN,
                    "traceId": TRACE,
                    "name": name,
                    "@timestamp": "2026-11-05 18:14:07.051",
                    "@message": "secret",
                }
            )
            for name in names
        ]
        + [
            row(
                **{
                    "session_id": RUN,
                    "traceId": TRACE,
                    "name": "execute_tool TicketTools___list_tickets secret bearer token",
                }
            )
        ],
        gateway_rows=[
            row(
                **{
                    "@timestamp": "2026-11-05 18:14:07.051",
                    "trace_id": TRACE,
                    "traceId": TRACE,
                    "decision": "ALLOW",
                    "policies": '["ticketops_v4_tenant_fence-0000000000"]',
                    "policy_ms": "101",
                    "@message": "secret",
                }
            )
        ],
    )
    response = setup(api, monkeypatch, logs)
    spans, gateway = response["sources"][2], response["sources"][1]
    assert [r["name"] for r in spans["records"][:5]] == names
    assert gateway["records"] == [
        {
            "@timestamp": "2026-11-05 18:14:07.051",
            "decision": "ALLOW",
            "policies": "ticketops_v4_tenant_fence-0000000000",
            "policy_ms": "101",
        }
    ]
    assert "secret" not in str(response)


def test_gateway_rejects_other_run_and_tenant_even_when_request_quotes_owned_uuid(api, monkeypatch):
    logs = Logs(
        gateway_rows=[
            row(
                **{
                    "trace_id": FOREIGN_TRACE,
                    "traceId": FOREIGN_TRACE,
                    "decision": "DENY",
                    "policies": '["policy-other"]',
                    "@message": f"foreign tenant payload quotes {RUN}",
                }
            ),
            row(
                **{
                    "trace_id": TRACE,
                    "traceId": FOREIGN_TRACE,
                    "decision": "DENY",
                    "policies": '["spoofed-policy"]',
                    "@message": f"quotes {RUN}",
                }
            ),
            row(
                **{
                    "trace_id": TRACE,
                    "traceId": TRACE,
                    "decision": "ALLOW",
                    "policies": '["own-policy"]',
                    "@message": "own run",
                }
            ),
        ]
    )
    result = setup(api, monkeypatch, logs)
    gateway = result["sources"][1]
    assert gateway["status"] == "partial" and gateway["discarded"] == 2
    assert gateway["matched"] == 1 and gateway["records"] == [
        {"decision": "ALLOW", "policies": "own-policy"}
    ]
    assert "policy-other" not in str(result) and "spoofed-policy" not in str(result)


def test_gateway_unavailable_without_verified_owned_spans(api, monkeypatch):
    for span_rows in (
        [],
        [row(**{"session_id": "foreign-run", "traceId": TRACE})],
        [row(**{"session_id": RUN, "traceId": "malformed"})],
        [row(**{"@timestamp": "2026-10-29 12:00:00.000", "traceId": TRACE})],
    ):
        logs = Logs(span_rows=span_rows)
        result = setup(api, monkeypatch, logs)
        gateway = result["sources"][1]
        assert gateway["status"] == "unavailable" and gateway["records"] == []
        assert not any("gateway" in c["logGroupNames"][0] for c in logs.calls)


def test_gateway_trace_id_cap_is_explicit_partial(api, monkeypatch):
    ids = [f"{i:032x}" for i in range(api.app.MAX_GATEWAY_TRACES + 1)]
    logs = Logs(
        span_rows=[row(**{"session_id": RUN, "traceId": t}) for t in ids],
        gateway_rows=[row(**{"trace_id": ids[0], "traceId": ids[0], "decision": "ALLOW"})],
    )
    result = setup(api, monkeypatch, logs)
    assert result["sources"][1]["status"] == "partial"
    assert result["sources"][2]["traces_omitted"] == 1
    assert len(logs.calls[3]["queryString"]) < 1500


def test_owner_fence_before_any_logs_or_ssm(api, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("AWS service accessed before owner check")

    monkeypatch.setattr(api.app.boto3, "client", forbidden)
    monkeypatch.setattr(api.app, "agent_param", forbidden)
    for run, status in [("invalid-run", 400), (RUN, 404)]:
        with pytest.raises(api.app.HttpError) as caught:
            api.app.run_trace(ALICE, run)
        assert caught.value.status == status
    api.tables["events"].put_item(
        Item={"run_id": RUN, "sk": api.app.OWNER_SK, "customer_id": "customer-a"}
    )
    for who, status in [
        ({**ALICE, "customer_id": "customer-b"}, 404),
        ({**ALICE, "role": "customer"}, 403),
    ]:
        with pytest.raises(api.app.HttpError) as caught:
            api.app.run_trace(who, RUN)
        assert caught.value.status == status


@pytest.mark.parametrize(
    "failure,status,error",
    [
        ("describe", "error", "DescribeLogGroups: RuntimeError"),
        ("start", "error", "StartQuery: RuntimeError"),
        ("results", "error", "GetQueryResults: RuntimeError"),
        (None, "error", "Logs Insights: Failed"),
    ],
)
def test_query_failures_not_empty_success(api, monkeypatch, failure, status, error):
    logs = Logs(status="Failed" if failure is None else "Complete", failure=failure)
    result = setup(api, monkeypatch, logs)
    for s in result["sources"]:
        if s["key"] == "gateway" and failure in ("start", "results", None):
            assert s["status"] == "unavailable" and not s["records"]
        else:
            assert s["status"] == status and s["error"] == error and not s["records"]
    assert "secret failure details" not in str(result)


@pytest.mark.parametrize("state", ["Failed", "Cancelled", "Timeout"])
def test_terminal_query_state_is_an_error(api, monkeypatch, state):
    result = setup(api, monkeypatch, Logs(status=state))
    assert all(
        (
            s["status"] == "unavailable"
            if s["key"] == "gateway"
            else s["status"] == "error" and s["error"] == f"Logs Insights: {state}"
        )
        for s in result["sources"]
    )


def test_unfinished_query_is_reported_as_running(api, monkeypatch):
    logs = Logs(status="Running")
    clock = iter([100.0, 101.0, 121.0])
    monkeypatch.setattr(api.app.time, "time", lambda: next(clock, 121.0))
    monkeypatch.setattr(api.app.time, "sleep", lambda _: None)
    result = setup(api, monkeypatch, logs)
    assert all(
        s["status"] == ("unavailable" if s["key"] == "gateway" else "running") and not s["records"]
        for s in result["sources"]
    )


def test_missing_log_group_is_not_success(api, monkeypatch):
    logs = Logs()
    logs.describe_log_groups = lambda **kwargs: {"logGroups": []}
    result = setup(api, monkeypatch, logs)
    assert not logs.calls
    assert all(s["status"] == "no log group yet" for s in result["sources"])


def test_v5_code_reads_v4_enforce_gateway_policy_not_missing_v5_engine(api, monkeypatch):
    class PolicyClient:
        def list_policies(self, policyEngineId):
            assert policyEngineId == "engine-v4"
            return {"policies": [{"policyId": "fence"}]}

        def get_policy(self, policyEngineId, policyId):
            assert policyEngineId == "engine-v4" and policyId == "fence"
            return {
                "name": "tenant fence",
                "status": "ACTIVE",
                "definition": {"cedar": {"statement": "permit(...)"}},
            }

    params = {
        "policy_engines": {"v1": "engine-v1", "v3": "engine-v3", "v4": "engine-v4"},
        "gateways": {"v4": "gateway-v4"},
    }
    monkeypatch.setattr(api.app, "agent_param", lambda key, default: json.dumps(params[key]))
    monkeypatch.setattr(
        api.app.boto3,
        "client",
        lambda name: PolicyClient() if name == "bedrock-agentcore-control" else None,
    )
    code = api.app.build_code("v5")
    assert code["gateway"] == "gateway-v4" and code["policy_engine"] == "engine-v4"
    assert code["policies"] == [
        {"name": "tenant fence", "status": "ACTIVE", "statement": "permit(...)"}
    ]
    assert all(control["on"] for control in code["controls"])


def test_real_handler_never_returns_foreign_gateway_policy_from_quoted_run(api, monkeypatch):
    logs = Logs(
        gateway_rows=[
            row(
                **{
                    "trace_id": FOREIGN_TRACE,
                    "traceId": FOREIGN_TRACE,
                    "decision": "DENY",
                    "policies": '["foreign-policy"]',
                    "@message": f"requestBody={{reason={RUN}}}",
                }
            ),
        ]
    )
    setup(api, monkeypatch, logs)
    event = {
        "requestContext": {"http": {"method": "GET"}},
        "rawPath": "/api/trace",
        "queryStringParameters": {"run": RUN},
        "headers": {"x-ticketops-access-token": "offline"},
    }
    response = api.app.lambda_handler(event, None)
    assert response["statusCode"] == 200
    data = json.loads(response["body"])
    gateway = next(s for s in data["sources"] if s["key"] == "gateway")
    assert gateway["status"] == "partial" and gateway["discarded"] == 1
    assert not gateway["records"] and "foreign-policy" not in str(data)
    assert "@message" not in str(data)
    api.tables["events"].rows[(RUN, api.app.OWNER_SK)]["customer_id"] = "customer-b"
    assert api.app.lambda_handler(event, None)["statusCode"] == 404


def test_http_loopback_real_handler_to_shipped_browser_live_schema(api, monkeypatch):
    """Actual HTTP GET → lambda_handler → Logs mock → shipped DOM-VM CloudWatch drawer."""
    import shutil
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from pathlib import Path

    logs = Logs(
        span_rows=[
            row(
                **{
                    "session_id": RUN,
                    "traceId": TRACE,
                    "name": "execute_tool TicketTools___list_tickets",
                }
            )
        ],
        gateway_rows=[
            row(
                **{
                    "trace_id": TRACE,
                    "traceId": TRACE,
                    "decision": "ALLOW",
                    "policies": '["ticketops_v4_tenant_fence-0000000000"]',
                    "policy_ms": "101",
                    "@message": "secret request",
                }
            )
        ],
    )
    setup(api, monkeypatch, logs)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit

            parts = urlsplit(self.path)
            result = api.app.lambda_handler(
                {
                    "requestContext": {"http": {"method": "GET"}},
                    "rawPath": parts.path,
                    "queryStringParameters": {k: v[0] for k, v in parse_qs(parts.query).items()},
                },
                None,
            )
            body = result["body"].encode()
            self.send_response(result["statusCode"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    if not (node := shutil.which("node")):
        pytest.skip("node unavailable for offline browser renderer")
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rendered = subprocess.run(
            [node, "tests/trace_http.mjs", str(server.server_port), RUN],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )
        assert rendered.returncode == 0, rendered.stderr
        assert "live-schema projection passed" in rendered.stdout
        assert len(logs.calls) == 8  # four for setup, four for actual HTTP GET
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_handler_mixed_gateway_records_render_safe_in_browser(api, monkeypatch):
    """Handler → real drawer JS; a Java-map requestBody quoting the run is not authority."""
    import shutil
    import subprocess
    from pathlib import Path

    logs = Logs(
        span_rows=[
            row(
                **{
                    "session_id": RUN,
                    "traceId": TRACE,
                    "name": "execute_tool TicketTools___list_tickets",
                }
            )
        ],
        gateway_rows=[
            row(
                **{
                    "trace_id": FOREIGN_TRACE,
                    "traceId": FOREIGN_TRACE,
                    "decision": "DENY",
                    "policies": '["foreign-policy"]',
                    "requestBody": f"{{reason=secret request {RUN}}}",
                }
            ),
            row(
                **{
                    "trace_id": TRACE,
                    "traceId": TRACE,
                    "decision": "ALLOW",
                    "policies": '["ticketops_v4_tenant_fence-0000000000"]',
                    "policy_ms": "101",
                    "@message": "secret request",
                }
            ),
        ],
    )
    setup(api, monkeypatch, logs)
    response = api.app.lambda_handler(
        {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/api/trace",
            "queryStringParameters": {"run": RUN},
        },
        None,
    )
    assert response["statusCode"] == 200
    data = json.loads(response["body"])
    gateway = next(s for s in data["sources"] if s["key"] == "gateway")
    assert gateway["status"] == "partial" and gateway["matched"] == 1
    assert gateway["records"][0]["decision"] == "ALLOW"
    assert "foreign-policy" not in response["body"] and "requestBody" not in response["body"]
    if not (node := shutil.which("node")):
        pytest.skip("node unavailable for offline browser renderer")
    root = Path(__file__).resolve().parents[1]
    rendered = subprocess.run(
        [node, "tests/cloud_trace_handler.mjs"],
        input=response["body"],
        text=True,
        capture_output=True,
        cwd=root,
        timeout=15,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "verified partial/owned evidence: passed" in rendered.stdout


@pytest.mark.parametrize("stage", ["start", "results", "failed"])
def test_gateway_failure_after_verified_spans_is_explicit(api, monkeypatch, stage):
    class GatewayFailure(Logs):
        def start_query(self, **kw):
            if stage == "start" and "gateway" in kw["logGroupNames"][0]:
                raise RuntimeError("secret Gateway exception")
            return super().start_query(**kw)

        def get_query_results(self, queryId):
            group = self.calls[int(queryId) - 1]["logGroupNames"][0]
            if "gateway" in group:
                if stage == "results":
                    raise RuntimeError("secret Gateway exception")
                if stage == "failed":
                    return {"status": "Failed", "results": []}
            return super().get_query_results(queryId)

    result = setup(api, monkeypatch, GatewayFailure())
    source = result["sources"][1]
    assert source["status"] == "error" and not source["records"]
    assert (
        source["error"]
        == {
            "start": "StartQuery: RuntimeError",
            "results": "GetQueryResults: RuntimeError",
            "failed": "Logs Insights: Failed",
        }[stage]
    )
    assert "secret Gateway exception" not in str(result)


def test_trace_result_row_caps_announce_partial_gateway_coverage(api, monkeypatch):
    logs = Logs(
        span_rows=[row(**{"session_id": RUN, "traceId": TRACE})] * 80,
        gateway_rows=[row(**{"trace_id": TRACE, "traceId": TRACE, "decision": "ALLOW"})] * 50,
    )
    result = setup(api, monkeypatch, logs)
    assert result["sources"][2]["limit_reached"] is True
    gateway = result["sources"][1]
    assert gateway["status"] == "partial" and gateway["matched"] == 50
    assert "80-row cap" in gateway["reason"] and "50-row cap" in gateway["reason"]
