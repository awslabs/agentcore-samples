# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""KYC tool Lambda exposed through AgentCore Gateway as an MCP target.

Implements the five data-retrieval tools the KYC agents call during a corporate
onboarding assessment. Each tool reads from the bundled synthetic customer
dataset (data/CUSTxxx/*.json) so the demo is deterministic and needs no
external data providers.

Gateway invocation contract:
  - event carries the tool arguments directly (no HTTP envelope)
  - the fully-qualified tool name arrives on
    context.client_context.custom["bedrockAgentCoreToolName"], prefixed with
    the Gateway target name (e.g. "kyc-tools___sanctions_screen")
  - the response is an MCP content array; the Gateway owns the HTTP layer
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DATA_DIR = Path(__file__).parent / "data"

# Gateway prefixes tool names with "<target-name>___". Strip anything up to
# and including the delimiter to recover the bare tool name.
TOOL_NAME_DELIMITER = "___"


class ToolError(Exception):
    """Raised when a tool cannot fulfil the request."""


def _load(customer_id: str, dataset: str) -> dict[str, Any]:
    """Load one synthetic dataset for a customer.

    Args:
        customer_id: Customer identifier, e.g. "CUST001".
        dataset: Dataset basename without extension, e.g. "profile".

    Raises:
        ToolError: If the customer or dataset does not exist.
    """
    # Reject path traversal — customer_id lands in a filesystem path.
    if not customer_id.replace("_", "").isalnum():
        raise ToolError(f"Invalid customer_id: {customer_id!r}")

    path = DATA_DIR / customer_id.upper() / f"{dataset}.json"
    if not path.is_file():
        # Do not enumerate the known customer IDs in the error: that hands a
        # caller the full set of valid identifiers. Harmless with synthetic
        # fixtures, but the wrong habit for a tool that would front real data.
        raise ToolError(f"No {dataset} data for customer {customer_id!r}.")
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def get_customer_profile(customer_id: str) -> dict[str, Any]:
    """Return corporate identity, ownership, and directorship details."""
    profile = _load(customer_id, "profile")
    return {
        "customer_id": profile["customer_id"],
        "legal_name": profile["name"],
        "account_type": profile.get("account_type"),
        "industry": profile.get("industry"),
        "incorporation": profile.get("incorporation", {}),
        "annual_revenue": profile.get("annual_revenue"),
        "directors": profile.get("directors", []),
        "beneficial_owners": profile.get("beneficial_owners", []),
        "kyc_status": profile.get("kyc_status"),
        "risk_flags": profile.get("risk_flags", []),
    }


def credit_bureau_report(customer_id: str) -> dict[str, Any]:
    """Return credit score, facilities, payment history, and key ratios."""
    credit = _load(customer_id, "credit_history")
    financials = credit.get("financial_statements", {})
    payments = credit.get("payment_history", {})

    total_payments = payments.get("on_time_payments", 0) + payments.get(
        "late_payments", 0
    )
    on_time_rate = (
        round(payments.get("on_time_payments", 0) / total_payments * 100, 1)
        if total_payments
        else None
    )

    return {
        "customer_id": credit["customer_id"],
        "credit_score": credit.get("credit_score"),
        "credit_rating": credit.get("credit_rating"),
        "existing_facilities": credit.get("existing_facilities", []),
        "payment_history": payments,
        "on_time_payment_rate_pct": on_time_rate,
        "financial_statements": financials,
        # Surfaced explicitly because these three drive the credit score most.
        "key_ratios": {
            "debt_to_equity": financials.get("debt_to_equity"),
            "current_ratio": financials.get("current_ratio"),
            "net_income": financials.get("net_income"),
        },
        "credit_inquiries_last_12_months": credit.get(
            "credit_inquiries_last_12_months"
        ),
    }


def sanctions_screen(customer_id: str) -> dict[str, Any]:
    """Screen the entity, its directors, and owners against sanctions and PEP lists."""
    compliance = _load(customer_id, "compliance")
    sanctions = compliance.get("sanctions_screening", {})
    pep = compliance.get("pep_screening", {})

    return {
        "customer_id": compliance["customer_id"],
        "sanctions_screening": sanctions,
        "pep_screening": pep,
        "kyc_verification": compliance.get("kyc_verification", {}),
        "aml_risk_rating": compliance.get("aml_risk_rating"),
        "enhanced_due_diligence_required": compliance.get(
            "enhanced_due_diligence_required"
        ),
        "next_review_date": compliance.get("next_review_date"),
        # A non-clear result on either list must block straight-through approval.
        "requires_manual_review": (
            sanctions.get("status") != "clear" or pep.get("status") != "clear"
        ),
    }


def transaction_history(customer_id: str) -> dict[str, Any]:
    """Return transaction volumes, counterparties, and AML pattern flags."""
    transactions = _load(customer_id, "transactions")
    return {
        "customer_id": transactions["customer_id"],
        "summary": transactions.get("summary", {}),
        "top_counterparties": transactions.get("top_counterparties", []),
        "geographic_distribution": transactions.get("geographic_distribution", {}),
        "high_risk_jurisdictions": transactions.get("high_risk_jurisdictions", []),
        "suspicious_patterns": transactions.get("suspicious_patterns", []),
        "large_transactions_over_100k": transactions.get(
            "large_transactions_over_100k"
        ),
    }


def adverse_media_scan(customer_id: str) -> dict[str, Any]:
    """Return adverse media findings for the entity and its principals."""
    compliance = _load(customer_id, "compliance")
    adverse = compliance.get("adverse_media", {})
    findings = adverse.get("findings", [])

    return {
        "customer_id": compliance["customer_id"],
        "status": adverse.get("status"),
        "last_check": adverse.get("last_check"),
        "findings": findings,
        "finding_count": len(findings),
    }


TOOLS = {
    "get_customer_profile": get_customer_profile,
    "credit_bureau_report": credit_bureau_report,
    "sanctions_screen": sanctions_screen,
    "transaction_history": transaction_history,
    "adverse_media_scan": adverse_media_scan,
}


def _resolve_tool_name(context) -> str:
    """Extract the bare tool name from the Gateway's client context."""
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    raw = custom.get("bedrockAgentCoreToolName", "")
    if not raw:
        raise ToolError(
            "Missing bedrockAgentCoreToolName in client context — "
            "this Lambda must be invoked through AgentCore Gateway."
        )
    # "kyc-tools___sanctions_screen" -> "sanctions_screen"
    return raw.split(TOOL_NAME_DELIMITER)[-1]


def _content(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload in the MCP content envelope the Gateway expects."""
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def handler(event, context):
    """Route a Gateway tool invocation to the matching KYC tool."""
    try:
        tool_name = _resolve_tool_name(context)
    except ToolError as exc:
        logger.error("Tool resolution failed: %s", exc)
        return _content({"error": str(exc)})

    tool = TOOLS.get(tool_name)
    if tool is None:
        logger.error("Unknown tool %r", tool_name)
        return _content(
            {"error": f"Unknown tool {tool_name!r}. Available: {sorted(TOOLS)}"}
        )

    customer_id = (event or {}).get("customer_id")
    if not customer_id:
        return _content({"error": "customer_id is required"})

    logger.info("Invoking %s for %s", tool_name, customer_id)
    try:
        result = tool(customer_id)
    except ToolError as exc:
        logger.warning("%s failed: %s", tool_name, exc)
        return _content({"error": str(exc)})
    except Exception:
        logger.exception("%s raised unexpectedly", tool_name)
        return _content({"error": f"{tool_name} failed unexpectedly"})

    return _content({"tool": tool_name, "result": result})
