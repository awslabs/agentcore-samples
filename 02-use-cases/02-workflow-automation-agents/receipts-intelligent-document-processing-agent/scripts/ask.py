#!/usr/bin/env python3
"""Ask the receipts agent one question about a user's expenses (conversational mode).

  python3 scripts/ask.py --user user-001 "How much did I spend at Mr D.I.Y.?"

SECURITY: the user_id is bound into a KMS-HMAC-signed identity token minted here (the
trusted invoker). The agent derives user_id ONLY from the verified token — editing the
request body cannot read another user's data. You need kms:GenerateMac on the identity
key (the IdentityKeyId stack output) to mint a token.
"""

import argparse
import base64
import json
import time
import uuid

import boto3

MAC_ALGORITHM = "HMAC_SHA_256"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def mint_identity(user_id: str, key_id: str, region: str, ttl: int = 900) -> str:
    """Sign {user_id, exp} with the KMS HMAC key — must match app/.../identity.py."""
    claim = json.dumps(
        {"user_id": user_id, "exp": int(time.time()) + ttl}, separators=(",", ":"), sort_keys=True
    ).encode()
    mac = boto3.client("kms", region_name=region).generate_mac(KeyId=key_id, MacAlgorithm=MAC_ALGORITHM, Message=claim)[
        "Mac"
    ]
    return f"{_b64u(claim)}.{_b64u(mac)}"


def _stack_out(region: str, stack: str, key_substr: str) -> str:
    # CDK names some outputs cleanly (RuntimeArn) and others with a construct prefix
    # (InfraIdentityKeyId...), so match by substring.
    cfn = boto3.client("cloudformation", region_name=region)
    outs = cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    for o in outs:
        if key_substr in o["OutputKey"]:
            return o["OutputValue"]
    raise SystemExit(f"stack output *{key_substr}* not found")


def ask(question: str, user_id: str, region: str, stack: str) -> str:
    runtime_arn = _stack_out(region, stack, "RuntimeArn")
    key_id = _stack_out(region, stack, "IdentityKeyId")
    token = mint_identity(user_id, key_id, region)

    client = boto3.client("bedrock-agentcore", region_name=region)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=f"chat-{uuid.uuid4().hex}",
        # NOTE: user_id is NOT trusted from here — only the signed identity_token is.
        payload=json.dumps({"question": question, "identity_token": token}).encode(),
    )
    raw = resp["response"]
    body = raw.read().decode() if hasattr(raw, "read") else raw
    data = json.loads(body) if isinstance(body, str) else body
    if isinstance(data, dict) and data.get("error"):
        return f"[error] {data['error']}"
    return data.get("answer", json.dumps(data)) if isinstance(data, dict) else str(data)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--user", required=True, help="the user_id to authenticate AS (signed into the token)")
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--stack", default="AgentCore-ReceiptsAgent-dev")
    args = ap.parse_args()
    print(ask(args.question, args.user, args.region, args.stack))


if __name__ == "__main__":
    main()
