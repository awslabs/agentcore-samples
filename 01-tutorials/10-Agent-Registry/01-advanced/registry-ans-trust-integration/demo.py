"""
End-to-end demo: AWS Agent Registry + ANS Integration.

Demonstrates the full flow:
1. Create a registry (or use existing)
2. Fetch ANS metadata for the live GoDaddy agent
3. Build an A2A agent card with ANS extension
4. Create the record in AWS Registry
5. Wait for DRAFT status
6. Submit for approval
7. Approve the record
8. Search for it
9. Print the full record showing ANS metadata in the extension

Usage:
    python demo.py --region us-east-1
    python demo.py --region us-east-1 --registry-id <existing-id>
"""

import argparse
import json
import logging
import time

from botocore.exceptions import ClientError

from ans_metadata import fetch_ans_metadata
import registry_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# The live GoDaddy ANS agent
LIVE_ANS_NAME = (
    "ans://v1.0.0.support-a08c16a8-f972-472f-b95f-3debacfcb201.helpagent.club"
)

# Base A2A agent card (without ANS extension — that gets added)
BASE_AGENT_CARD = {
    "protocolVersion": "0.3.0",
    "name": "GoDaddy Customer Support Agent",
    "description": "ANS-registered customer support agent with trust verification via GoDaddy Transparency Log",
    "url": "https://support-a08c16a8-f972-472f-b95f-3debacfcb201.helpagent.club/a2a",
    "version": "1.0.0",
    "capabilities": {
        "streaming": False,
    },
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "preferredTransport": "JSONRPC",
    "skills": [
        {
            "id": "answer-questions",
            "name": "Answer Questions",
            "description": "Answer customer questions about products and services",
            "tags": [],
        },
        {
            "id": "order-lookup",
            "name": "Order Lookup",
            "description": "Look up order status and details",
            "tags": [],
        },
    ],
}


def run_demo(region: str = "us-east-1", registry_id: str | None = None):
    """Run the full end-to-end demo."""

    print("\n" + "=" * 70)
    print("  AWS Agent Registry + ANS Integration Demo")
    print("=" * 70)

    # ── Step 1: Create or use existing registry ──
    print("\n📋 Step 1: Registry setup")
    if registry_id:
        print(f"  Using existing registry: {registry_id}")
    else:
        try:
            registry_id = registry_client.create_registry(
                name="ans-integration-demo",
                description="Demo registry for AWS Agent Registry + ANS integration. Auto-approval enabled.",
                region=region,
            )
            print(f"  ✅ Created registry: {registry_id}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConflictException":
                print("  ⚠️  Registry already exists, finding it...")
                import boto3

                cp = boto3.client("bedrock-agentcore-control", region_name=region)
                registries = cp.list_registries().get("registries", [])
                for reg in registries:
                    if (
                        reg.get("name") == "ans-integration-demo"
                        and reg.get("status") == "READY"
                    ):
                        registry_id = reg["registryId"]
                        break
                if not registry_id:
                    raise RuntimeError("Could not find existing registry") from e
                print(f"  ✅ Found existing registry: {registry_id}")
            else:
                raise

    # ── Step 2: Fetch ANS metadata ──
    print("\n🔍 Step 2: Fetching ANS metadata for live GoDaddy agent")
    print(f"  ANS Name: {LIVE_ANS_NAME}")
    ans_meta = fetch_ans_metadata(LIVE_ANS_NAME)
    print(f"  Status:        {ans_meta['status']}")
    print(f"  Host:          {ans_meta['host']}")
    print(
        f"  Badge URL:     {ans_meta['badgeUrl'][:80]}..."
        if ans_meta["badgeUrl"]
        else "  Badge URL:     (none)"
    )
    print(f"  Trust Profile: {ans_meta['trustProfile']}")
    print(f"  Trust Composite: {ans_meta['trustComposite']}")
    tv = ans_meta["trustVector"]
    print(
        f"  Trust Vector:  I={tv['integrity']} Id={tv['identity']} S={tv['solvency']} B={tv['behavior']} Sa={tv['safety']}"
    )

    # ── Step 3: Build agent card with ANS extension ──
    print("\n🔧 Step 3: Building A2A agent card with ANS extension")
    agent_card_json = registry_client.build_agent_card_with_ans(
        BASE_AGENT_CARD, ans_meta
    )
    card_parsed = json.loads(agent_card_json)
    ext_count = len(card_parsed.get("capabilities", {}).get("extensions", []))
    print(f"  Agent card built with {ext_count} extension(s)")

    # ── Step 4: Create record ──
    print("\n📝 Step 4: Creating A2A record in AWS Agent Registry")
    record_id = None
    try:
        record_id = registry_client.create_agent_record(
            registry_id=registry_id,
            name="godaddy-support-agent-ans",
            description="GoDaddy customer support agent with ANS public identity and trust verification",
            agent_card_with_ans_extension=agent_card_json,
            version="1.0.0",
            region=region,
        )
        print(f"  ✅ Created record: {record_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            print("  ⚠️  Record already exists, finding it...")
            records = registry_client.list_records(registry_id, region=region)
            for rec in records:
                if rec.get("name") == "godaddy-support-agent-ans":
                    record_id = rec.get("recordId", rec.get("registryRecordId", ""))
                    break
            if not record_id:
                raise RuntimeError("Could not find existing record") from e
            print(f"  ✅ Found existing record: {record_id}")
        else:
            raise

    # ── Step 5: Check DRAFT status ──
    print("\n⏳ Step 5: Checking record status")
    time.sleep(3)
    record = registry_client.get_record(registry_id, record_id, region=region)
    status = record.get("status", "UNKNOWN")
    print(f"  Record status: {status}")

    # ── Step 6: Submit for approval ──
    if status == "DRAFT":
        print("\n📤 Step 6: Submitting for approval")
        registry_client.submit_for_approval(registry_id, record_id, region=region)
        print("  ✅ Submitted for approval")
        time.sleep(3)
    else:
        print(f"\n📤 Step 6: Record is {status}, skipping submit")

    # ── Step 7: Approve the record ──
    record = registry_client.get_record(registry_id, record_id, region=region)
    status = record.get("status", "UNKNOWN")
    if status == "PENDING_APPROVAL":
        print("\n✅ Step 7: Approving the record")
        registry_client.approve_record(
            registry_id,
            record_id,
            reason="Approved: ANS metadata verified, trust vector computed",
            region=region,
        )
        print("  ✅ Record approved")
        time.sleep(3)
    elif status == "APPROVED":
        print("\n✅ Step 7: Record already APPROVED")
    else:
        print(f"\n⚠️  Step 7: Record is {status}, cannot approve")

    # ── Step 8: Search for it ──
    print("\n🔍 Step 8: Searching for the agent")
    print("  ⏳ Waiting 15s for search index propagation...")
    time.sleep(15)
    try:
        results = registry_client.search_records(
            registry_id, "customer support agent", region=region
        )
        if results:
            print(f"  ✅ Found {len(results)} result(s):")
            for r in results:
                print(
                    f"     - [{r.get('descriptorType', 'N/A')}] {r.get('name', 'unknown')}"
                )
        else:
            print("  ⏳ No results yet (index may still be propagating)")
    except Exception as e:
        print(f"  ⚠️  Search error: {e}")

    # ── Step 9: Print full record with ANS metadata ──
    print("\n📄 Step 9: Full record with ANS metadata")
    print("-" * 70)
    final_record = registry_client.get_record(registry_id, record_id, region=region)
    # Print key fields
    print(f"  Name:            {final_record.get('name')}")
    print(f"  Status:          {final_record.get('status')}")
    print(f"  Descriptor Type: {final_record.get('descriptorType')}")
    print(f"  Version:         {final_record.get('recordVersion')}")

    # Extract and print ANS extension
    try:
        card_raw = final_record["descriptors"]["a2a"]["agentCard"]["inlineContent"]
        card = json.loads(card_raw)
        for ext in card.get("capabilities", {}).get("extensions", []):
            if ext.get("uri") == "https://ans-protocol.org/ext/ans-identity/v1":
                print("\n  ANS Extension:")
                print(json.dumps(ext, indent=4))
                break
    except Exception:
        print("  (Could not extract ANS extension from record)")

    print("\n" + "=" * 70)
    print("  Demo complete!")
    print(f"  Registry ID: {registry_id}")
    print(f"  Record ID:   {record_id}")
    print("=" * 70 + "\n")

    return registry_id, record_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AWS Agent Registry + ANS Integration Demo"
    )
    parser.add_argument(
        "--region", default="us-east-1", help="AWS region (default: us-east-1)"
    )
    parser.add_argument(
        "--registry-id",
        default=None,
        help="Use existing registry ID instead of creating one",
    )
    args = parser.parse_args()

    run_demo(region=args.region, registry_id=args.registry_id)
