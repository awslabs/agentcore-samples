"""
File Operations with AgentCore Code Interpreter.

Demonstrates the direct Code Interpreter API (no agent framework):
  1. Start a Code Interpreter session
  2. Write local files (data.csv, stats.py) into the sandbox
  3. List sandbox contents to verify
  4. Execute the analysis script in the sandbox
  5. Display results and stop the session

The Code Interpreter provides an isolated sandbox with a Python runtime,
file system, and shell. Files you write survive for the duration of the
session and can be read by subsequent executions.

Prerequisites:
    pip install -r ../requirements.txt

IAM permissions required:
    bedrock-agentcore:CreateCodeInterpreter
    bedrock-agentcore:StartCodeInterpreterSession
    bedrock-agentcore:InvokeCodeInterpreter
    bedrock-agentcore:StopCodeInterpreterSession
    bedrock-agentcore:DeleteCodeInterpreter
    bedrock-agentcore:ListCodeInterpreters
    bedrock-agentcore:GetCodeInterpreter

Usage:
    python file_operations.py
"""

import json
import os
import pprint
from typing import Any, Dict

from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

# ── Configuration ──────────────────────────────────────────────────────────────

REGION = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "samples")


# ── Helpers ────────────────────────────────────────────────────────────────────


def read_file(file_path: str) -> str:
    """Read a local file, returning empty string on error."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"[warn] File not found: {file_path}")
        return ""
    except Exception as exc:
        print(f"[warn] Could not read {file_path}: {exc}")
        return ""


def call_tool(client: CodeInterpreter, tool_name: str, arguments: Dict[str, Any]) -> str:
    """Invoke a sandbox tool and return the JSON-encoded result."""
    response = client.invoke(tool_name, arguments)
    for event in response["stream"]:
        return json.dumps(event["result"])
    return json.dumps({"isError": True, "content": []})


# ── Demo ───────────────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("AgentCore Code Interpreter — File Operations Demo")
    print("=" * 60)

    # 1. Read local sample files
    print("\n[1] Reading local sample files...")
    data_csv = read_file(os.path.join(SAMPLES_DIR, "data.csv"))
    stats_py = read_file(os.path.join(SAMPLES_DIR, "stats.py"))
    if not data_csv or not stats_py:
        print("  ERROR: Sample files not found. Make sure samples/ directory exists.")
        return

    files_to_create = [
        {"path": "data.csv", "text": data_csv},
        {"path": "stats.py", "text": stats_py},
    ]
    print(f"  data.csv : {len(data_csv):,} bytes")
    print(f"  stats.py : {len(stats_py):,} bytes")

    # 2. Start Code Interpreter session
    print(f"\n[2] Starting Code Interpreter session (region: {REGION})...")
    code_client = CodeInterpreter(REGION)
    code_client.start()
    print("  Session started.")

    try:
        # 3. Write files into sandbox
        print("\n[3] Writing files to sandbox...")
        result_json = call_tool(code_client, "writeFiles", {"content": files_to_create})
        result = json.loads(result_json)
        print(f"  writeFiles: {result['content'][0]['text']}")

        # 4. Verify files exist
        print("\n[4] Listing sandbox files...")
        list_json = call_tool(code_client, "listFiles", {"path": ""})
        list_result = json.loads(list_json)
        for item in list_result.get("content", []):
            print(f"  {item.get('description', 'unknown'):12} {item.get('name', '')}")

        # 5. Execute the stats script
        print("\n[5] Executing stats.py in sandbox...")
        exec_json = call_tool(
            code_client,
            "executeCode",
            {
                "code": stats_py,
                "language": "python",
                "clearContext": True,
            },
        )
        exec_result = json.loads(exec_json)

        print("\n  Full result:")
        pprint.pprint(exec_result)

        stdout = exec_result.get("structuredContent", {}).get("stdout", "")
        if stdout:
            print("\n  Standard output from stats.py:")
            print(stdout)

    finally:
        # 6. Stop session
        print("\n[6] Stopping Code Interpreter session...")
        code_client.stop()
        print("  Session stopped.")

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
