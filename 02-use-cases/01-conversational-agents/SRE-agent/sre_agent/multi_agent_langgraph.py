#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
import os
import random
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.errors import GraphRecursionError

from .agent_state import AgentState
from .constants import SREConstants
from .graph_builder import build_multi_agent_graph
from .logging_config import configure_logging, should_show_debug_traces

# Configure logging if not already configured (e.g., when imported by agent_runtime)
if not logging.getLogger().handlers:
    # Check if DEBUG is already set in environment
    debug_from_env = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    configure_logging(debug_from_env)

logger = logging.getLogger(__name__)

# Load environment variables from .env file in sre_agent directory
load_dotenv(Path(__file__).parent / ".env")


def _get_user_from_env() -> str:
    """Get user_id from environment variable.

    Returns:
        user_id from USER_ID environment variable or default
    """
    user_id = os.getenv("USER_ID")
    if user_id:
        logger.info(f"Using user_id from environment: {user_id}")
        return user_id
    else:
        # Fallback to default user_id
        default_user_id = SREConstants.agents.default_user_id
        logger.warning(
            f"USER_ID not set in environment, using default: {default_user_id}"
        )
        return default_user_id


def _get_session_from_env(mode: str) -> str:
    """Get session_id from environment variable or generate one.

    Args:
        mode: "interactive" or "prompt" for auto-generation prefix

    Returns:
        session_id from SESSION_ID environment variable or auto-generated
    """
    session_id = os.getenv("SESSION_ID")
    if session_id:
        logger.info(f"Using session_id from environment: {session_id}")
        return session_id
    else:
        # Auto-generate session_id
        auto_session_id = f"{mode}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        logger.info(
            f"SESSION_ID not set in environment, auto-generated: {auto_session_id}"
        )
        return auto_session_id


class Spinner:
    """Simple spinner animation with elapsed time display."""

    def __init__(self, message: str = "Thinking", show_time: bool = True):
        self.message = message
        self.show_time = show_time
        self.spinning = False
        self.thread: Optional[threading.Thread] = None
        self.start_time: Optional[float] = None
        self.spinner_chars = SREConstants.app.spinner_chars

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """Start the spinner animation."""
        self.spinning = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._spin)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        """Stop the spinner animation."""
        if self.spinning:
            self.spinning = False
            if self.thread:
                self.thread.join()
            # Clear the spinner line
            sys.stdout.write("\r" + " " * 50 + "\r")
            sys.stdout.flush()

    def _spin(self):
        """Internal method to run the spinner animation."""
        i = 0
        while self.spinning:
            elapsed = time.time() - self.start_time
            if self.show_time:
                time_str = f" ({elapsed:.1f}s)"
            else:
                time_str = ""

            spinner_char = self.spinner_chars[i % len(self.spinner_chars)]
            sys.stdout.write(f"\r{spinner_char} {self.message}{time_str}")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1


def _archive_old_reports(output_dir: str) -> None:
    """Archive reports from previous days into date-based folders."""
    try:
        output_path = Path(output_dir)
        if not output_path.exists():
            return

        # Get today's date in the format used in filenames (YYYYMMDD)
        today = datetime.now().strftime("%Y%m%d")

        # Process all .md and .log files in the reports directory
        for file_path in output_path.glob("*.md"):
            if not file_path.is_file():
                continue

            # Extract date from filename (format: YYYYMMDD)
            # Handle both old format (query_YYYYMMDD_HHMMSS.md) and new format (query_user_id_USER_YYYYMMDD_HHMMSS.md)
            filename = file_path.name
            date_match = re.search(r"202[0-9]{5}", filename)

            if date_match:
                date_part = date_match.group()

                # Only move files that are not from today
                if date_part != today:
                    # Extract year, month, day
                    year = date_part[:4]
                    month = date_part[4:6]
                    day = date_part[6:8]
                    date_folder_name = f"{year}-{month}-{day}"

                    # Create date folder if it doesn't exist
                    date_folder = output_path / date_folder_name
                    date_folder.mkdir(exist_ok=True)

                    # Move file to date folder
                    destination = date_folder / filename
                    if not destination.exists():  # Avoid overwriting existing files
                        shutil.move(str(file_path), str(destination))
                        logger.info(f"Archived {filename} to {date_folder_name}/")

        # Also process .log files
        for file_path in output_path.glob("*.log"):
            if not file_path.is_file():
                continue

            # Extract date from filename (format: YYYYMMDD)
            # Handle both old format (query_YYYYMMDD_HHMMSS.log) and new format (query_user_id_USER_YYYYMMDD_HHMMSS.log)
            filename = file_path.name
            date_match = re.search(r"202[0-9]{5}", filename)

            if date_match:
                date_part = date_match.group()

                # Only move files that are not from today
                if date_part != today:
                    # Extract year, month, day
                    year = date_part[:4]
                    month = date_part[4:6]
                    day = date_part[6:8]
                    date_folder_name = f"{year}-{month}-{day}"

                    # Create date folder if it doesn't exist
                    date_folder = output_path / date_folder_name
                    date_folder.mkdir(exist_ok=True)

                    # Move file to date folder
                    destination = date_folder / filename
                    if not destination.exists():  # Avoid overwriting existing files
                        shutil.move(str(file_path), str(destination))
                        logger.info(f"Archived {filename} to {date_folder_name}/")

    except Exception as e:
        logger.warning(f"Failed to archive old reports: {e}")


def _save_final_response_to_markdown(
    query: str,
    final_response: str,
    user_id: Optional[str] = None,
    timestamp: Optional[datetime] = None,
    output_dir: str = ".",
    filename_prefix: str = "sre_investigation",
) -> str:
    """Save final response to a markdown file."""
    if timestamp is None:
        timestamp = datetime.now()

    # Create output directory if it doesn't exist
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Archive old reports before saving new one
    _archive_old_reports(output_dir)

    # Create filename with query and timestamp
    # Clean the query string for filename use
    clean_query = (
        query.replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace("?", "_")
        .replace(":", "_")
        .replace(",", "_")
        .replace(".", "_")
    )
    # Remove special characters that might cause issues
    clean_query = "".join(c for c in clean_query if c.isalnum() or c in "_-")
    # Remove leading/trailing underscores and collapse multiple underscores
    clean_query = "_".join(part for part in clean_query.split("_") if part)
    # Limit length to avoid overly long filenames (increased from 50 to 80 for better descriptiveness)
    if len(clean_query) > 80:
        clean_query = clean_query[:80]
    # Ensure we have a meaningful filename
    if not clean_query or len(clean_query) < 3:
        clean_query = "query"

    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")

    # Include user_id in filename if provided
    if user_id:
        filename = f"{clean_query}_user_id_{user_id}_{timestamp_str}.md"
    else:
        filename = f"{clean_query}_{timestamp_str}.md"

    filepath = output_path / filename

    # Create markdown content
    markdown_content = f"""# SRE Investigation Report

**Generated:** {timestamp.strftime("%Y-%m-%d %H:%M:%S")}

**Query:** {query}

---

{final_response}

---
*Report generated by SRE Multi-Agent Assistant*
"""

    try:
        # Write to file
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        logger.info(f"Final response saved to: {filepath}")
        return str(filepath)

    except Exception as e:
        logger.error(f"Failed to save final response to markdown: {e}")
        print(f"❌ Failed to save report: {e}")
        return ""


@tool
def get_current_time() -> str:
    """Get current date and time in ISO format.

    This tool provides the current timestamp which is essential for debugging
    time-sensitive issues and correlating events across different systems.

    Returns:
        str: Current datetime in ISO format (YYYY-MM-DDTHH:MM:SS)
    """
    return datetime.now().isoformat()


def _get_anthropic_api_key() -> str:
    """Get Anthropic API key from environment variables."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is required for Anthropic provider"
        )
    return api_key


def _read_gateway_config() -> tuple[str, str]:
    """Read gateway URI from config and access token from environment."""
    try:
        # Load environment variables from sre_agent directory
        load_dotenv(Path(__file__).parent / ".env")

        # Read gateway URI and region from agent_config.yaml
        config_path = Path(__file__).parent / "config" / "agent_config.yaml"
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        # Handle case where 'gateway' key might be None
        gateway_config = config.get("gateway") or {}
        gateway_uri = (
            gateway_config.get("uri") if isinstance(gateway_config, dict) else None
        )
        if not gateway_uri:
            raise ValueError(
                "Gateway URI not found in agent_config.yaml under 'gateway.uri'"
            )

        # Get AWS region with fallback logic: config -> AWS_REGION env var -> us-east-1
        # Handle case where 'aws' key might be None
        aws_config = config.get("aws") or {}
        aws_region = aws_config.get("region") if isinstance(aws_config, dict) else None
        if not aws_region:
            aws_region = os.environ.get("AWS_REGION", "us-east-1")

        # Read access token from environment
        access_token = os.getenv("GATEWAY_ACCESS_TOKEN")
        if not access_token:
            raise ValueError("GATEWAY_ACCESS_TOKEN environment variable is required")

        return gateway_uri.rstrip("/"), access_token, aws_region
    except Exception as e:
        logger.error(f"Error reading gateway configuration: {e}")
        raise


def create_mcp_client() -> MultiServerMCPClient:
    """Create and return MultiServerMCPClient with gateway configuration."""
    gateway_uri, access_token, _ = _read_gateway_config()  # Region not needed here

    # Configure MCP server connection
    client = MultiServerMCPClient(
        {
            "gateway": {
                "url": f"{gateway_uri}/mcp",
                "transport": "streamable_http",
                "headers": {"Authorization": f"Bearer {access_token}"},
            }
        }
    )

    return client


async def create_multi_agent_system(
    provider: str = "bedrock",
    checkpointer=None,
    force_delete_memory: bool = False,
    export_graph: bool = False,
    graph_output_path: str = "./docs/sre_agent_architecture.md",
    region_name: str = None,
    **llm_kwargs,
):
    """Create multi-agent system with MCP tools."""
    logger.info(f"Creating multi-agent system with provider: {provider}")

    # Get Anthropic API key if needed
    if provider == "anthropic" and not llm_kwargs.get("api_key"):
        llm_kwargs["api_key"] = _get_anthropic_api_key()

    # Add region_name to llm_kwargs for bedrock provider
    if provider == "bedrock" and region_name:
        llm_kwargs["region_name"] = region_name
        logger.info(f"Using AWS region for Bedrock: {region_name}")

    # Create MCP client and get tools with retry logic
    mcp_tools = []
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            client = create_mcp_client()
            # Add timeout for MCP tool loading to prevent hanging
            all_mcp_tools = await asyncio.wait_for(
                client.get_tools(),
                timeout=SREConstants.timeouts.mcp_tools_timeout_seconds,
            )

            # Don't filter out x-amz-agentcore-search as it's a global tool
            mcp_tools = all_mcp_tools

            logger.info(f"Retrieved {len(mcp_tools)} tools from MCP")

            # Print tool information (only in debug mode)
            logger.info(f"MCP tools loaded: {len(mcp_tools)}")
            if should_show_debug_traces():
                print(f"\nMCP tools loaded: {len(mcp_tools)}")
                for tool in mcp_tools:
                    tool_name = getattr(tool, "name", "unknown")
                    tool_desc = getattr(tool, "description", "No description")
                    print(f"  - {tool_name}: {tool_desc[:80]}...")
                    logger.info(f"  - {tool_name}: {tool_desc[:80]}...")

            # Success - break out of retry loop
            break

        except asyncio.TimeoutError:
            logger.warning("MCP tool loading timed out after 30 seconds")
            mcp_tools = []
            break  # Don't retry on timeout

        except Exception as e:
            retry_count += 1
            error_msg = str(e)

            # Check if it's a rate limiting error (429)
            if "429" in error_msg or "Too Many Requests" in error_msg:
                if retry_count < max_retries:
                    # Exponential backoff with jitter
                    base_delay = 2**retry_count  # 2, 4, 8 seconds
                    jitter = random.uniform(0, 1)  # Add 0-1 second random jitter
                    wait_time = base_delay + jitter

                    logger.warning(
                        f"Rate limited by MCP server (attempt {retry_count}/{max_retries}). "
                        f"Waiting {wait_time:.1f} seconds before retry..."
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(
                        f"Failed to load MCP tools after {max_retries} retries: {e}"
                    )
                    mcp_tools = []
            else:
                # For other errors, don't retry
                logger.warning(f"Failed to load MCP tools: {e}")
                mcp_tools = []
                break

    # Combine local tools with MCP tools
    local_tools = [get_current_time]

    # Add memory tools if memory system is enabled
    memory_tools = []
    try:
        from .memory.client import SREMemoryClient
        from .memory.config import _load_memory_config
        from .memory.tools import create_memory_tools

        memory_config = _load_memory_config()
        if memory_config.enabled:
            logger.debug("Adding memory tools to agent tool list")
            # Use the region from parameter if provided, otherwise use config default
            memory_region = region_name if region_name else memory_config.region
            memory_client = SREMemoryClient(
                memory_name=memory_config.memory_name,
                region=memory_region,
                force_delete=force_delete_memory,
            )
            logger.info(f"Using AWS region for memory: {memory_region}")
            memory_tools = create_memory_tools(memory_client)
            logger.info(f"Added {len(memory_tools)} memory tools to agent tool list")
        else:
            logger.info("Memory system disabled - no memory tools added")
    except Exception as e:
        logger.warning(f"Failed to add memory tools: {e}")
        memory_tools = []

    all_tools = local_tools + mcp_tools + memory_tools

    # Debug: Show all tools being passed to agents
    logger.info(f"Total tools being passed to agents: {len(all_tools)}")
    logger.info(f"  - Local tools: {len(local_tools)}")
    logger.info(f"  - MCP tools: {len(mcp_tools)}")
    logger.info(f"  - Memory tools: {len(memory_tools)}")

    # Log detailed memory tool information
    if memory_tools:
        logger.info("Memory tools details:")
        for tool in memory_tools:
            logger.info(f"    Tool: {getattr(tool, 'name', 'unknown')}")
            logger.info(
                f"      Description: {getattr(tool, 'description', 'No description')}"
            )
            # Log args schema details
            args_schema = getattr(tool, "args_schema", None)
            if args_schema:
                logger.info(f"      Args schema: {args_schema.__name__}")
                # Handle both Pydantic v1 and v2
                if hasattr(args_schema, "model_fields"):
                    # Pydantic v2
                    for field_name, field_info in args_schema.model_fields.items():
                        field_type = str(field_info.annotation)
                        field_desc = (
                            field_info.description
                            if field_info.description
                            else "No description"
                        )
                        field_default = (
                            str(field_info.default)
                            if field_info.default is not None
                            else "No default"
                        )
                        logger.info(
                            f"        - {field_name}: {field_type} (description: {field_desc}, default: {field_default})"
                        )
                elif hasattr(args_schema, "__fields__"):
                    # Pydantic v1
                    for field_name, field_info in args_schema.__fields__.items():
                        field_type = str(field_info.type_)
                        field_desc = (
                            field_info.field_info.description
                            if hasattr(field_info.field_info, "description")
                            else "No description"
                        )
                        field_default = (
                            str(field_info.default)
                            if field_info.default is not None
                            else "No default"
                        )
                        logger.info(
                            f"        - {field_name}: {field_type} (description: {field_desc}, default: {field_default})"
                        )
            else:
                logger.info("      Args schema: No schema")
            # Log additional attributes if present
            if hasattr(tool, "memory_client"):
                logger.info("      Has memory_client: Yes")
            logger.info(f"      Tool class: {tool.__class__.__name__}")

    logger.info("All tool names:")
    for tool in all_tools:
        tool_name = getattr(tool, "name", "unknown")
        tool_description = getattr(tool, "description", "No description")
        # Extract just the first line of description for cleaner logging
        description_first_line = (
            tool_description.split("\n")[0].strip()
            if tool_description
            else "No description"
        )
        logger.info(f"  - {tool_name}: {description_first_line}")

    logger.info(f"Additional local tools: {len(local_tools)}")
    if should_show_debug_traces():
        print(f"\nAdditional local tools: {len(local_tools)}")
        for tool in local_tools:
            # Extract just the first line of description
            description = (
                tool.description.split("\n")[0].strip()
                if tool.description
                else "No description"
            )
            print(f"  - {tool.name}: {description}")
            logger.info(f"  - {tool.name}: {description}")

    # Build the multi-agent graph
    graph = build_multi_agent_graph(
        tools=all_tools,
        llm_provider=provider,
        force_delete_memory=force_delete_memory,
        export_graph=export_graph,
        graph_output_path=graph_output_path,
        **llm_kwargs,
    )

    return graph, all_tools


def _save_conversation_state(
    messages: list,
    state: Dict[str, Any],
    filename: str = SREConstants.app.conversation_state_file,
):
    """Save conversation state to a file."""
    try:
        # Convert messages to serializable format
        serializable_messages = []
        for msg in messages:
            if hasattr(msg, "model_dump"):
                serializable_messages.append(msg.model_dump())
            elif hasattr(msg, "dict"):
                serializable_messages.append(msg.dict())
            elif hasattr(msg, "content"):
                serializable_messages.append(
                    {"role": getattr(msg, "role", "unknown"), "content": msg.content}
                )
            else:
                serializable_messages.append(str(msg))

        # Convert state to serializable format
        serializable_state = {}
        if isinstance(state, dict):
            # Filter out non-serializable items
            for k, v in state.items():
                if k == "messages":
                    continue  # Already handled above
                elif isinstance(v, (str, int, float, bool, list, dict, type(None))):
                    serializable_state[k] = v
                else:
                    serializable_state[k] = str(v)

        with open(filename, "w") as f:
            json.dump(
                {
                    "messages": serializable_messages,
                    "state": serializable_state,
                    "timestamp": datetime.now().isoformat(),
                },
                f,
                indent=2,
            )
        logger.debug(f"Saved conversation state to {filename}")
    except Exception as e:
        logger.error(f"Failed to save conversation state: {e}")


def _load_conversation_state(
    filename: str = SREConstants.app.conversation_state_file,
) -> tuple[Optional[list], Optional[Dict[str, Any]]]:
    """Load conversation state from a file."""
    try:
        if Path(filename).exists():
            with open(filename, "r") as f:
                data = json.load(f)
                logger.info(f"Loaded conversation state from {filename}")
                return data.get("messages", []), data.get("state", {})
    except Exception as e:
        logger.error(f"Failed to load conversation state: {e}")
    return None, None


async def _run_interactive_session(
    provider: str,
    save_state: bool = True,
    output_dir: str = "./reports",
    save_markdown: bool = True,
    force_delete_memory: bool = False,
    region_name: str = "us-east-1",
):
    """Run an interactive multi-turn conversation session."""
    # Buffer to store last query and response for /savereport command
    last_response = None
    # Track the original query for report naming (resets after each /savereport)
    original_query = None
    # Session ID management - generates new session after /savereport or at start
    current_session_id = f"interactive-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    print("\n🤖 Starting interactive multi-agent SRE assistant...")
    logger.info("🤖 Starting interactive multi-agent SRE assistant...")
    print("Commands:")
    print("  /exit or /quit - End the session")
    print("  /clear - Clear conversation history")
    print("  /save - Save conversation state")
    print("  /load - Load previous conversation state")
    print("  /savereport - Save the last query's investigation report")
    print("  /history - Show conversation history")
    print("  /agents - Show available agents")
    print("  /help - Show this help message")
    print(
        "\nNote: Investigation reports are not saved automatically in interactive mode."
    )
    print("      Use /savereport to save the last query's report when needed.")
    print("\n" + "=" * 80 + "\n")

    # Load previous conversation if exists
    saved_messages, saved_state = None, None
    if save_state:
        saved_messages, saved_state = _load_conversation_state()

    # Create multi-agent system
    graph, all_tools = await create_multi_agent_system(
        provider,
        force_delete_memory=force_delete_memory,
        export_graph=False,  # Don't export in interactive mode each time
        region_name=region_name,
    )

    # Initialize conversation state
    messages = []
    if saved_messages:
        # Convert saved messages to LangChain format
        for msg in saved_messages:
            if isinstance(msg, dict):
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=msg.get("content", "")))
                elif msg.get("role") == "assistant":
                    messages.append(AIMessage(content=msg.get("content", "")))

    while True:
        try:
            # Get user input
            user_input = input("\n👤 You: ").strip()

            # Handle commands
            if user_input.lower() in ["/exit", "/quit"]:
                print("\n👋 Goodbye!")
                if save_state and messages:
                    _save_conversation_state(messages, {})
                break

            elif user_input.lower() == "/clear":
                messages = []
                last_response = None
                original_query = None
                print("✨ Conversation history and report buffer cleared.")
                continue

            elif user_input.lower() == "/save":
                _save_conversation_state(messages, {})
                print("💾 Conversation state saved.")
                continue

            elif user_input.lower() == "/load":
                loaded_messages, loaded_state = _load_conversation_state()
                if loaded_messages is not None:
                    messages = []
                    for msg in loaded_messages:
                        if isinstance(msg, dict):
                            if msg.get("role") == "user":
                                messages.append(
                                    HumanMessage(content=msg.get("content", ""))
                                )
                            elif msg.get("role") == "assistant":
                                messages.append(
                                    AIMessage(content=msg.get("content", ""))
                                )
                    print("📂 Previous conversation loaded.")
                else:
                    print("❌ No saved conversation found.")
                continue

            elif user_input.lower() == "/savereport":
                if original_query and last_response:
                    user_id = _get_user_from_env()
                    filepath = _save_final_response_to_markdown(
                        original_query,
                        last_response,
                        user_id=user_id,
                        output_dir=output_dir,
                    )
                    if filepath:
                        print(f"📄 Investigation report saved to: {filepath}")
                        # Clear the buffer after saving and reset for next investigation
                        last_response = None
                        original_query = None
                        # Generate new session ID for next conversation
                        current_session_id = (
                            f"interactive-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                        )
                        logger.info(
                            f"Generated new session ID for next conversation: {current_session_id}"
                        )
                        print("✨ New conversation session started.")
                    else:
                        print("❌ Failed to save report")
                else:
                    print(
                        "❌ No investigation report available to save. Complete a query first."
                    )
                continue

            elif user_input.lower() == "/history":
                print("\n📜 Conversation History:")
                for msg in messages:
                    if hasattr(msg, "content"):
                        role = type(msg).__name__.replace("Message", "").lower()
                        content = msg.content
                        print(
                            f"{role.upper()}: {content[:100]}..."
                            if len(content) > 100
                            else f"{role.upper()}: {content}"
                        )
                continue

            elif user_input.lower() == "/agents":
                print("\n🤝 Available Agents:")
                print("  1. Supervisor Agent - Orchestrates and routes queries")
                print(
                    "  2. Kubernetes Infrastructure Agent - K8s operations and monitoring"
                )
                print("  3. Application Logs Agent - Log analysis and searching")
                print(
                    "  4. Performance Metrics Agent - Performance and resource metrics"
                )
                print(
                    "  5. Operational Runbooks Agent - Procedures and troubleshooting guides"
                )
                continue

            elif user_input.lower() == "/help":
                print("\n🤖 SRE Multi-Agent Assistant Help")
                print("=" * 50)
                print("\nCommands:")
                print("  /exit or /quit - End the session")
                print("  /clear - Clear conversation history")
                print("  /save - Save conversation state")
                print("  /load - Load previous conversation state")
                print("  /savereport - Save the last query's investigation report")
                print("  /history - Show conversation history")
                print("  /agents - Show available agents")
                print("  /help - Show this help message")
                print("\nReport Saving:")
                print(
                    "  • Investigation reports are NOT saved automatically in interactive mode"
                )
                print("  • Use /savereport after completing a query to save its report")
                print("  • Reports are saved as markdown files with descriptive names")
                print("  • Use /save to save conversation state separately")
                print("\nTips:")
                print(
                    "  • Ask specific questions about infrastructure, logs, metrics, or procedures"
                )
                print(
                    "  • The agents will collaborate to provide comprehensive answers"
                )
                print("  • You can continue conversations and ask follow-up questions")
                continue

            if not user_input:
                continue

            # Get user_id from environment and use input as-is
            user_id = _get_user_from_env()
            cleaned_query = user_input

            # Track original query for report naming (only set if not already set)
            if original_query is None:
                original_query = cleaned_query  # Use cleaned query for reports

            # Process with multi-agent system
            print("\n🤖 Multi-Agent System: Processing...\n")
            logger.info("🤖 Multi-Agent System: Processing...")

            # Add user message with cleaned query
            messages.append(HumanMessage(content=cleaned_query))

            # Create initial state
            initial_state: AgentState = {
                "messages": messages,
                "next": "supervisor",
                "agent_results": {},
                "current_query": cleaned_query,
                "metadata": {},
                "requires_collaboration": False,
                "agents_invoked": [],
                "final_response": None,
                "auto_approve_plan": False,  # Default to False for interactive mode
                "user_id": user_id,  # Add extracted user_id
                "session_id": current_session_id,  # Add session ID for conversation tracking
            }

            # Stream the graph execution
            try:
                # Start initial spinner for supervisor
                spinner = Spinner("🧭 Supervisor analyzing query")
                spinner.start()

                # Stream with timeout protection
                timeout_seconds = SREConstants.timeouts.graph_execution_timeout_seconds
                start_time = asyncio.get_event_loop().time()

                async for event in graph.astream(initial_state):
                    # Check for timeout
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed > timeout_seconds:
                        raise asyncio.TimeoutError(
                            f"Graph execution exceeded {timeout_seconds} seconds"
                        )
                    # Stop spinner when we get an event
                    if spinner:
                        spinner.stop()
                        spinner = None

                    # Print progress updates
                    for node_name, node_output in event.items():
                        if node_name == "supervisor":
                            next_agent = node_output.get("next", "unknown")
                            metadata = node_output.get("metadata", {})
                            reasoning = metadata.get("routing_reasoning", "")

                            # Display investigation plan only once when first created
                            if metadata.get("plan_pending_approval"):
                                plan_text = metadata.get("plan_text", "")
                                if plan_text:
                                    print(f"\n📋 {plan_text}")
                                    logger.info(f"📋 {plan_text}")
                            elif metadata.get("show_plan") and not metadata.get(
                                "plan_shown"
                            ):
                                plan_text = metadata.get("plan_text", "")
                                if plan_text:
                                    print(f"\n📋 {plan_text}")
                                    logger.info(f"📋 {plan_text}")
                                # Mark plan as shown to avoid repetition
                                metadata["plan_shown"] = True

                            if next_agent != "FINISH":
                                print(f"🧭 Supervisor: Routing to {next_agent}")
                                logger.info(f"🧭 Supervisor: Routing to {next_agent}")
                                if reasoning:
                                    print(f"   Reasoning: {reasoning}")
                                    logger.info(f"   Reasoning: {reasoning}")
                                # Start spinner for next agent
                                agent_display = next_agent.replace("_", " ").title()
                                spinner = Spinner(f"🤖 {agent_display} thinking")
                                spinner.start()
                            elif metadata.get("plan_pending_approval"):
                                print("🧭 Supervisor: Plan created, awaiting approval")

                        elif node_name in [
                            "kubernetes_agent",
                            "logs_agent",
                            "metrics_agent",
                            "runbooks_agent",
                        ]:
                            agent_name = node_name.replace("_agent", "").title()
                            print(f"\n🔧 {agent_name} Agent:")
                            logger.info(f"🔧 {agent_name} Agent:")

                            # Extract and display tool traces from metadata
                            metadata = node_output.get("metadata", {})
                            # Look for traces using various possible key formats
                            agent_messages = []
                            for key, value in metadata.items():
                                if "_trace" in key and isinstance(value, list):
                                    agent_messages = value
                                    break

                            # Show debug info about trace messages found (only in debug mode)
                            if should_show_debug_traces():
                                print(
                                    f"   🔍 DEBUG: agent_messages = {len(agent_messages) if agent_messages else 0}"
                                )
                            if agent_messages and should_show_debug_traces():
                                print(
                                    f"   📋 Found {len(agent_messages)} trace messages:"
                                )
                                for i, msg in enumerate(agent_messages):
                                    msg_type = type(msg).__name__
                                    if hasattr(msg, "content"):
                                        content_preview = str(
                                            msg.content
                                        )  # Show full content
                                    else:
                                        content_preview = "No content"
                                    print(
                                        f"      {i + 1}. {msg_type}: {content_preview}"
                                    )
                                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                                        print(
                                            f"         Tool calls: {len(msg.tool_calls)}"
                                        )
                                    if hasattr(msg, "tool_call_id"):
                                        print(
                                            f"         Tool response for: {getattr(msg, 'tool_call_id', 'unknown')}"
                                        )
                            elif should_show_debug_traces():
                                print("   ⚠️  No trace messages found in metadata")
                                logger.info("   ⚠️  No trace messages found in metadata")

                            # Display tool calls and results like in langgraph_agent.py (only in debug mode)
                            if should_show_debug_traces():
                                for msg in agent_messages:
                                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                                        print("   📞 Calling tools:")
                                        logger.info("   📞 Calling tools:")
                                        for tc in msg.tool_calls:
                                            tool_name = tc.get("name", "unknown")
                                            tool_args = tc.get("args", {})
                                            tool_id = tc.get("id", "unknown")
                                            print(f"      {tool_name}(")
                                            logger.info(f"      {tool_name}(")
                                            if tool_args:
                                                for (
                                                    arg_name,
                                                    arg_value,
                                                ) in tool_args.items():
                                                    # Show full values
                                                    value_str = repr(arg_value)
                                                    print(
                                                        f"        {arg_name}={value_str}"
                                                    )
                                                    logger.info(
                                                        f"        {arg_name}={value_str}"
                                                    )
                                            print(f"      ) [id: {tool_id}]")
                                            logger.info(f"      ) [id: {tool_id}]")

                                    elif hasattr(msg, "tool_call_id"):
                                        # This is a tool response
                                        tool_name = getattr(msg, "name", "unknown_tool")
                                        tool_call_id = getattr(
                                            msg, "tool_call_id", "unknown"
                                        )
                                        result_content = msg.content

                                        print(
                                            f"   🛠️  {tool_name} [id: {tool_call_id}]:"
                                        )
                                        if isinstance(result_content, str):
                                            try:
                                                parsed_result = json.loads(
                                                    result_content
                                                )
                                                # Pretty print full output
                                                formatted = json.dumps(
                                                    parsed_result, indent=2
                                                )
                                                lines = formatted.split("\n")
                                                for line in lines:
                                                    print(f"      {line}")
                                            except Exception:
                                                # Not JSON, print full string
                                                lines = result_content.split("\n")
                                                for line in lines:
                                                    print(f"      {line}")

                            # Show agent's full final response
                            agent_results = node_output.get("agent_results", {})
                            for agent_key, result in agent_results.items():
                                if (
                                    agent_key in node_name
                                    or node_name.replace("_agent", "")
                                    in agent_key.lower()
                                ):
                                    if result:
                                        print("   💡 Full Response:")
                                        logger.info("   💡 Full Response:")
                                        print(f"      {result}")
                                        logger.info(f"      {result}")

                        elif node_name == "aggregate":
                            final_response = node_output.get("final_response", "")
                            if final_response:
                                print(f"\n💬 Final Response:\n{final_response}")
                                logger.info(f"💬 Final Response: {final_response}")
                                # Add assistant message to history
                                messages.append(AIMessage(content=final_response))
                                # Store for /savereport command instead of auto-saving
                                if save_markdown:
                                    last_response = final_response
                                    print(
                                        "\n💡 Use /savereport to save this investigation report."
                                    )

            except asyncio.TimeoutError:
                if spinner:
                    spinner.stop()
                print(
                    "\n❌ Error: Investigation timed out after 10 minutes. The system may be stuck."
                )
                print(
                    "💡 Tip: Try rephrasing your question or breaking it into smaller parts."
                )
                logger.error("Graph execution timed out after 600 seconds")
            except GraphRecursionError:
                if spinner:
                    spinner.stop()
                print(
                    "\n❌ Error: Maximum recursion limit reached. The agents may be stuck in a loop."
                )
                print("💡 Tip: Try rephrasing your question or being more specific.")
            except Exception as e:
                if spinner:
                    spinner.stop()
                logger.error(f"Error in multi-agent execution: {e}")
                print(f"\n❌ Error: {e}")
            finally:
                # Always clean up spinner
                if spinner:
                    spinner.stop()

            # Auto-save after each turn if enabled
            if save_state:
                _save_conversation_state(messages, {})

        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted. Type /exit to quit.")
            continue
        except Exception as e:
            logger.error(f"Error in conversation: {e}")
            print(f"\n❌ Error: {e}")


async def main():
    """Main function for control flow."""
    parser = argparse.ArgumentParser(
        description="Multi-agent SRE assistant with specialized agents"
    )
    parser.add_argument(
        "--provider",
        choices=["bedrock", "anthropic"],
        default="bedrock",
        help="Model provider to use (default: bedrock)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging and trace output",
    )
    parser.add_argument(
        "--prompt",
        help="Single prompt to send to the multi-agent system (if not provided, starts interactive mode)",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Start interactive multi-turn conversation mode",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Disable automatic conversation state saving in interactive mode",
    )
    parser.add_argument(
        "--output-dir",
        default=SREConstants.app.default_output_dir,
        help=f"Directory to save investigation reports (default: {SREConstants.app.default_output_dir})",
    )
    parser.add_argument(
        "--no-markdown",
        action="store_true",
        help="Disable saving final responses to markdown files",
    )
    parser.add_argument(
        "--force-delete-memory",
        action="store_true",
        help="Force delete and recreate the memory system (WARNING: This will delete all saved memories)",
    )
    parser.add_argument(
        "--export-graph",
        action="store_true",
        help="Export the agent architecture as a Mermaid diagram",
    )
    parser.add_argument(
        "--graph-output",
        default="./docs/sre_agent_architecture.md",
        help="Path to save the exported Mermaid diagram (default: ./docs/sre_agent_architecture.md)",
    )

    args = parser.parse_args()

    # Configure logging based on debug flag
    debug_enabled = configure_logging(args.debug)

    # Load AWS region with fallback logic: config -> AWS_REGION env var -> us-east-1
    try:
        config_path = Path(__file__).parent / "config" / "agent_config.yaml"
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        # Try to get region from config first
        # Handle case where 'aws' key might be None
        aws_config = config.get("aws") or {}
        aws_region = aws_config.get("region") if isinstance(aws_config, dict) else None

        if aws_region:
            logger.info(f"Using AWS region from agent_config.yaml: {aws_region}")
        else:
            # Fallback to AWS_REGION environment variable
            aws_region = os.environ.get("AWS_REGION")
            if aws_region:
                logger.info(
                    f"Using AWS region from AWS_REGION environment variable: {aws_region}"
                )
            else:
                # Final fallback to us-east-1
                aws_region = "us-east-1"
                logger.info(f"Using default AWS region: {aws_region}")

    except Exception as e:
        logger.warning(f"Failed to load AWS region from config: {e}")
        # Try environment variable, then default
        aws_region = os.environ.get("AWS_REGION", "us-east-1")
        logger.info(f"Using AWS region fallback: {aws_region}")

    # Set environment variable so other modules can check debug status
    os.environ["DEBUG"] = "true" if debug_enabled else "false"

    logger.info(f"Starting multi-agent system with provider: {args.provider}")
    if debug_enabled:
        logger.info("Debug logging enabled")

    try:
        logger.info(f"🚀 Starting SRE Agent with provider: {args.provider}")

        # Interactive mode
        if args.interactive or not args.prompt:
            # Export graph before starting interactive session if requested
            if args.export_graph:
                print(f"\n📊 Exporting agent architecture to {args.graph_output}...")
                await create_multi_agent_system(
                    provider=args.provider,
                    force_delete_memory=args.force_delete_memory,
                    export_graph=True,
                    graph_output_path=args.graph_output,
                    region_name=aws_region,
                )

            await _run_interactive_session(
                provider=args.provider,
                save_state=not args.no_save,
                output_dir=args.output_dir,
                save_markdown=not args.no_markdown,
                force_delete_memory=args.force_delete_memory,
                region_name=aws_region,
            )
        # Single prompt mode
        else:
            try:
                graph, all_tools = await create_multi_agent_system(
                    args.provider,
                    force_delete_memory=args.force_delete_memory,
                    export_graph=args.export_graph,
                    graph_output_path=args.graph_output,
                    region_name=aws_region,
                )
                logger.info("Multi-agent system created successfully")
            except Exception as e:
                from .llm_utils import (
                    LLMAccessError,
                    LLMAuthenticationError,
                    LLMProviderError,
                )

                if isinstance(
                    e, (LLMAuthenticationError, LLMAccessError, LLMProviderError)
                ):
                    print(f"\n❌ {type(e).__name__}:")
                    print(str(e))
                    print("\n💡 Quick fix: Try running with the other provider:")
                    other_provider = (
                        "anthropic" if args.provider == "bedrock" else "bedrock"
                    )
                    print(
                        f'   sre-agent --provider {other_provider} --prompt "your query"'
                    )
                    return
                else:
                    raise

            # Get user_id from environment and use prompt as-is
            user_id = _get_user_from_env()
            cleaned_query = args.prompt

            # Generate session ID for this prompt-mode conversation
            prompt_session_id = f"prompt-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            logger.info(
                f"Generated session ID for prompt mode: {prompt_session_id}, user_id: {user_id}"
            )

            # Create initial state
            initial_state: AgentState = {
                "messages": [HumanMessage(content=cleaned_query)],
                "next": "supervisor",
                "agent_results": {},
                "current_query": cleaned_query,
                "metadata": {},
                "requires_collaboration": False,
                "agents_invoked": [],
                "final_response": None,
                "auto_approve_plan": True,  # Auto-approve plans in prompt mode
                "user_id": user_id,  # Add extracted user_id
                "session_id": prompt_session_id,  # Add session ID for conversation tracking
            }

            print("🤖 Multi-Agent System:\n")

            # Execute the graph
            # Start initial spinner for supervisor
            spinner = Spinner("🧭 Supervisor analyzing query")
            spinner.start()

            try:
                # Stream with timeout protection
                timeout_seconds = SREConstants.timeouts.graph_execution_timeout_seconds
                start_time = asyncio.get_event_loop().time()

                async for event in graph.astream(initial_state):
                    # Check for timeout
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed > timeout_seconds:
                        raise asyncio.TimeoutError(
                            f"Graph execution exceeded {timeout_seconds} seconds"
                        )
                    # Stop spinner when we get an event
                    if spinner:
                        spinner.stop()
                        spinner = None

                    for node_name, node_output in event.items():
                        if node_name == "supervisor":
                            next_agent = node_output.get("next", "unknown")
                            metadata = node_output.get("metadata", {})
                            reasoning = metadata.get("routing_reasoning", "")

                            # Display investigation plan only once when first created
                            if metadata.get("plan_pending_approval"):
                                plan_text = metadata.get("plan_text", "")
                                if plan_text:
                                    print(f"\n📋 {plan_text}")
                                    logger.info(f"📋 {plan_text}")
                            elif metadata.get("show_plan") and not metadata.get(
                                "plan_shown"
                            ):
                                plan_text = metadata.get("plan_text", "")
                                if plan_text:
                                    print(f"\n📋 {plan_text}")
                                    logger.info(f"📋 {plan_text}")
                                # Mark plan as shown to avoid repetition
                                metadata["plan_shown"] = True

                            if next_agent != "FINISH":
                                print(f"🧭 Supervisor: Routing to {next_agent}")
                                logger.info(f"🧭 Supervisor: Routing to {next_agent}")
                                if reasoning:
                                    print(f"   Reasoning: {reasoning}")
                                    logger.info(f"   Reasoning: {reasoning}")
                                # Start spinner for next agent
                                agent_display = next_agent.replace("_", " ").title()
                                spinner = Spinner(f"🤖 {agent_display} thinking")
                                spinner.start()
                            elif metadata.get("plan_pending_approval"):
                                print("🧭 Supervisor: Plan created, awaiting approval")

                        elif node_name in [
                            "kubernetes_agent",
                            "logs_agent",
                            "metrics_agent",
                            "runbooks_agent",
                        ]:
                            agent_name = node_name.replace("_agent", "").title()
                            print(f"\n🔧 {agent_name} Agent:")
                            logger.info(f"🔧 {agent_name} Agent:")

                            # Extract and display tool traces from metadata
                            metadata = node_output.get("metadata", {})
                            # Look for traces using various possible key formats
                            agent_messages = []
                            for key, value in metadata.items():
                                if "_trace" in key and isinstance(value, list):
                                    agent_messages = value
                                    break

                            # Show debug info about trace messages found (only in debug mode)
                            if should_show_debug_traces():
                                print(
                                    f"   🔍 DEBUG: agent_messages = {len(agent_messages) if agent_messages else 0}"
                                )
                            if agent_messages and should_show_debug_traces():
                                print(
                                    f"   📋 Found {len(agent_messages)} trace messages:"
                                )
                                for i, msg in enumerate(agent_messages):
                                    msg_type = type(msg).__name__
                                    if hasattr(msg, "content"):
                                        content_preview = str(
                                            msg.content
                                        )  # Show full content
                                    else:
                                        content_preview = "No content"
                                    print(
                                        f"      {i + 1}. {msg_type}: {content_preview}"
                                    )
                                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                                        print(
                                            f"         Tool calls: {len(msg.tool_calls)}"
                                        )
                                    if hasattr(msg, "tool_call_id"):
                                        print(
                                            f"         Tool response for: {getattr(msg, 'tool_call_id', 'unknown')}"
                                        )
                            elif should_show_debug_traces():
                                print("   ⚠️  No trace messages found in metadata")
                                logger.info("   ⚠️  No trace messages found in metadata")

                            # Display tool calls and results like in langgraph_agent.py (only in debug mode)
                            if should_show_debug_traces():
                                for msg in agent_messages:
                                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                                        print("   📞 Calling tools:")
                                        logger.info("   📞 Calling tools:")
                                        for tc in msg.tool_calls:
                                            tool_name = tc.get("name", "unknown")
                                            tool_args = tc.get("args", {})
                                            tool_id = tc.get("id", "unknown")
                                            print(f"      {tool_name}(")
                                            logger.info(f"      {tool_name}(")
                                            if tool_args:
                                                for (
                                                    arg_name,
                                                    arg_value,
                                                ) in tool_args.items():
                                                    # Show full values
                                                    value_str = repr(arg_value)
                                                    print(
                                                        f"        {arg_name}={value_str}"
                                                    )
                                                    logger.info(
                                                        f"        {arg_name}={value_str}"
                                                    )
                                            print(f"      ) [id: {tool_id}]")
                                            logger.info(f"      ) [id: {tool_id}]")

                                    elif hasattr(msg, "tool_call_id"):
                                        # This is a tool response
                                        tool_name = getattr(msg, "name", "unknown_tool")
                                        tool_call_id = getattr(
                                            msg, "tool_call_id", "unknown"
                                        )
                                        result_content = msg.content

                                        print(
                                            f"   🛠️  {tool_name} [id: {tool_call_id}]:"
                                        )
                                        if isinstance(result_content, str):
                                            try:
                                                parsed_result = json.loads(
                                                    result_content
                                                )
                                                # Pretty print full output
                                                formatted = json.dumps(
                                                    parsed_result, indent=2
                                                )
                                                lines = formatted.split("\n")
                                                for line in lines:
                                                    print(f"      {line}")
                                            except Exception:
                                                # Not JSON, print full string
                                                lines = result_content.split("\n")
                                                for line in lines:
                                                    print(f"      {line}")

                            # Show agent's full final response
                            agent_results = node_output.get("agent_results", {})
                            for agent_key, result in agent_results.items():
                                if (
                                    agent_key in node_name
                                    or node_name.replace("_agent", "")
                                    in agent_key.lower()
                                ):
                                    if result:
                                        print("   💡 Full Response:")
                                        logger.info("   💡 Full Response:")
                                        print(f"      {result}")
                                        logger.info(f"      {result}")

                        elif node_name == "aggregate":
                            final_response = node_output.get("final_response", "")
                            if final_response:
                                print(f"\n💬 Final Response:\n{final_response}")
                                logger.info(f"💬 Final Response: {final_response}")
                                # Save final response to markdown file (auto-save in single query mode)
                                if not args.no_markdown:
                                    _save_final_response_to_markdown(
                                        args.prompt,
                                        final_response,
                                        user_id=user_id,
                                        output_dir=args.output_dir,
                                    )
            except asyncio.TimeoutError:
                if spinner:
                    spinner.stop()
                print(
                    "\n❌ Error: Investigation timed out after 10 minutes. The system may be stuck."
                )
                print(
                    "💡 Tip: Try rephrasing your question or breaking it into smaller parts."
                )
                logger.error("Graph execution timed out after 600 seconds")
            finally:
                # Always clean up spinner
                if spinner:
                    spinner.stop()

    except Exception as e:
        logger.error(f"Error in multi-agent system: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
