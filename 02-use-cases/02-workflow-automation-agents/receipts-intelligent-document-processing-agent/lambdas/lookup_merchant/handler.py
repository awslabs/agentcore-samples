"""Gateway tool: lookup_merchant — normalize a merchant name against the catalog.

Best-effort: returns the catalog entry if the normalized key matches, else a
passthrough with the cleaned name so the agent always gets a usable answer. The
Merchants table is optional (spec §8); a missing table/entry is not an error.
"""

import json
import os
import re

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("MERCHANTS_TABLE", "ReceiptsAgent-Merchants"))


def _normalize(name: str) -> str:
    """Lowercase, strip punctuation/extra space → a stable lookup key."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def handler(event, context):
    name = event.get("name", "")
    if not name:
        return json.dumps({"error": "name is required"})

    key = _normalize(name)
    try:
        item = table.get_item(Key={"merchantKey": key}).get("Item")
    except Exception:
        item = None  # catalog optional — degrade to passthrough

    if item:
        return json.dumps({"matched": True, "merchant": item}, default=str)

    # No catalog hit: return a normalized passthrough the agent can still use.
    return json.dumps({"matched": False, "merchant": {"merchantKey": key, "displayName": name.strip()}})
