from __future__ import annotations

import os
from typing import Any

DEFAULT_HEURIST_BASE_URL = "https://mesh.heurist.xyz/x402/agents/YahooFinanceAgent"
DEFAULT_X402_TRUST_THRESHOLD = 50
DEFAULT_X402_TRUST_CACHE_TTL_SECONDS = 300
DEFAULT_X402_TRUST_FAIL_CLOSED = True
DEFAULT_SERVICE_ID = "heurist_yahoo_finance"

HEURIST_YAHOO_FINANCE_OPERATIONS: dict[str, dict[str, Any]] = {
    "resolve_symbol": {
        "required": {"query"},
        "description": "Resolve a company name, ticker fragment, or market term.",
    },
    "quote_snapshot": {
        "required": {"symbols"},
        "description": "Fetch a compact quote snapshot for one or more exact symbols.",
    },
    "price_history": {
        "required": {"symbols"},
        "description": "Fetch normalized OHLCV price history.",
    },
    "technical_snapshot": {
        "required": {"symbols"},
        "description": "Fetch a technical-analysis snapshot for exact symbols.",
    },
    "options_expirations": {
        "required": {"symbol"},
        "description": "Fetch available options expirations for an underlying.",
    },
    "options_chain": {
        "required": {"symbol", "expiration"},
        "description": "Fetch an options chain snapshot for one expiration.",
    },
    "futures_snapshot": {
        "required": {"symbols"},
        "description": "Fetch a futures snapshot with recent history context.",
    },
    "news_search": {
        "required": {"query"},
        "description": "Search recent headlines and source URLs.",
    },
    "market_overview": {
        "required": {"market"},
        "description": "Fetch a compact benchmark summary for one market region.",
    },
    "company_fundamentals": {
        "required": {"symbols"},
        "description": "Fetch equity fundamentals.",
    },
    "analyst_snapshot": {
        "required": {"symbols"},
        "description": "Fetch analyst ratings, targets, and estimate trends.",
    },
    "fund_snapshot": {
        "required": {"symbols"},
        "description": "Fetch ETF or mutual-fund holdings and exposures.",
    },
    "equity_screen": {
        "required": {"screen_name"},
        "description": "Run a curated equity screen.",
    },
}


def resolve_heurist_base_url() -> str:
    return os.environ.get("HEURIST_YAHOO_FINANCE_BASE_URL", DEFAULT_HEURIST_BASE_URL)


def resolve_x402_trust_threshold() -> int:
    return int(os.environ.get("X402_TRUST_THRESHOLD", str(DEFAULT_X402_TRUST_THRESHOLD)))


def resolve_x402_trust_cache_ttl_seconds() -> int:
    return int(
        os.environ.get(
            "X402_TRUST_CACHE_TTL_SECONDS",
            str(DEFAULT_X402_TRUST_CACHE_TTL_SECONDS),
        )
    )


def resolve_x402_trust_fail_closed() -> bool:
    return os.environ.get("X402_TRUST_FAIL_CLOSED", "1") != "0"


def default_x402_service_registry() -> dict[str, dict[str, Any]]:
    return {
        DEFAULT_SERVICE_ID: {
            "base_url": resolve_heurist_base_url(),
            "operations": HEURIST_YAHOO_FINANCE_OPERATIONS,
        }
    }


def supported_service_ids() -> list[str]:
    return sorted(default_x402_service_registry())


def supported_service_operations(service_id: str = DEFAULT_SERVICE_ID) -> list[str]:
    registry = default_x402_service_registry()
    service = registry.get(service_id)
    if service is None:
        raise ValueError(f"Unsupported x402 service: {service_id}")
    return sorted(service["operations"])


def validate_service_operation(
    service_id: str,
    operation: str,
    payload: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> None:
    spec = operations.get(operation)
    if spec is None:
        raise ValueError(f"Unsupported operation for x402 service {service_id}: {operation}")

    missing = sorted(field for field in spec["required"] if payload.get(field) in (None, "", [], {}))
    if missing:
        raise ValueError(f"Missing required parameters for {service_id}.{operation}: {', '.join(missing)}")
