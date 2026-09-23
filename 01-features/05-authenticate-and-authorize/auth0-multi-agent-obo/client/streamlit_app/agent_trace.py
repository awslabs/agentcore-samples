# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Agent Interaction Trace Model

Captures and displays agent interactions, tool calls, and authorization decisions
for debugging and demonstration purposes.
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum


class TraceEventType(Enum):
    """Types of trace events."""

    REQUEST_RECEIVED = "request_received"
    JWT_VALIDATION = "jwt_validation"
    USER_CONTEXT_EXTRACTED = "user_context_extracted"
    INTENT_DETECTED = "intent_detected"
    AGENT_ROUTED = "agent_routed"
    TOOL_INVOKED = "tool_invoked"
    AUTHORIZATION_CHECK = "authorization_check"
    DATA_RETRIEVED = "data_retrieved"
    RESPONSE_GENERATED = "response_generated"
    ERROR = "error"


@dataclass
class TraceEvent:
    """Single trace event in the agent interaction flow."""

    event_type: TraceEventType
    timestamp: float
    agent: str
    description: str
    details: Dict[str, Any] = field(default_factory=dict)
    duration_ms: Optional[float] = None
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "formatted_time": datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S.%f")[:-3],
            "agent": self.agent,
            "description": self.description,
            "details": self.details,
            "duration_ms": self.duration_ms,
            "success": self.success,
        }


@dataclass
class AgentTrace:
    """
    Complete trace of an agent interaction.

    Captures the full flow from request to response including:
    - JWT validation
    - User context extraction
    - Intent detection
    - Agent routing
    - Tool invocations
    - Authorization decisions
    """

    trace_id: str
    session_id: str
    user_query: str
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    events: List[TraceEvent] = field(default_factory=list)
    final_response: Optional[str] = None
    success: bool = True
    error: Optional[str] = None

    def add_event(
        self,
        event_type: TraceEventType,
        agent: str,
        description: str,
        details: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        success: bool = True,
        timestamp: Optional[float] = None,
    ):
        """Add an event to the trace."""
        self.events.append(
            TraceEvent(
                event_type=event_type,
                timestamp=timestamp or time.time(),
                agent=agent,
                description=description,
                details=details or {},
                duration_ms=duration_ms,
                success=success,
            )
        )

    def complete(self, response: str, success: bool = True, error: Optional[str] = None):
        """Mark the trace as complete."""
        self.end_time = time.time()
        self.final_response = response
        self.success = success
        self.error = error

    @property
    def duration_ms(self) -> Optional[float]:
        """Total trace duration in milliseconds."""
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "user_query": self.user_query,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "end_time": datetime.fromtimestamp(self.end_time).isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "events": [e.to_dict() for e in self.events],
            "final_response": self.final_response,
            "success": self.success,
            "error": self.error,
        }


def generate_mock_trace(query: str, session_id: str, user_id: str, customer_id: str, user_email: str) -> AgentTrace:
    """
    Generate a mock trace for demonstration purposes.

    This simulates what a real agent interaction trace would look like,
    showing the authorization flow and agent routing.
    """
    import uuid

    trace = AgentTrace(trace_id=str(uuid.uuid4())[:8], session_id=session_id, user_query=query)

    # Determine which agent would handle this query
    query_lower = query.lower()
    if any(word in query_lower for word in ["account", "balance", "savings", "transaction"]):
        target_agent = "accounts_agent"
        action = "get_accounts"
    elif any(word in query_lower for word in ["profile", "address", "email", "phone"]):
        target_agent = "profile_agent"
        action = "get_profile"
    else:
        target_agent = "coordinator_agent"
        action = "general_query"

    # Compute timestamps arithmetically to simulate event progression
    base_time = time.time()

    # Event 1: Request received
    trace.add_event(
        event_type=TraceEventType.REQUEST_RECEIVED,
        agent="coordinator_agent",
        description="Received user request",
        details={"query_length": len(query), "session_id": session_id},
        timestamp=base_time,
    )

    # Event 2: JWT Validation
    trace.add_event(
        event_type=TraceEventType.JWT_VALIDATION,
        agent="coordinator_agent",
        description="JWT token validated against Auth0",
        details={
            "issuer": f"https://{os.getenv('AUTH0_DOMAIN', 'your-tenant.auth0.com')}/",
            "audience": "https://agentcore-financial-api",
            "token_valid": True,
            "claims_extracted": ["sub", "email", "name", "customer_id"],
        },
        duration_ms=45.2,
        timestamp=base_time + 0.01,
    )

    # Event 3: User context extracted
    trace.add_event(
        event_type=TraceEventType.USER_CONTEXT_EXTRACTED,
        agent="coordinator_agent",
        description="User identity context extracted from JWT",
        details={
            "user_id": user_id,
            "customer_id": customer_id,
            "email": user_email,
            "scopes": [
                "openid",
                "profile",
                "email",
                "profile:personal:read",
                "profile:preferences:read",
            ],
        },
        timestamp=base_time + 0.02,
    )

    # Event 4: Intent detection
    trace.add_event(
        event_type=TraceEventType.INTENT_DETECTED,
        agent="coordinator_agent",
        description=f"Detected intent: {action}",
        details={"intent": action, "confidence": 0.95, "target_agent": target_agent},
        timestamp=base_time + 0.03,
    )

    # Event 5: Agent routing
    trace.add_event(
        event_type=TraceEventType.AGENT_ROUTED,
        agent="coordinator_agent",
        description=f"Routing request to {target_agent}",
        details={
            "source_agent": "coordinator_agent",
            "target_agent": target_agent,
            "forwarded_claims": ["sub", "customer_id", "email", "permissions"],
        },
        timestamp=base_time + 0.04,
    )

    # Event 6: Authorization check at target agent
    trace.add_event(
        event_type=TraceEventType.AUTHORIZATION_CHECK,
        agent=target_agent,
        description=f"Authorization check: customer {customer_id} accessing own data",
        details={
            "action": action,
            "resource_type": target_agent.replace("_agent", ""),
            "customer_id": customer_id,
            "authorized": True,
            "reason": "Customer requesting own data",
            "access_level": "owner",
        },
        timestamp=base_time + 0.05,
    )

    # Event 7: Tool invocation
    trace.add_event(
        event_type=TraceEventType.TOOL_INVOKED,
        agent=target_agent,
        description=f"Invoking tool: {action}",
        details={
            "tool_name": action,
            "parameters": {
                "customer_id": customer_id,
                "user_id": user_id,
                "include_auth_details": True,
            },
        },
        duration_ms=120.5,
        timestamp=base_time + 0.06,
    )

    # Event 8: Data retrieved
    trace.add_event(
        event_type=TraceEventType.DATA_RETRIEVED,
        agent=target_agent,
        description="Data retrieved successfully",
        details={
            "records_returned": 3,
            "data_filtered_by": "customer_id",
            "authorization_enforced": True,
        },
        timestamp=base_time + 0.07,
    )

    # Event 9: Response generated
    trace.add_event(
        event_type=TraceEventType.RESPONSE_GENERATED,
        agent="coordinator_agent",
        description="Response generated and returned to user",
        details={
            "response_length": 500,
            "contains_sensitive_data": False,
            "data_masked": False,
        },
        timestamp=base_time + 0.08,
    )

    # Complete the trace
    trace.complete(response="[Response would be here]", success=True)

    return trace


def generate_unauthorized_trace(
    query: str, session_id: str, user_id: str, customer_id: str, target_account: str
) -> AgentTrace:
    """
    Generate a mock trace showing an unauthorized access attempt.

    This demonstrates what happens when a customer tries to access
    another customer's data.
    """
    import uuid

    trace = AgentTrace(trace_id=str(uuid.uuid4())[:8], session_id=session_id, user_query=query)

    # Compute timestamps arithmetically to simulate event progression
    base_time = time.time()

    # Event 1: Request received
    trace.add_event(
        event_type=TraceEventType.REQUEST_RECEIVED,
        agent="coordinator_agent",
        description="Received user request",
        details={"query": query[:50]},
        timestamp=base_time,
    )

    # Event 2: JWT Validation
    trace.add_event(
        event_type=TraceEventType.JWT_VALIDATION,
        agent="coordinator_agent",
        description="JWT token validated",
        details={"token_valid": True},
        timestamp=base_time + 0.01,
    )

    # Event 3: User context extracted
    trace.add_event(
        event_type=TraceEventType.USER_CONTEXT_EXTRACTED,
        agent="coordinator_agent",
        description="User context extracted",
        details={"user_id": user_id, "customer_id": customer_id},
        timestamp=base_time + 0.02,
    )

    # Event 4: Intent and routing
    trace.add_event(
        event_type=TraceEventType.AGENT_ROUTED,
        agent="coordinator_agent",
        description="Routing to accounts_agent",
        details={"target_agent": "accounts_agent"},
        timestamp=base_time + 0.03,
    )

    # Event 5: AUTHORIZATION DENIED
    trace.add_event(
        event_type=TraceEventType.AUTHORIZATION_CHECK,
        agent="accounts_agent",
        description=f"AUTHORIZATION DENIED: Account {target_account} belongs to another customer",
        details={
            "action": "get_account_balance",
            "resource": target_account,
            "customer_id": customer_id,
            "authorized": False,
            "reason": "Account belongs to another customer",
            "audit_event": "unauthorized_access_attempt",
        },
        success=False,
        timestamp=base_time + 0.04,
    )

    # Event 6: Error response
    trace.add_event(
        event_type=TraceEventType.ERROR,
        agent="accounts_agent",
        description="Access denied - returning error response",
        details={
            "error_code": "AUTHORIZATION_DENIED",
            "message": f"You do not have access to account {target_account}",
        },
        success=False,
        timestamp=base_time + 0.05,
    )

    # Complete with error
    trace.complete(
        response=f"Access denied: You do not have permission to view account {target_account}",
        success=False,
        error="AUTHORIZATION_DENIED",
    )

    return trace
