#!/usr/bin/env python3
"""
Memory Manager for Bedrock AgentCore

This module handles the creation and management of memory resources for the Strands Data Analyst Assistant.
It provides functions to create and retrieve memory resources using the Bedrock AgentCore Memory Client.

Usage:
    python3 memory_manager.py create <memory_name> <parameter_store_name>
    python3 memory_manager.py list
"""

import sys
import logging
import boto3
from typing import Dict, Any, Optional, List
from bedrock_agentcore.memory import MemoryClient
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_MEMORY_NAME = "AssistantAgentMemory"
DEFAULT_EXPIRY_DAYS = 7


def create_memory(
    memory_name: str = DEFAULT_MEMORY_NAME,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
    parameter_store_name: Optional[str] = None,
) -> Optional[str]:
    """
    Create a new memory resource for the agent and store the memory ID in parameter store

    Args:
        memory_name (str): Name for the memory resource
        expiry_days (int): Retention period for short-term memory
        parameter_store_name (str): Name of the parameter store to update with memory ID

    Returns:
        str: Memory ID if successful, None otherwise
    """
    logger.info(f"Creating memory resource: {memory_name}")
    client = MemoryClient()

    try:
        # Create memory resource for short-term conversation storage
        memory = client.create_memory_and_wait(
            name=memory_name,
            strategies=[],  # No strategies means only short-term memory is used
            description="Short-term memory for data analyst assistant",
            event_expiry_days=expiry_days,  # Retention period for short-term memory (up to 365 days)
        )
        memory_id = memory["id"]
        logger.info(f"✅ Created memory: {memory_id}")

        # Store memory ID in parameter store if parameter_store_name is provided
        if parameter_store_name:
            try:
                ssm_client = boto3.client("ssm")
                ssm_client.put_parameter(
                    Name=parameter_store_name,
                    Value=memory_id,
                    Type="String",
                    Overwrite=True,
                )
                logger.info(f"✅ Stored memory ID in parameter store: {parameter_store_name}")
            except Exception as e:
                logger.error(f"❌ Failed to store memory ID in parameter store: {e}")

        return memory_id
    except ClientError as e:
        logger.info(f"❌ ERROR: {e}")
        return None
    except Exception as e:
        # Log any errors during memory creation
        logger.error(f"❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return None


def list_memories() -> List[Dict[str, Any]]:
    """
    List all available memory resources

    Returns:
        List[Dict]: List of memory resources
    """
    logger.info("Listing memory resources...")
    client = MemoryClient()

    try:
        memories = client.list_memories()
        logger.info(f"Found {len(memories)} memory resources:")

        if memories:
            print("\n📋 Memory Resources:")
            print("-" * 60)
            for i, memory in enumerate(memories, 1):
                memory_id = memory.get("id", "N/A")
                memory_name = memory.get("name", "N/A")
                status = memory.get("status", "N/A")
                created_time = memory.get("createdTime", "N/A")

                print(f"{i}. Name: {memory_name}")
                print(f"   ID: {memory_id}")
                print(f"   Status: {status}")
                print(f"   Created: {created_time}")
                print("-" * 60)
        else:
            print("No memory resources found.")

        return memories
    except Exception as e:
        logger.error(f"❌ ERROR listing memories: {e}")
        import traceback

        traceback.print_exc()
        return []


def main():
    """Main function to handle command line arguments"""
    if len(sys.argv) < 2:
        print("Usage: python3 memory_manager.py [create|list]")
        print("  create <memory_name> <parameter_store_name> - Create a new memory resource")
        print("  list   - List all existing memory resources")
        sys.exit(1)

    action = sys.argv[1].lower()

    if action == "create":
        if len(sys.argv) != 4:
            print("Usage: python3 memory_manager.py create <memory_name> <parameter_store_name>")
            print("  <memory_name> - Name for the memory resource")
            print("  <parameter_store_name> - Name of the parameter store to update with memory ID")
            sys.exit(1)

        memory_name = sys.argv[2]
        parameter_store_name = sys.argv[3]

        print(f"🚀 Creating memory resource: {memory_name}")
        print(f"📝 Parameter store name: {parameter_store_name}")

        memory_id = create_memory(memory_name=memory_name, parameter_store_name=parameter_store_name)
        if memory_id:
            print("✅ Memory created successfully!")
            print(f"Memory ID: {memory_id}")
            print(f"Memory ID stored in parameter store: {parameter_store_name}")
        else:
            print("❌ Failed to create memory")
            sys.exit(1)
    elif action == "list":
        print("📋 Listing memory resources...")
        memories = list_memories()
        if not memories:
            print("No memories found or error occurred")
    else:
        print(f"❌ Unknown action: {action}")
        print("Available actions: create, list")
        sys.exit(1)


if __name__ == "__main__":
    main()
