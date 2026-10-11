"""Shared test fixtures for the Batch Payments Agent."""

import pytest


@pytest.fixture
def mock_402_response():
    """Mock x402 payment-required response from Spraay batch endpoint."""
    return {
        "status_code": 402,
        "headers": {
            "PAYMENT-REQUIRED": (
                "eyJzY2hlbWUiOiJleGFjdCIsIm5ldHdvcmsiOiJlaXAxNTU6ODQ1MyIs"
                "ImFzc2V0IjoiMHg4MzZlZmNlNmYzNjViOGUzMzliZjc3OTE2NjI1ZGIx"
                "IiwiYW1vdW50IjoiMTAwMDAiLCJyZWNpcGllbnQiOiIweDFhMmIzYzRk"
                "NWU2ZjdhOGI5YzBkMWUyZjNhNGI1YzZkN2U4ZjkiLCJ0aW1lb3V0Ij"
                "oxNzE5MDAwMDAwfQ=="
            ),
        },
        "body": {"error": "Payment required", "amount": "0.01 USDC"},
    }


@pytest.fixture
def mock_batch_success_response():
    """Mock successful batch transfer response after payment."""
    return {
        "status_code": 200,
        "body": {
            "status": "success",
            "transaction_hash": "0xabc123def456789...",
            "chain": "base",
            "batch_contract": "0x1646452F98E36A3c9Cfc3eDD8868221E207B5eEC",
            "recipients_count": 3,
            "recipients": [
                {"address": "0xAbc123...", "amount": "0.001", "status": "confirmed"},
                {"address": "0xDef456...", "amount": "0.001", "status": "confirmed"},
                {"address": "0x789abc...", "amount": "0.001", "status": "confirmed"},
            ],
            "total_amount": "0.003 ETH",
            "gas_used": 85000,
        },
    }


@pytest.fixture
def sample_batch_request():
    """Sample batch payment request body."""
    return {
        "chain": "base",
        "token": "ETH",
        "recipients": [
            {"address": "0xAbc123...", "amount": "0.001"},
            {"address": "0xDef456...", "amount": "0.001"},
            {"address": "0x789abc...", "amount": "0.001"},
        ],
    }


@pytest.fixture
def sample_payroll_request():
    """Sample payroll batch with mixed amounts."""
    return {
        "chain": "base",
        "token": "USDC",
        "recipients": [
            {"address": "0xAlice...", "amount": "500.00"},
            {"address": "0xBob...", "amount": "300.00"},
            {"address": "0xCarol...", "amount": "200.00"},
            {"address": "0xDave...", "amount": "450.00"},
            {"address": "0xEve...", "amount": "350.00"},
        ],
    }
