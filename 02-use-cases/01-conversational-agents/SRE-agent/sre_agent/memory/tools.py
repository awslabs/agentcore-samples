import json
import logging
from typing import List, Optional, Type

from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from .client import SREMemoryClient
from .strategies import (
    InfrastructureKnowledge,
    InvestigationSummary,
    UserPreference,
    _retrieve_infrastructure_knowledge,
    _retrieve_investigation_summaries,
    _retrieve_user_preferences,
    _save_infrastructure_knowledge,
    _save_investigation_summary,
    _save_user_preference,
)

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


def _sanitize_actor_id(actor_id: str) -> str:
    """Sanitize actor_id to comply with AWS Bedrock memory regex: [a-zA-Z0-9][a-zA-Z0-9-_/]*"""
    # Replace spaces with hyphens, keep original case, keep only allowed characters
    sanitized = actor_id.replace(" ", "-")
    # Keep only alphanumeric, hyphens, underscores, and forward slashes (preserve case)
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in "-_/")
    # Ensure it starts with alphanumeric
    if sanitized and not sanitized[0].isalnum():
        sanitized = "a" + sanitized
    return sanitized or "default-actor"


class SavePreferenceInput(BaseModel):
    """Input schema for SavePreferenceTool."""

    content: UserPreference = Field(description="User preference data")
    context: str = Field(
        description="REQUIRED: Context describing where/why this preference was captured"
    )
    actor_id: str = Field(description="Actor ID for memory storage")


class SaveInfrastructureInput(BaseModel):
    """Input schema for SaveInfrastructureTool."""

    content: InfrastructureKnowledge = Field(
        description="Infrastructure knowledge data"
    )
    context: str = Field(
        description="REQUIRED: Context describing where/why this knowledge was discovered"
    )
    actor_id: str = Field(description="Actor ID for memory storage")
    session_id: str = Field(
        description="REQUIRED: Session ID for infrastructure memory storage"
    )


class SaveInvestigationInput(BaseModel):
    """Input schema for SaveInvestigationTool."""

    content: InvestigationSummary = Field(description="Investigation summary data")
    context: str = Field(
        description="REQUIRED: Context describing the investigation circumstances"
    )
    actor_id: str = Field(description="Actor ID for memory storage")
    session_id: str = Field(
        description="REQUIRED: Session ID for investigation memory storage"
    )


class SavePreferenceTool(BaseTool):
    """Tool for saving user preferences to long-term memory."""

    name: str = "save_preference"
    description: str = """Save user preferences to long-term memory.
    Use this to remember:
    - Escalation contacts and notification channels
    - Workflow preferences and operational styles
    - User-specific configurations and settings

    Required fields:
    - content: UserPreference object with:
      - user_id: str (unique identifier for the user)
      - preference_type: str (escalation, notification, workflow, style)
      - preference_value: dict (the actual preference data)
    - context: str (REQUIRED - describes where/why this preference was captured)
    - actor_id: str (REQUIRED - use the user_id from content.user_id, NOT the agent actor_id)

    CRITICAL: For preferences, actor_id MUST be the user_id (e.g., "Alice") to ensure
    preferences are saved to the correct user namespace (/sre/users/{user_id}/preferences).
    """
    args_schema: Type[BaseModel] = SavePreferenceInput

    def __init__(self, memory_client: SREMemoryClient, **kwargs):
        super().__init__(**kwargs)
        # Store memory client as instance attribute (not Pydantic field)
        object.__setattr__(self, "_memory_client", memory_client)

    @property
    def memory_client(self) -> SREMemoryClient:
        """Get the memory client."""
        return getattr(self, "_memory_client")

    def _run(
        self,
        content: UserPreference,
        context: str,
        actor_id: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Save user preference."""
        try:
            sanitized_actor_id = _sanitize_actor_id(actor_id)
            logger.info(
                f"save_preference called: context={context}, actor_id={actor_id} -> {sanitized_actor_id}, content={json.dumps(content.model_dump(), indent=2, default=str)}"
            )

            # Always set context
            if not content.context:
                content.context = context

            success = _save_user_preference(
                self.memory_client, sanitized_actor_id, content
            )

            result = (
                f"Saved user preference: {content.preference_type} for user {content.user_id}"
                if success
                else f"Failed to save user preference: {content.preference_type}"
            )
            logger.info(f"save_preference result: {result}")
            return result

        except Exception as e:
            error_msg = f"Error saving user preference: {str(e)}"
            logger.error(f"save_preference exception: {error_msg}", exc_info=True)
            return error_msg


class SaveInfrastructureTool(BaseTool):
    """Tool for saving infrastructure knowledge to long-term memory."""

    name: str = "save_infrastructure"
    description: str = """Save infrastructure knowledge to long-term memory.
    Use this to remember:
    - Service dependencies and relationships
    - Infrastructure patterns and configurations
    - Performance baselines and thresholds

    Required fields:
    - content: InfrastructureKnowledge object with:
      - service_name: str (name of the service or infrastructure component)
      - knowledge_type: str (dependency, pattern, config, baseline)
      - knowledge_data: dict (the actual knowledge data)
      - confidence: float (optional - confidence level 0.0-1.0, defaults to 0.8)
    - context: str (REQUIRED - describes where/why this knowledge was discovered)
    - actor_id: str (REQUIRED - use "sre-agent-{agent_name}")
    - session_id: str (REQUIRED - session ID for infrastructure memory storage)
    """
    args_schema: Type[BaseModel] = SaveInfrastructureInput

    def __init__(self, memory_client: SREMemoryClient, **kwargs):
        super().__init__(**kwargs)
        # Store memory client as instance attribute (not Pydantic field)
        object.__setattr__(self, "_memory_client", memory_client)

    @property
    def memory_client(self) -> SREMemoryClient:
        """Get the memory client."""
        return getattr(self, "_memory_client")

    def _run(
        self,
        content: InfrastructureKnowledge,
        context: str,
        actor_id: str,
        session_id: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Save infrastructure knowledge."""
        try:
            sanitized_actor_id = _sanitize_actor_id(actor_id)
            logger.info(
                f"save_infrastructure called: context={context}, actor_id={actor_id} -> {sanitized_actor_id}, content={json.dumps(content.model_dump(), indent=2, default=str)}"
            )

            # Always set context
            if not content.context:
                content.context = context

            success = _save_infrastructure_knowledge(
                self.memory_client, sanitized_actor_id, content, session_id
            )

            result = (
                f"Saved infrastructure knowledge: {content.knowledge_type} for {content.service_name}"
                if success
                else f"Failed to save infrastructure knowledge for {content.service_name}"
            )
            logger.info(f"save_infrastructure result: {result}")
            return result

        except Exception as e:
            error_msg = f"Error saving infrastructure knowledge: {str(e)}"
            logger.error(f"save_infrastructure exception: {error_msg}", exc_info=True)
            return error_msg


class SaveInvestigationTool(BaseTool):
    """Tool for saving investigation summaries to long-term memory."""

    name: str = "save_investigation"
    description: str = """Save investigation summaries to long-term memory.
    Use this to remember:
    - Investigation timeline and actions taken
    - Key findings and resolution strategies
    - Incident patterns and lessons learned

    Required fields:
    - content: InvestigationSummary object with:
      - incident_id: str (unique identifier for the incident)
      - query: str (original user query that started the investigation)
      - timeline: list (optional - timeline of investigation events)
      - actions_taken: list (optional - list of actions taken during investigation)
      - resolution_status: str (completed, ongoing, escalated)
      - key_findings: list (optional - key findings from the investigation)
    - context: str (REQUIRED - describes the investigation circumstances)
    - actor_id: str (REQUIRED - use "sre-agent-{agent_name}")
    - session_id: str (REQUIRED - session ID for investigation memory storage)
    """
    args_schema: Type[BaseModel] = SaveInvestigationInput

    def __init__(self, memory_client: SREMemoryClient, **kwargs):
        super().__init__(**kwargs)
        # Store memory client as instance attribute (not Pydantic field)
        object.__setattr__(self, "_memory_client", memory_client)

    @property
    def memory_client(self) -> SREMemoryClient:
        """Get the memory client."""
        return getattr(self, "_memory_client")

    def _run(
        self,
        content: InvestigationSummary,
        context: str,
        actor_id: str,
        session_id: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Save investigation summary."""
        try:
            sanitized_actor_id = _sanitize_actor_id(actor_id)
            logger.info(
                f"save_investigation called: context={context}, actor_id={actor_id} -> {sanitized_actor_id}, content={json.dumps(content.model_dump(), indent=2, default=str)}"
            )

            # Always set context
            if not content.context:
                content.context = context

            success = _save_investigation_summary(
                self.memory_client,
                sanitized_actor_id,
                content.incident_id,
                content,
                session_id,
            )

            result = (
                f"Saved investigation summary for incident {content.incident_id}"
                if success
                else f"Failed to save investigation summary for {content.incident_id}"
            )
            logger.info(f"save_investigation result: {result}")
            return result

        except Exception as e:
            error_msg = f"Error saving investigation summary: {str(e)}"
            logger.error(f"save_investigation exception: {error_msg}", exc_info=True)
            return error_msg


class RetrieveMemoryInput(BaseModel):
    """Input schema for RetrieveMemoryTool."""

    memory_type: str = Field(
        description="Type of memory: 'preference', 'infrastructure', or 'investigation'"
    )
    query: str = Field(description="Search query to find relevant memories")
    actor_id: str = Field(description="Actor ID to search memories for")
    max_results: int = Field(
        description="Maximum number of results to return", default=5
    )
    session_id: Optional[str] = Field(
        description="Session ID (required for infrastructure and investigation memories)",
        default=None,
    )


class RetrieveMemoryTool(BaseTool):
    """Tool for retrieving memories during SRE operations."""

    name: str = "retrieve_memory"
    description: str = """Retrieve relevant information from long-term memory.
    Query for:
    - User preferences for current context (escalation, notification, workflow preferences)
    - Infrastructure knowledge about services (dependencies, patterns, baselines)
    - Past investigation summaries (similar issues, resolution strategies)

    Parameters:
    - memory_type: "preference", "infrastructure", or "investigation"
    - query: search terms for relevant memories
    - actor_id: user_id for preferences/investigations, agent actor_id for infrastructure
    - max_results: maximum number of results (default 5)
    - session_id: optional - if None, searches across all sessions (useful for planning)
    """
    args_schema: Type[BaseModel] = RetrieveMemoryInput

    def __init__(
        self, memory_client: SREMemoryClient, user_id: Optional[str] = None, **kwargs
    ):
        super().__init__(**kwargs)
        # Store memory client as instance attribute (not Pydantic field)
        object.__setattr__(self, "_memory_client", memory_client)
        # Store user_id for infrastructure/investigation retrievals
        object.__setattr__(self, "_user_id", user_id)

    @property
    def memory_client(self) -> SREMemoryClient:
        """Get the memory client."""
        return getattr(self, "_memory_client")

    @property
    def user_id(self) -> Optional[str]:
        """Get the user_id."""
        return getattr(self, "_user_id", None)

    def set_user_id(self, user_id: str) -> None:
        """Set the user_id for this tool instance."""
        object.__setattr__(self, "_user_id", user_id)

    def _run(
        self,
        memory_type: str,
        query: str,
        actor_id: str,
        max_results: int = 5,
        session_id: Optional[str] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Retrieve memories based on query."""
        try:
            # For infrastructure and investigation memories, use the user_id if available
            if memory_type in ["infrastructure", "investigation"] and self.user_id:
                effective_actor_id = self.user_id
                logger.info(
                    f"retrieve_memory: Overriding actor_id for {memory_type} - using user_id={self.user_id} instead of {actor_id}"
                )
            else:
                effective_actor_id = actor_id

            sanitized_actor_id = _sanitize_actor_id(effective_actor_id)
            logger.info(
                f"retrieve_memory called: type={memory_type}, query='{query}', actor_id={actor_id} -> {sanitized_actor_id}, max_results={max_results}"
            )

            if memory_type == "preference":
                logger.info(
                    f"Retrieving user preferences for actor_id={sanitized_actor_id}"
                )
                preferences = _retrieve_user_preferences(
                    self.memory_client, sanitized_actor_id, query
                )

                # Convert to dict for JSON serialization
                results = [pref.model_dump() for pref in preferences[:max_results]]
                logger.info(
                    f"retrieve_memory found {len(results)} user preferences (limited to {max_results})"
                )
                return json.dumps(results, indent=2, default=str)

            elif memory_type == "infrastructure":
                # Use the passed session_id parameter (None = cross-session search, specific = session-specific)
                search_type = (
                    "cross-session search"
                    if session_id is None
                    else f"session-specific search (session: {session_id})"
                )
                logger.info(
                    f"Retrieving infrastructure knowledge for actor_id={sanitized_actor_id} ({search_type})"
                )
                knowledge = _retrieve_infrastructure_knowledge(
                    self.memory_client,
                    sanitized_actor_id,
                    query,
                    session_id=session_id,  # Use the passed parameter
                )

                # Convert to dict for JSON serialization
                results = [know.model_dump() for know in knowledge[:max_results]]
                logger.info(
                    f"retrieve_memory found {len(results)} infrastructure knowledge items (limited to {max_results})"
                )
                return json.dumps(results, indent=2, default=str)

            elif memory_type == "investigation":
                # Use the passed session_id parameter (None = cross-session search, specific = session-specific)
                search_type = (
                    "cross-session search"
                    if session_id is None
                    else f"session-specific search (session: {session_id})"
                )
                logger.info(
                    f"Retrieving investigation summaries for actor_id={sanitized_actor_id} ({search_type})"
                )
                summaries = _retrieve_investigation_summaries(
                    self.memory_client,
                    sanitized_actor_id,
                    query,
                    session_id=session_id,  # Use the passed parameter
                )

                # Convert to dict for JSON serialization
                results = [summary.model_dump() for summary in summaries[:max_results]]
                logger.info(
                    f"retrieve_memory found {len(results)} investigation summaries (limited to {max_results})"
                )
                return json.dumps(results, indent=2, default=str)

            else:
                error_result = {
                    "error": f"Unknown memory type: {memory_type}",
                    "supported_types": [
                        "preference",
                        "infrastructure",
                        "investigation",
                    ],
                }
                logger.warning(
                    f"retrieve_memory error: unknown memory type {memory_type}"
                )
                return json.dumps(error_result, indent=2)

        except Exception as e:
            error_result = {"error": f"Error retrieving {memory_type} memory: {str(e)}"}
            logger.error(
                f"retrieve_memory exception: {error_result['error']}", exc_info=True
            )
            return json.dumps(error_result, indent=2)


def create_memory_tools(memory_client: SREMemoryClient) -> List[BaseTool]:
    """Create memory tools for the agent.

    Args:
        memory_client: The memory client instance
    """
    return [
        SavePreferenceTool(memory_client),
        SaveInfrastructureTool(memory_client),
        SaveInvestigationTool(memory_client),
        RetrieveMemoryTool(memory_client),
    ]


def update_memory_tools_user_id(memory_tools: List[BaseTool], user_id: str) -> None:
    """Update the user_id for all RetrieveMemoryTool instances in the list.

    Args:
        memory_tools: List of memory tools
        user_id: The user_id to set
    """
    for tool in memory_tools:
        if hasattr(tool, "name") and tool.name == "retrieve_memory":
            if hasattr(tool, "set_user_id"):
                tool.set_user_id(user_id)
