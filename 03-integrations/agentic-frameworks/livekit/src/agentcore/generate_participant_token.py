"""
Generate participant token for joining the test room
"""

import os

from dotenv import load_dotenv
from livekit import api

load_dotenv()


def generate_token():
    """Generate token for participant to join test room"""

    livekit_api_key = os.getenv("LIVEKIT_API_KEY")
    livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")
    livekit_url = os.getenv("LIVEKIT_URL")

    # Read room name from file (created by invoke_local.py)
    try:
        with open(".room_name", "r") as f:
            room_name = f.read().strip()
    except FileNotFoundError:
        print("ERROR: Room name file not found!")
        print("Please run 'python invoke_local.py' first to create the room.")
        return

    participant_name = "Test Candidate"

    # Generate participant token
    token = api.AccessToken(livekit_api_key, livekit_api_secret)
    token.with_identity("test-candidate")
    token.with_name(participant_name)
    token.with_grants(
        api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        )
    )

    participant_token = token.to_jwt()

    print("=" * 60)
    print("LIVEKIT PARTICIPANT TOKEN")
    print("=" * 60)
    print(f"Room Name: {room_name}")
    print(f"LiveKit URL: {livekit_url}")
    print(f"Participant Name: {participant_name}")
    print("=" * 60)
    print(f"\nToken:\n{participant_token}")
    print("\n" + "=" * 60)
    print("Use this token in LiveKit Playground:")
    print("1. Go to: https://agents-playground.livekit.io/")
    print("2. Enter the token above")
    print("3. Enable microphone and start speaking!")
    print("=" * 60)


if __name__ == "__main__":
    generate_token()
