"""Typed schemas for the claims pipeline."""

from dataclasses import dataclass, field
from typing import Literal

Decision = Literal["APPROVE", "DENY", "ESCALATE"]


@dataclass
class TypedClaimSummary:
    """Structured claim details collected by the Intake Agent."""

    policy_number: str
    incident_type: str
    incident_date: str
    description: str
    damage_description: str
    estimated_amount: float
    reporting_timeline: str
    documentation: list[str]
    injuries: bool
    police_report: bool
    actor_id: str
    policyholder_name: str
    contact_info: str = ""


@dataclass
class TypedDecision:
    """Structured decision from the Adjudication Agent."""

    decision: Decision
    amount: float | None = None
    internal_reasoning: str = ""
    customer_reasoning: str = ""
    cited_patterns: list[str] = field(default_factory=list)
    customer_next_steps: str = ""
