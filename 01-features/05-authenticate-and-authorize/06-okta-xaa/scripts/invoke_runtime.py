"""Invoke the deployed AgentCore Runtime with a real Okta ID token.

Signs the user in (OIDC PKCE, reusing test_xaa_flow.login_and_get_id_token),
then calls the runtime's HTTPS invocations endpoint with:
  * Authorization: Bearer <ID token>   -> satisfies the CUSTOM_JWT authorizer
  * payload {"prompt": ..., "id_token": <ID token>}  -> the agent reads this

Env:
  AGENT_RUNTIME_ARN   the deployed runtime ARN
  AWS_REGION          default us-east-1

Usage: python3 invoke_runtime.py "what is on my todo list?"
"""

from __future__ import annotations

import os
import sys
import urllib.parse

import httpx
from dotenv import load_dotenv

load_dotenv()

from test_xaa_flow import login_and_get_id_token

ARN = os.environ["AGENT_RUNTIME_ARN"]
REGION = os.environ.get("AWS_REGION", "us-east-1")


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "what is on my todo list?"
    id_token = login_and_get_id_token()

    arn_enc = urllib.parse.quote(ARN, safe="")
    url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{arn_enc}/invocations?qualifier=DEFAULT"
    headers = {"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"}
    body = {"prompt": prompt, "id_token": id_token}

    print(f"\nInvoking runtime with prompt: {prompt!r}\n")
    with httpx.stream("POST", url, headers=headers, json=body, timeout=180) as r:
        print("HTTP", r.status_code)
        for line in r.iter_lines():
            if line:
                print(line)


if __name__ == "__main__":
    main()
