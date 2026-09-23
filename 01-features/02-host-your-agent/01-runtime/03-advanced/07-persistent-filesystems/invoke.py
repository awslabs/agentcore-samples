"""
Persistent Filesystem Demo — shows that files survive session stop/resume.

Flow:
1. Create a session, add some notes
2. Stop the session (microVM shuts down)
3. Resume the SAME session — notes are still there
4. Clean up

Usage:
    python deploy.py   # deploy the agent first
    python invoke.py   # run this demo
"""

import json
import sys
import time
import uuid

import boto3


def load_config() -> dict:
    try:
        with open("runtime_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


def invoke(client, arn: str, prompt: str, session_id: str) -> str:
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
        runtimeSessionId=session_id,
    )
    return response["response"].read().decode("utf-8")


def main():
    config = load_config()
    arn = config["runtime_arn"]
    region = config["region"]
    client = boto3.client("bedrock-agentcore", region_name=region)
    session_id = f"persist-demo-{uuid.uuid4()}"

    print("═══ Persistent Filesystem Demo ═══")
    print(f"Runtime: {arn}")
    print(f"Session: {session_id}\n")

    # ── Part 1: Add notes ─────────────────────────────────────────────────
    print("── Part 1: Adding notes to persistent storage ──\n")

    print("  → Add note: 'Buy groceries'")
    resp = invoke(client, arn, "Add a note: Buy groceries", session_id)
    print(f"  ← {resp[:200]}\n")

    print("  → Add note: 'Review pull request #42'")
    resp = invoke(client, arn, "Add a note: Review pull request #42", session_id)
    print(f"  ← {resp[:200]}\n")

    print("  → List all notes")
    resp = invoke(client, arn, "List all my notes", session_id)
    print(f"  ← {resp[:300]}\n")

    # ── Part 2: Stop the session ──────────────────────────────────────────
    print("── Part 2: Stopping the session (microVM shuts down) ──\n")

    try:
        client.stop_runtime_session(agentRuntimeArn=arn, runtimeSessionId=session_id)
        print("  ✓ Session stopped — microVM is shut down")
        print("  Waiting 10s before resuming...\n")
        time.sleep(10)
    except Exception as e:
        print(f"  Warning: {e}\n")

    # ── Part 3: Resume the same session ───────────────────────────────────
    print("── Part 3: Resuming the SAME session ──\n")
    print(f"  Using same session ID: {session_id}")
    print("  If persistent storage works, notes should still be there.\n")

    print("  → List all notes (after session restart)")
    resp = invoke(client, arn, "List all my notes", session_id)
    print(f"  ← {resp[:300]}\n")

    # ── Part 4: Clean up session ──────────────────────────────────────────
    print("── Part 4: Cleanup ──\n")
    try:
        client.stop_runtime_session(agentRuntimeArn=arn, runtimeSessionId=session_id)
        print("  ✓ Session stopped")
    except Exception as e:
        print(f"  Warning: {e}")

    print("\n✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
