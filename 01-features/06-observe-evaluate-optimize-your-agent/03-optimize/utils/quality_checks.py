"""Deterministic response checks for Continuous Insights replays."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

_INTERNAL_TAG = re.compile(
    r"</?(?:think|thinking|analysis|reasoning|response|final)\b[^>]*>",
    re.IGNORECASE,
)
_MISSING_INFORMATION = re.compile(
    r"\b(?:not found|not available|could not find|couldn't find|"
    r"unable to find|did not find|does not exist|does not recognize|"
    r"not recognized|could not be retrieved|couldn't be retrieved|"
    r"issue retrieving|do not have|don't have|no (?:record|information))\b",
    re.IGNORECASE,
)
_EMPLOYEE_ID = re.compile(r"\bEMP-[A-Z0-9-]+\b", re.IGNORECASE)
_PTO_DETAIL = re.compile(
    r"\b(?:accru(?:e|al)|rolls? over|business days|15 days|20 days)\b",
    re.IGNORECASE,
)
_BENEFIT_DETAIL = re.compile(
    r"\b(?:premium|deductible|blue shield|kaiser|health insurance)\b",
    re.IGNORECASE,
)


def evaluate_record(record: dict[str, Any]) -> dict[str, bool]:
    """Evaluate checks applicable to one captured session."""
    response = record.get("response_summary", "")
    scenario = record.get("scenario")
    checks = {
        "delivered": record.get("delivery_status") == "delivered",
        "non_empty_response": bool(response.strip()),
        "response_hygiene": not bool(_INTERNAL_TAG.search(response)),
    }
    if scenario == "success":
        checks["successful_pto_answer"] = (
            "10" in response and "remaining" in response.lower() and "pto" in response.lower()
        )
    elif scenario == "unknown_employee":
        mentioned_ids = {value.upper() for value in _EMPLOYEE_ID.findall(response)}
        checks["grounded_unknown_employee"] = bool(_MISSING_INFORMATION.search(response)) and mentioned_ids.issubset(
            {"EMP-999"}
        )
    elif scenario == "unknown_pay_stub":
        mentioned_ids = {value.upper() for value in _EMPLOYEE_ID.findall(response)}
        checks["grounded_missing_pay_stub"] = bool(_MISSING_INFORMATION.search(response)) and mentioned_ids.issubset(
            {"EMP-003"}
        )
    elif scenario == "unsupported_policy":
        checks["grounded_unsupported_policy"] = (
            "sabbatical" in response.lower()
            and bool(_MISSING_INFORMATION.search(response))
            and not bool(_PTO_DETAIL.search(response))
        )
    elif scenario == "unsupported_benefit":
        checks["grounded_unsupported_benefit"] = (
            "gym" in response.lower()
            and bool(_MISSING_INFORMATION.search(response))
            and not bool(_BENEFIT_DETAIL.search(response))
        )
    return checks


def summarize_records(
    records: Iterable[dict[str, Any]],
) -> dict[str, dict[str, float | int]]:
    """Return pass counts and rates for applicable checks."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for record in records:
        for name, passed in evaluate_record(record).items():
            counts[name][1] += 1
            counts[name][0] += int(passed)
    return {
        name: {
            "passed": passed,
            "total": total,
            "rate": passed / total if total else 0.0,
        }
        for name, (passed, total) in sorted(counts.items())
    }


def quality_gate_failures(
    summary: dict[str, dict[str, float | int]],
    minimum_rate: float = 0.95,
) -> list[str]:
    """Return checks that do not meet their acceptance threshold."""
    exact_checks = {"delivered", "non_empty_response", "response_hygiene"}
    return [
        name
        for name, result in summary.items()
        if float(result["rate"]) < (1.0 if name in exact_checks else minimum_rate)
    ]
