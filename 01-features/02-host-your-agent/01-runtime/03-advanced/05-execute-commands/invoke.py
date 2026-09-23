"""
Execute Commands Demo — run shell commands inside a runtime session.

Demonstrates the invoke_agent_runtime_command API:
1. First invokes the agent to create a session (microVM)
2. Then runs shell commands inside that session
3. Streams stdout/stderr in real time
4. Stops the session when done

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


def execute_command(client, arn: str, session_id: str, command: str, timeout: int = 60) -> int:
    """Execute a shell command in the runtime session and stream output.

    Returns the exit code.
    """
    print(f"\n  $ {command}")
    print("  " + "-" * 50)

    response = client.invoke_agent_runtime_command(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        body={"command": command, "timeout": timeout},
    )

    exit_code = -1
    for event in response.get("stream", []):
        if "chunk" in event:
            chunk = event["chunk"]

            if "contentDelta" in chunk:
                delta = chunk["contentDelta"]
                if "stdout" in delta:
                    for line in delta["stdout"].splitlines():
                        print(f"  {line}")
                if "stderr" in delta:
                    for line in delta["stderr"].splitlines():
                        print(f"  [stderr] {line}")

            if "contentStop" in chunk:
                exit_code = chunk["contentStop"].get("exitCode", -1)
                status = chunk["contentStop"].get("status", "UNKNOWN")
                print(f"  [exit: {exit_code}, status: {status}]")

    return exit_code


def main():
    config = load_config()
    arn = config["runtime_arn"]
    region = config["region"]
    client = boto3.client("bedrock-agentcore", region_name=region)
    session_id = f"cmd-demo-{uuid.uuid4()}"

    print("═══ Execute Commands Demo ═══")
    print(f"Runtime: {arn}")
    print(f"Session: {session_id}")

    # Step 1: Invoke the agent to create a session (starts the microVM)
    print("\n  Initializing session with an agent invocation...")
    client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=json.dumps({"prompt": "hello"}).encode("utf-8"),
        runtimeSessionId=session_id,
    )
    print("  ✓ Session created\n")

    # Step 2: Execute commands inside the running session
    # Note: commands run as binaries (shell features like pipes/redirects are not supported)
    commands = [
        ("Check Python version", "python3 --version"),
        ("List installed packages", "python3 -m pip list"),
        ("Check disk space", "df -h /"),
        ("Show running processes", "ps aux"),
        (
            "Run inline Python",
            "python3 -c \"import platform; print(f'OS: {platform.system()} {platform.release()}')\"",
        ),
        (
            "Write and read a file",
            "python3 -c \"import pathlib; p = pathlib.Path('/tmp/test.txt'); p.write_text('Hello from AgentCore!'); print(p.read_text())\"",
        ),
    ]

    for label, cmd in commands:
        print(f"═══ {label} ═══")
        execute_command(client, arn, session_id, cmd)

    # Step 3: Stop the session to release resources
    print("\n═══ Cleanup ═══")
    try:
        client.stop_runtime_session(agentRuntimeArn=arn, runtimeSessionId=session_id)
        print("  ✓ Session stopped")
    except Exception as e:
        print(f"  Warning: {e}")

    print("\n✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
