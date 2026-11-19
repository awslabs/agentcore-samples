"""Tests for outcomes.py: reading claim outcomes from runtime log records."""

import outcomes

SID = "ci-large-house-fire-" + "a" * 32


def record(message, session=SID):
    return f'{{"timestamp": "2026-01-01T00:00:00Z", "message": "{message}: ...", "sessionId": "{session}"}}'


def test_classify_reads_scenario_and_outcome():
    assert outcomes.classify(record("Claim created (auto-approved)")) == ("large-house-fire", "approved automatically")
    assert outcomes.classify(record("Human review requested")) == ("large-house-fire", "sent to human review")
    assert outcomes.classify(record("Rejection notification sent")) == ("large-house-fire", "rejected")


def test_classify_ignores_records_without_a_session_or_an_outcome():
    assert outcomes.classify("2026-01-01 INFO [main.py:366] - Claim created (auto-approved): ...") is None
    assert outcomes.classify(record("Memory unavailable")) is None


def test_tally_counts_each_session_once():
    other = "ci-unknown-policy-" + "b" * 32
    messages = [
        record("Claim created (auto-approved)"),
        record("Claim created (auto-approved)"),
        record("Rejection notification sent", other),
    ]
    counts = outcomes.tally(messages)
    assert counts["large-house-fire"]["approved automatically"] == 1
    assert counts["unknown-policy"]["rejected"] == 1
