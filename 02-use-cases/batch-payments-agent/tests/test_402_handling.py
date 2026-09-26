"""Tests for x402 batch payment flow handling.

Validates that the agent correctly handles the HTTP 402 → payment → retry flow
when executing batch payments through Spraay x402 endpoints.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestX402BatchPaymentFlow:
    """Test the x402 payment flow for batch transfers."""

    def test_402_response_detected(self, mock_402_response):
        """Agent correctly identifies an HTTP 402 from the batch endpoint."""
        assert mock_402_response["status_code"] == 402
        assert "PAYMENT-REQUIRED" in mock_402_response["headers"]

    def test_x402_payload_extraction(self, mock_402_response):
        """Agent extracts x402 payment payload for AgentCore Payments."""
        import base64

        payload_b64 = mock_402_response["headers"]["PAYMENT-REQUIRED"]
        payload = json.loads(base64.b64decode(payload_b64))

        assert payload["scheme"] == "exact"
        assert payload["network"] == "eip155:8453"  # Base
        assert "amount" in payload
        assert "recipient" in payload
        assert "timeout" in payload

    def test_batch_success_after_payment(self, mock_batch_success_response):
        """Agent receives batch result with per-recipient confirmation."""
        assert mock_batch_success_response["status_code"] == 200
        body = mock_batch_success_response["body"]
        assert body["status"] == "success"
        assert "transaction_hash" in body
        assert body["recipients_count"] == 3
        assert all(r["status"] == "confirmed" for r in body["recipients"])

    def test_batch_contract_address(self, mock_batch_success_response):
        """Response references the correct Spraay batch contract."""
        body = mock_batch_success_response["body"]
        assert body["batch_contract"] == "0x1646452F98E36A3c9Cfc3eDD8868221E207B5eEC"

    def test_batch_cost_estimation(self):
        """estimate_batch_cost returns correct fee structure."""
        from agent.tools import estimate_batch_cost

        result = estimate_batch_cost(
            tool_use_id="test", recipient_count=10, operation="transfer"
        )
        assert result["status"] == "success"
        assert result["estimated_fee"]["currency"] == "USDC"
        assert result["comparison"]["individual_txs"] == 10
        assert result["comparison"]["batch_txs"] == 1

    def test_batch_cost_capped_at_max(self):
        """estimate_batch_cost caps at max fee for large batches."""
        from agent.tools import estimate_batch_cost

        result = estimate_batch_cost(
            tool_use_id="test", recipient_count=1000, operation="transfer"
        )
        assert result["estimated_fee"]["amount"] <= 0.05  # max for transfer

    def test_payroll_batch_cost(self):
        """estimate_batch_cost returns higher fees for payroll operations."""
        from agent.tools import estimate_batch_cost

        transfer = estimate_batch_cost(
            tool_use_id="test", recipient_count=5, operation="transfer"
        )
        payroll = estimate_batch_cost(
            tool_use_id="test", recipient_count=5, operation="payroll"
        )
        assert payroll["estimated_fee"]["amount"] > transfer["estimated_fee"]["amount"]

    def test_unknown_operation_error(self):
        """estimate_batch_cost returns error for unknown operations."""
        from agent.tools import estimate_batch_cost

        result = estimate_batch_cost(
            tool_use_id="test", recipient_count=5, operation="unknown"
        )
        assert result["status"] == "error"
        assert "available_operations" in result

    def test_supported_chains(self):
        """get_supported_chains returns correct chain info."""
        from agent.tools import get_supported_chains

        result = get_supported_chains(tool_use_id="test")
        assert result["status"] == "success"
        chains = result["chains"]
        assert chains["total_chains"] == 16

        primary_names = [c["name"] for c in chains["primary"]]
        assert "Base" in primary_names
        assert "Ethereum" in primary_names
        assert "Solana" in primary_names


class TestBatchPaymentRequest:
    """Test batch payment request construction."""

    def test_batch_request_structure(self, sample_batch_request):
        """Batch payment request has correct structure."""
        assert sample_batch_request["chain"] == "base"
        assert sample_batch_request["token"] == "ETH"
        assert len(sample_batch_request["recipients"]) == 3

    def test_batch_request_recipients(self, sample_batch_request):
        """Each recipient has address and amount."""
        for recipient in sample_batch_request["recipients"]:
            assert "address" in recipient
            assert "amount" in recipient
            assert recipient["address"].startswith("0x")

    def test_payroll_request_mixed_amounts(self, sample_payroll_request):
        """Payroll batch supports different amounts per recipient."""
        amounts = [float(r["amount"]) for r in sample_payroll_request["recipients"]]
        assert len(set(amounts)) > 1  # Not all the same amount
        assert sample_payroll_request["token"] == "USDC"
        assert len(sample_payroll_request["recipients"]) == 5

    def test_payroll_total(self, sample_payroll_request):
        """Payroll batch amounts sum correctly."""
        total = sum(float(r["amount"]) for r in sample_payroll_request["recipients"])
        assert total == 1800.00  # 500 + 300 + 200 + 450 + 350
