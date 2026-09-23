#!/usr/bin/env python3

import logging
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """State shared across all agents in the multi-agent system.

    This state is passed between agents and maintains conversation history,
    intermediate results, and routing information.
    """

    # Conversation messages using LangGraph's message annotation
    messages: Annotated[List[BaseMessage], add_messages]

    # Which agent should act next (set by supervisor)
    next: Literal["kubernetes", "logs", "metrics", "runbooks", "FINISH"]

    # Intermediate results from each agent
    agent_results: Dict[str, Any]

    # Current query being processed
    current_query: Optional[str]

    # Metadata about the conversation
    metadata: Dict[str, Any]

    # Flag to indicate if we need multiple agents
    requires_collaboration: bool

    # List of agents that have already responded
    agents_invoked: List[str]

    # Final aggregated response (set by supervisor)
    final_response: Optional[str]

    # Auto-approve plans without user confirmation (defaults to False)
    auto_approve_plan: Optional[bool]

    # Memory-related fields
    user_id: Optional[str]  # For user preference tracking
    incident_id: Optional[str]  # For investigation tracking
    actor_id: Optional[str]  # Actor ID for memory storage and retrieval
    session_id: Optional[str]  # Session ID for conversation grouping
    memory_context: Optional[Dict[str, Any]]  # Retrieved memory context
    captured_preferences: Optional[
        List[Dict[str, Any]]
    ]  # Preferences captured during session
    captured_knowledge: Optional[
        List[Dict[str, Any]]
    ]  # Infrastructure knowledge captured
