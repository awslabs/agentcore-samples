from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from bedrock_agentcore.payments.manager import InsufficientBudget, PaymentError
from payment import PaymentConfig, X402PaymentClient, _http_get, payment_region


@dataclass
class FakeResponse:
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)


class FakeTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def get(self, url: httpx.URL, address: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.requests.append({"url": str(url), "address": address, "headers": headers})
        return self.responses.pop(0)


class FakeManager:
    def __init__(self) -> None:
        self.header_calls: list[dict[str, Any]] = []

    def generate_payment_header(self, **kwargs: Any) -> dict[str, str]:
        self.header_calls.append(kwargs)
        return {"PAYMENT-SIGNATURE": "proof"}

    def get_payment_session(self, payment_session_id: str, user_id: str) -> dict[str, Any]:
        return {
            "limits": {"maxSpendAmount": {"value": "0.25", "currency": "USD"}},
            "availableLimits": {"availableSpendAmount": {"value": "0.20", "currency": "USD"}},
            "expiryTimeInMinutes": 60,
            "paymentSessionId": payment_session_id,
            "userId": user_id,
        }


def config(**overrides: Any) -> PaymentConfig:
    values = {
        "manager_arn": "arn:manager",
        "instrument_id": "instrument",
        "session_id": "session",
        "user_id": "user",
        "region": "us-east-1",
        "allowed_hosts": frozenset({"merchant.example"}),
    }
    values.update(overrides)
    return PaymentConfig(**values)


def public_resolver(_host: str, _port: int) -> list[str]:
    return ["8.8.8.8"]


def test_returns_free_content_without_payment() -> None:
    transport = FakeTransport([FakeResponse(200, '{"source":"public"}')])
    manager = FakeManager()
    client = X402PaymentClient(
        config(),
        manager,
        get=transport.get,
        resolver=public_resolver,
    )

    result = json.loads(client.fetch("https://merchant.example/data"))

    assert result["ok"] is True
    assert result["source_url"] == "https://merchant.example/data"
    assert result["payment_made"] is False
    assert manager.header_calls == []


def test_settles_402_and_uses_version_aware_sdk_header() -> None:
    challenge = json.dumps(
        {
            "x402Version": 2,
            "accepts": [{"network": "eip155:84532", "amount": "1000", "asset": "USDC"}],
        }
    )
    transport = FakeTransport(
        [
            FakeResponse(402, challenge, {"payment-required": "challenge"}),
            FakeResponse(200, '{"premium":"evidence"}'),
        ]
    )
    manager = FakeManager()
    client = X402PaymentClient(
        config(),
        manager,
        get=transport.get,
        resolver=public_resolver,
        token_factory=lambda: "stable-token",
    )

    result = json.loads(client.fetch("https://merchant.example/data"))

    assert result["payment_made"] is True
    assert result["payment_attempts"] == 1
    assert transport.requests[1]["headers"] == {"PAYMENT-SIGNATURE": "proof"}
    assert manager.header_calls[0]["client_token"] == "stable-token"


def test_does_not_sign_or_fetch_again_when_merchant_still_returns_402() -> None:
    transport = FakeTransport(
        [
            FakeResponse(402, '{"x402Version":2,"accepts":[]}'),
            FakeResponse(402, '{"x402Version":2,"accepts":[]}'),
        ]
    )
    manager = FakeManager()
    client = X402PaymentClient(
        config(),
        manager,
        get=transport.get,
        resolver=public_resolver,
        token_factory=lambda: "one-token",
    )

    result = json.loads(client.fetch("https://merchant.example/data"))

    assert result["payment_attempts"] == 1
    assert result["payment_made"] is None
    assert result["ok"] is False
    assert json.loads(client.fetch("https://merchant.example/data")) == result
    assert len(manager.header_calls) == 1
    assert len(transport.requests) == 2


def test_blocks_unapproved_hosts_before_network_access() -> None:
    transport = FakeTransport([])
    client = X402PaymentClient(
        config(),
        FakeManager(),
        get=transport.get,
        resolver=public_resolver,
    )

    result = json.loads(client.fetch("https://unapproved.example/data"))

    assert result["ok"] is False
    assert "not approved" in result["error"]
    assert transport.requests == []


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "::1", "224.0.0.1", "::ffff:8.8.8.8"])
def test_blocks_private_dns_results(address: str) -> None:
    transport = FakeTransport([])
    client = X402PaymentClient(
        config(),
        FakeManager(),
        get=transport.get,
        resolver=lambda _host, _port: [address],
    )

    result = json.loads(client.fetch("https://merchant.example/data"))

    assert result["ok"] is False
    assert "private or non-routable" in result["error"]
    assert transport.requests == []


def test_session_status_exposes_budget_not_resource_ids() -> None:
    client = X402PaymentClient(config(), FakeManager(), resolver=public_resolver)

    result = json.loads(client.session_status())

    assert result["maximum_spend"] == "0.25"
    assert result["available_spend"] == "0.20"
    assert "session" not in result
    assert "instrument" not in result


@pytest.mark.parametrize("status", [200, 302, 402, 503])
def test_repeated_tool_calls_reuse_the_terminal_result(status: int) -> None:
    transport = FakeTransport([FakeResponse(402, "{}"), FakeResponse(status, "merchant result")])
    manager = FakeManager()
    client = X402PaymentClient(config(), manager, get=transport.get, resolver=public_resolver)

    first = client.fetch("https://merchant.example/data")
    second = client.fetch("https://merchant.example/data")

    assert first == second
    assert len(transport.requests) == 2
    assert len(manager.header_calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://merchant.example/data",
        "https://user:password@merchant.example/data",
        "https://merchant.example:0/data",
        "https://merchant.example:99999/data",
        "https://merchant.example:invalid/data",
        "https://[invalid/data",
    ],
)
def test_invalid_urls_fail_before_transport_or_payment(url: str) -> None:
    transport = FakeTransport([])
    manager = FakeManager()
    client = X402PaymentClient(config(), manager, get=transport.get, resolver=public_resolver)

    result = json.loads(client.fetch(url))

    assert result["ok"] is False
    assert result["payment_made"] is False
    assert result["payment_attempts"] == 0
    assert transport.requests == []
    assert manager.header_calls == []


def test_pins_the_same_address_for_the_challenge_and_paid_request() -> None:
    transport = FakeTransport([FakeResponse(402, "{}"), FakeResponse(200, "paid")])
    resolutions = []

    def resolver(host, port):
        resolutions.append((host, port))
        return ["8.8.8.8"] if len(resolutions) == 1 else ["127.0.0.1"]

    client = X402PaymentClient(config(), FakeManager(), get=transport.get, resolver=resolver)
    result = json.loads(client.fetch("https://merchant.example/data"))

    assert result["payment_made"] is True
    assert resolutions == [("merchant.example", 443)]
    assert [request["address"] for request in transport.requests] == ["8.8.8.8", "8.8.8.8"]


def test_transport_preserves_tls_hostname_and_does_not_reuse_cookies(monkeypatch) -> None:
    requests = []
    real_client = httpx.Client

    def handler(request):
        requests.append(request)
        return httpx.Response(402, headers={"set-cookie": "challenge=initial"}, text="{}")

    def client_factory(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["verify"] is True
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("payment.httpx.Client", client_factory)
    url = httpx.URL("https://merchant.example:8443/data?q=AMZN")
    _http_get(url, "8.8.8.8")
    _http_get(url, "8.8.8.8", {"PAYMENT-SIGNATURE": "proof"})

    assert all(request.url.host == "8.8.8.8" for request in requests)
    assert all(request.headers["host"] == "merchant.example:8443" for request in requests)
    assert all(request.extensions["sni_hostname"] == "merchant.example" for request in requests)
    assert all("cookie" not in request.headers for request in requests)
    assert requests[1].headers["PAYMENT-SIGNATURE"] == "proof"


def test_budget_rejection_stops_without_requesting_paid_content(monkeypatch) -> None:
    transport = FakeTransport([FakeResponse(402, "{}")])
    manager = FakeManager()

    def reject(**kwargs):
        raise InsufficientBudget("provider details must not reach the model")

    monkeypatch.setattr(manager, "generate_payment_header", reject)
    client = X402PaymentClient(config(), manager, get=transport.get, resolver=public_resolver)

    result = json.loads(client.fetch("https://merchant.example/data"))

    assert result["payment_made"] is False
    assert result["error"] == "Payment rejected: InsufficientBudget"
    assert len(transport.requests) == 1


def test_lost_paid_response_is_unknown_and_never_retried() -> None:
    requests = []
    manager = FakeManager()

    def get(url, address, headers):
        requests.append(headers)
        if headers:
            raise httpx.ReadTimeout("lost response")
        return FakeResponse(402, "{}")

    client = X402PaymentClient(config(), manager, get=get, resolver=public_resolver)
    result = json.loads(client.fetch("https://merchant.example/data"))

    assert result["payment_made"] is None
    assert result["payment_attempts"] == 1
    assert json.loads(client.fetch("https://merchant.example/data")) == result
    assert len(requests) == 2
    assert len(manager.header_calls) == 1


def test_payment_sdk_failure_does_not_expose_exception_details(monkeypatch) -> None:
    manager = FakeManager()

    def fail(**kwargs):
        raise PaymentError("provider-specific private details")

    monkeypatch.setattr(manager, "generate_payment_header", fail)
    transport = FakeTransport([FakeResponse(402, "{}")])
    client = X402PaymentClient(config(), manager, get=transport.get, resolver=public_resolver)

    result = client.fetch("https://merchant.example/data")

    assert "private details" not in result
    assert json.loads(result)["payment_made"] is None
    assert len(transport.requests) == 1


def test_payment_region_is_independent_of_model_region(monkeypatch) -> None:
    arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:payment-manager/sample"
    for name, value in {
        "PAYMENT_MANAGER_ARN": arn,
        "PAYMENT_INSTRUMENT_ID": "instrument",
        "PAYMENT_SESSION_ID": "session",
        "PAYMENT_USER_ID": "user",
        "PAID_RESEARCH_ALLOWED_HOSTS": "merchant.example",
        "AWS_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)

    assert PaymentConfig.from_env().region == "us-west-2"
    assert payment_region(arn) == "us-west-2"
