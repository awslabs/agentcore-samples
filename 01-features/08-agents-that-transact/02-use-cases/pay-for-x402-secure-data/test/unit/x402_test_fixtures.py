from __future__ import annotations


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self.responses.pop(0)


class FakeTrustClient:
    def __init__(self, responses=None, side_effect=None):
        self.responses = list(responses or [])
        self.side_effect = side_effect
        self.calls = []

    def score_endpoint(self, url, *, headers=None):
        self.calls.append((url, headers))
        if self.side_effect:
            raise self.side_effect
        return self.responses.pop(0)


class FakeServiceClient:
    def __init__(self, result=None):
        self.result = result or {"ok": True}
        self.calls = []

    def call_operation(self, operation, payload, *, headers=None):
        self.calls.append((operation, payload, headers))
        return self.result


def service_registry(client: FakeServiceClient | None = None):
    return {
        "heurist_yahoo_finance": {
            "base_url": "https://target.example/x402/yahoo",
            "operations": {
                "quote_snapshot": {"required": {"symbols"}},
                "price_history": {"required": {"symbols"}},
            },
            "client": client or FakeServiceClient(),
        },
        "paid_research_api": {
            "base_url": "https://target.example/x402/research",
            "operations": {
                "search": {"required": {"query"}},
            },
            "client": FakeServiceClient({"results": []}),
        },
    }
