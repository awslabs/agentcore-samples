"""
Invoke AgentCore deployed in AWS Bedrock
"""

import json
import os
import uuid

import boto3
from dotenv import load_dotenv
from livekit import api

load_dotenv()

# AgentCore ARN from deployment
AGENT_ARN = os.getenv(
    "AGENTCORE_ARN",
    "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/nova_realtime_agentcore-OM1tW86pRg",
)


def invoke_agentcore(room_name="interview-room"):
    """Invoke AWS AgentCore agent to join LiveKit room"""

    # Get LiveKit credentials
    livekit_url = os.getenv("LIVEKIT_URL")
    livekit_api_key = os.getenv("LIVEKIT_API_KEY")
    livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")

    # Generate agent token for the room
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

    # Prepare payload
    payload = {"room_url": livekit_url, "room_token": agent_token}

    print("=" * 60)
    print("INVOKING AWS AGENTCORE")
    print("=" * 60)
    print(f"Agent ARN: {AGENT_ARN}")
    print(f"Room: {room_name}")
    print(f"LiveKit URL: {livekit_url}")
    print("=" * 60)

    # Initialize AgentCore client
    client = boto3.client("bedrock-agentcore", region_name="us-east-1")

    try:
        # Invoke the agent
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_ARN,
            runtimeSessionId=str(uuid.uuid4()),
            payload=json.dumps(payload).encode(),
            qualifier="DEFAULT",
        )

        # Read response
        content = []
        for chunk in response.get("response", []):
            content.append(chunk.decode("utf-8"))

        result = json.loads("".join(content))

        print("\nResponse:")
        print(json.dumps(result, indent=2))

        print("\n" + "=" * 60)
        print("AGENT INVOKED SUCCESSFULLY!")
        print(f"Agent is joining room: {room_name}")
        print("Candidates can now join via UI")
        print("=" * 60)

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    import sys

    room_name = sys.argv[1] if len(sys.argv) > 1 else "interview-room"
    invoke_agentcore(room_name)
