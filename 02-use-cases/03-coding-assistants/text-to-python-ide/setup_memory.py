#!/usr/bin/env python3
"""
setup_memory.py — Create the AgentCore Memory resource for the Text-to-Python IDE.

Run once before deploying. Writes memory_info.json with the memory ID.

Usage:
    python setup_memory.py           # create memory
    python setup_memory.py --delete  # delete memory
"""

import argparse
import json
import os
import sys
import time

import boto3
from dotenv import load_dotenv

load_dotenv(override=True)

REGION = os.getenv("AWS_REGION", "us-east-1")
MEMORY_NAME = "text_to_python_ide_memory"
INFO_FILE = "memory_info.json"


def get_client():
    session = boto3.Session(profile_name=os.getenv("AWS_PROFILE", "default"), region_name=REGION)
    return session.client("bedrock-agentcore-control", region_name=REGION)


def wait_for_active(cp, memory_id, label="Memory"):
    print(f"⏳ Waiting for {label} to become ACTIVE...")
    for _ in range(60):
        resp = cp.get_memory(memoryId=memory_id)
        # Handle both {"memory": {"status": ...}} and {"status": ...} formats
        mem = resp.get("memory", resp)
        status = mem.get("status", "UNKNOWN")
        print(f"   Status: {status}")
        if status == "ACTIVE":
            return
        if status == "FAILED":
            print(f"❌ {label} failed: {mem.get('failureReason')}")
            sys.exit(1)
        time.sleep(10)
    print(f"❌ Timed out waiting for {label}")
    sys.exit(1)


def create():
    cp = get_client()

    # Check if memory already exists
    existing = cp.list_memories().get("memories", [])
    for m in existing:
        memory_id = m.get("memoryId") or m.get("id", "")
        # Match by name in the ID/ARN (list response may not include name field)
        if m.get("name") == MEMORY_NAME or MEMORY_NAME in memory_id or MEMORY_NAME in m.get("arn", ""):
            print(f"✅ Memory already exists: {memory_id}")
            _save(memory_id)
            return memory_id

    print(f"🚀 Creating AgentCore Memory '{MEMORY_NAME}'...")
    resp = cp.create_memory(
        name=MEMORY_NAME,
        description="Persistent memory for Text-to-Python IDE — stores code generation and execution history",
        memoryExecutionRoleArn=f"arn:aws:iam::{boto3.client('sts').get_caller_identity()['Account']}:role/AgentCoreTextToPythonIDERole",
        eventExpiryDuration=90,  # days
        memoryStrategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "code_knowledge",
                    "description": "Extracts reusable code patterns, functions, and solutions the user has built",
                    "namespaces": ["ide/{actorId}/knowledge/"],
                }
            },
            {
                "summaryMemoryStrategy": {
                    "name": "session_summary",
                    "description": "Summarises each coding session so the agent remembers what was worked on",
                    "namespaces": ["ide/{actorId}/sessions/{sessionId}/"],
                }
            },
        ],
    )

    # Response key may vary by SDK version
    memory_id = (
        resp.get("memory", {}).get("id", "")
        or resp.get("memoryId")
        or resp.get("memory", {}).get("memoryId")
        or resp.get("id", "")
    )
    if not memory_id:
        # Try to find it from list
        print(f"   Response keys: {list(resp.keys())}")
        existing = cp.list_memories().get("memories", [])
        for m in existing:
            mid = m.get("memoryId") or m.get("id", "")
            if MEMORY_NAME in mid or m.get("name") == MEMORY_NAME:
                memory_id = mid
                break
    if not memory_id:
        print(f"❌ Could not determine memory ID from response: {resp}")
        sys.exit(1)
    print(f"✅ Memory created: {memory_id}")
    wait_for_active(cp, memory_id)
    _save(memory_id)
    return memory_id


def delete():
    if not os.path.exists(INFO_FILE):
        print("ℹ️  No memory_info.json found, nothing to delete")
        return

    with open(INFO_FILE) as f:
        info = json.load(f)

    memory_id = info["memory_id"]
    cp = get_client()
    print(f"🗑️  Deleting memory {memory_id}...")
    cp.delete_memory(memoryId=memory_id)
    os.remove(INFO_FILE)
    print("✅ Memory deleted")


def _save(memory_id):
    info = {"memory_id": memory_id, "region": REGION}
    with open(INFO_FILE, "w") as f:
        json.dump(info, f, indent=2)
    print(f"📝 Saved to {INFO_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()

    if args.delete:
        delete()
    else:
        create()
