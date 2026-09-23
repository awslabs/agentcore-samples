import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from bedrock_agentcore.memory import MemoryClient

from .config import _load_memory_config

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


class SREMemoryClient:
    """Wrapper for AgentCore Memory client tailored for SRE operations."""

    def __init__(
        self,
        memory_name: str = "sre_agent_memory",
        region: str = "us-east-1",
        force_delete: bool = False,
    ):
        self.client = MemoryClient(region_name=region)
        self.memory_name = memory_name
        self.config = _load_memory_config()
        self.memory_ids = {}
        self.force_delete = force_delete
        self._initialize_memories()

    def _initialize_memories(self):
        """Initialize different memory strategies."""
        try:
            logger.info(f"Initializing memory system with name: {self.memory_name}")

            # Check for existing memory first
            existing_memory = self._find_existing_memory()

            if existing_memory and not self.force_delete:
                # Use existing memory
                self.memory_id = existing_memory["id"]
                logger.info(
                    f"Using existing memory: {self.memory_id} (name: {existing_memory['name']})"
                )
                logger.info(
                    f"Memory status: {existing_memory.get('status', 'unknown')}"
                )

                # Write memory ID to file for helper scripts
                self._write_memory_id_to_file()

                # Check if strategies are already configured
                existing_strategies = existing_memory.get("strategies", [])
                strategy_count = len(existing_strategies)

                if strategy_count >= 3:  # We expect 3 strategies
                    logger.info(
                        f"Found {strategy_count} existing strategies - memory is already configured"
                    )
                    # Check if all strategies are ACTIVE
                    creating_count = sum(
                        1 for s in existing_strategies if s.get("status") == "CREATING"
                    )
                    if creating_count > 0:
                        logger.warning(
                            f"{creating_count} strategies are still in CREATING state - memory system may not be fully operational"
                        )
                    return  # Memory is already configured
                else:
                    logger.info(
                        f"Found {strategy_count} strategies, expected 3 - will add missing ones"
                    )
                    # Check which strategies exist
                    existing_names = {s.get("name") for s in existing_strategies}
                    logger.info(f"Existing strategy names: {existing_names}")
            else:
                if existing_memory and self.force_delete:
                    logger.warning(
                        f"Force delete enabled - deleting existing memory: {existing_memory['id']}"
                    )
                    try:
                        self.client.delete_memory(existing_memory["id"])
                        logger.info("Waiting for memory deletion to complete...")
                        import time

                        time.sleep(5)  # Wait for deletion
                    except Exception as e:
                        logger.error(f"Failed to delete existing memory: {e}")

                # Create new memory
                max_retention = max(
                    self.config.preferences_retention_days,
                    self.config.infrastructure_retention_days,
                    self.config.investigation_retention_days,
                )
                logger.info(f"Creating memory with {max_retention} day retention")

                base_memory = self.client.create_memory(
                    name=self.memory_name,
                    description="SRE Agent long-term memory system",
                    event_expiry_days=max_retention,
                )
                self.memory_id = base_memory["id"]
                logger.info(f"Created new memory: {self.memory_id}")

                # Write memory ID to file for helper scripts
                self._write_memory_id_to_file()

            # Check what strategies need to be added (in case of partial configuration)
            existing_names = set()
            if existing_memory:
                existing_strategies = existing_memory.get("strategies", [])
                existing_names = {s.get("name") for s in existing_strategies}
                logger.info(f"Existing strategy names: {existing_names}")

            # Add user preferences strategy if not exists
            if "user_preferences" not in existing_names:
                logger.info("Adding user preferences strategy...")
                self.client.add_user_preference_strategy_and_wait(
                    memory_id=self.memory_id,
                    name="user_preferences",
                    description="User preferences for escalation, notification, and workflows",
                    namespaces=["/sre/users/{actorId}/preferences"],
                )
                logger.info("Added user preferences strategy")
            else:
                logger.info("User preferences strategy already exists, skipping")

            # Add infrastructure knowledge strategy (semantic) if not exists
            if "infrastructure_knowledge" not in existing_names:
                logger.info("Adding infrastructure knowledge strategy...")
                self.client.add_semantic_strategy_and_wait(
                    memory_id=self.memory_id,
                    name="infrastructure_knowledge",
                    description="Infrastructure knowledge including dependencies and patterns",
                    namespaces=["/sre/infrastructure/{actorId}/{sessionId}"],
                )
                logger.info("Added infrastructure knowledge strategy")
            else:
                logger.info(
                    "Infrastructure knowledge strategy already exists, skipping"
                )

            # Add investigation summaries strategy if not exists
            if "investigation_summaries" not in existing_names:
                logger.info("Adding investigation summaries strategy...")
                self.client.add_summary_strategy_and_wait(
                    memory_id=self.memory_id,
                    name="investigation_summaries",
                    description="Investigation summaries with timeline and findings",
                    namespaces=["/sre/investigations/{actorId}/{sessionId}"],
                )
                logger.info("Added investigation summaries strategy")
            else:
                logger.info("Investigation summaries strategy already exists, skipping")
            logger.info(f"Memory system initialization complete for {self.memory_name}")

        except Exception as e:
            logger.error(f"Failed to initialize memories: {e}", exc_info=True)
            # For development, we'll continue without failing completely
            # In production, you might want to raise the exception
            self.memory_id = None
            logger.warning("Memory system will operate in offline mode")

    def save_event(
        self,
        memory_type: str,
        actor_id: str,
        event_data: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> bool:
        """Save an event to memory using create_event API.

        actor_id is always required. session_id is required for infrastructure
        and investigations memory types, but optional for preferences.
        """
        if not self.memory_id:
            logger.warning("Memory system not initialized, skipping save")
            return False

        if not actor_id:
            raise ValueError("actor_id is required for save_event")

        # Validate session_id based on memory type
        if memory_type in ["infrastructure", "investigations"] and not session_id:
            raise ValueError(f"session_id is required for {memory_type} memory type")

        try:
            # Add detailed traces for debugging
            logger.info("=== SAVE_EVENT TRACE START ===")
            logger.info("Input parameters:")
            logger.info(f"  memory_type: {memory_type}")
            logger.info(f"  actor_id: {actor_id}")
            logger.info(f"  session_id: {session_id}")
            logger.info(f"  memory_id: {self.memory_id}")
            logger.info(f"  event_data: {event_data}")

            # Convert event data to message format
            messages = [
                (str(event_data), "ASSISTANT")  # Store as assistant message
            ]

            logger.info("Calling create_event with:")
            logger.info(f"  memory_id: {self.memory_id}")
            logger.info(f"  actor_id: {actor_id}")
            logger.info(f"  session_id: {session_id}")
            logger.info(f"  messages: {messages}")

            # For preferences, use a default session_id since the API requires it
            # but the namespace doesn't use it
            actual_session_id = session_id if session_id else "preferences-default"

            result = self.client.create_event(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=actual_session_id,
                messages=messages,
            )

            event_id = result.get("eventId", "unknown")
            logger.info(f"create_event result: {result}")
            logger.info("=== SAVE_EVENT TRACE END ===")
            logger.info(
                f"Saved {memory_type} event for {actor_id} (event_id: {event_id})"
            )
            logger.info(f"Event data size: {len(str(event_data))} characters")
            return True

        except Exception as e:
            logger.error(
                f"Failed to save {memory_type} event for {actor_id}: {e}", exc_info=True
            )
            return False

    def retrieve_memories(
        self,
        memory_type: str,
        actor_id: str,
        query: str,
        max_results: int = 10,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve memories using the retrieve_memories API."""
        if not self.memory_id:
            logger.warning("Memory system not initialized, returning empty results")
            return []

        try:
            # Get appropriate namespace (session_id only needed for infrastructure/investigations)
            namespace = self._get_namespace(memory_type, actor_id, session_id)

            logger.info(
                f"Retrieving {memory_type} memories: actor_id={actor_id}, namespace={namespace}, query='{query}'"
            )

            result = self.client.retrieve_memories(
                memory_id=self.memory_id,
                namespace=namespace,
                query=query,
                top_k=max_results,
            )

            logger.info(
                f"Retrieved {len(result)} {memory_type} memories for {actor_id}"
            )
            if result:
                logger.debug(
                    f"First result keys: {list(result[0].keys()) if result else 'N/A'}"
                )
                # Log the actual memory contents for debugging (debug level to reduce noise)
                logger.debug(
                    f"All {len(result)} {memory_type} memory records for {actor_id}:"
                )
                for i, memory in enumerate(result):
                    logger.debug(f"Memory {i + 1}: {memory}")
                    if "content" in memory:
                        logger.debug(f"Memory {i + 1} content: {memory['content']}")
                    else:
                        logger.debug(f"Memory {i + 1} has no 'content' field")

            return result

        except Exception as e:
            logger.error(
                f"Failed to retrieve {memory_type} memories for {actor_id}: {e}",
                exc_info=True,
            )
            return []

    def _get_namespace(
        self, memory_type: str, actor_id: str, session_id: Optional[str] = None
    ) -> str:
        """Get the appropriate namespace for a memory type and actor.

        Based on the namespace templates defined in memory initialization:
        - preferences: /sre/users/{actorId}/preferences (no sessionId - always user-wide)
        - infrastructure: /sre/infrastructure/{actorId}/{sessionId} (session-specific) or
                         /sre/infrastructure/{actorId} (cross-session when session_id=None)
        - investigations: /sre/investigations/{actorId}/{sessionId} (session-specific) or
                         /sre/investigations/{actorId} (cross-session when session_id=None)

        Args:
            memory_type: Type of memory (preferences, infrastructure, investigations)
            actor_id: Actor identifier (user or agent)
            session_id: Session identifier. If None, enables cross-session search for infrastructure/investigations
        """
        if memory_type == "preferences":
            # Preferences are always user-wide, ignore session_id
            return f"/sre/users/{actor_id}/preferences"
        elif memory_type == "infrastructure":
            if session_id is None:
                # Cross-session search: use base namespace to search across all sessions
                return f"/sre/infrastructure/{actor_id}"
            else:
                # Session-specific search: include session_id in namespace
                return f"/sre/infrastructure/{actor_id}/{session_id}"
        elif memory_type == "investigations":
            if session_id is None:
                # Cross-session search: use base namespace to search across all sessions
                return f"/sre/investigations/{actor_id}"
            else:
                # Session-specific search: include session_id in namespace
                return f"/sre/investigations/{actor_id}/{session_id}"
        else:
            return f"/sre/default/{actor_id}"

    def _find_existing_memory(self) -> Optional[Dict[str, Any]]:
        """Find existing memory by name."""
        try:
            logger.info(f"Searching for existing memory with name: {self.memory_name}")
            memories = self.client.list_memories(max_results=100)

            for memory in memories:
                memory_id = memory.get("id", "")
                # Check if memory ID starts with our memory name (since name field might not be returned)
                if memory_id.startswith(f"{self.memory_name}-"):
                    logger.info(
                        f"Found existing memory: {memory_id} with status {memory.get('status')}"
                    )
                    # Get full memory details since list might not include all fields
                    try:
                        from bedrock_agentcore.memory import MemoryControlPlaneClient

                        cp_client = MemoryControlPlaneClient(
                            region_name=self.client.gmcp_client._client_config.region_name
                        )
                        full_memory = cp_client.get_memory(memory_id)
                        return full_memory
                    except Exception as e:
                        logger.warning(f"Failed to get full memory details: {e}")
                        # Return what we have
                        memory["id"] = memory_id
                        return memory

            logger.info(
                f"No existing memory found with name prefix: {self.memory_name}"
            )
            return None

        except Exception as e:
            logger.warning(f"Failed to list memories: {e}")
            return None

    def _write_memory_id_to_file(self) -> None:
        """Write memory ID to .memory_id file for helper scripts."""
        try:
            # Write to project root only (where manage_memories.py expects it)
            project_root = Path(__file__).parent.parent.parent
            memory_id_file = project_root / ".memory_id"

            memory_id_file.write_text(self.memory_id)
            logger.info(f"Wrote memory ID {self.memory_id} to {memory_id_file}")

        except Exception as e:
            logger.warning(f"Failed to write memory ID to file: {e}")
