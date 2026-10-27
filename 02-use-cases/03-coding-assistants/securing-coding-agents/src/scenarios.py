"""Demo scenario runner for Gateway-level Cedar policy enforcement."""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from .utils import green, red


def run_gateway_scenarios(gateway_url: str, bearer_token: str, scenarios: list[dict]) -> list[dict]:
    """Run all Gateway scenarios and return results."""
    results = []
    for s in scenarios:
        result = _run_one(gateway_url, bearer_token, s)
        results.append(result)

        decision = result["decision"]
        name = result["scenario"]
        tool = result["tool"]
        reason = result.get("reason", "")

        if decision == "ALLOW":
            print(f"  {green('ALLOW')} {name}: {tool}")
        else:
            print(f"  {red('DENY')}  {name}: {tool}")
            if reason:
                print(f"        {reason}")

    return results


def _run_one(gateway_url: str, bearer_token: str, scenario: dict) -> dict:
    """Run a single scenario via JSON-RPC to the Gateway."""
    tool_name = f"{scenario['target']}___{scenario['tool']}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": scenario.get("input", {})},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {bearer_token}",
    }

    try:
        resp = requests.post(gateway_url, json=payload, headers=headers, timeout=30)
    except requests.ConnectionError:
        return {
            "scenario": scenario["name"],
            "tool": tool_name,
            "decision": "FAILED",
            "expected": scenario["expected"],
            "reason": "Connection error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Always try to parse JSON, regardless of status code
    resp_json = {}
    try:
        resp_json = resp.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        resp_json = {"error": {"message": f"Non-JSON response (HTTP {resp.status_code})"}}

    decision = "ALLOW" if resp.status_code == 200 and "error" not in resp_json else "DENY"

    reason = ""
    if decision == "DENY" and isinstance(resp_json, dict) and "error" in resp_json:
        err = resp_json["error"]
        reason = err.get("message", "") if isinstance(err, dict) else str(err)

    return {
        "scenario": scenario["name"],
        "tool": tool_name,
        "decision": decision,
        "expected": scenario["expected"],
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def print_summary(results: list[dict]) -> bool:
    """Print scenario summary with match/mismatch counts. Returns True if all passed."""
    matches = sum(1 for r in results if r["decision"] == r["expected"])
    total = len(results)
    mismatches = total - matches

    print(f"\n  Results: {matches}/{total} scenarios matched expected decision.")
    if mismatches:
        print("  Mismatches:")
        for r in results:
            if r["decision"] != r["expected"]:
                print(f"    {r['scenario']}: expected {r['expected']}, got {r['decision']}")

    return mismatches == 0
