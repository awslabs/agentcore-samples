"""Demo: Drive a Claude Managed Agents session through the gateway (Anthropic SDK).

Same flow as invoke.py, but using the Anthropic Python SDK pointed at the
gateway target. The SDK base_url is the gateway target root (no /v1 suffix); the
SDK appends /v1/... itself.

Auth model: the Entra ID gateway JWT is passed as auth_token so the SDK sends it
as Authorization: Bearer (inbound). The client's Claude key is supplied as an
explicit x-api-key default header, which the gateway forwards outbound. We use
default_headers rather than the SDK's api_key argument because passing both
api_key and auth_token to one client is rejected.

Requires in environment or .env:
  GATEWAY_URL    - shared gateway URL (written by deploy_gateway.py)
  TARGET_NAME    - passthrough target name (written by deploy.py)
  BEARER_TOKEN   - Entra ID gateway access token (aud: api://<gateway-client-id>)
  CLAUDE_API_KEY - your Claude API key with Managed Agents beta access

Usage:
    uv run python scripts/managed-agents-custom/sdk_demo.py
"""

import os
import sys

from anthropic import Anthropic

PROMPT = (
    "Create a Python script that generates the first 20 Fibonacci numbers "
    "and saves them to fibonacci.txt"
)


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def main():
    load_env()

    gateway_url = get_required_env("GATEWAY_URL")
    target_name = get_required_env("TARGET_NAME")
    bearer_token = get_required_env("BEARER_TOKEN")
    claude_api_key = get_required_env("CLAUDE_API_KEY")

    # Point the SDK at the gateway target root. auth_token sends the Entra JWT as
    # Authorization: Bearer (inbound); the x-api-key default header carries the
    # client's Claude key, which the gateway forwards outbound.
    base = f"{gateway_url.rstrip('/')}/{target_name}"
    client = Anthropic(
        base_url=base,
        auth_token=bearer_token,
        default_headers={"x-api-key": claude_api_key},
    )

    print(f"Gateway target base: {base}\n")

    agent = client.beta.agents.create(
        name="Coding Assistant",
        model="claude-opus-4-8",
        system="You are a helpful coding assistant. Write clean, well-documented code.",
        tools=[{"type": "agent_toolset_20260401"}],
    )
    print(f"Agent ID: {agent.id}, version: {agent.version}")

    environment = client.beta.environments.create(
        name="quickstart-env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    print(f"Environment ID: {environment.id}")

    session = client.beta.sessions.create(
        agent=agent.id,
        environment_id=environment.id,
        title="Quickstart session",
    )
    print(f"Session ID: {session.id}\n")

    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": PROMPT}],
                }
            ],
        )

        for event in stream:
            match event.type:
                case "agent.message":
                    for block in event.content:
                        print(block.text, end="", flush=True)
                case "agent.tool_use":
                    print(f"\n[Using tool: {event.name}]")
                case "session.status_idle":
                    print("\n\nAgent finished.")
                    break


if __name__ == "__main__":
    main()
