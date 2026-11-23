"""Create one WebRTC Runtime V2 from an existing ARM64 image and execution role."""

import argparse
import json
import os
import re
import time
import uuid
from pathlib import Path

import boto3

RECEIPT = Path(__file__).with_name("webrtc-runtime.json")
TAG = "webrtc-sample-deployment"


def plan(region, account, name, image, role, channel, subnets, groups, token):
    """Validate the imported resources before making a mutating call (commercial AWS)."""
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com/"
    if not re.fullmatch(r"[0-9]{12}", account) or not re.fullmatch(r"[a-z]{2}-[a-z0-9-]+-[0-9]+", region):
        raise ValueError("Expected a commercial AWS Region and 12-digit account ID")
    if not image.startswith(registry) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}", image[len(registry) :]
    ):
        raise ValueError("Use a same-account, same-Region private ECR image by digest")
    if not re.fullmatch(rf"arn:aws:iam::{account}:role/[\w+=,.@/-]+", role):
        raise ValueError("Use a prepared role ARN from the deployment account")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,47}", name) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,256}", channel):
        raise ValueError("Invalid Runtime or KVS channel name")
    for ids, prefix in ((subnets, "subnet"), (groups, "sg")):
        if not ids or not all(re.fullmatch(rf"{prefix}-(?:[0-9a-f]{{8}}|[0-9a-f]{{17}})", i) for i in ids):
            raise ValueError(f"Invalid {prefix} ID")
    return {
        "agentRuntimeName": name,
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image}},
        "networkConfiguration": {
            "networkMode": "VPC",
            "networkModeConfig": {"subnets": subnets, "securityGroups": groups},
        },
        "platformVersion": "V2",
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "environmentVariables": {"AWS_REGION": region, "AWS_DEFAULT_REGION": region, "KVS_CHANNEL_NAME": channel},
        "roleArn": role,
        "clientToken": token,
        "tags": {TAG: token},
    }


def store(stream, data):
    json.dump(data, stream, indent=2)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())


def wait(client, runtime_id, deleting=False):
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        try:
            info = client.get_agent_runtime(agentRuntimeId=runtime_id)
        except client.exceptions.ResourceNotFoundException:
            if deleting:
                return
            raise
        if info["status"].endswith("FAILED"):
            raise RuntimeError(f"Runtime {runtime_id}: {info['status']}; inspect its service logs")
        if not deleting and info["status"] == "READY":
            if info.get("platformVersion") != "V2":
                raise RuntimeError("Runtime does not report V2; retain receipt for cleanup")
            return
        time.sleep(15)
    raise TimeoutError("Runtime transition timed out; retain receipt and inspect the runtime")


def create(client, region, account, request, receipt=RECEIPT):
    token = request["clientToken"]
    state = {"region": region, "account": account, "name": request["agentRuntimeName"], "token": token}
    # Reserve the receipt before create, including on name conflicts and ambiguous responses.
    with receipt.open("x", encoding="utf-8") as handle:
        store(handle, state)
    result = client.create_agent_runtime(**request)
    state.update(runtime_id=result["agentRuntimeId"], runtime_arn=result["agentRuntimeArn"])
    temporary = receipt.with_suffix(f".{token}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        store(handle, state)
    temporary.replace(receipt)
    print(f"Runtime: {state['runtime_arn']}\nReceipt: {receipt}", flush=True)
    wait(client, state["runtime_id"])


def cleanup(client, region, account, receipt=RECEIPT):
    state = json.loads(receipt.read_text(encoding="utf-8"))
    if (account, region) != (state["account"], state["region"]):
        raise ValueError("Receipt account or Region mismatch")
    if "runtime_id" not in state:
        raise ValueError("Incomplete receipt. Reconcile by deployment tag; do not delete by name.")
    rid = state["runtime_id"]
    arn = f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{rid}"
    if arn != state["runtime_arn"]:
        raise ValueError("Receipt ARN mismatch")
    try:
        current = client.get_agent_runtime(agentRuntimeId=rid)
    except client.exceptions.ResourceNotFoundException:
        receipt.unlink()
        return
    if current["agentRuntimeArn"] != arn or current["agentRuntimeName"] != state["name"]:
        raise ValueError("Runtime identity mismatch")
    tags = client.list_tags_for_resource(resourceArn=arn)["tags"]
    if tags.get(TAG) != state["token"]:
        raise ValueError("Runtime ownership tag mismatch")
    if current["status"] != "DELETING":
        client.delete_agent_runtime(agentRuntimeId=rid)
    wait(client, rid, deleting=True)
    receipt.unlink()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--region", required=True)
    cli.add_argument("--account", required=True, help="Expected AWS account (verified with STS)")
    sub = cli.add_subparsers(dest="action", required=True)
    deploy = sub.add_parser("create")
    for key in ["image", "role", "channel"]:
        deploy.add_argument(f"--{key}", required=True)
    deploy.add_argument("--name", default="bidi_05_webrtc_agent")
    deploy.add_argument("--subnets", required=True, nargs="+")
    deploy.add_argument("--security-groups", required=True, nargs="+")
    sub.add_parser("cleanup")
    args = cli.parse_args()
    session = boto3.Session(region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != args.account:
        cli.error("AWS credentials belong to another account")
    client = session.client("bedrock-agentcore-control")
    if args.action == "cleanup":
        cleanup(client, args.region, args.account)
    else:
        request = plan(
            args.region,
            args.account,
            args.name,
            args.image,
            args.role,
            args.channel,
            args.subnets,
            args.security_groups,
            str(uuid.uuid4()),
        )
        create(client, args.region, args.account, request)


if __name__ == "__main__":
    main()
