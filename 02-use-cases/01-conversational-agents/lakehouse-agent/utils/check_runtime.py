#!/usr/bin/env python3
"""Check runtime status"""

import boto3
import json
from dotenv import load_dotenv
import os

load_dotenv()

runtime_id = os.getenv("LAKEHOUSE_AGENT_RUNTIME_ID", "lakehouse_agent-Hhb3lX6y7M")
region = os.getenv("AWS_REGION", "us-east-1")

print(f"Checking runtime: {runtime_id}")
print(f"Region: {region}")

client = boto3.client("bedrock-agentcore", region_name=region)

try:
    response = client.get_runtime(runtimeIdentifier=runtime_id)
    print("\n✅ Runtime found:")
    print(json.dumps(response, indent=2, default=str))
except Exception as e:
    print(f"\n❌ Error: {e}")
    print("\nTrying to list all runtimes...")
    try:
        response = client.list_runtimes()
        print(json.dumps(response, indent=2, default=str))
    except Exception as e2:
        print(f"❌ Error listing runtimes: {e2}")
