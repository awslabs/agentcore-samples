"""
Device Management System - Agent Runtime

This module implements the core conversational AI agent for the Device Management System
using Amazon Bedrock AgentCore Runtime and the Strands Agents SDK. The agent handles
natural language processing, conversation management, and tool execution for IoT device
management operations.

Key Features:
    - Natural language understanding using Amazon Bedrock models
    - Conversation context management with sliding window
    - MCP (Model Context Protocol) tool integration
    - Real-time WebSocket communication
    - Comprehensive observability with logging and tracing

Architecture:
    - Strands Agent: Core conversational AI using Amazon Bedrock
    - MCP Client: Communication with Gateway for tool execution
    - Conversation Manager: Context and history management
    - Authentication: Amazon Cognito OAuth integration
    - Observability: CloudWatch Logs and X-Ray tracing

Environment Variables:
    MCP_SERVER_URL: Gateway endpoint for MCP tool access
    COGNITO_DOMAIN: Amazon Cognito domain for authentication
    COGNITO_CLIENT_ID: OAuth client ID
    COGNITO_CLIENT_SECRET: OAuth client secret
    AWS_REGION: AWS region for services

Example Usage:
    The agent runtime is typically deployed as a service and accessed via WebSocket
    connections from the frontend application. It processes natural language queries
    and executes device management operations through the MCP protocol.
"""

import os
import json
import logging
import requests
from dotenv import load_dotenv
import access_token

# Import Strands Agents SDK
from strands import Agent
from strands.models import BedrockModel
from strands.agent.conversation_manager import SlidingWindowConversationManager
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Load environment variables
load_dotenv()

# Initialize the AgentCore Runtime App
app = BedrockAgentCoreApp()


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Set logging level for specific libraries
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.INFO)
logging.getLogger("strands").setLevel(logging.INFO)

# MCP Server configuration
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL")
logger.info(f"MCP_SERVER_URL set to: {MCP_SERVER_URL}")

# Configure conversation management for production
conversation_manager = SlidingWindowConversationManager(
    window_size=25,  # Limit history size
)


# Function to check if MCP server is running
def check_mcp_server():
    try:
        # Get the bearer token
        jwt_token = os.getenv("BEARER_TOKEN")

        logger.info(f"Checking MCP server at URL: {MCP_SERVER_URL}")

        # If no bearer token, try to get one from Cognito
        if not jwt_token:
            logger.info("No bearer token available, trying to get one from Cognito...")
            try:
                jwt_token = access_token.get_gateway_access_token()
                # Note: Not logging actual token for security reasons
                logger.info(f"Cognito token obtained: {'Yes' if jwt_token else 'No'}")
            except Exception as e:
                logger.error(f"Error getting Cognito token: {str(e)}", exc_info=True)

        if jwt_token:
            headers = {
                "Authorization": f"Bearer {jwt_token}",
                "Content-Type": "application/json",
            }
            payload = {
                "jsonrpc": "2.0",
                "id": "test",
                "method": "tools/list",
                "params": {},
            }

            try:
                response = requests.post(f"{MCP_SERVER_URL}/mcp", headers=headers, json=payload, timeout=10)
                response.raise_for_status()
                logger.info(f"MCP server response status: {response.status_code}")

                has_tools = "tools" in response.text
                return has_tools
            except requests.exceptions.RequestException as e:
                logger.error(f"Request exception when checking MCP server: {str(e)}")
                return False
        else:
            # Try without token for local testing
            logger.info("No bearer token available, trying health endpoint")
            try:
                response = requests.get(f"{MCP_SERVER_URL}/health", timeout=5)
                response.raise_for_status()
                logger.info(f"Health endpoint response status: {response.status_code}")

                return response.status_code == 200
            except requests.exceptions.RequestException as e:
                logger.error(f"Health endpoint request exception: {str(e)}")
                return False
    except Exception as e:
        logger.error(f"Error checking MCP server: {str(e)}", exc_info=True)
        return False


# Initialize Strands Agent with MCP tools
def initialize_agent():
    try:
        # Get OAuth token for authentication
        logger.info("Starting agent initialization...")

        # First try to get token from environment variable (for Docker)
        jwt_token = os.getenv("BEARER_TOKEN")

        # If not available in environment, try to get from Cognito
        if not jwt_token:
            logger.info("No token in environment, trying Cognito...")
            try:
                # jwt_token = asyncio.run(access_token.get_gateway_access_token())
                jwt_token = access_token.get_gateway_access_token()
                # Note: Not logging actual token for security reasons
                logger.info("Token retrieved successfully")
            except Exception as e:
                logger.error(f"Error getting Cognito token: {str(e)}", exc_info=True)

        # Create MCP client with authentication headers
        gateway_endpoint = os.getenv("gateway_endpoint", MCP_SERVER_URL)
        logger.info(f"Using gateway endpoint: {gateway_endpoint}")

        # Try to resolve the hostname to check network connectivity
        try:
            import socket

            hostname = gateway_endpoint.split("://")[1].split("/")[0]
            ip_address = socket.gethostbyname(hostname)
            logger.info(f"Hostname resolved to IP: {ip_address}")
        except Exception as e:
            logger.error(f"Error resolving hostname: {str(e)}")

        headers = {"Authorization": f"Bearer {jwt_token}"} if jwt_token else {}

        try:
            logger.info("Creating MCP client...")

            # Create the MCP client
            mcp_client = MCPClient(lambda: streamablehttp_client(url=f"{gateway_endpoint}/mcp", headers=headers))
            logger.info("MCP Client setup complete")

            # Enter the context manager
            mcp_client.__enter__()

            # Get the tools from the MCP server
            logger.info("Listing tools from MCP server...")
            tools = mcp_client.list_tools_sync()
            logger.info(f"Loaded {len(tools)} tools from MCP server")

            # Log available tools
            if tools and len(tools) > 0:
                # Try to access the tool name using the correct attribute
                tool_names = []
                for tool in tools:
                    # Check if the tool has a 'schema' attribute that might contain the name
                    if hasattr(tool, "schema") and hasattr(tool.schema, "name"):
                        tool_names.append(tool.schema.name)
                    # Or if it has a direct attribute that contains the name
                    elif hasattr(tool, "tool_name"):
                        tool_names.append(tool.tool_name)
                    # Or if it's in the __dict__
                    elif "_name" in vars(tool):
                        tool_names.append(vars(tool)["_name"])
                    else:
                        # If we can't find the name, use a placeholder
                        tool_names.append(f"Tool-{id(tool)}")

                logger.info(f"Available tools: {', '.join(tool_names)}")

        except Exception as e:
            logger.error(f"Error setting up MCP client: {str(e)}", exc_info=True)
            return None, None

        # Create an agent with these tools
        try:
            logger.info("Creating Strands Agent with tools...")
            model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"  # Using Claude Sonnet
            model = BedrockModel(model_id=model_id)

            agent = Agent(
                model=model,
                tools=tools,
                conversation_manager=conversation_manager,
                system_prompt="""
                You are an AI assistant for Device Remote Management. Help the user with their query.
                You have access to tools that can retrieve real data from the Device Remote Management system.
                
                Available tools:
                - list_devices: List all devices in the system
                - get_device_settings: Get settings for a specific device
                - list_wifi_networks: List all WiFi networks for a specific device
                - list_users: List all users in the system
                - query_user_activity: Query user activity within a time period
                - update_wifi_ssid: Update the SSID of a Wi-Fi network on a device
                - update_wifi_security: Update the security type of a Wi-Fi network on a device
                
                Use these tools to help users manage their Device devices and accounts.
                """,
            )
            logger.info("Agent created successfully")

            return agent, mcp_client
        except Exception as e:
            logger.error(f"Error creating agent: {str(e)}", exc_info=True)
            return None, None
    except Exception as e:
        logger.error(f"Error initializing agent: {str(e)}", exc_info=True)
        return None, None


# Initialize the agent if MCP server is running
agent = None
mcp_client = None
if check_mcp_server():
    agent, mcp_client = initialize_agent()
    if agent:
        logger.info("Agent initialized successfully")
    else:
        logger.warning("Failed to initialize agent")
else:
    logger.warning("MCP server is not running. Agent initialization skipped.")


# Function to format response for better display
def format_response(text):
    """Format the response for better display using plain text formatting"""
    if not isinstance(text, str):
        text = str(text)

    # Check if the response contains JSON data
    json_start = text.find("{")
    json_end = text.rfind("}")
    if json_start >= 0 and json_end > json_start:
        try:
            json_str = text[json_start : json_end + 1]
            json_data = json.loads(json_str)

            # Format based on the type of data
            if isinstance(json_data, list) and len(json_data) > 0:
                # Check if it's a list of devices
                if isinstance(json_data[0], dict) and all(key in json_data[0] for key in ["device_id", "model"]):
                    formatted_text = format_device_list(json_data)
                    return text[:json_start] + formatted_text + text[json_end + 1 :]
                # Check if it's a list of users
                elif isinstance(json_data[0], dict) and all(key in json_data[0] for key in ["user_id", "username"]):
                    formatted_text = format_user_list(json_data)
                    return text[:json_start] + formatted_text + text[json_end + 1 :]
                # Check if it's a list of activities
                elif isinstance(json_data[0], dict) and all(
                    key in json_data[0] for key in ["user_id", "activity_type"]
                ):
                    formatted_text = format_activity_list(json_data)
                    return text[:json_start] + formatted_text + text[json_end + 1 :]
                # Generic list of objects
                elif isinstance(json_data[0], dict):
                    formatted_text = format_generic_list(json_data)
                    return text[:json_start] + formatted_text + text[json_end + 1 :]
            # Single object with wifi_networks
            elif isinstance(json_data, dict) and "wifi_networks" in json_data:
                formatted_text = format_wifi_networks(json_data)
                return text[:json_start] + formatted_text + text[json_end + 1 :]
            # Single object
            elif isinstance(json_data, dict):
                # Check if it's device settings
                if "device_id" in json_data and "settings" in json_data:
                    formatted_text = format_device_settings(json_data)
                    return text[:json_start] + formatted_text + text[json_end + 1 :]
                # Check if it's a WiFi update result
                elif "device_id" in json_data and "network_id" in json_data and "old_ssid" in json_data:
                    formatted_text = format_wifi_update(json_data)
                    return text[:json_start] + formatted_text + text[json_end + 1 :]
                # Check if it's a WiFi security update result
                elif "device_id" in json_data and "network_id" in json_data and "old_security_type" in json_data:
                    formatted_text = format_wifi_security_update(json_data)
                    return text[:json_start] + formatted_text + text[json_end + 1 :]
                # Generic object
                else:
                    formatted_text = format_generic_object(json_data)
                    return text[:json_start] + formatted_text + text[json_end + 1 :]
        except Exception as e:
            logger.debug(f"Error formatting JSON: {str(e)}")

    # If no JSON formatting was applied, return the original text
    return text


def format_device_list(devices):
    """Format a list of devices using plain text formatting"""
    if not devices or not isinstance(devices, list):
        return "No devices found."

    result = "Devices in Device Remote Management System\n\n"

    # Add a header row
    result += "Name                  | Device ID  | Model     | Status     | IP Address      | Last Connected\n"
    result += "----------------------|------------|-----------|------------|-----------------|---------------\n"

    # Add each device
    for device in devices:
        name = device.get("name", "N/A")
        device_id = device.get("device_id", "N/A")
        model = device.get("model", "N/A")
        status = device.get("connection_status", "N/A")
        ip = device.get("ip_address", "N/A")
        last_connected = device.get("last_connected", "N/A")

        # Format the row with fixed column widths
        result += f"{name[:20].ljust(20)} | {device_id[:10].ljust(10)} | {model[:9].ljust(9)} | {status[:10].ljust(10)} | {ip[:15].ljust(15)} | {str(last_connected)[:15]}\n"

    return result


def format_user_list(users):
    """Format a list of users using plain text formatting"""
    if not users or not isinstance(users, list):
        return "No users found."

    result = "Users in Device Remote Management System\n\n"

    # Add a header row
    result += "Username              | User ID    | Email                          | Role       | Last Login\n"
    result += "----------------------|------------|--------------------------------|------------|---------------\n"

    # Add each user
    for user in users:
        username = user.get("username", "N/A")
        user_id = user.get("user_id", "N/A")
        email = user.get("email", "N/A")
        role = user.get("role", "N/A")
        last_login = user.get("last_login", "N/A")

        # Format the row with fixed column widths
        result += f"{username[:20].ljust(20)} | {user_id[:10].ljust(10)} | {email[:30].ljust(30)} | {role[:10].ljust(10)} | {str(last_login)[:15]}\n"

    return result


def format_activity_list(activities):
    """Format a list of user activities using plain text formatting"""
    if not activities or not isinstance(activities, list):
        return "No activities found."

    result = "User Activities in Device Remote Management System\n\n"

    # Add a header row
    result += "Username              | Activity Type        | Description                    | Timestamp           | IP Address\n"
    result += "----------------------|---------------------|-------------------------------|---------------------|---------------\n"

    # Add each activity
    for activity in activities:
        username = activity.get("username", "N/A")
        activity_type = activity.get("activity_type", "N/A")
        description = activity.get("description", "N/A")
        timestamp = activity.get("timestamp", "N/A")
        ip_address = activity.get("ip_address", "N/A")

        # Format the row with fixed column widths
        result += f"{username[:20].ljust(20)} | {activity_type[:19].ljust(19)} | {description[:30].ljust(30)} | {str(timestamp)[:19].ljust(19)} | {ip_address[:15]}\n"

    return result


def format_wifi_networks(data):
    """Format WiFi networks list using plain text formatting"""
    if not data or not isinstance(data, dict) or "wifi_networks" not in data:
        return "No WiFi networks found."

    device_id = data.get("device_id", "Unknown")
    device_name = data.get("device_name", "Unknown Device")
    networks = data.get("wifi_networks", [])

    if not networks:
        return f"No WiFi networks found for device {device_name} ({device_id})."

    result = f"WiFi Networks for Device: {device_name} ({device_id})\n\n"

    # Add a header row
    result += "SSID                             | Network ID | Security    | Enabled | Channel | Signal\n"
    result += "----------------------------------|------------|------------|---------|---------|-------\n"

    # Add each network
    for network in networks:
        ssid = network.get("ssid", "N/A")
        network_id = network.get("network_id", "N/A")
        security = network.get("security_type", "N/A")
        enabled = network.get("enabled", "N/A")
        channel = network.get("channel", "N/A")
        signal = network.get("signal_strength", "N/A")

        # Format the row with fixed column widths
        result += f"{ssid[:34].ljust(34)} | {str(network_id)[:10].ljust(10)} | {str(security)[:10].ljust(10)} | {str(enabled)[:7].ljust(7)} | {str(channel)[:7].ljust(7)} | {str(signal)[:7]}\n"

    return result


def format_device_settings(settings):
    """Format device settings using plain text formatting"""
    if not settings or not isinstance(settings, dict):
        return "No settings found."

    device_name = settings.get("device_name", "Unknown Device")
    device_id = settings.get("device_id", "N/A")
    model = settings.get("model", "N/A")
    firmware = settings.get("firmware_version", "N/A")
    status = settings.get("connection_status", "N/A")

    result = f"Settings for Device: {device_name} ({device_id})\n\n"

    # Device information section
    result += "DEVICE INFORMATION\n"
    result += "=================\n"
    result += f"Model:    {model}\n"
    result += f"Firmware: {firmware}\n"
    result += f"Status:   {status}\n\n"

    # Configuration settings section
    if "settings" in settings and settings["settings"]:
        result += "CONFIGURATION SETTINGS\n"
        result += "=====================\n"

        # Get the maximum key length for alignment
        max_key_length = max([len(key) for key in settings["settings"].keys()])

        for key, value in sorted(settings["settings"].items()):
            result += f"{key.ljust(max_key_length + 2)}: {value}\n"

    return result


def format_wifi_update(result):
    """Format WiFi SSID update result using plain text formatting"""
    if not result or not isinstance(result, dict):
        return "No update result available."

    if "error" in result:
        return f"Error updating WiFi SSID: {result['error']}"

    device_name = result.get("device_name", "Unknown Device")
    device_id = result.get("device_id", "N/A")
    network_id = result.get("network_id", "N/A")
    old_ssid = result.get("old_ssid", "N/A")
    new_ssid = result.get("new_ssid", "N/A")
    status = result.get("status", "N/A")

    output = "WiFi SSID Update Successful\n"
    output += "==========================\n\n"
    output += f"Device:        {device_name} (ID: {device_id})\n"
    output += f"Network ID:    {network_id}\n"
    output += f"Previous SSID: {old_ssid}\n"
    output += f"New SSID:      {new_ssid}\n"
    output += f"Status:        {status}\n"

    return output


def format_wifi_security_update(result):
    """Format WiFi security type update result using plain text formatting"""
    if not result or not isinstance(result, dict):
        return "No update result available."

    if "error" in result:
        return f"Error updating WiFi security type: {result['error']}"

    device_name = result.get("device_name", "Unknown Device")
    device_id = result.get("device_id", "N/A")
    network_id = result.get("network_id", "N/A")
    ssid = result.get("ssid", "N/A")
    old_security_type = result.get("old_security_type", "N/A")
    new_security_type = result.get("new_security_type", "N/A")
    status = result.get("status", "N/A")

    output = "WiFi Security Type Update Successful\n"
    output += "==================================\n\n"
    output += f"Device:             {device_name} (ID: {device_id})\n"
    output += f"Network ID:         {network_id}\n"
    output += f"SSID:               {ssid}\n"
    output += f"Previous Security:  {old_security_type}\n"
    output += f"New Security:       {new_security_type}\n"
    output += f"Status:             {status}\n"

    return output


def format_generic_list(items):
    """Format a generic list of objects using plain text formatting"""
    if not items or not isinstance(items, list):
        return "No items found."

    # Get all unique keys from all items
    all_keys = set()
    for item in items:
        if isinstance(item, dict):
            all_keys.update(item.keys())

    # If no keys found, just return a simple list
    if not all_keys:
        result = "Results:\n\n"
        for i, item in enumerate(items, 1):
            result += f"{i}. {item}\n"
        return result

    # Convert keys to a sorted list for consistent column order
    keys = sorted(all_keys)

    # Create a header row with column names
    result = "Results:\n\n"
    header = ""
    separator = ""

    # Calculate column widths (minimum 10 characters, maximum 20)
    col_widths = {}
    for key in keys:
        # Find the maximum length of values for this key
        max_val_length = max([len(str(item.get(key, ""))) for item in items if isinstance(item, dict)] + [len(key)])
        col_widths[key] = min(max(max_val_length, 10), 20)

    # Create header and separator
    for key in keys:
        width = col_widths[key]
        header += f"{key[:width].ljust(width)} | "
        separator += "-" * width + "-|-"

    # Remove trailing separator characters
    header = header[:-3]
    separator = separator[:-3]

    result += header + "\n" + separator + "\n"

    # Add rows
    for item in items:
        if not isinstance(item, dict):
            continue

        row = ""
        for key in keys:
            width = col_widths[key]
            value = str(item.get(key, "N/A"))
            row += f"{value[:width].ljust(width)} | "

        # Remove trailing separator characters
        row = row[:-3]
        result += row + "\n"

    return result


def format_generic_object(obj):
    """Format a generic object using plain text formatting"""
    if not obj or not isinstance(obj, dict):
        return "No data available."

    if "error" in obj:
        return f"Error: {obj['error']}"

    result = "Result:\n\n"

    # Get the maximum key length for alignment
    max_key_length = max([len(key) for key in obj.keys()])

    for key, value in sorted(obj.items()):
        if isinstance(value, dict):
            # Nested object
            result += f"{key}:\n"
            result += "-" * len(key) + "\n"

            # Get the maximum nested key length for alignment
            nested_max_key_length = max([len(k) for k in value.keys()]) if value else 0

            for k, v in sorted(value.items()):
                result += f"  {k.ljust(nested_max_key_length + 2)}: {v}\n"

            result += "\n"
        elif isinstance(value, list):
            # List of values
            result += f"{key}:\n"
            result += "-" * len(key) + "\n"

            if all(isinstance(item, dict) for item in value):
                # List of objects - get all unique keys
                all_keys = set()
                for item in value:
                    all_keys.update(item.keys())

                # Calculate column widths
                col_widths = {}
                for k in all_keys:
                    max_val_length = max([len(str(item.get(k, ""))) for item in value] + [len(k)])
                    col_widths[k] = min(max(max_val_length, 10), 20)

                # Create header
                header = "  "
                separator = "  "
                for k in sorted(all_keys):
                    width = col_widths[k]
                    header += f"{k[:width].ljust(width)} | "
                    separator += "-" * width + "-|-"

                # Remove trailing separator characters
                header = header[:-3]
                separator = separator[:-3]

                result += header + "\n" + separator + "\n"

                # Add rows
                for item in value:
                    row = "  "
                    for k in sorted(all_keys):
                        width = col_widths[k]
                        v = str(item.get(k, "N/A"))
                        row += f"{v[:width].ljust(width)} | "

                    # Remove trailing separator characters
                    row = row[:-3]
                    result += row + "\n"
            else:
                # Simple list
                for i, item in enumerate(value, 1):
                    result += f"  {i}. {item}\n"

            result += "\n"
        else:
            # Simple value
            result += f"{key.ljust(max_key_length + 2)}: {value}\n"

    return result


@app.entrypoint
async def process_request(payload):
    """
    Process requests from AgentCore Runtime with streaming support
    This is the entry point for the AgentCore Runtime
    """
    global agent, mcp_client
    try:
        # Extract the user message from the payload
        user_message = payload.get("prompt", "No prompt found in input, please provide a message")
        logger.info(f"Received user message: {user_message}")

        # Check if agent is initialized
        if not agent:
            logger.info("Agent not initialized, checking MCP server status...")
            # Try to initialize the agent if MCP server is running
            if check_mcp_server():
                logger.info("MCP server is running, attempting to initialize agent...")
                agent, mcp_client = initialize_agent()
                if not agent:
                    error_msg = "Failed to initialize agent. Please ensure MCP server is running correctly."
                    logger.error(error_msg)
                    yield {"error": error_msg}
                    return
                logger.info("Agent initialized successfully")
            else:
                error_msg = "Agent is not initialized. Please ensure MCP server is running."
                logger.error(error_msg)
                yield {"error": error_msg}
                return

        # Use Strands Agent to process the message with streaming
        logger.info("Processing message with Strands Agent (streaming)...")
        try:
            # Stream response using agent.stream_async
            stream = agent.stream_async(user_message)
            async for event in stream:
                logger.debug(f"Streaming event: {event}")

                # Process different event types
                if "data" in event:
                    # Text chunk from the model
                    chunk = event["data"]
                    formatted_chunk = format_response(chunk)
                    yield {"type": "chunk", "data": chunk, "formatted": formatted_chunk}
                elif "current_tool_use" in event:
                    # Tool use information
                    tool_info = event["current_tool_use"]
                    yield {
                        "type": "tool_use",
                        "tool_name": tool_info.get("name", "Unknown tool"),
                        "tool_input": tool_info.get("input", {}),
                        "tool_id": tool_info.get("toolUseId", ""),
                    }
                elif "reasoning" in event and event["reasoning"]:
                    # Reasoning information
                    yield {
                        "type": "reasoning",
                        "reasoning_text": event.get("reasoningText", ""),
                    }
                elif "result" in event:
                    # Final result
                    result = event["result"]
                    if hasattr(result, "message") and hasattr(result.message, "content"):
                        if isinstance(result.message.content, list) and len(result.message.content) > 0:
                            final_response = result.message.content[0].get("text", "")
                        else:
                            final_response = str(result.message.content)
                    else:
                        final_response = str(result)

                    yield {
                        "type": "complete",
                        "final_response": format_response(final_response),
                    }
                else:
                    # Pass through other events
                    yield event

        except Exception as e:
            logger.error(f"Error in streaming mode: {str(e)}", exc_info=True)
            yield {"error": f"Error processing request with agent (streaming): {str(e)}"}

    except Exception as e:
        error_msg = f"Error processing request: {str(e)}"
        logger.error(error_msg, exc_info=True)
        yield {"error": error_msg}


if __name__ == "__main__":
    # Test MCP server connection at startup
    logger.info("Testing MCP server connection at startup...")
    try:
        jwt_token = access_token.get_gateway_access_token()
        headers = (
            {"Authorization": f"Bearer {jwt_token}", "Content-Type": "application/json"}
            if jwt_token
            else {"Content-Type": "application/json"}
        )
        payload = {
            "jsonrpc": "2.0",
            "id": "startup-test",
            "method": "tools/list",
            "params": {},
        }
        response = requests.post(f"{MCP_SERVER_URL}/mcp", headers=headers, json=payload, timeout=10)
        logger.info(f"Direct test response status: {response.status_code}")
    except Exception as e:
        logger.error(f"Error in direct test: {str(e)}", exc_info=True)

    # Run the AgentCore Runtime App
    app.run()
