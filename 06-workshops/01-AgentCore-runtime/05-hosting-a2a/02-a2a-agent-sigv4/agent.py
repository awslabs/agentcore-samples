"""
Simple A2A Agent with IAM Authentication

This agent demonstrates A2A protocol support with IAM-based authentication.
It provides a simple greeting tool to demonstrate agent functionality.
"""

import os
import logging
from strands import Agent, tool
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")
host, port = "127.0.0.1", 9000


@tool
def greet_user(name: str) -> str:
    """Greet a user by name.

    Args:
        name (str): The name of the user to greet

    Returns:
        str: A friendly greeting message
    """
    return f"Hello, {name}! Welcome to the A2A agent with IAM authentication."


@tool
def get_agent_info() -> str:
    """Get information about this agent.

    Returns:
        str: Information about the agent's capabilities
    """
    return "I am an A2A agent deployed on AgentCore Runtime with IAM authentication. I can greet users and provide information about myself."


# System prompt for the agent
system_prompt = """You are a helpful A2A agent deployed on Amazon Bedrock AgentCore Runtime.

You use AWS IAM authentication for secure communication.

Your capabilities:
- Greet users by name
- Provide information about yourself
- Demonstrate A2A protocol with IAM auth

Keep responses concise and friendly."""

# Create the agent with tools
agent = Agent(
    system_prompt=system_prompt,
    tools=[greet_user, get_agent_info],
    name="A2A IAM Auth Agent",
    description="A simple A2A agent demonstrating IAM authentication on AgentCore Runtime",
)

# Create A2A server
a2a_server = A2AServer(agent=agent, http_url=runtime_url, serve_at_root=True)

# Create FastAPI app
app = FastAPI()


@app.get("/ping")
def ping():
    """Health check endpoint"""
    return {"status": "healthy"}


# Mount A2A server
app.mount("/", a2a_server.to_fastapi_app())

if __name__ == "__main__":
    logger.info(f"Starting A2A agent on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
