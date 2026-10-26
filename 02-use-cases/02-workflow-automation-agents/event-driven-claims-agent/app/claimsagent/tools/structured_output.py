"""Structured output tools for the dual-agent pipeline.

These tools replace regex parsing by letting agents submit decisions
as typed tool calls. The tool captures the structured data and makes it
available to the orchestrator via a shared state dict.

Concurrency note: state is held in module-level globals and reset per
invocation via ``reset_state()``. This assumes a single in-flight invocation
per process (the AgentCore Runtime model). It is NOT safe for concurrent
invocations sharing one container — for that, move this state onto the
request ``context`` instead of module globals.
"""

import json

from strands import tool

# Shared state for capturing structured outputs from agents (single-flight; see module docstring)
_last_decision = {}
_last_validation = {}


def get_last_decision() -> dict:
    """Get the last captured decision from the Claims Processor."""
    return _last_decision.copy()


def get_last_validation() -> dict:
    """Get the last captured validation from the Validation Agent."""
    return _last_validation.copy()


def reset_state():
    """Reset captured state between invocations."""
    global _last_decision, _last_validation
    _last_decision = {}
    _last_validation = {}


@tool
def submit_decision(
    decision: str,
    amount: int,
    policy_number: str,
    category: str,
    description: str,
    reasoning: str,
    coverage_check: str,
) -> str:
    """Submit your final claim decision with all required fields.

    Call this tool ONCE after completing your evaluation to record your decision.

    Args:
        decision: Must be exactly ACCEPT or REJECT.
        amount: Dollar amount of the claim as an integer.
        policy_number: The policy number being claimed against.
        category: Claim category (auto_collision, property_damage, theft, natural_disaster, medical).
        description: Brief description of the claim.
        reasoning: Detailed explanation of why you accepted or rejected.
        coverage_check: Summary of coverage verification (limits, active status, deductible).
    """
    global _last_decision
    _last_decision = {
        "decision": decision.upper(),
        "amount": amount,
        "policy_number": policy_number,
        "category": category,
        "description": description,
        "reasoning": reasoning,
        "coverage_check": coverage_check,
    }
    return json.dumps({"status": "recorded", "decision": decision.upper()})


@tool
def submit_validation(
    confidence: int,
    routing: str,
    validation_notes: str,
    concerns: str,
) -> str:
    """Submit your validation assessment of the Claims Processor's decision.

    Call this tool ONCE after completing your independent review.

    Args:
        confidence: Score from 0-100 representing decision quality.
        routing: Must be exactly AUTO_APPROVE or HUMAN_REVIEW.
        validation_notes: Your assessment of the processor's decision.
        concerns: Any red flags or issues. Use "None" if clean.
    """
    global _last_validation
    _last_validation = {
        "confidence": max(0, min(100, confidence)),
        "routing": routing.upper(),
        "validation_notes": validation_notes,
        "concerns": concerns,
    }
    return json.dumps({"status": "recorded", "routing": routing.upper(), "confidence": confidence})
