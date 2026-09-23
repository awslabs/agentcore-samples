#!/usr/bin/env python3
"""
invoke_runtime.py — Invoke the deployed AgentCore Runtime via HTTP POST /invocations.

Reads runtime_info.json written by deploy_runtime.py.

Usage:
    python invoke_runtime.py --action health
    python invoke_runtime.py --action generate_code --prompt "write a fibonacci function"
    python invoke_runtime.py --action execute_code  --code "print(2+2)"
"""

import argparse
import json
import os
import sys
import urllib.parse

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from dotenv import load_dotenv

load_dotenv()

RUNTIME_INFO_FILE = "runtime_info.json"


def load_runtime_info():
    if not os.path.exists(RUNTIME_INFO_FILE):
        print(f"❌ {RUNTIME_INFO_FILE} not found. Run deploy_runtime.py first.")
        sys.exit(1)
    with open(RUNTIME_INFO_FILE) as f:
        return json.load(f)


def get_data_plane_endpoint(region: str) -> str:
    return f"https://bedrock-agentcore.{region}.amazonaws.com"


def invoke(runtime_arn: str, endpoint_name: str, region: str, payload: dict):
    session = boto3.Session(profile_name=os.getenv("AWS_PROFILE", "default"), region_name=region)

    # Build the invocation URL
    encoded_arn = urllib.parse.quote(runtime_arn, safe="")
    url = f"{get_data_plane_endpoint(region)}/runtimes/{encoded_arn}/invocations?qualifier={endpoint_name}"

    body = json.dumps(payload).encode("utf-8")

    # Sign with SigV4
    credentials = session.get_credentials().get_frozen_credentials()
    request = AWSRequest(method="POST", url=url, data=body, headers={"Content-Type": "application/json"})
    SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(request)

    print("🔌 Invoking AgentCore Runtime...")
    print(f"   ARN      : {runtime_arn}")
    print(f"   Endpoint : {endpoint_name}")
    print(f"   Payload  : {json.dumps(payload)}\n")

    resp = requests.post(
        url,
        data=body,
        headers=dict(request.headers),
        verify=False,  # macOS cert workaround
        timeout=120,
    )

    if resp.ok:
        print("✅ Response:")
        try:
            print(json.dumps(resp.json(), indent=2))
        except Exception:
            print(resp.text)
    else:
        print(f"❌ HTTP {resp.status_code}: {resp.text}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", default="health", choices=["health", "generate_code", "execute_code"])
    parser.add_argument("--prompt", default="", help="Prompt for generate_code")
    parser.add_argument("--code", default="", help="Code for execute_code")
    parser.add_argument("--session-id", default=None)
    args = parser.parse_args()

    info = load_runtime_info()

    payload = {"action": args.action}
    if args.session_id:
        payload["session_id"] = args.session_id
    if args.action == "generate_code":
        if not args.prompt:
            print("❌ --prompt is required for generate_code")
            sys.exit(1)
        payload["prompt"] = args.prompt
    elif args.action == "execute_code":
        if not args.code:
            print("❌ --code is required for execute_code")
            sys.exit(1)
        payload["code"] = args.code

    invoke(
        runtime_arn=info["runtime_arn"],
        endpoint_name=info["endpoint_name"],
        region=info["region"],
        payload=payload,
    )


if __name__ == "__main__":
    main()
