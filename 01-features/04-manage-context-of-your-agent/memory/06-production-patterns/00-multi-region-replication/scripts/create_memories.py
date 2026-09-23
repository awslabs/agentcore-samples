"""Create matching source + target memories for the replication sample.

Creates an AgentCore memory in the source region and an identically-configured
memory in the target region, using the bedrock-agentcore-control API. Prints the
two memory IDs, which feed into ``deploy_streaming.sh`` and the dual-writer.

Both memories use the same strategy config so that namespaces line up across
regions. (Strategy IDs are per-memory and are not forwarded; replicated records
are associated by namespace.)

Usage::

    python scripts/create_memories.py \
        --name my_agent_memory \
        --source-region us-east-1 \
        --target-region us-west-2

``--name`` takes letters, digits and underscores only: CreateMemory constrains the
name to ``[a-zA-Z][a-zA-Z0-9_]{0,47}``, so a hyphen is rejected by the service.
"""

import argparse
import json
import re
import time

import boto3
from botocore.exceptions import ClientError

# From the CreateMemory service model. botocore does not enforce it client-side,
# so an invalid name only fails once the request reaches the service.
MEMORY_NAME_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,47}$")


def memory_name(value):
    """Reject a --name that CreateMemory will refuse, including our own suffix."""
    if not MEMORY_NAME_RE.match(f"{value}_source"):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not usable: '{value}_source' must match "
            "[a-zA-Z][a-zA-Z0-9_]{0,47} (letters, digits and underscores, no hyphens)"
        )
    return value


def _wait_active(client, memory_id, label, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get_memory(memoryId=memory_id)["memory"]["status"]
        print(f"  {label}: {status}")
        if status == "ACTIVE":
            return
        if status == "FAILED":
            raise SystemExit(f"{label} memory creation FAILED")
        time.sleep(15)
    raise SystemExit(f"Timed out waiting for {label} to become ACTIVE")


def create_memory(region, name, strategies):
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    try:
        resp = client.create_memory(
            name=name,
            description="Cross-region replication sample",
            memoryStrategies=strategies,
            eventExpiryDuration=90,
        )
    except ClientError as exc:
        raise SystemExit(f"create_memory failed in {region}: {exc}")
    memory_id = resp["memory"]["id"]
    print(f"Created memory {memory_id} in {region}")
    _wait_active(client, memory_id, region)
    return memory_id


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True, type=memory_name)
    p.add_argument("--source-region", required=True)
    p.add_argument("--target-region", required=True)
    p.add_argument(
        "--out",
        default="memories.json",
        help="where to write the resulting memory IDs",
    )
    args = p.parse_args()

    # A simple semantic strategy; mirror your real config here. Identical config
    # in both regions keeps namespaces / strategy IDs aligned for replication.
    strategies = [
        {
            "semanticMemoryStrategy": {
                "name": "semantic",
                "namespaceTemplates": ["/facts/{actorId}/"],
            }
        }
    ]

    source_id = create_memory(args.source_region, f"{args.name}_source", strategies)
    target_id = create_memory(args.target_region, f"{args.name}_target", strategies)

    out = {
        "source_memory_id": source_id,
        "target_memory_id": target_id,
        "source_region": args.source_region,
        "target_region": args.target_region,
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print("\nMemory IDs written to", args.out)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
