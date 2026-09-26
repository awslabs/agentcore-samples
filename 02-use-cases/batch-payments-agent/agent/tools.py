"""Spraay x402 batch payment tools for AgentCore agents.

Primary tools for multi-recipient batch payments — the core capability.
Supporting tools for discovery, pricing, and chain info.

x402 payment flow:
1. Call a paid endpoint → receive HTTP 402 with payment details
2. AgentCore Payments plugin handles the micropayment automatically
3. Retry with payment proof → receive the batch transaction result
"""

import json
import logging
from typing import Any

import httpx
from strands.types.tools import tool

from agent.config import AgentConfig

logger = logging.getLogger(__name__)
config = AgentConfig()


# ---------------------------------------------------------------------------
# PRIMARY TOOLS — Batch Payments
# ---------------------------------------------------------------------------


@tool
def batch_transfer(
    chain: str,
    token: str,
    recipients: str,
) -> dict[str, Any]:
    """Execute a multi-recipient batch payment in a single blockchain transaction.

    This is the core capability: pay N wallets atomically in one tx instead of N
    separate transactions. All recipients succeed or all fail — no partial transfers.

    Spraay's batch contract handles gas optimization and atomic execution.
    The agent pays a single x402 micropayment ($0.01–$0.05 USDC) as a service fee.

    Args:
        chain: Target blockchain (e.g., 'base', 'ethereum', 'arbitrum').
        token: Token to send ('ETH', 'USDC', or any ERC-20 contract address).
        recipients: JSON array of recipient objects, each with 'address' and 'amount'.
            Example: '[{"address": "0xAbc...", "amount": "0.001"}, ...]'

    Returns:
        On 402: x402 payment details for AgentCore Payments to process.
        On success: transaction hash, per-recipient status, gas used.
    """
    url = f"{config.spraay_gateway_url}/api/v1/batch/transfer"
    headers = {"Content-Type": "application/json"}

    parsed_recipients = json.loads(recipients) if isinstance(recipients, str) else recipients

    body = {
        "chain": chain.lower(),
        "token": token,
        "recipients": parsed_recipients,
    }

    try:
        response = httpx.post(url, json=body, headers=headers, timeout=60)

        if response.status_code == 402:
            payment_header = response.headers.get(
                "PAYMENT-REQUIRED",
                response.headers.get("X-Payment-Required", ""),
            )
            recipient_count = len(parsed_recipients)
            return {
                "status": "payment_required",
                "http_status": 402,
                "x402_payload": payment_header,
                "url": url,
                "recipient_count": recipient_count,
                "message": (
                    f"Batch payment for {recipient_count} recipients requires an "
                    f"x402 micropayment. Use AgentCore Payments to complete the "
                    f"transaction, then retry with the payment proof header."
                ),
            }

        if response.status_code == 200:
            return {"status": "success", "data": response.json()}

        return {
            "status": "error",
            "http_status": response.status_code,
            "message": response.text,
        }

    except httpx.RequestError as e:
        return {"status": "error", "message": str(e)}


@tool
def batch_transfer_with_payment(
    chain: str,
    token: str,
    recipients: str,
    payment_proof: str,
) -> dict[str, Any]:
    """Retry a batch transfer with x402 payment proof after payment is complete.

    Call this after AgentCore Payments has processed the micropayment from
    the initial batch_transfer call.

    Args:
        chain: Target blockchain (e.g., 'base', 'ethereum').
        token: Token to send ('ETH', 'USDC', etc.).
        recipients: JSON array of recipient objects (same as batch_transfer).
        payment_proof: Payment proof string from AgentCore Payments.

    Returns:
        Transaction hash, per-recipient confirmation, gas used.
    """
    url = f"{config.spraay_gateway_url}/api/v1/batch/transfer"
    headers = {
        "Content-Type": "application/json",
        "X-PAYMENT": payment_proof,
    }

    parsed_recipients = json.loads(recipients) if isinstance(recipients, str) else recipients
    body = {
        "chain": chain.lower(),
        "token": token,
        "recipients": parsed_recipients,
    }

    try:
        response = httpx.post(url, json=body, headers=headers, timeout=60)

        if response.status_code == 200:
            return {"status": "success", "data": response.json()}

        return {
            "status": "error",
            "http_status": response.status_code,
            "message": response.text,
        }

    except httpx.RequestError as e:
        return {"status": "error", "message": str(e)}


@tool
def estimate_batch_cost(
    recipient_count: int,
    operation: str = "transfer",
) -> dict[str, Any]:
    """Estimate the x402 service fee for a batch operation.

    This is the Spraay service fee paid via x402, not the on-chain gas cost.
    Gas is included in the batch execution and optimized by Spraay's contract.

    Args:
        recipient_count: Number of recipients in the batch.
        operation: Type of batch ('transfer', 'payroll', 'escrow').

    Returns:
        Estimated service fee in USDC and per-recipient breakdown.
    """
    pricing = {
        "transfer": {"base": 0.01, "per_recipient": 0.001, "max": 0.05},
        "payroll": {"base": 0.05, "per_recipient": 0.002, "max": 0.25},
        "escrow": {"base": 0.05, "per_recipient": 0.003, "max": 0.25},
    }

    op = operation.lower()
    if op not in pricing:
        return {
            "status": "error",
            "message": f"Unknown operation: {operation}",
            "available_operations": list(pricing.keys()),
        }

    tier = pricing[op]
    estimated = min(
        tier["base"] + (tier["per_recipient"] * recipient_count),
        tier["max"],
    )

    return {
        "status": "success",
        "operation": operation,
        "recipient_count": recipient_count,
        "estimated_fee": {
            "amount": round(estimated, 4),
            "currency": "USDC",
            "note": "x402 service fee — on-chain gas is included by Spraay",
        },
        "comparison": {
            "individual_txs": recipient_count,
            "batch_txs": 1,
            "savings": f"{recipient_count - 1} fewer transactions",
        },
    }


# ---------------------------------------------------------------------------
# SUPPORTING TOOLS — Discovery, Pricing, Chain Info
# ---------------------------------------------------------------------------


@tool
def discover_spraay_services() -> dict[str, Any]:
    """Discover available paid services from the Spraay x402 gateway.

    Returns a list of available endpoint categories, pricing, and supported
    blockchain networks. Use this to find out what services are available
    before calling specific endpoints.
    """
    try:
        response = httpx.get(
            f"{config.spraay_gateway_url}/api/v1/categories",
            timeout=30,
        )
        if response.status_code == 200:
            return {
                "status": "success",
                "categories": response.json(),
                "gateway_url": config.spraay_gateway_url,
                "note": "Use request_spraay_endpoint to call specific endpoints.",
            }
        return {
            "status": "error",
            "code": response.status_code,
            "message": response.text,
        }
    except httpx.RequestError as e:
        return {"status": "error", "message": str(e)}


@tool
def request_spraay_endpoint(
    method: str,
    path: str,
    body: str = "",
) -> dict[str, Any]:
    """Call a Spraay x402 gateway endpoint. Handles the x402 payment flow.

    If the endpoint returns HTTP 402, the response includes the x402 payment
    details needed by AgentCore Payments to complete the transaction. The
    AgentCore Payments plugin will automatically handle payment and retry.

    Args:
        method: HTTP method (GET or POST).
        path: API path (e.g., '/api/v1/batch/transfer').
        body: JSON request body for POST requests.

    Returns:
        The endpoint response, or x402 payment details if payment is required.
    """
    url = f"{config.spraay_gateway_url}{path}"
    headers = {"Content-Type": "application/json"}

    try:
        if method.upper() == "POST" and body:
            parsed_body = json.loads(body) if isinstance(body, str) else body
            response = httpx.post(url, json=parsed_body, headers=headers, timeout=60)
        else:
            response = httpx.get(url, headers=headers, timeout=60)

        # x402 Payment Required — return details for AgentCore Payments
        if response.status_code == 402:
            payment_header = response.headers.get(
                "PAYMENT-REQUIRED",
                response.headers.get("X-Payment-Required", ""),
            )
            return {
                "status": "payment_required",
                "http_status": 402,
                "x402_payload": payment_header,
                "url": url,
                "message": (
                    "This endpoint requires an x402 micropayment. "
                    "Use AgentCore Payments to complete the transaction, "
                    "then retry with the payment proof header."
                ),
            }

        # Successful response
        if response.status_code == 200:
            try:
                return {
                    "status": "success",
                    "data": response.json(),
                }
            except json.JSONDecodeError:
                return {
                    "status": "success",
                    "data": response.text,
                }

        return {
            "status": "error",
            "http_status": response.status_code,
            "message": response.text,
        }

    except httpx.RequestError as e:
        return {"status": "error", "message": str(e)}


@tool
def request_spraay_endpoint_with_payment(
    method: str,
    path: str,
    payment_proof: str,
    body: str = "",
) -> dict[str, Any]:
    """Retry a Spraay endpoint with x402 payment proof after payment is complete.

    Args:
        method: HTTP method (GET or POST).
        path: API path (e.g., '/api/v1/batch/transfer').
        payment_proof: The payment proof string from AgentCore Payments.
        body: JSON request body for POST requests.

    Returns:
        The endpoint response after payment verification.
    """
    url = f"{config.spraay_gateway_url}{path}"
    headers = {
        "Content-Type": "application/json",
        "X-PAYMENT": payment_proof,
    }

    try:
        if method.upper() == "POST" and body:
            parsed_body = json.loads(body) if isinstance(body, str) else body
            response = httpx.post(url, json=parsed_body, headers=headers, timeout=60)
        else:
            response = httpx.get(url, headers=headers, timeout=60)

        if response.status_code == 200:
            try:
                return {"status": "success", "data": response.json()}
            except json.JSONDecodeError:
                return {"status": "success", "data": response.text}

        return {
            "status": "error",
            "http_status": response.status_code,
            "message": response.text,
        }

    except httpx.RequestError as e:
        return {"status": "error", "message": str(e)}


@tool
def get_supported_chains() -> dict[str, Any]:
    """Get the list of blockchain networks supported by Spraay.

    Returns supported chains with their chain IDs and capabilities.
    """
    # Spraay's known supported chains — primary + secondary
    chains = {
        "primary": [
            {"name": "Base", "chain_id": 8453, "native_token": "ETH"},
            {"name": "Ethereum", "chain_id": 1, "native_token": "ETH"},
            {"name": "Solana", "chain_id": "solana", "native_token": "SOL"},
        ],
        "secondary": [
            {"name": "Arbitrum", "chain_id": 42161, "native_token": "ETH"},
            {"name": "Optimism", "chain_id": 10, "native_token": "ETH"},
            {"name": "Polygon", "chain_id": 137, "native_token": "MATIC"},
            {"name": "Avalanche", "chain_id": 43114, "native_token": "AVAX"},
            {"name": "BSC", "chain_id": 56, "native_token": "BNB"},
            {"name": "Fantom", "chain_id": 250, "native_token": "FTM"},
            {"name": "Gnosis", "chain_id": 100, "native_token": "xDAI"},
            {"name": "Celo", "chain_id": 42220, "native_token": "CELO"},
            {"name": "Linea", "chain_id": 59144, "native_token": "ETH"},
            {"name": "Scroll", "chain_id": 534352, "native_token": "ETH"},
            {"name": "zkSync", "chain_id": 324, "native_token": "ETH"},
            {"name": "Canton Network", "chain_id": "canton", "native_token": "Canton"},
        ],
        "total_chains": 16,
        "total_endpoints": 170,
        "payment_network": "Base (USDC via x402)",
    }
    return {"status": "success", "chains": chains}


@tool
def estimate_spraay_cost(
    endpoint_category: str,
    num_calls: int = 1,
) -> dict[str, Any]:
    """Estimate the cost of calling Spraay endpoints.

    Args:
        endpoint_category: Category name (e.g., 'batch_payments', 'pricing',
            'wallet', 'defi', 'research', 'rpc').
        num_calls: Number of calls to estimate for.

    Returns:
        Estimated cost in USDC.
    """
    # Spraay's pricing tiers
    pricing = {
        "batch_payments": {"min": 0.01, "max": 0.05, "unit": "per batch tx"},
        "escrow": {"min": 0.05, "max": 0.25, "unit": "per escrow op"},
        "bridge": {"min": 0.05, "max": 0.25, "unit": "per bridge tx"},
        "payroll": {"min": 0.05, "max": 0.25, "unit": "per payroll batch"},
        "pricing": {"min": 0.001, "max": 0.005, "unit": "per price query"},
        "wallet": {"min": 0.001, "max": 0.005, "unit": "per wallet query"},
        "defi": {"min": 0.005, "max": 0.01, "unit": "per defi query"},
        "research": {"min": 0.005, "max": 0.01, "unit": "per search"},
        "rpc": {"min": 0.001, "max": 0.005, "unit": "per rpc call"},
        "oracle": {"min": 0.005, "max": 0.01, "unit": "per oracle query"},
        "ai_inference": {"min": 0.03, "max": 0.05, "unit": "per inference"},
        "compute_futures": {"min": 0.01, "max": 0.05, "unit": "per contract"},
    }

    category = endpoint_category.lower().replace(" ", "_")
    if category not in pricing:
        return {
            "status": "error",
            "message": f"Unknown category: {endpoint_category}",
            "available_categories": list(pricing.keys()),
        }

    tier = pricing[category]
    return {
        "status": "success",
        "category": endpoint_category,
        "price_range": f"${tier['min']:.3f} – ${tier['max']:.3f} {tier['unit']}",
        "estimated_total": {
            "min": round(tier["min"] * num_calls, 4),
            "max": round(tier["max"] * num_calls, 4),
            "currency": "USDC",
            "num_calls": num_calls,
        },
    }
