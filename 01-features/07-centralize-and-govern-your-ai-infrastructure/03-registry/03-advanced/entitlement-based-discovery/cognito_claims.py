"""Cognito V3_0 pre token generation trigger for the four sample agents."""

import json
import os
from typing import Any

CLAIMS = {
    "finance": {
        "data_access": ["financial", "engineering"],
        "compliance": ["SOX"],
        "sensitivity": "high",
    },
    "engineering": {
        "data_access": ["engineering"],
        "compliance": ["none"],
        "sensitivity": "medium",
    },
    "hr-case": {
        "data_access": ["PII"],
        "compliance": ["HIPAA"],
        "sensitivity": "medium",
    },
    "hr-audit": {
        "data_access": ["PII"],
        "compliance": ["SOX", "HIPAA"],
        "sensitivity": "medium",
    },
}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Issue claims by Cognito app client ID for client credentials grants."""
    if event.get("triggerSource") != "TokenGeneration_ClientCredentials":
        return event
    configured_clients = json.loads(os.environ["EBD_CLIENT_CALLERS"])
    caller = configured_clients.get(event["callerContext"]["clientId"])
    if caller not in CLAIMS:
        raise ValueError("Unknown sample app client")
    event["response"]["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {"claimsToAddOrOverride": CLAIMS[caller]}
    }
    return event
