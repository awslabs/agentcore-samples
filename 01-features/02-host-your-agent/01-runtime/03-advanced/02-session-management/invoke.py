"""
Session Management Demo — demonstrates session continuity, isolation, and lifecycle.

Deploys its own agent (run deploy.py first), then:
1. Shows that the same session ID retains conversation context
2. Shows that different session IDs are completely isolated
3. Shows how to stop sessions to release resources

Usage:
    python deploy.py   # deploy the agent first
    python invoke.py   # run this demo
"""

import json
import sys
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
    """Invoke the agent with a specific session ID."""
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

    session_a = f"session-a-{uuid.uuid4()}"
    session_b = f"session-b-{uuid.uuid4()}"

    # ── Part 1: Session Continuity ────────────────────────────────────────
    print("═══ Part 1: Session Continuity ═══")
    print(f"Using session ID: {session_a}\n")

    print("  → Sending: 'My name is Alice and I work at Acme Corp.'")
    resp = invoke(client, arn, "My name is Alice and I work at Acme Corp.", session_a)
    print(f"  ← {resp[:300]}\n")

    print("  → Sending: 'What is my name and where do I work?'")
    resp = invoke(client, arn, "What is my name and where do I work?", session_a)
    print(f"  ← {resp[:300]}\n")

    print("  ✓ The agent remembers — same session ID shares the microVM\n")

    # ── Part 2: Session Isolation ─────────────────────────────────────────
    print("═══ Part 2: Session Isolation ═══")
    print(f"Using a NEW session ID: {session_b}\n")

    print("  → Sending: 'What is my name?' (to a fresh session)")
    resp = invoke(client, arn, "What is my name?", session_b)
    print(f"  ← {resp[:300]}\n")

    print("  ✓ The agent doesn't know — different session = isolated microVM\n")

    # ── Part 3: Stop Sessions ─────────────────────────────────────────────
    print("═══ Part 3: Stopping Sessions ═══")
    print("Stopping sessions releases microVM resources and saves costs.\n")

    for label, sid in [("A", session_a), ("B", session_b)]:
        try:
            client.stop_runtime_session(agentRuntimeArn=arn, runtimeSessionId=sid)
            print(f"  ✓ Stopped session {label}: {sid}")
        except Exception as e:
            print(f"  Warning (session {label}): {e}")

    print("\n✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
