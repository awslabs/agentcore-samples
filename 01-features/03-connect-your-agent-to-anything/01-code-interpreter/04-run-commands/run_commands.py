"""
Running Shell and AWS CLI Commands via AgentCore Code Interpreter.

Demonstrates using the Code Interpreter's executeCommand tool to:
  1. Create a Code Interpreter resource with a custom execution role
  2. Start a session
  3. Run shell commands (echo, pip install)
  4. Upload a local file to the sandbox
  5. Create an S3 bucket and upload the file via aws cli commands
  6. List the S3 bucket contents
  7. Clean up: stop session, delete Code Interpreter, delete S3 bucket

The execution role must trust bedrock-agentcore.amazonaws.com and have
AmazonS3FullAccess (or equivalent) to allow S3 operations from within
the sandbox.

Prerequisites:
    pip install -r ../requirements.txt
    export EXECUTION_ROLE_ARN=arn:aws:iam::<account>:role/<role-name>

IAM permissions on your local caller:
    bedrock-agentcore:CreateCodeInterpreter
    bedrock-agentcore:StartCodeInterpreterSession
    bedrock-agentcore:InvokeCodeInterpreter
    bedrock-agentcore:StopCodeInterpreterSession
    bedrock-agentcore:DeleteCodeInterpreter
    s3:CreateBucket, s3:PutObject, s3:GetObject, s3:ListBucket, s3:DeleteBucket, s3:DeleteObject

Usage:
    export EXECUTION_ROLE_ARN=arn:aws:iam::123456789012:role/my-role
    python run_commands.py
    python run_commands.py --skip-s3   # skip S3 operations
"""

import argparse
import json
import os
import sys
import time
from typing import Any, Dict

import boto3
from boto3.session import Session

from bedrock_agentcore._utils import endpoints

# ── Configuration ──────────────────────────────────────────────────────────────

boto_session = Session()
REGION = boto_session.region_name or "us-west-2"
ACCOUNT_ID = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "samples")
LOCAL_FILE = os.path.join(SAMPLES_DIR, "stats.py")


# ── Helpers ────────────────────────────────────────────────────────────────────


def call_tool(
    dp_client,
    interpreter_id: str,
    session_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> str:
    """Invoke a code interpreter tool via the boto3 data-plane client."""
    response = dp_client.invoke_code_interpreter(
        codeInterpreterIdentifier=interpreter_id,
        sessionId=session_id,
        name=tool_name,
        arguments=arguments,
    )
    for event in response["stream"]:
        return json.dumps(event["result"], indent=2)
    return json.dumps({"isError": True})


def print_result(label: str, result_json: str):
    result = json.loads(result_json)
    stdout = result.get("structuredContent", {}).get("stdout", "")
    stderr = result.get("structuredContent", {}).get("stderr", "")
    exit_code = result.get("structuredContent", {}).get("exitCode", "?")
    print(f"\n  [{label}] exit={exit_code}")
    if stdout:
        print(f"  stdout: {stdout.strip()}")
    if stderr:
        print(f"  stderr: {stderr.strip()[:200]}")


# ── Main ───────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Code Interpreter run-commands demo")
    parser.add_argument(
        "--execution-role-arn",
        default=os.getenv("EXECUTION_ROLE_ARN"),
        help="IAM role ARN for the Code Interpreter (env: EXECUTION_ROLE_ARN)",
    )
    parser.add_argument(
        "--skip-s3",
        action="store_true",
        help="Skip S3 operations (still demos shell commands)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.execution_role_arn:
        print(
            "ERROR: --execution-role-arn is required (or set EXECUTION_ROLE_ARN env var).\n"
            "The role must trust bedrock-agentcore.amazonaws.com and have S3 permissions."
        )
        sys.exit(1)

    print("=" * 60)
    print("AgentCore Code Interpreter — Run Commands Demo")
    print("=" * 60)
    print(f"  Region:         {REGION}")
    print(f"  Execution role: {args.execution_role_arn}")

    # ── AWS clients ────────────────────────────────────────────────────────────
    data_endpoint = endpoints.get_data_plane_endpoint(REGION)
    ctrl_endpoint = endpoints.get_control_plane_endpoint(REGION)

    cp_client = boto3.client("bedrock-agentcore-control", region_name=REGION, endpoint_url=ctrl_endpoint)
    dp_client = boto3.client("bedrock-agentcore", region_name=REGION, endpoint_url=data_endpoint)

    # ── 1. Create Code Interpreter ─────────────────────────────────────────────
    unique_name = f"run_cmds_{int(time.time()) % 100000}"
    print(f"\n[1] Creating Code Interpreter '{unique_name}'...")
    resp = cp_client.create_code_interpreter(
        name=unique_name,
        description="Demo: run shell and AWS CLI commands",
        executionRoleArn=args.execution_role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
    )
    interpreter_id = resp["codeInterpreterId"]
    print(f"  Interpreter ID: {interpreter_id}")

    # ── 2. Start session ───────────────────────────────────────────────────────
    print("\n[2] Starting session...")
    sess_resp = dp_client.start_code_interpreter_session(
        codeInterpreterIdentifier=interpreter_id,
        name="run_cmds_session",
        sessionTimeoutSeconds=900,
    )
    session_id = sess_resp["sessionId"]
    print(f"  Session ID: {session_id}")

    try:
        # ── 3. Shell commands ──────────────────────────────────────────────────
        print("\n[3] Running shell commands...")
        result = call_tool(
            dp_client,
            interpreter_id,
            session_id,
            "executeCommand",
            {"command": "echo 'Hello from the AgentCore Code Interpreter sandbox!'"},
        )
        print_result("echo", result)

        result = call_tool(
            dp_client,
            interpreter_id,
            session_id,
            "executeCommand",
            {"command": "python3 --version"},
        )
        print_result("python --version", result)

        result = call_tool(
            dp_client,
            interpreter_id,
            session_id,
            "executeCommand",
            {"command": "pip install pandas --quiet"},
        )
        print_result("pip install pandas", result)

        # ── 4. Upload file to sandbox ──────────────────────────────────────────
        print("\n[4] Writing stats.py to sandbox...")
        if os.path.exists(LOCAL_FILE):
            with open(LOCAL_FILE, "r") as f:
                file_content = f.read()
            write_result = call_tool(
                dp_client,
                interpreter_id,
                session_id,
                "writeFiles",
                {"content": [{"path": "stats.py", "text": file_content}]},
            )
            write_data = json.loads(write_result)
            print(f"  writeFiles: {write_data.get('content', [{}])[0].get('text', '')}")
        else:
            print(f"  WARNING: {LOCAL_FILE} not found, skipping file upload.")

        # ── 5. S3 operations ───────────────────────────────────────────────────
        if not args.skip_s3:
            bucket_name = f"agentcore-demo-{int(time.time()) % 100000}"
            s3_path = f"s3://{bucket_name}"
            print(f"\n[5] S3 operations (bucket: {bucket_name})...")

            result = call_tool(
                dp_client,
                interpreter_id,
                session_id,
                "executeCommand",
                {"command": f"aws s3 mb {s3_path} --region {REGION}"},
            )
            print_result("s3 mb", result)

            result = call_tool(
                dp_client,
                interpreter_id,
                session_id,
                "executeCommand",
                {"command": f"aws s3 cp stats.py {s3_path}/"},
            )
            print_result("s3 cp", result)

            result = call_tool(
                dp_client,
                interpreter_id,
                session_id,
                "executeCommand",
                {"command": f"aws s3 ls {s3_path}/"},
            )
            print_result("s3 ls", result)

            # Cleanup S3 bucket
            print(f"\n  Cleaning up S3 bucket {bucket_name}...")
            s3 = boto3.client("s3", region_name=REGION)
            try:
                objs = s3.list_objects_v2(Bucket=bucket_name).get("Contents", [])
                for obj in objs:
                    s3.delete_object(Bucket=bucket_name, Key=obj["Key"])
                s3.delete_bucket(Bucket=bucket_name)
                print(f"  Deleted {bucket_name}")
            except Exception as exc:
                print(f"  Warning during S3 cleanup: {exc}")
        else:
            print("\n[5] Skipping S3 operations (--skip-s3).")

    finally:
        # ── 6. Stop session and delete interpreter ─────────────────────────────
        print("\n[6] Stopping session...")
        dp_client.stop_code_interpreter_session(codeInterpreterIdentifier=interpreter_id, sessionId=session_id)
        print("  Session stopped.")

        print("  Deleting Code Interpreter...")
        cp_client.delete_code_interpreter(codeInterpreterId=interpreter_id)
        print("  Interpreter deleted.")

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
