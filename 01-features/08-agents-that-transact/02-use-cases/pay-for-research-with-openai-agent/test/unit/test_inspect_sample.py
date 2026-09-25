import json

from inspect_sample import inspect_sample


def test_inspection_builds_three_agents_without_credentials_or_payments(monkeypatch) -> None:
    monkeypatch.setenv("PAID_RESEARCH_URL", "https://merchant.example/data")
    monkeypatch.setenv("PAYMENT_MANAGER_ARN", "do-not-display-this-value")
    monkeypatch.setenv("BEDROCK_OPENAI_WEB_SEARCH_ENABLED", "false")
    report = inspect_sample("Assess AMZN")

    assert report["mode"] == "offline"
    assert report["payment_configuration_present"]["PAYMENT_MANAGER_ARN"] is True
    assert "do-not-display-this-value" not in json.dumps(report)
    assert report["team"]["lead_tools"] == ["research_public_evidence", "research_premium_evidence"]
    assert report["team"]["premium_tools"] == ["fetch_approved_premium_source", "payment_session_status"]
    assert report["team"]["public_tools"] == []


def test_inspection_can_disable_the_configured_premium_source(monkeypatch) -> None:
    monkeypatch.setenv("PAID_RESEARCH_URL", "https://merchant.example/data")
    report = inspect_sample("Assess AMZN", public_only=True)

    assert report["team"]["lead_tools"] == ["research_public_evidence"]
    assert report["team"]["premium_tools"] == []
