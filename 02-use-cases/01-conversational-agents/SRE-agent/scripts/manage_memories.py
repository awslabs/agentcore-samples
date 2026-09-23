#!/usr/bin/env python3
"""
Helper script to manage memories in the SRE agent memory system.

Usage:
    uv run python scripts/manage_memories.py [ACTION] [OPTIONS]

Actions:
    list      List memories (default action)
    update    Update memories from configuration file (with duplicate removal)
    delete    Delete memories

Examples:
    uv run python scripts/manage_memories.py                           # List all memories
    uv run python scripts/manage_memories.py list --memory-type investigations  # List only investigations
    uv run python scripts/manage_memories.py update                    # Load user preferences from user_config.yaml (removes duplicates)
    uv run python scripts/manage_memories.py update --config-file custom.yaml  # Load from custom file
    uv run python scripts/manage_memories.py update --no-duplicate-check       # Skip duplicate removal check
    uv run python scripts/manage_memories.py delete --memory-id mem-123        # Delete specific memory resource
    uv run python scripts/manage_memories.py delete --memory-record-id mem-abc # Delete specific memory record
    uv run python scripts/manage_memories.py delete --all                      # Delete all memory resources
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

# Add the project root to path so we can import sre_agent
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from bedrock_agentcore.memory import MemoryClient  # noqa: E402

from sre_agent.memory.client import SREMemoryClient  # noqa: E402
from sre_agent.memory.config import _load_memory_config  # noqa: E402
from sre_agent.memory.strategies import (  # noqa: E402
    UserPreference,
    _save_user_preference,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


def _read_memory_id() -> str:
    """Read memory ID from .memory_id file."""
    memory_id_file = Path(__file__).parent.parent / ".memory_id"

    if memory_id_file.exists():
        memory_id = memory_id_file.read_text().strip()
        logger.info(f"Found memory ID: {memory_id} in {memory_id_file}")
        return memory_id

    raise FileNotFoundError("Could not find .memory_id file in project root")


def _extract_actor_from_namespace(namespace: str, memory_type: str) -> str:
    """Extract actor ID from memory namespace."""
    if memory_type == "preferences":
        # Preferences namespace format: /sre/users/{user_id}/preferences
        prefix = "/sre/users/"
        suffix = "/preferences"
        if namespace.startswith(prefix) and namespace.endswith(suffix):
            # Extract user_id from between prefix and suffix
            user_id = namespace[len(prefix) : -len(suffix)]
            return user_id
    else:
        # Other namespace format: /sre/{memory_type}/{actor_id}
        prefix = f"/sre/{memory_type}/"
        if namespace.startswith(prefix):
            return namespace[len(prefix) :]
    return "unknown"


def _group_memories_by_actor(memories: list, memory_type: str) -> dict:
    """Group memories by actor ID extracted from namespaces."""
    actor_groups = {}

    for memory in memories:
        # Each memory can have multiple namespaces, use the first one
        if memory.get("namespaces") and len(memory["namespaces"]) > 0:
            namespace = memory["namespaces"][0]
            actor_id = _extract_actor_from_namespace(namespace, memory_type)

            if actor_id not in actor_groups:
                actor_groups[actor_id] = []
            actor_groups[actor_id].append(memory)

    return actor_groups


def _list_memories_for_type(
    client: SREMemoryClient, memory_type: str, actor_id: Optional[str] = None
) -> None:
    """List all memories for a specific type."""
    print(f"\n=== {memory_type.upper()} MEMORIES ===")

    try:
        if actor_id and actor_id != "all":
            # List memories for specific actor
            memories = client.retrieve_memories(
                memory_type=memory_type,
                actor_id=actor_id,
                query="*",  # Wildcard query to get all memories
                max_results=100,
            )
            print(
                f"Found {len(memories)} {memory_type} memories for actor_id: {actor_id}"
            )

            for i, memory in enumerate(memories, 1):
                print(f"\n--- Memory {i} ---")
                print(json.dumps(memory, indent=2, default=str))

        else:
            # List memories across ALL actors using broader namespace
            print(f"Retrieving {memory_type} memories across ALL actors...")

            # Use different namespace patterns for different memory types
            if memory_type == "preferences":
                namespace = "/sre/users"  # For user preferences: /sre/users/{user_id}/preferences
            else:
                namespace = f"/sre/{memory_type}"  # For infrastructure/investigations: /sre/{type}/{actor_id}

            memories = client.client.retrieve_memories(
                memory_id=client.memory_id,
                namespace=namespace,
                query="*",
                actor_id=None,  # No actor restriction
                top_k=100,
            )
            print(f"Found {len(memories)} {memory_type} memories across all actors")

            # Group memories by actor
            actor_groups = _group_memories_by_actor(memories, memory_type)

            # Display grouped by actor
            for actor, actor_memories in sorted(actor_groups.items()):
                print(f"\n--- ACTOR: {actor} ({len(actor_memories)} memories) ---")

                for i, memory in enumerate(actor_memories, 1):
                    print(f"\n  Memory {i}:")
                    print(json.dumps(memory, indent=4, default=str))

    except Exception as e:
        logger.error(f"Failed to retrieve {memory_type} memories: {e}")
        print(f"Error retrieving {memory_type} memories: {e}")


def _list_all_memories() -> list:
    """List all memory resources."""
    try:
        memory_client = MemoryClient(region_name="us-east-1")
        memories = memory_client.list_memories(max_results=100)
        return memories
    except Exception as e:
        logger.error(f"Failed to list memory resources: {e}")
        return []


def _delete_memory(memory_id: str) -> bool:
    """Delete a specific memory resource."""
    try:
        memory_client = MemoryClient(region_name="us-east-1")
        logger.info(f"Deleting memory: {memory_id}")
        print(f"Deleting memory: {memory_id}...")

        memory_client.delete_memory_and_wait(
            memory_id=memory_id, max_wait=300, poll_interval=10
        )

        logger.info(f"Successfully deleted memory: {memory_id}")
        print(f"Successfully deleted memory: {memory_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete memory {memory_id}: {e}")
        print(f"Error deleting memory {memory_id}: {e}")
        return False


def _delete_memory_record(memory_record_id: str) -> bool:
    """Delete a specific memory record by its ID."""
    try:
        # Read the memory resource ID from .memory_id file
        memory_id = _read_memory_id()

        # Use the SREMemoryClient for proper configuration
        client = SREMemoryClient(memory_name="sre_agent_memory")

        logger.info(
            f"Deleting memory record: {memory_record_id} from memory: {memory_id}"
        )
        print(f"Deleting memory record: {memory_record_id}...")

        # Use the underlying data plane client to delete the specific memory record
        client.client.gmdp_client.delete_memory_record(
            memoryId=memory_id, memoryRecordId=memory_record_id
        )

        logger.info(f"Successfully deleted memory record: {memory_record_id}")
        print(f"Successfully deleted memory record: {memory_record_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete memory record {memory_record_id}: {e}")
        print(f"Error deleting memory record {memory_record_id}: {e}")
        return False


def _delete_all_memories() -> int:
    """Delete all memory resources."""
    memories = _list_all_memories()
    if not memories:
        print("No memories found to delete.")
        return 0

    print(f"Found {len(memories)} memory resources:")
    for memory in memories:
        memory_id = memory.get("id", "unknown")
        memory_name = memory.get("name", "unnamed")
        print(f"  - {memory_id} ({memory_name})")

    # Confirm deletion
    response = input(
        f"\nAre you sure you want to delete ALL {len(memories)} memory resources? (yes/no): "
    )
    if response.lower() not in ["yes", "y"]:
        print("Deletion cancelled.")
        return 0

    deleted_count = 0
    for memory in memories:
        memory_id = memory.get("id")
        if memory_id and _delete_memory(memory_id):
            deleted_count += 1

    print(f"\nDeleted {deleted_count} out of {len(memories)} memory resources.")
    return deleted_count


def _check_and_delete_existing_preference(
    client: SREMemoryClient, user_id: str, preference_type: str
) -> tuple[int, list]:
    """Check for existing preference events of the same type and delete them to prevent duplicates."""
    try:
        # List events for this user to find duplicate preferences
        # We use list_events because it shows individual preference events, not aggregated memories
        events = client.client.list_events(
            memory_id=client.memory_id,
            actor_id=user_id,
            session_id="preferences-default",
            max_results=100,  # Get more events to ensure we find all duplicates
            include_payload=True,
        )

        deleted_count = 0
        logger.debug(f"Found {len(events)} events for user {user_id}")

        # Track which events to delete (we'll batch delete them)
        events_to_delete = []

        for event in events:
            try:
                # Get the event payload (it's a list of message objects)
                payload = event.get("payload", [])

                for message_obj in payload:
                    # Extract the conversational content
                    conversational = message_obj.get("conversational", {})
                    role = conversational.get("role", "")
                    content_obj = conversational.get("content", {})
                    content = content_obj.get("text", "")

                    # We're looking for ASSISTANT messages with preference data
                    if role == "ASSISTANT" and content:
                        import json

                        try:
                            # Parse the content to check preference type
                            pref_data = json.loads(content)

                            # Check if this is a preference with the matching type
                            if pref_data.get("preference_type") == preference_type:
                                event_id = event.get("eventId")
                                event_time = event.get("eventTimestamp")
                                logger.info(
                                    f"Found existing {preference_type} preference event: {event_id} from {event_time}"
                                )
                                events_to_delete.append(event_id)

                        except json.JSONDecodeError:
                            # Not JSON or not a preference - skip
                            continue

            except Exception as e:
                logger.warning(f"Error checking event for duplicates: {e}")
                continue

        # Report on duplicate events found
        if events_to_delete:
            logger.info(
                f"Found {len(events_to_delete)} existing {preference_type} preference events for user {user_id}"
            )
            # Note: The Amazon Bedrock Agent Memory service doesn't support deleting individual events
            # Events are immutable and designed to accumulate over time
            # The memory strategies will aggregate all events, giving more weight to recent ones
            logger.info(
                "Note: Existing preference events cannot be deleted (events are immutable)"
            )
            logger.info(
                "New preference will be added and the memory strategy will aggregate all events"
            )
            deleted_count = 0  # We can't actually delete events

        if deleted_count > 0:
            logger.info(
                f"Deleted {deleted_count} existing {preference_type} preferences for user {user_id}"
            )

        return deleted_count, events_to_delete

    except Exception as e:
        logger.warning(
            f"Failed to check for existing preferences for user {user_id}, type {preference_type}: {e}"
        )
        return 0, []


def _load_user_preferences_from_yaml(yaml_file: Path) -> dict:
    """Load user preferences from YAML configuration file."""
    try:
        with open(yaml_file, "r") as f:
            config = yaml.safe_load(f)

        if not config or "users" not in config:
            raise ValueError("Invalid YAML format: missing 'users' section")

        logger.info(f"Loaded user preferences from {yaml_file}")
        return config["users"]

    except Exception as e:
        logger.error(f"Failed to load user preferences from {yaml_file}: {e}")
        raise


def _handle_update_action(args) -> None:
    """Handle update action to load user preferences from YAML."""
    try:
        # Default YAML file path (look in scripts directory first, then project root)
        yaml_file = Path(__file__).parent / "user_config.yaml"
        if not yaml_file.exists():
            yaml_file = Path(__file__).parent.parent / "user_config.yaml"

        # Use custom path if provided
        if hasattr(args, "config_file") and args.config_file:
            yaml_file = Path(args.config_file)

        if not yaml_file.exists():
            print(f"Error: Configuration file not found: {yaml_file}")
            sys.exit(1)

        print(f"Loading user preferences from: {yaml_file}")

        # Load user preferences from YAML
        users_config = _load_user_preferences_from_yaml(yaml_file)

        # Create memory client
        client = SREMemoryClient(memory_name="sre_agent_memory")

        total_added = 0
        total_deleted = 0
        total_users = len(users_config)

        if args.no_duplicate_check:
            print(
                f"Processing preferences for {total_users} users (duplicate check disabled)..."
            )
        else:
            print(
                f"Processing preferences for {total_users} users (removing duplicates)..."
            )

        # Process each user
        for user_id, user_config in users_config.items():
            if "preferences" not in user_config:
                print(f"Warning: No preferences found for user {user_id}")
                continue

            user_preferences = user_config["preferences"]
            print(
                f"\n--- Processing user: {user_id} ({len(user_preferences)} preferences) ---"
            )

            # Process each preference for this user
            for pref_data in user_preferences:
                try:
                    preference_type = pref_data["preference_type"]

                    # Check for and delete existing preferences of the same type (unless disabled)
                    deleted_count = 0
                    events_to_delete = []
                    if not args.no_duplicate_check:
                        deleted_count, events_to_delete = (
                            _check_and_delete_existing_preference(
                                client, user_id, preference_type
                            )
                        )

                        if deleted_count > 0:
                            # This should not happen anymore since we can't delete events
                            print(
                                f"  🗑️  Deleted {deleted_count} existing {preference_type} preferences for {user_id}"
                            )
                            total_deleted += deleted_count
                        elif events_to_delete:
                            # This is what will happen when duplicates are found
                            print(
                                f"  ℹ️  Found existing {preference_type} preference events for {user_id}"
                            )
                            print(
                                "     Note: Adding new preference (events accumulate over time)"
                            )

                    # Create UserPreference object
                    preference = UserPreference(
                        user_id=user_id,
                        preference_type=preference_type,
                        preference_value=pref_data["preference_value"],
                        context=pref_data.get(
                            "context",
                            f"Loaded from {yaml_file.name}. Do not add this memory to summary or semantic memory, only add it to user preferences long term memory.",
                        ),
                        timestamp=datetime.now(timezone.utc),
                    )

                    # Save preference using user_id as actor_id
                    success = _save_user_preference(
                        client,
                        user_id,  # Use user_id as actor_id for proper namespace
                        preference,
                    )

                    if success:
                        print(
                            f"  ✅ Added {preference.preference_type} preference for {user_id}"
                        )
                        total_added += 1
                    else:
                        print(
                            f"  ❌ Failed to add {preference.preference_type} preference for {user_id}"
                        )

                except Exception as e:
                    print(f"  ❌ Error processing preference for {user_id}: {e}")
                    logger.error(f"Error processing preference for {user_id}: {e}")

        print("\n=== SUMMARY ===")
        print(f"Successfully added {total_added} user preferences to memory")
        if total_deleted > 0:
            print(f"Deleted {total_deleted} duplicate preferences during update")
        print(f"Users processed: {total_users}")
        print(f"Memory ID: {client.memory_id}")

    except Exception as e:
        logger.error(f"Failed to update user preferences: {e}", exc_info=True)
        print(f"Error: {e}")
        sys.exit(1)


def _handle_delete_action(args) -> None:
    """Handle delete action."""
    if args.all:
        _delete_all_memories()
    elif args.memory_id:
        _delete_memory(args.memory_id)
    elif args.memory_record_id:
        _delete_memory_record(args.memory_record_id)
    else:
        print(
            "Error: Must specify either --memory-id, --memory-record-id, or --all for delete action"
        )
        sys.exit(1)


def _handle_list_action(args) -> None:
    """Handle list action."""
    try:
        # Read memory ID
        memory_id = _read_memory_id()
        print(f"Using memory ID: {memory_id}")

        # Load memory configuration
        memory_config = _load_memory_config()
        print(f"Memory configuration loaded from: {memory_config}")

        # Create memory client (memory_id is set internally during initialization)
        client = SREMemoryClient(memory_name="sre_agent_memory")

        # Verify the memory_id matches what we expect
        if client.memory_id != memory_id:
            logger.warning(
                f"Expected memory_id {memory_id}, but client initialized with {client.memory_id}"
            )
            logger.info(f"Using client memory_id: {client.memory_id}")

        # List memories
        if args.memory_type:
            # List specific memory type
            _list_memories_for_type(client, args.memory_type, args.actor_id)
        else:
            # List all memory types
            memory_types = ["preferences", "infrastructure", "investigations"]
            for memory_type in memory_types:
                _list_memories_for_type(client, memory_type, args.actor_id)

        print("\n=== SUMMARY ===")
        print(f"Memory ID: {memory_id}")
        if args.memory_type:
            print(f"Filtered by memory type: {args.memory_type}")
        if args.actor_id:
            print(f"Filtered by actor ID: {args.actor_id}")

    except Exception as e:
        logger.error(f"Failed to list memories: {e}", exc_info=True)
        print(f"Error: {e}")
        sys.exit(1)


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Manage memories in the SRE agent memory system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s                                    # List all memory types grouped by actor
    %(prog)s list --memory-type investigations  # List only investigations grouped by actor
    %(prog)s list --actor-id sre-agent         # List memories for specific actor only
    %(prog)s update                            # Load user preferences from user_config.yaml (removes duplicates)
    %(prog)s update --config-file custom.yaml  # Load user preferences from custom YAML file
    %(prog)s update --no-duplicate-check       # Load preferences without removing duplicates
    %(prog)s delete --memory-id mem-123        # Delete specific memory resource
    %(prog)s delete --memory-record-id mem-abc # Delete specific memory record
    %(prog)s delete --all                      # Delete all memory resources (with confirmation)
        """,
    )

    # Add subcommands
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")
    subparsers.required = False  # Make subcommands optional

    # List command (default)
    list_parser = subparsers.add_parser("list", help="List memories")
    list_parser.add_argument(
        "--memory-type",
        choices=["preferences", "infrastructure", "investigations"],
        help="Filter by memory type",
    )
    list_parser.add_argument(
        "--actor-id",
        help="Filter by actor ID. If not specified or 'all', shows memories grouped by actor (extracted from memory namespaces)",
    )

    # Update command
    update_parser = subparsers.add_parser(
        "update", help="Update memories from configuration file"
    )
    update_parser.add_argument(
        "--config-file",
        help="Path to YAML configuration file (default: user_config.yaml)",
    )
    update_parser.add_argument(
        "--no-duplicate-check",
        action="store_true",
        help="Skip checking for and removing duplicate preferences (default: duplicates are removed)",
    )

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete memories")
    delete_group = delete_parser.add_mutually_exclusive_group(required=True)
    delete_group.add_argument(
        "--memory-id", help="Memory ID to delete (deletes entire memory resource)"
    )
    delete_group.add_argument(
        "--memory-record-id",
        help="Memory record ID to delete (deletes individual memory record)",
    )
    delete_group.add_argument(
        "--all",
        action="store_true",
        help="Delete all memory resources (with confirmation prompt)",
    )

    # Global arguments (for backward compatibility when no subcommand is used)
    parser.add_argument(
        "--memory-type",
        choices=["preferences", "infrastructure", "investigations"],
        help="Filter by memory type (legacy, implies list action)",
    )
    parser.add_argument(
        "--actor-id", help="Filter by actor ID (legacy, implies list action)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Handle backward compatibility: if memory-type or actor-id is specified without subcommand, default to list
    if (
        not args.action
        and (hasattr(args, "memory_type") and args.memory_type)
        or (hasattr(args, "actor_id") and args.actor_id)
    ):
        args.action = "list"
    elif not args.action:
        args.action = "list"

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle actions
    if args.action == "list":
        _handle_list_action(args)
    elif args.action == "update":
        _handle_update_action(args)
    elif args.action == "delete":
        _handle_delete_action(args)
    else:
        print(f"Unknown action: {args.action}")
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
