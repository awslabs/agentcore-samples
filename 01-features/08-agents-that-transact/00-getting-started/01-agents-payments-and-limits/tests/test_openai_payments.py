"""Offline regression tests for the local and Runtime OpenAI payment paths."""

import importlib
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from agents import function_tool
from bedrock_agentcore.payments.manager import InsufficientBudget, PaymentError, PaymentSessionExpired

TUTORIAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TUTORIAL_DIR))
sys.path.insert(0, str(TUTORIAL_DIR.parent / "02-deploy-to-agentcore-runtime"))
tool = importlib.import_module("openai_x402_tool")
agent = importlib.import_module("openai_payment_agent")
runtime = importlib.import_module("openai_payment_runtime")

PUBLIC_IP = "93.184.216.34"
URL = "https://merchant.example:8443/paid?topic=news"


def dns_result(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 8443))


@pytest.fixture
def harness(monkeypatch):
    """Use real HTTPX request construction with fake DNS, HTTP, and payments."""
    manager = Mock()
    manager.generate_payment_header.return_value = {"X-PAYMENT": "test-proof"}
    monkeypatch.setattr(tool, "PaymentManager", Mock(return_value=manager))
    resolver = Mock(return_value=[dns_result(PUBLIC_IP)])
    monkeypatch.setattr(tool.socket, "getaddrinfo", resolver)
    requests = []
    client_options = []
    responses = []
    real_client = httpx.Client

    def respond(request):
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def client(**kwargs):
        client_options.append(kwargs)
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(tool.httpx, "Client", client)
    fetch = tool.build_x402_fetch("manager", "instrument", "session", "user", "us-east-1")
    return SimpleNamespace(
        manager=manager,
        resolver=resolver,
        requests=requests,
        options=client_options,
        responses=responses,
        fetch=fetch,
    )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "0.0.0.0",
        "::",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:93.184.216.34",
        "2001:db8::1",
    ],
)
def test_unsafe_dns_answer_blocks_all_requests(harness, address):
    # Reject the whole answer even if the first address is public.
    harness.resolver.return_value = [dns_result(PUBLIC_IP), dns_result(address)]
    result = json.loads(harness.fetch(URL))
    assert "non-public" in result["error"]
    assert result["payment_attempts"] == 0
    assert not harness.requests
    harness.manager.generate_payment_header.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://merchant.example/",
        "/relative",
        "https:///missing-host",
        "https://merchant.example:bad/",
        "https://merchant.example:65536/",
        "https://merchant.example:0/",
        "https://user:password@merchant.example/",
        "https://[fe80::1%25en0]/",
        "https://[not-an-ip]/",
    ],
)
def test_invalid_url_returns_an_error_before_dns(harness, url):
    assert "error" in json.loads(harness.fetch(url))
    harness.resolver.assert_not_called()
    assert not harness.requests


@pytest.mark.parametrize("answers", [[], socket.gaierror("DNS unavailable")])
def test_dns_failure_is_reported(harness, answers):
    if isinstance(answers, Exception):
        harness.resolver.side_effect = answers
    else:
        harness.resolver.return_value = answers
    assert json.loads(harness.fetch(URL))["error"] == "Cannot resolve hostname"
    assert not harness.requests


def test_paid_request_pins_dns_and_preserves_tls_host_without_cookies(harness):
    # A second hostname lookup would return an internal address.
    harness.resolver.side_effect = [[dns_result(PUBLIC_IP)], [dns_result("127.0.0.1")]]
    harness.responses.extend(
        [
            httpx.Response(402, headers={"set-cookie": "challenge=untrusted"}, text="payment challenge"),
            httpx.Response(200, text="paid content"),
        ]
    )
    result = json.loads(harness.fetch(URL))
    assert result == {"status_code": 200, "body": "paid content", "payment_made": True, "payment_attempts": 1}
    harness.resolver.assert_called_once()
    assert len(harness.requests) == 2
    for request in harness.requests:
        assert request.url.host == PUBLIC_IP
        assert request.url.port == 8443
        assert request.url.raw_path == b"/paid?topic=news"
        assert request.headers["host"] == "merchant.example:8443"
        assert request.extensions["sni_hostname"] == "merchant.example"
        assert "cookie" not in request.headers
    assert "x-payment" not in harness.requests[0].headers
    assert harness.requests[1].headers["x-payment"] == "test-proof"
    assert harness.options == [
        {"verify": True, "trust_env": False, "follow_redirects": False, "timeout": 30},
        {"verify": True, "trust_env": False, "follow_redirects": False, "timeout": 30},
    ]
    harness.manager.generate_payment_header.assert_called_once()
    params = harness.manager.generate_payment_header.call_args.kwargs
    assert params["payment_required_request"]["body"] == "payment challenge"
    assert params["payment_session_id"] == "session"
    assert params["client_token"]
    assert "test-proof" not in json.dumps(result)


def test_public_ipv6_is_supported(harness):
    harness.resolver.return_value = [dns_result("2606:4700:4700::1111")]
    harness.responses.append(httpx.Response(200))
    assert json.loads(harness.fetch(URL))["status_code"] == 200
    assert harness.requests[0].url.host == "2606:4700:4700::1111"


@pytest.mark.parametrize("status", [200, 302, 403, 500])
def test_no_payment_without_a_402_and_no_redirects(harness, status):
    harness.responses.append(httpx.Response(status, headers={"location": "https://127.0.0.1/"}))
    result = json.loads(harness.fetch(URL))
    assert result["status_code"] == status
    assert result["payment_made"] is False
    assert result["payment_attempts"] == 0
    assert len(harness.requests) == 1
    harness.manager.generate_payment_header.assert_not_called()


@pytest.mark.parametrize("status", [302, 402, 403, 500])
def test_no_second_payment_or_redirect_after_proof(harness, status):
    harness.responses.extend([httpx.Response(402), httpx.Response(status, headers={"location": "https://127.0.0.1/"})])
    result = json.loads(harness.fetch(URL))
    assert result["status_code"] == status
    assert result["payment_made"] is None
    assert result["payment_attempts"] == 1
    assert "unknown" in result["error"]
    assert len(harness.requests) == 2
    harness.manager.generate_payment_header.assert_called_once()


@pytest.mark.parametrize("paid", [False, True])
def test_timeouts_distinguish_before_and_after_payment(harness, paid):
    if paid:
        harness.responses.append(httpx.Response(402))
    harness.responses.append(httpx.ReadTimeout("request timed out"))
    result = json.loads(harness.fetch(URL))
    assert result["payment_made"] is (None if paid else False)
    assert result["payment_attempts"] == int(paid)
    assert ("after proof generation" if paid else "Initial merchant") in result["error"]
    assert harness.manager.generate_payment_header.call_count == int(paid)


@pytest.mark.parametrize("error", [InsufficientBudget("budget"), PaymentSessionExpired("expired")])
def test_payment_rejection_is_reported_without_replay(harness, error):
    harness.responses.append(httpx.Response(402))
    harness.manager.generate_payment_header.side_effect = error
    result = json.loads(harness.fetch(URL))
    assert type(error).__name__ in result["error"]
    assert result["payment_made"] is False
    assert len(harness.requests) == 1


def test_unknown_payment_error_does_not_claim_no_payment_or_expose_proof(harness):
    harness.responses.append(httpx.Response(402))
    harness.manager.generate_payment_header.side_effect = PaymentError("sensitive test-proof")
    result = json.loads(harness.fetch(URL))
    assert result["payment_made"] is None
    assert "test-proof" not in json.dumps(result)
    assert len(harness.requests) == 1


def test_missing_user_names_the_actual_config(harness):
    fetch = tool.build_x402_fetch("manager", "instrument", "session", "", "us-east-1")
    error = json.loads(fetch(URL))["error"]
    assert "USER_ID" in error and "user_id" in error
    assert "PAYMENT_USER_ID" not in error
    harness.resolver.assert_not_called()


def test_tool_schema_is_typed_and_read_only(harness):
    schema = function_tool(harness.fetch).params_json_schema
    assert schema["properties"]["url"]["type"] == "string"
    assert schema["properties"]["method"]["type"] == "string"
    assert schema["properties"]["method"]["const"] == "GET"
    assert schema["additionalProperties"] is False
    assert "Only GET" in json.loads(harness.fetch(URL, method="POST"))["error"]
    harness.resolver.assert_not_called()


def test_run_agent_returns_only_final_output(monkeypatch):
    monkeypatch.setattr(agent.Runner, "run_sync", Mock(return_value=SimpleNamespace(final_output="Answer")))
    assert agent.run_agent(Mock(), "prompt") == "Answer"


def test_model_disables_openai_tracing_and_uses_shared_defaults(monkeypatch):
    tracing = Mock()
    token = Mock(return_value="fake-token")
    client = Mock()
    monkeypatch.setattr(agent, "set_tracing_disabled", tracing)
    monkeypatch.setattr(agent, "provide_token", token)
    monkeypatch.setattr(agent, "AsyncOpenAI", client)
    model = agent.build_model()
    tracing.assert_called_once_with(True)
    token.assert_called_once_with(region=agent.DEFAULT_MODEL_REGION)
    assert model.model == agent.DEFAULT_MODEL_ID
    assert agent.DEFAULT_MODEL_REGION in client.call_args.kwargs["base_url"]


@pytest.mark.parametrize("custom", [False, True])
def test_local_and_runtime_share_model_settings(monkeypatch, custom):
    monkeypatch.setattr(agent, "load_dotenv", Mock())
    for name, value in {
        "PAYMENT_MANAGER_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:payment-manager/example",
        "INSTRUMENT_ID": "instrument",
        "USER_ID": "user",
    }.items():
        monkeypatch.setenv(name, value)
    for name, value in {"BEDROCK_OPENAI_MODEL_ID": "custom-model", "BEDROCK_OPENAI_MODEL_REGION": "us-west-2"}.items():
        if custom:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    config = agent.load_config()
    model = Mock()
    monkeypatch.setattr(runtime, "build_model", model)
    monkeypatch.setattr(runtime, "build_x402_fetch", Mock())
    monkeypatch.setattr(runtime, "build_agent", Mock())
    monkeypatch.setattr(runtime, "run_agent", Mock(return_value="Answer"))
    payload = {
        "prompt": "Fetch the endpoint",
        "payment_manager_arn": config.payment_manager_arn,
        "payment_session_id": "session",
        "payment_instrument_id": "instrument",
        "user_id": "user",
    }
    assert runtime.invoke({"prompt": json.dumps(payload)}) == {"result": "Answer"}
    model.assert_called_once_with(config.model_region, config.model_id)
    if not custom:
        assert (config.model_region, config.model_id) == (agent.DEFAULT_MODEL_REGION, agent.DEFAULT_MODEL_ID)


def test_runtime_rejects_missing_payment_context(monkeypatch):
    build = Mock()
    monkeypatch.setattr(runtime, "build_x402_fetch", build)
    assert "payment_session_id" in runtime.invoke({"prompt": "Fetch the endpoint"})["error"]
    build.assert_not_called()
