import argparse
import asyncio
import json
import logging
import urllib.parse
from typing import Any, Optional
from uuid import uuid4
from urllib.parse import quote

import httpx
import requests
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart

from utils import get_ssm_parameter, get_aws_info, get_m2m_token_for_agent
import sys
from pathlib import Path

# Add host_adk_agent scripts to path for get_m2m_token function
sys.path.insert(0, str(Path(__file__).parent.parent / "host_adk_agent" / "scripts"))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Suppress debug output from specific libraries
logging.getLogger("bedrock_agentcore").setLevel(logging.ERROR)
logging.getLogger("bedrock_agentcore.identity_client").setLevel(logging.ERROR)
logging.getLogger("botocore").setLevel(logging.ERROR)
logging.getLogger("botocore.credentials").setLevel(logging.ERROR)

DEFAULT_TIMEOUT = 300  # set request timeout to 5 minutes

# Get AWS region and account ID dynamically
account_id, region = get_aws_info()

moniter_agent_id = get_ssm_parameter("/monitoragent/agentcore/runtime-id")
websearch_agent_id = get_ssm_parameter("/websearchagent/agentcore/runtime-id")
hostagent_agent_id = get_ssm_parameter("/hostagent/agentcore/runtime-id")

moniter_agent_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{moniter_agent_id}"

websearch_agent_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{websearch_agent_id}"

hostagent_agent_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{hostagent_agent_id}"


def create_message(*, role: Role = Role.user, text: str) -> Message:
    """Create a message for A2A protocol."""
    return Message(
        kind="message",
        role=role,
        parts=[Part(TextPart(kind="text", text=text))],
        message_id=uuid4().hex,
    )


def fetch_agent_card(bearer_token: str, agent_arn: str):
    """Fetch agent card from the runtime endpoint."""
    # URL encode the agent ARN
    escaped_agent_arn = quote(agent_arn, safe="")

    # Construct the URL
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_agent_arn}/invocations/.well-known/agent-card.json"
    print(f"URL: {url}")
    # Generate a unique session ID
    session_id = str(uuid4())
    logger.info(f"Fetching agent card with session ID: {session_id}")

    # Set headers
    headers = {
        "Accept": "*/*",
        "Authorization": f"Bearer {bearer_token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }

    try:
        # Make the request
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        # Parse and return agent card
        agent_card = response.json()
        logger.info("Agent card fetched successfully")
        logger.info(json.dumps(agent_card, indent=2))
        return agent_card

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching agent card: {e}")
        return None


async def send_message(message: str, session_id: str, bearer_token: str, agent_arn: str):
    """Send a message to the agent using A2A protocol."""
    # Construct runtime URL
    escaped_agent_arn = quote(agent_arn, safe="")
    runtime_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_agent_arn}/invocations"

    # Add authentication headers
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actorid": "TestActor",
    }

    print("\n🤖 Assistant: ", end="", flush=True)

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers) as httpx_client:
        # Get agent card from the runtime URL
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=runtime_url)
        agent_card = await resolver.get_agent_card()

        # Create client using factory
        config = ClientConfig(
            httpx_client=httpx_client,
            streaming=False,  # Use non-streaming mode for sync response
        )
        factory = ClientFactory(config)
        client = factory.create(agent_card)

        # Create and send message
        msg = create_message(text=message)

        # With streaming=False, this will yield exactly one result
        async for event in client.send_message(msg):
            if isinstance(event, Message):
                # Extract and print text content from Message
                for part in event.parts:
                    if hasattr(part, "text") and part.text:
                        print(part.text, flush=True)

                return event
            elif isinstance(event, tuple) and len(event) == 2:
                # (Task, UpdateEvent) tuple - extract text from Task artifacts
                task, update_event = event

                # Extract text from task artifacts
                if hasattr(task, "artifacts") and task.artifacts:
                    for artifact in task.artifacts:
                        if hasattr(artifact, "parts") and artifact.parts:
                            for part in artifact.parts:
                                # The part has a 'root' attribute containing the actual TextPart
                                if hasattr(part, "root") and hasattr(part.root, "text"):
                                    print(part.root.text, flush=True)

                return task
            else:
                # Fallback for other response types
                return event


def invoke_endpoint(
    agent_arn: str,
    payload,
    session_id: str,
    bearer_token: Optional[str],
    endpoint_name: str = "DEFAULT",
    stream: bool = True,
) -> Any:
    """Invoke the host agent endpoint directly."""
    escaped_arn = urllib.parse.quote(agent_arn, safe="")

    _, region = get_aws_info()

    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations"

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actorid": "TestActor",
    }

    try:
        body = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError:
        body = {"payload": payload}

    response = requests.post(
        url,
        params={"qualifier": endpoint_name},
        headers=headers,
        json=body,
        timeout=DEFAULT_TIMEOUT,
        stream=stream,
    )

    if not stream:
        print(
            response.content.decode("utf-8").replace("\\n", "\n").replace('"', ""),
            flush=True,
        )
    else:
        for line in response.iter_lines(chunk_size=1):
            if line:
                line = line.decode("utf-8")

                # Skip lines that look like Python object representations (debug output)
                if line.startswith("content=") or line.startswith("  ") or "grounding_metadata=" in line:
                    continue

                if line.startswith("data: "):
                    data_content = line[6:]  # Remove "data: " prefix

                    try:
                        # Try to parse as JSON
                        parsed = json.loads(data_content)

                        # Check for transfer_to_agent action
                        if isinstance(parsed, dict) and "actions" in parsed:
                            actions = parsed.get("actions", {})
                            transfer_agent = actions.get("transfer_to_agent")
                            if transfer_agent:
                                print(
                                    f"\n[Transferring to agent: {transfer_agent}]\n",
                                    flush=True,
                                )

                        # Check for the response format with content.parts[].text
                        if isinstance(parsed, dict) and "content" in parsed:
                            content = parsed.get("content", {})
                            parts = content.get("parts", [])
                            for part in parts:
                                if isinstance(part, dict) and "text" in part and part["text"] is not None:
                                    text = part["text"]
                                    # Handle escaped newlines
                                    text = text.replace("\\n", "\n")
                                    print(text, end="", flush=True)
                        # Check for complex event structure with contentBlockDelta
                        elif isinstance(parsed, dict) and "event" in parsed:
                            event = parsed["event"]
                            if isinstance(event, dict) and "contentBlockDelta" in event:
                                delta = event["contentBlockDelta"].get("delta", {})
                                if "text" in delta:
                                    text = delta["text"]
                                    text = text.replace("\\n", "\n")
                                    print(text, end="", flush=True)
                        # If parsed is just a string, print it directly
                        elif isinstance(parsed, str):
                            text = parsed.replace("\\n", "\n")
                            print(text, end="", flush=True)
                    except json.JSONDecodeError:
                        # Silently skip non-JSON data
                        pass


def send_message_to_host(message: str, session_id: str, bearer_token: str, agent_arn: str):
    """Send a message to the host agent using direct endpoint invocation."""
    payload = {"prompt": message}
    invoke_endpoint(
        agent_arn=agent_arn,
        payload=payload,
        session_id=session_id,
        bearer_token=bearer_token,
        stream=True,
    )


def get_bearer_token_for_ssm_prefix(ssm_prefix: str):
    """Get bearer token for authentication using SSM prefix."""
    access_token, _ = get_m2m_token_for_agent(ssm_prefix)
    return access_token


def get_bearer_token_for_host_agent():
    """Get bearer token for host agent using username/password authentication."""
    from get_m2m_token import get_m2m_token

    return get_m2m_token()


if __name__ == "__main__":
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Connect to a Bedrock agent")
    parser.add_argument(
        "--agent",
        choices=["monitor", "websearch", "host"],
        required=True,
        help="Agent to connect to: 'monitor', 'websearch', or 'host'",
    )
    args = parser.parse_args()

    # Set variables based on agent choice
    if args.agent == "monitor":
        selected_ssm_prefix = "/monitoragent"
        selected_agent_arn = moniter_agent_arn
        print(f"\n🔍 Using Monitor Agent (ID: {moniter_agent_id})")
    elif args.agent == "websearch":
        selected_ssm_prefix = "/websearchagent"
        selected_agent_arn = websearch_agent_arn
        print(f"\n🔍 Using WebSearch Agent (ID: {websearch_agent_id})")
    else:  # host
        selected_ssm_prefix = "/hostagent"
        selected_agent_arn = hostagent_agent_arn
        print(f"\n🔍 Using Host Agent (ID: {hostagent_agent_id})")

    # For host agent, use direct endpoint invocation without fetching agent card
    if args.agent == "host":
        # Get bearer token once at the start of the session (uses username/password)
        print("\n🔐 Authenticating with username/password...")
        bearer_token = get_bearer_token_for_host_agent()

        # Start interactive session for host agent
        session_id = str(uuid4())
        print(f"🤖 Starting interactive session (Session ID: {session_id})")
        print("Type 'q' or 'quit' to exit.\n")

        while True:
            user_input = input("👤 You: ").strip()

            if user_input.lower() in ["q", "quit"]:
                print("👋 Goodbye!")
                break

            if not user_input:
                continue

            # Send message using direct endpoint invocation with reused token
            print("\n🤖 Assistant: ", end="", flush=True)
            send_message_to_host(user_input, session_id, bearer_token, selected_agent_arn)
            print("\n")
    else:
        # For monitor and websearch agents, use A2A protocol
        # Get bearer token once at the start of the session
        print("\n🔐 Authenticating...")
        bearer_token = get_bearer_token_for_ssm_prefix(selected_ssm_prefix)

        # Fetch and display the agent card
        print("📋 Fetching agent card...\n")
        card = fetch_agent_card(bearer_token, selected_agent_arn)

        if not card:
            print("❌ Failed to fetch agent card. Exiting.")
            exit(1)

        # Display raw agent card
        print("=" * 60)
        print("AGENT CARD")
        print("=" * 60)
        print(json.dumps(card, indent=2))
        print("=" * 60)

        # Start interactive session
        session_id = str(uuid4())
        print(f"\n🤖 Starting interactive session (Session ID: {session_id})")
        print("Type 'q' or 'quit' to exit.\n")

        while True:
            user_input = input("👤 You: ").strip()

            if user_input.lower() in ["q", "quit"]:
                print("👋 Goodbye!")
                break

            if not user_input:
                continue

            # Send message using async A2A protocol with reused token
            asyncio.run(send_message(user_input, session_id, bearer_token, selected_agent_arn))
            print()
