"""
Invoke local AgentCore server with LiveKit room
"""

import asyncio
import os

# Direct import for local testing
import sys

from dotenv import load_dotenv
from livekit import api

sys.path.insert(0, os.path.dirname(__file__))
from nova_realtime_agentcore import invoke

load_dotenv()


class MockContext:
    def __init__(self, session_id="test-session"):
        self.session_id = session_id


async def invoke_agentcore():
    """Invoke AgentCore with LiveKit room credentials"""

    # Get LiveKit credentials
    livekit_url = os.getenv("LIVEKIT_URL")
    livekit_api_key = os.getenv("LIVEKIT_API_KEY")
    livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")

    # Generate room and token with timestamp
    import time

    room_name = f"agentcore-test-{int(time.time())}"

    # Save room name to file for token generator
    with open(".room_name", "w") as f:
        f.write(room_name)

    token = api.AccessToken(livekit_api_key, livekit_api_secret)
    token.with_identity("agentcore-agent")
    token.with_name("AgentCore Interview Agent")
    token.with_grants(
        api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        )
    )
    agent_token = token.to_jwt()

    print("=" * 60)
    print("INVOKING AGENTCORE LOCAL SERVER")
    print("=" * 60)
    print(f"Room: {room_name}")
    print(f"LiveKit URL: {livekit_url}")
    print("Agent will join room and wait for participants...")
    print("\nIMPORTANT: Run 'python generate_participant_token.py' to get token")
    print("=" * 60)

    # Prepare payload
    payload = {"room_url": livekit_url, "room_token": agent_token}

    context = MockContext()

    # Invoke AgentCore directly (local testing)
    try:
        print("\nInvoking AgentCore...")
        result = await invoke(payload, context)

        print(f"\nResult: {result}")

        if "error" not in result:
            print("\n" + "=" * 60)
            print("AGENT IS NOW IN THE ROOM!")
            print(f"Join the room '{room_name}' from UI or LiveKit Playground")
            print("=" * 60)

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(invoke_agentcore())
