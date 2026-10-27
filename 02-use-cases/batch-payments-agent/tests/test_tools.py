"""Unit tests for Spraay x402 batch payment and supporting tools."""

from unittest.mock import MagicMock, patch

import pytest


class TestEstimateBatchCost:
    """Tests for the batch-specific cost estimator."""

    def test_small_batch_transfer(self):
        from agent.tools import estimate_batch_cost

        result = estimate_batch_cost(
            tool_use_id="test", recipient_count=3, operation="transfer"
        )
        assert result["status"] == "success"
        # base 0.01 + (3 * 0.001) = 0.013
        assert result["estimated_fee"]["amount"] == 0.013
        assert result["comparison"]["batch_txs"] == 1
        assert result["comparison"]["individual_txs"] == 3

    def test_large_batch_capped(self):
        from agent.tools import estimate_batch_cost

        result = estimate_batch_cost(
            tool_use_id="test", recipient_count=500, operation="transfer"
        )
        assert result["estimated_fee"]["amount"] == 0.05  # capped at max

    def test_payroll_operation(self):
        from agent.tools import estimate_batch_cost

        result = estimate_batch_cost(
            tool_use_id="test", recipient_count=10, operation="payroll"
        )
        assert result["status"] == "success"
        # base 0.05 + (10 * 0.002) = 0.07
        assert result["estimated_fee"]["amount"] == 0.07

    def test_escrow_operation(self):
        from agent.tools import estimate_batch_cost

        result = estimate_batch_cost(
            tool_use_id="test", recipient_count=5, operation="escrow"
        )
        assert result["status"] == "success"
        # base 0.05 + (5 * 0.003) = 0.065
        assert result["estimated_fee"]["amount"] == 0.065

    def test_single_recipient_batch(self):
        """Even a single-recipient batch works (edge case)."""
        from agent.tools import estimate_batch_cost

        result = estimate_batch_cost(
            tool_use_id="test", recipient_count=1, operation="transfer"
        )
        assert result["status"] == "success"
        assert result["comparison"]["savings"] == "0 fewer transactions"


class TestEstimateSprayCost:
    """Tests for the general endpoint cost estimator."""

    def test_pricing_category(self):
        from agent.tools import estimate_spraay_cost

        result = estimate_spraay_cost(
            tool_use_id="test", endpoint_category="pricing", num_calls=10
        )
        assert result["status"] == "success"
        assert result["estimated_total"]["min"] == 0.01
        assert result["estimated_total"]["max"] == 0.05

    def test_defi_category(self):
        from agent.tools import estimate_spraay_cost

        result = estimate_spraay_cost(
            tool_use_id="test", endpoint_category="defi", num_calls=1
        )
        assert result["status"] == "success"
        assert result["estimated_total"]["min"] == 0.005

    def test_rpc_category(self):
        from agent.tools import estimate_spraay_cost

        result = estimate_spraay_cost(
            tool_use_id="test", endpoint_category="rpc", num_calls=100
        )
        assert result["status"] == "success"
        assert result["estimated_total"]["min"] == 0.1  # 100 * 0.001

    def test_all_categories_valid(self):
        from agent.tools import estimate_spraay_cost

        categories = [
            "batch_payments", "escrow", "bridge", "payroll",
            "pricing", "wallet", "defi", "research", "rpc",
            "oracle", "ai_inference", "compute_futures",
        ]
        for cat in categories:
            result = estimate_spraay_cost(
                tool_use_id="test", endpoint_category=cat, num_calls=1
            )
            assert result["status"] == "success", f"Failed for category: {cat}"


class TestGetSupportedChains:
    """Tests for the get_supported_chains tool."""

    def test_returns_primary_and_secondary(self):
        from agent.tools import get_supported_chains

        result = get_supported_chains(tool_use_id="test")
        chains = result["chains"]
        assert len(chains["primary"]) == 3
        assert len(chains["secondary"]) >= 10

    def test_base_is_primary(self):
        from agent.tools import get_supported_chains

        result = get_supported_chains(tool_use_id="test")
        primary = result["chains"]["primary"]
        base = next(c for c in primary if c["name"] == "Base")
        assert base["chain_id"] == 8453

    def test_payment_network_is_base(self):
        from agent.tools import get_supported_chains

        result = get_supported_chains(tool_use_id="test")
        assert "Base" in result["chains"]["payment_network"]
