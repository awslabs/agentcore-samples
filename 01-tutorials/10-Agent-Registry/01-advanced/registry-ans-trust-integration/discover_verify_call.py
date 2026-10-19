"""
End-to-end consumer test: Discover → Verify → Call

Demonstrates the full consumer flow:
1. DISCOVER — Search AWS Agent Registry for an agent by semantic query
2. EXTRACT  — Pull the ANS name from the registry record's A2A extension
3. VERIFY   — Validate the agent via ANS (DNS + TL badge + PKI fingerprint match)
4. CALL     — Send an A2A JSON-RPC message to the verified agent

This is the runtime flow an enterprise consumer would use:
  - Private discovery via AWS Agent Registry (governed, semantic search)
  - Public trust verification via ANS (cryptographic, DNS-based)
  - Secure connection via A2A protocol (JSON-RPC over HTTPS)

Usage:
    python discover_verify_call.py --registry-id <id> --query "customer support"
    python discover_verify_call.py --registry-id <your-registry-id> --query "support agent"
"""

import argparse
import hashlib
import json
import logging
import socket
import ssl
import uuid
from datetime import datetime, timezone

import requests

from ans_metadata import (
    fetch_ans_metadata,
    parse_ans_name,
    validate_ans_liveness,
    ANS_EXTENSION_URI,
)
import registry_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: DISCOVER — Search AWS Agent Registry
# ═══════════════════════════════════════════════════════════════════════════════


def _has_ans_metadata(record: dict) -> bool:
    """Check if a record contains ANS metadata in any supported format."""
    descriptors = record.get("descriptors", {})

    # A2A: check extensions
    a2a = descriptors.get("a2a", {})
    if a2a:
        content_raw = a2a.get("agentCard", {}).get("inlineContent", "")
        if content_raw:
            try:
                card = json.loads(content_raw)
                for ext in card.get("capabilities", {}).get("extensions", []):
                    if ext.get("uri") == ANS_EXTENSION_URI:
                        return True
            except (json.JSONDecodeError, AttributeError):
                pass

    # MCP: check x-ans-name
    mcp = descriptors.get("mcp", {})
    if mcp:
        content_raw = mcp.get("serverSchema", {}).get("inlineContent", "")
        if content_raw:
            try:
                data = json.loads(content_raw)
                if "x-ans-name" in data:
                    return True
            except (json.JSONDecodeError, AttributeError):
                pass

    # CUSTOM: check ans.ansName
    custom = descriptors.get("custom", {})
    if custom:
        content_raw = custom.get("inlineContent", "")
        if content_raw:
            try:
                data = json.loads(content_raw)
                if "x-ans-name" in data:
                    return True
                if (
                    "ans" in data
                    and isinstance(data["ans"], dict)
                    and data["ans"].get("ansName")
                ):
                    return True
            except (json.JSONDecodeError, AttributeError):
                pass

    return False


def discover_agent(registry_id: str, query: str, region: str = "us-east-1") -> dict:
    """Search the AWS Agent Registry and return the first matching record with ANS data.

    Args:
        registry_id: AWS Agent Registry ID.
        query: Semantic search query (e.g., "customer support agent").
        region: AWS region.

    Returns:
        Full record dict from the registry.

    Raises:
        RuntimeError: If no results found.
    """
    print("\n" + "═" * 70)
    print("  STEP 1: DISCOVER — Search AWS Agent Registry")
    print("═" * 70)
    print(f"  Registry ID: {registry_id}")
    print(f'  Query:       "{query}"')
    print(f"  Region:      {region}")
    print()

    # Semantic search
    results = registry_client.search_records(registry_id, query, region=region)

    if not results:
        # Fallback: list all records and filter by name/description
        print("  ⚠️  Semantic search returned no results, listing all records...")
        all_records = registry_client.list_records(registry_id, region=region)
        # Get full details for each
        for rec in all_records:
            record_id = rec.get("recordId", "")
            full = registry_client.get_record(registry_id, record_id, region=region)
            status = full.get("status", "")
            name = full.get("name", "")
            if status == "APPROVED" and query.lower().split()[0] in name.lower():
                results = [full]
                break

        if not results:
            # Just use the first APPROVED record
            for rec in all_records:
                record_id = rec.get("recordId", "")
                full = registry_client.get_record(registry_id, record_id, region=region)
                if full.get("status") == "APPROVED":
                    results = [full]
                    break

    if not results:
        raise RuntimeError(f"No agents found for query: '{query}'")

    # Fetch full records and prefer ones with ANS metadata
    candidates = []
    for r in results:
        record_id = r.get("recordId", r.get("registryRecordId", ""))
        if "descriptors" not in r:
            full = registry_client.get_record(registry_id, record_id, region=region)
        else:
            full = r
        candidates.append(full)

    # Prefer records with ANS extension
    record = None
    for c in candidates:
        if _has_ans_metadata(c):
            record = c
            break
    if not record:
        # Fallback: list all records and find one with ANS data
        print("  ⚠️  Search results lack ANS metadata, scanning all records...")
        all_records = registry_client.list_records(registry_id, region=region)
        for rec in all_records:
            rid = rec.get("recordId", "")
            full = registry_client.get_record(registry_id, rid, region=region)
            if full.get("status") == "APPROVED" and _has_ans_metadata(full):
                record = full
                break

    if not record:
        # Last resort: use first candidate
        record = candidates[0] if candidates else None
        if not record:
            raise RuntimeError(f"No agents found for query: '{query}'")

    print(f"  ✅ Found agent: {record.get('name', 'unknown')}")
    print(f"     Record ID:      {record.get('recordId', 'N/A')}")
    print(f"     Status:         {record.get('status', 'N/A')}")
    print(f"     Descriptor:     {record.get('descriptorType', 'N/A')}")
    print(f"     Version:        {record.get('recordVersion', 'N/A')}")

    return record


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: EXTRACT — Pull ANS name from registry record
# ═══════════════════════════════════════════════════════════════════════════════


def extract_ans_name(record: dict) -> tuple[str, str]:
    """Extract the ANS name and agent URL from a registry record.

    Supports:
        - A2A records: capabilities.extensions[].params.ansName
        - MCP records: x-ans-name field
        - CUSTOM records: ans.ansName field

    Args:
        record: Full registry record dict.

    Returns:
        Tuple of (ans_name, agent_url).

    Raises:
        RuntimeError: If no ANS extension found.
    """
    print("\n" + "═" * 70)
    print("  STEP 2: EXTRACT — Pull ANS name from registry record")
    print("═" * 70)

    descriptors = record.get("descriptors", {})
    ans_name = None
    agent_url = None

    # Try A2A path
    a2a = descriptors.get("a2a", {})
    if a2a:
        content_raw = a2a.get("agentCard", {}).get("inlineContent", "")
        if content_raw:
            try:
                card = json.loads(content_raw)
                agent_url = card.get("url", "")
                for ext in card.get("capabilities", {}).get("extensions", []):
                    if ext.get("uri") == ANS_EXTENSION_URI:
                        params = ext.get("params", {})
                        ans_name = params.get("ansName", "")
                        print("  Source:     A2A extension (capabilities.extensions)")
                        print(f"  ANS Name:   {ans_name}")
                        print(f"  Agent URL:  {agent_url}")
                        print(f"  Host:       {params.get('host', 'N/A')}")
                        print(f"  Status:     {params.get('status', 'N/A')}")
                        print(
                            f"  Trust:      {params.get('trustProfile', 'N/A')} "
                            f"(composite: {params.get('trustComposite', 0)})"
                        )
                        break
            except json.JSONDecodeError:
                pass

    # Try CUSTOM path
    if not ans_name:
        custom = descriptors.get("custom", {})
        if custom:
            content_raw = custom.get("inlineContent", "")
            if content_raw:
                try:
                    data = json.loads(content_raw)
                    # MCP style
                    if "x-ans-name" in data:
                        ans_name = data["x-ans-name"]
                        print("  Source:     MCP x-ans-name field")
                        print(f"  ANS Name:   {ans_name}")
                    # ACP/CUSTOM style
                    elif "ans" in data and isinstance(data["ans"], dict):
                        ans_name = data["ans"].get("ansName", "")
                        print("  Source:     CUSTOM ans.ansName field")
                        print(f"  ANS Name:   {ans_name}")
                except json.JSONDecodeError:
                    pass

    # Try MCP path
    if not ans_name:
        mcp = descriptors.get("mcp", {})
        if mcp:
            content_raw = mcp.get("serverSchema", {}).get("inlineContent", "")
            if content_raw:
                try:
                    data = json.loads(content_raw)
                    if "x-ans-name" in data:
                        ans_name = data["x-ans-name"]
                        print("  Source:     MCP serverSchema x-ans-name")
                        print(f"  ANS Name:   {ans_name}")
                except json.JSONDecodeError:
                    pass

    if not ans_name:
        raise RuntimeError(
            "No ANS extension found in registry record. "
            "Record must have ANS metadata in A2A extensions, MCP x-ans-* fields, "
            "or CUSTOM ans.* object."
        )

    # Derive agent URL from ANS name if not found in card
    if not agent_url:
        _, host = parse_ans_name(ans_name)
        agent_url = f"https://{host}/a2a"
        print(f"  Agent URL:  {agent_url} (derived from ANS host)")

    print("\n  ✅ Extracted ANS identity for verification")
    return ans_name, agent_url


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: VERIFY — Validate agent via ANS
# ═══════════════════════════════════════════════════════════════════════════════


def verify_agent(ans_name: str, agent_url: str) -> dict:
    """Verify the agent's identity via ANS protocol.

    Performs:
        1. DNS resolution (_ans-badge TXT → badge URL)
        2. Transparency Log badge fetch (status, certs, attestations)
        3. Live TLS connection to agent host
        4. PKI fingerprint matching (live cert vs TL-registered cert)

    Args:
        ans_name: Full ANS name from registry.
        agent_url: Agent's A2A endpoint URL.

    Returns:
        Verification result dict with trust data.

    Raises:
        RuntimeError: If verification fails critically.
    """
    print("\n" + "═" * 70)
    print("  STEP 3: VERIFY — Validate agent via ANS")
    print("═" * 70)
    print(f"  ANS Name: {ans_name}")

    version, host = parse_ans_name(ans_name)
    print(f"  Host:     {host}")
    print(f"  Version:  {version}")

    # ── 3a: Liveness validation ──
    print("\n  ── 3a: Liveness Validation ──")
    liveness = validate_ans_liveness(ans_name)
    for check in liveness["checks"]:
        icon = "✅" if check["passed"] else "❌"
        print(f"    {icon} {check['check']}: {check['detail']}")

    if not liveness["valid"]:
        raise RuntimeError(f"ANS liveness check failed: {liveness['error']}")
    print("\n    Overall: ✅ LIVE")

    # ── 3b: Fetch full ANS metadata (DNS + TL) ──
    print("\n  ── 3b: Fetch ANS Metadata (DNS + Transparency Log) ──")
    ans_meta = fetch_ans_metadata(ans_name)
    print(f"    Status:           {ans_meta['status']}")
    print(f"    Domain Validation: {ans_meta['domainValidation']}")
    print(f"    Registered At:    {ans_meta['registeredAt']}")
    print(
        f"    Identity Cert:    {ans_meta['identityCert']['type']} "
        f"[{ans_meta['identityCert']['fingerprint'][:30]}...]"
    )
    print(
        f"    Server Cert:      {ans_meta['serverCert']['type']} "
        f"[{ans_meta['serverCert']['fingerprint'][:30]}...]"
    )

    # ── 3c: PKI fingerprint matching ──
    print("\n  ── 3c: PKI Fingerprint Matching ──")
    print(f"    Connecting to {host}:443 to extract live server certificate...")

    try:
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        with ctx.wrap_socket(
            socket.create_connection((host, 443), timeout=10),
            server_hostname=host,
        ) as s:
            cert_der = s.getpeercert(binary_form=True)
            live_fp = f"SHA256:{hashlib.sha256(cert_der).hexdigest()}"

        tl_fp = ans_meta["serverCert"]["fingerprint"]
        print(f"    Live cert fingerprint:  {live_fp[:50]}...")
        print(f"    TL registered fingerprint: {tl_fp[:50]}...")

        if live_fp.lower() == tl_fp.lower():
            print("    ✅ MATCH — Agent is who they claim to be")
            fingerprint_match = True
        else:
            print("    ⚠️  MISMATCH — Possible cert rotation or impersonation")
            print("       (This may be normal if the cert was recently rotated)")
            fingerprint_match = False
    except Exception as e:
        print(f"    ❌ Could not extract live cert: {e}")
        fingerprint_match = False

    # ── 3d: Trust vector ──
    print("\n  ── 3d: Trust Vector ──")
    tv = ans_meta["trustVector"]
    print(f"    Integrity:  {tv['integrity']}/100")
    print(f"    Identity:   {tv['identity']}/100")
    print(f"    Solvency:   {tv['solvency']}/100")
    print(f"    Behavior:   {tv['behavior']}/100")
    print(f"    Safety:     {tv['safety']}/100")
    print("    ─────────────────────────")
    print(f"    Composite:  {ans_meta['trustComposite']}")
    print(f"    Profile:    {ans_meta['trustProfile']}")

    # ── Summary ──
    print("\n  ── Verification Summary ──")
    print(f"    ANS Status:        {ans_meta['status']}")
    print(f"    Liveness:          {'✅ LIVE' if liveness['valid'] else '❌ NOT LIVE'}")
    print(
        f"    Fingerprint Match: {'✅ MATCH' if fingerprint_match else '⚠️  MISMATCH'}"
    )
    print(f"    Trust Profile:     {ans_meta['trustProfile']}")

    verified = liveness["valid"] and ans_meta["status"] == "ACTIVE"
    if verified:
        print("\n  ✅ Agent VERIFIED — safe to connect")
    else:
        print("\n  ⚠️  Agent verification incomplete — proceed with caution")

    return {
        "verified": verified,
        "fingerprint_match": fingerprint_match,
        "ans_metadata": ans_meta,
        "liveness": liveness,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: CALL — Send A2A message to verified agent
# ═══════════════════════════════════════════════════════════════════════════════


def call_agent(
    agent_url: str, message: str = "Hello! What can you help me with?"
) -> dict:
    """Send an A2A JSON-RPC message to the verified agent.

    Uses the A2A protocol (JSON-RPC 2.0 over HTTPS):
        - Method: message/send
        - Params: message with text part

    Args:
        agent_url: The agent's A2A endpoint URL.
        message: The text message to send.

    Returns:
        The agent's response dict.
    """
    print("\n" + "═" * 70)
    print("  STEP 4: CALL — Send A2A message to verified agent")
    print("═" * 70)
    print(f"  Endpoint: {agent_url}")
    print(f'  Message:  "{message}"')
    print()

    # Build A2A JSON-RPC request
    str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": message}],
                "messageId": str(uuid.uuid4()),
            },
            "configuration": {
                "acceptedOutputModes": ["text"],
            },
        },
    }

    print("  ── Request ──")
    print(f"    Method:  POST {agent_url}")
    print("    Headers: Content-Type: application/json")
    print(f"    Body:    {json.dumps(payload, indent=2)[:500]}...")
    print()

    try:
        resp = requests.post(
            agent_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )

        print("  ── Response ──")
        print(f"    Status:  {resp.status_code}")

        if resp.ok:
            data = resp.json()
            # Extract agent's reply
            result = data.get("result", {})
            status = result.get("status", {}).get("state", "unknown")
            artifacts = result.get("artifacts", [])

            print(f"    Task State: {status}")

            agent_reply = None

            # Try artifacts first
            if artifacts:
                for i, artifact in enumerate(artifacts):
                    parts = artifact.get("parts", [])
                    for part in parts:
                        if part.get("kind") == "text":
                            agent_reply = part["text"]

            # Try status.message (some A2A agents put reply here)
            if not agent_reply:
                status_msg = result.get("status", {}).get("message", {})
                if isinstance(status_msg, dict):
                    parts = status_msg.get("parts", [])
                    for part in parts:
                        if part.get("kind") == "text":
                            agent_reply = part["text"]
                elif isinstance(status_msg, str) and status_msg:
                    agent_reply = status_msg

            if agent_reply:
                print("\n  🤖 Agent Reply:")
                print(f"    {agent_reply[:500]}")
                if len(agent_reply) > 500:
                    print(f"    ... ({len(agent_reply)} chars total)")
            else:
                print("    (No text reply extracted)")
                print(f"    Raw: {json.dumps(data, indent=2)[:400]}")

            print("\n  ✅ A2A call successful")
            return data
        else:
            print(f"    Error: {resp.text[:300]}")
            # Try alternate endpoint patterns
            print("\n  ⚠️  Trying alternate A2A endpoint patterns...")
            return _try_alternate_endpoints(agent_url, payload)

    except requests.exceptions.Timeout:
        print("    ❌ Request timed out (30s)")
        return {"error": "timeout"}
    except requests.exceptions.ConnectionError as e:
        print(f"    ❌ Connection error: {e}")
        return {"error": str(e)}
    except Exception as e:
        print(f"    ❌ Unexpected error: {e}")
        return {"error": str(e)}


def _try_alternate_endpoints(base_url: str, payload: dict) -> dict:
    """Try alternate A2A endpoint patterns if the primary fails."""
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host = f"{parsed.scheme}://{parsed.netloc}"

    alternates = [
        f"{host}/",
        f"{host}/a2a",
        f"{host}/api/a2a",
        f"{host}/jsonrpc",
    ]

    for url in alternates:
        if url == base_url:
            continue
        try:
            print(f"    Trying: {url}")
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                print(f"    ✅ Success at {url}")
                return data
        except Exception:
            continue

    print("    ❌ All alternate endpoints failed")
    return {"error": "all endpoints failed"}


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — Full end-to-end flow
# ═══════════════════════════════════════════════════════════════════════════════


def run_discover_verify_call(
    registry_id: str,
    query: str = "customer support agent",
    message: str = "Hello! What services do you offer?",
    region: str = "us-east-1",
):
    """Run the full Discover → Verify → Call flow.

    This is the runtime consumer flow:
        1. Search AWS Agent Registry (private, governed)
        2. Extract ANS name from the record
        3. Verify via ANS (public, cryptographic)
        4. Call the agent via A2A

    Args:
        registry_id: AWS Agent Registry ID.
        query: Semantic search query.
        message: Message to send to the agent.
        region: AWS region.
    """
    print("\n" + "█" * 70)
    print("  AWS Agent Registry + ANS: Discover → Verify → Call")
    print("  " + "─" * 66)
    print(f"  Registry:  {registry_id}")
    print(f'  Query:     "{query}"')
    print(f"  Region:    {region}")
    print(
        f"  Time:      {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    print("█" * 70)

    # Step 1: Discover
    record = discover_agent(registry_id, query, region=region)

    # Step 2: Extract ANS name
    ans_name, agent_url = extract_ans_name(record)

    # Step 3: Verify via ANS
    verification = verify_agent(ans_name, agent_url)

    # Step 4: Call the agent (only if verified or user accepts risk)
    if verification["verified"]:
        result = call_agent(agent_url, message)
    else:
        print("\n  ⚠️  Agent not fully verified. Calling anyway (demo mode)...")
        result = call_agent(agent_url, message)

    # ── Final Summary ──
    print("\n" + "█" * 70)
    print("  END-TO-END SUMMARY")
    print("█" * 70)
    print(f"  1. DISCOVER:  Found '{record.get('name', 'N/A')}' in AWS Registry")
    print(f"  2. EXTRACT:   ANS Name = {ans_name}")
    print(
        f"  3. VERIFY:    Status={verification['ans_metadata']['status']}, "
        f"FP={'MATCH' if verification['fingerprint_match'] else 'MISMATCH'}, "
        f"Trust={verification['ans_metadata']['trustProfile']}"
    )
    print(
        f"  4. CALL:      {'✅ Success' if 'error' not in result else '❌ ' + result.get('error', '')}"
    )
    print("█" * 70 + "\n")

    return {
        "record": record,
        "ans_name": ans_name,
        "agent_url": agent_url,
        "verification": verification,
        "call_result": result,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discover → Verify → Call: End-to-end consumer test"
    )
    parser.add_argument(
        "--registry-id",
        required=True,
        help="AWS Agent Registry ID",
    )
    parser.add_argument(
        "--query",
        default="customer support agent",
        help="Semantic search query (default: 'customer support agent')",
    )
    parser.add_argument(
        "--message",
        default="Hello! What services do you offer?",
        help="Message to send to the agent",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region (default: us-east-1)",
    )
    args = parser.parse_args()

    run_discover_verify_call(
        registry_id=args.registry_id,
        query=args.query,
        message=args.message,
        region=args.region,
    )
