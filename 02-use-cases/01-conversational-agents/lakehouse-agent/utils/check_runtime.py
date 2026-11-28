#!/usr/bin/env python3
"""Check runtime status"""

import json
import os
import sys
from pathlib import Path

import boto3

# Importable both as `python utils/check_runtime.py` (sys.path[0] is utils/) and
# as `python -m utils.check_runtime` (sys.path[0] is the sample root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.env_file import load_env_file

# Load .env with the sample's own loader (utils/env_file.py), so no python-dotenv
# dependency is needed. Variables already exported in the environment win.
load_env_file()

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
