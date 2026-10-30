"""
Generate participant token for specific room
"""

import os
import sys

from dotenv import load_dotenv
from livekit import api

load_dotenv()


def generate_token(room_name):
    """Generate token for participant to join specific room"""

    livekit_api_key = os.getenv("LIVEKIT_API_KEY")
    livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")
    livekit_url = os.getenv("LIVEKIT_URL")

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
    print("2. Paste the token above")
    print("3. Enable microphone and start speaking!")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_token_for_room.py <room_name>")
        print("Example: python generate_token_for_room.py test-interview-room")
        sys.exit(1)

    room_name = sys.argv[1]
    generate_token(room_name)
