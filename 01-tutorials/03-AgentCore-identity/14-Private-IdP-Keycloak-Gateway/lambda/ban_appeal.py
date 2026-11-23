"""
Ban appeal tools Lambda for AgentCore Gateway.
AgentCore Gateway invokes this with the tool arguments directly.
"""

import json


def handler(event, context):
    player_id = event.get("player_id", "unknown")

    # If 'reason' is present, it's a submit_appeal call
    if "reason" in event:
        return {
            "appeal_id": "APL-78291",
            "status": "SUBMITTED",
            "player_id": player_id,
            "reason": event["reason"],
            "estimated_review_days": 3,
        }

    # Otherwise it's check_enforcement_status
    return {
        "status": "BANNED",
        "reason": "Cheating - aimbot detected",
        "appeal_eligible": True,
        "player_id": player_id,
        "ban_date": "2026-04-15",
        "game": "FC Madden 26",
    }
