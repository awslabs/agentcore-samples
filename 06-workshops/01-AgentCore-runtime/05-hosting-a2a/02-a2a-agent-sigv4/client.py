"""
A2A Client with IAM Authentication

This client demonstrates how to connect to an A2A agent deployed on AgentCore Runtime
using AWS IAM (SigV4) authentication.
"""

import asyncio
import logging
import sys
from uuid import uuid4
from urllib.parse import quote

import boto3
import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300  # 5 minutes


class SigV4HTTPXAuth(httpx.Auth):
    """HTTPX Auth class that signs requests with AWS SigV4."""

    def __init__(self, credentials, service: str, region: str):
        self.credentials = credentials
        self.service = service
        self.region = region
        self.signer = SigV4Auth(credentials, service, region)

    def auth_flow(self, request: httpx.Request):
        """Signs the request with SigV4 and adds the signature to the request headers."""
        headers = dict(request.headers)
        headers.pop("connection", None)  # Remove connection header for signature

        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=headers,
        )

        self.signer.add_auth(aws_request)
        request.headers.update(dict(aws_request.headers))

        yield request


def create_message(*, role: Role = Role.user, text: str) -> Message:
    """Create an A2A message."""
    return Message(
        kind="message",
        role=role,
        parts=[Part(TextPart(kind="text", text=text))],
        message_id=uuid4().hex,
    )


def format_agent_response(event):
    """Extract and format agent response for human readability."""
    # Handle tuple response (event might be (response, metadata))
    response = event[0] if isinstance(event, tuple) else event

    if hasattr(response, "artifacts") and response.artifacts and len(response.artifacts) > 0:
        artifact = response.artifacts[0]
        if artifact.parts and len(artifact.parts) > 0:
            return artifact.parts[0].root.text

    # Fallback: concatenate all agent messages from history
    if hasattr(response, "history"):
        agent_messages = [msg.parts[0].root.text for msg in response.history if msg.role.value == "agent" and msg.parts]
        return "".join(agent_messages)

    # Last resort: return string representation
    return str(response)


async def test_agent(agent_arn: str, message: str):
    """Test the A2A agent with IAM authentication."""

    # Get AWS session and credentials
    boto_session = boto3.Session()
    region = boto_session.region_name
    credentials = boto_session.get_credentials()

    logger.info(f"Using AWS region: {region}")
    logger.info(f"Testing agent: {agent_arn}")

    # Construct the runtime URL
    escaped_agent_arn = quote(agent_arn, safe="")
    runtime_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_agent_arn}/invocations/"

    # Generate session ID
    session_id = str(uuid4())
    logger.info(f"Session ID: {session_id}")

    # Create SigV4 auth
    auth = SigV4HTTPXAuth(credentials, "bedrock-agentcore", region)

    # Additional headers for AgentCore
    headers = {
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, auth=auth, headers=headers) as httpx_client:
            # Get agent card
            logger.info("Fetching agent card...")
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=runtime_url)
            agent_card = await resolver.get_agent_card()

            logger.info(f"Agent: {agent_card.name}")
            logger.info(f"Description: {agent_card.description}")

            # Create A2A client
            config = ClientConfig(
                httpx_client=httpx_client,
                streaming=False,
            )
            factory = ClientFactory(config)
            client = factory.create(agent_card)

            # Send message
            logger.info(f"\nSending message: {message}")
            msg = create_message(text=message)

            async for event in client.send_message(msg):
                response_text = format_agent_response(event)
                logger.info(f"\nAgent response:\n{response_text}")
                return response_text

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


async def main():
    """Main function to test the agent."""

    # Get agent ARN from environment or command line
    import os

    agent_arn = os.environ.get("AGENT_ARN")

    if not agent_arn:
        if len(sys.argv) > 1:
            agent_arn = sys.argv[1]
        else:
            logger.error("Please provide AGENT_ARN environment variable or as command line argument")
            sys.exit(1)

    # Test messages
    test_messages = [
        "Hello! What can you do?",
        "Please greet me. My name is Alice.",
        "Tell me about yourself.",
    ]

    for message in test_messages:
        logger.info("\n" + "=" * 60)
        await test_agent(agent_arn, message)
        await asyncio.sleep(1)  # Brief pause between requests


if __name__ == "__main__":
    asyncio.run(main())
