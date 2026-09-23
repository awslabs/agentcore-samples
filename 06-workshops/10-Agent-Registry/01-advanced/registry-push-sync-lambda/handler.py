"""
Lambda triggered by EventBridge UpdateAgentRuntime CloudTrail events.

Flow:
  1. CloudTrail event arrives via EventBridge (same-account or cross-account)
  2. Extract runtime ARN → build MCP URL → identify source account
  3. Get OAuth token via AgentCore Identity credential provider
  4. Call MCP server: initialize → tools/list
  5. Find matching AWS Agent Registry record by runtime ARN in server schema
  6. Compare MCP tools with registry tools — update only if changed

Supports multi-account: credential providers are looked up by account ID.

Env vars per account (replace {ACCT} with the 12-digit account ID):
  CREDENTIAL_PROVIDER_{ACCT} — AgentCore Identity OAuth2 credential provider name
  CREDENTIAL_SCOPE_{ACCT}    — OAuth scope for the MCP server (optional)

Global env vars:
  REGISTRY_ID             — Registry ID to search and update records in
  WORKLOAD_IDENTITY_NAME  — AgentCore workload identity name for this Lambda
"""

import json
import os
import urllib.parse
import requests
import boto3


def get_bearer_token(account_id=None):
    """Get OAuth bearer token via AgentCore Identity credential provider.

    Two-step process:
      1. Get a workload access token from AgentCore Identity (identifies this Lambda)
      2. Use it to fetch an OAuth token from the credential provider (M2M flow)

    The credential provider stores the Cognito/OAuth config securely in AgentCore
    Identity, so no client secrets are needed in Lambda env vars.
    """
    acct = account_id or ""
    provider_name = os.environ.get(f"CREDENTIAL_PROVIDER_{acct}") or os.environ.get("CREDENTIAL_PROVIDER", "")
    scope_str = os.environ.get(f"CREDENTIAL_SCOPE_{acct}") or os.environ.get("CREDENTIAL_SCOPE", "")
    scopes = [s.strip() for s in scope_str.split(",") if s.strip()] if scope_str else []
    workload_name = os.environ.get("WORKLOAD_IDENTITY_NAME", "")

    if not provider_name:
        raise ValueError(f"No CREDENTIAL_PROVIDER configured for account {acct}")
    if not workload_name:
        raise ValueError("WORKLOAD_IDENTITY_NAME env var not set")

    region = os.environ.get("AWS_REGION", "us-west-2")
    client = boto3.client("bedrock-agentcore", region_name=region)

    # Step 1: Get workload access token (identifies this Lambda as a trusted workload)
    wat_response = client.get_workload_access_token(
        workloadName=workload_name,
    )
    print(f"Workload access token response keys: {list(wat_response.keys())}")
    workload_token = wat_response.get("workloadAccessToken") or wat_response.get("accessToken", "")
    if not workload_token:
        raise ValueError(f"No access token in workload response: {list(wat_response.keys())}")

    # Step 2: Use workload token to get OAuth token from the credential provider
    response = client.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=provider_name,
        oauth2Flow="M2M",
        scopes=scopes,
    )
    return response["accessToken"]


def _parse_sse_json(body):
    """Extract JSON from an SSE or plain JSON response body."""
    text = body if isinstance(body, str) else body.decode("utf-8")
    text = text.strip()
    # If it's plain JSON, parse directly
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    # SSE format: lines like "event: message\ndata: {...}\n\n"
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise ValueError(f"Could not parse response: {text[:200]}")


def _mcp_headers(token, session_id=None):
    """Build HTTP headers for MCP JSON-RPC requests (streamable-http transport)."""
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    if session_id:
        h["Mcp-Session-Id"] = session_id
    return h


def call_tools_list(mcp_url, token):
    """Call the MCP server's initialize + tools/list methods and return the result.

    MCP streamable-http requires initialize before tools/list.
    The session_id from initialize is passed to tools/list if the server uses sessions.
    """
    # Validate URL scheme to prevent file:// or custom scheme access
    if not mcp_url.startswith("https://"):
        raise ValueError(f"Only HTTPS URLs are allowed, got: {mcp_url[:50]}")

    # Step 1: Initialize MCP session
    init_payload = {
        "jsonrpc": "2.0",
        "id": "init-1",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "tool-sync-lambda", "version": "1.0.0"},
        },
    }

    init_resp = requests.post(
        mcp_url,
        json=init_payload,
        headers=_mcp_headers(token),
        timeout=30,
    )
    init_resp.raise_for_status()
    session_id = init_resp.headers.get("Mcp-Session-Id")
    init_result = _parse_sse_json(init_resp.text)  # noqa: F841 — parsed to validate response
    print(f"MCP session initialized, session_id={session_id}")

    # Step 2: Call tools/list
    list_payload = {
        "jsonrpc": "2.0",
        "id": "list-1",
        "method": "tools/list",
        "params": {},
    }

    list_resp = requests.post(
        mcp_url,
        json=list_payload,
        headers=_mcp_headers(token, session_id),
        timeout=30,
    )
    list_resp.raise_for_status()
    return _parse_sse_json(list_resp.text)


def _extract_mcp_url(event):
    """Extract MCP URL and account ID from a CloudTrail UpdateAgentRuntime event.

    Returns:
        (mcp_url, account_id) tuple. Both None if extraction fails.
    """
    detail = event.get("detail", {})
    runtime_arn = detail.get("responseElements", {}).get("agentRuntimeArn", "")
    if not runtime_arn:
        return None, None

    # Extract account ID from ARN: arn:aws:bedrock-agentcore:region:ACCOUNT:runtime/id
    arn_parts = runtime_arn.split(":")
    account_id = arn_parts[4] if len(arn_parts) > 4 else None

    region = detail.get("awsRegion", "us-west-2")
    encoded_arn = runtime_arn.replace(":", "%3A").replace("/", "%2F")
    mcp_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
    return mcp_url, account_id


def _find_record_by_mcp_url(client, registry_id, mcp_url):
    """Search registry records to find one whose server schema contains the runtime ARN.

    Tries three matching strategies:
      1. Decoded ARN (e.g. arn:aws:bedrock-agentcore:...:runtime/TrimMCP-xxx)
      2. URL-encoded ARN (e.g. arn%3Aaws%3Abedrock-agentcore%3A...%2FTrimMCP-xxx)
      3. Full MCP URL match

    Returns:
        (record_id, full_record) tuple. Both None if no match found.
    """
    # Extract the runtime ARN from the MCP URL for matching
    # URL format: .../runtimes/arn%3A...%2Fruntime%2F<id>/invocations...
    # Decode to get: arn:aws:bedrock-agentcore:region:account:runtime/id
    try:
        decoded_url = urllib.parse.unquote(mcp_url)
        # Extract just the runtime path: arn:aws:bedrock-agentcore:...:runtime/xxx
        runtime_marker = "/runtimes/"
        idx = decoded_url.find(runtime_marker)
        if idx >= 0:
            runtime_arn = decoded_url[idx + len(runtime_marker) :].split("/invocations")[0]
        else:
            runtime_arn = None
    except Exception:
        runtime_arn = None

    records = client.list_registry_records(registryId=registry_id)
    record_list = records.get("registryRecords", [])
    print(f"Found {len(record_list)} registry records")
    for rec in record_list:
        record_id = rec.get("registryRecordId") or rec.get("recordId") or rec.get("id", "")
        record_name = rec.get("name", "?")
        record_status = rec.get("status", "?")
        print(f"  Record: {record_id} | {record_name} | status={record_status} | keys={list(rec.keys())}")
        if not record_id:
            print(f"  Warning: could not get record ID from: {list(rec.keys())}")
            continue
        if record_status == "DRAFT":
            print(f"  Skipping DRAFT record {record_id} ({record_name}) — must be APPROVED first")
            continue
        try:
            full = client.get_registry_record(
                registryId=registry_id,
                recordId=record_id,
            )
            descriptors = full.get("descriptors", {})
            mcp_desc = descriptors.get("mcp", {})
            server_schema = mcp_desc.get("server", {})
            inline = server_schema.get("inlineContent", "")

            # Match on runtime ARN (decoded or encoded) in the server schema
            if runtime_arn and runtime_arn in inline:
                print(f"Found matching record (by ARN): {record_id} ({rec.get('name', '?')})")
                return record_id, full
            # Also check URL-encoded ARN
            encoded_arn = runtime_arn.replace(":", "%3A").replace("/", "%2F") if runtime_arn else None
            if encoded_arn and encoded_arn in inline:
                print(f"Found matching record (by encoded ARN): {record_id} ({rec.get('name', '?')})")
                return record_id, full
            if mcp_url in inline or urllib.parse.unquote(mcp_url).rstrip("?qualifier=DEFAULT") in inline:
                print(f"Found matching record (by URL): {record_id} ({rec.get('name', '?')})")
                return record_id, full
        except Exception as e:
            print(f"Error checking record {record_id}: {e}")
            continue

    print(f"No matching record found among {len(record_list)} records.")
    print(f"  Looking for runtime ARN: {runtime_arn}")
    print(f"  Looking for MCP URL: {mcp_url}")
    return None, None


def _get_registry_client():
    """Create a boto3 client for the AWS Agent Registry control plane.

    Uses the bedrock-agentcore-control service model included in boto3 >= 1.42.87.
    """
    region = os.environ.get("AWS_REGION", "us-west-2")
    return boto3.client("bedrock-agentcore-control", region_name=region)


def _normalize_tools(tools):
    """Normalize tool list for comparison — extract name, description, inputSchema."""
    normalized = []
    for t in sorted(tools, key=lambda x: x.get("name", "")):
        normalized.append(
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {}),
            }
        )
    return normalized


def _get_registry_tools_from_record(full_record):
    """Get the current tools from an AWS Agent Registry record's tools definition."""
    descriptors = full_record.get("descriptors", {})
    mcp_desc = descriptors.get("mcp", {})
    tools_def = mcp_desc.get("tools", {})
    inline = tools_def.get("inlineContent", "")
    if not inline:
        return []
    try:
        return json.loads(inline).get("tools", [])
    except (json.JSONDecodeError, TypeError):
        return []


def sync_registry_if_changed(mcp_tools, mcp_url):
    """Compare MCP server tools with AWS Agent Registry record tools. Update only if different.

    Steps:
      1. Find the AWS Agent Registry record matching this MCP server's URL
      2. Extract existing tools from the record's tools.inlineContent
      3. Normalize both tool lists (sort by name, compare name/description/inputSchema)
      4. If identical → skip update
      5. If different → log the diff and update the registry record

    Returns:
        dict with 'action' key: 'no_change', 'updated', or 'skipped'
    """
    registry_id = os.environ["REGISTRY_ID"]
    client = _get_registry_client()

    # Find the matching record
    record_id, full_record = _find_record_by_mcp_url(client, registry_id, mcp_url)
    if not record_id:
        print(f"No matching registry record found for {mcp_url}")
        return {"action": "skipped", "reason": "no matching record"}

    # Get current tools from registry
    registry_tools = _get_registry_tools_from_record(full_record)

    # Compare normalized tool lists
    mcp_normalized = _normalize_tools(mcp_tools)
    registry_normalized = _normalize_tools(registry_tools)

    if mcp_normalized == registry_normalized:
        print(f"No change detected. Registry record {record_id} is up to date ({len(registry_tools)} tools).")
        return {
            "action": "no_change",
            "record_id": record_id,
            "tool_count": len(registry_tools),
        }

    # Tools differ — update the registry
    print(f"Change detected! Registry has {len(registry_tools)} tools, MCP server has {len(mcp_tools)} tools.")

    # Log the diff
    mcp_names = {t["name"] for t in mcp_normalized}
    reg_names = {t["name"] for t in registry_normalized}
    added = mcp_names - reg_names
    removed = reg_names - mcp_names
    if added:
        print(f"  Added: {added}")
    if removed:
        print(f"  Removed: {removed}")
    if not added and not removed:
        print("  Tool definitions changed (same names, different schemas/descriptions)")

    tool_schema_content = json.dumps({"tools": mcp_tools})
    client.update_registry_record(
        registryId=registry_id,
        recordId=record_id,
        descriptors={
            "optionalValue": {
                "mcp": {
                    "optionalValue": {
                        "tools": {
                            "optionalValue": {
                                "protocolVersion": "2025-06-18",
                                "inlineContent": tool_schema_content,
                            }
                        }
                    }
                }
            }
        },
    )
    print(f"Updated registry record {record_id} in registry {registry_id}")
    return {
        "action": "updated",
        "record_id": record_id,
        "old_count": len(registry_tools),
        "new_count": len(mcp_tools),
    }


def handler(event, context):
    """Lambda entry point. Triggered by EventBridge on UpdateAgentRuntime events."""
    mcp_url, account_id = _extract_mcp_url(event)
    if not mcp_url:
        print(f"Could not extract mcp_url from event: {json.dumps(event, default=str)[:500]}")
        return {"statusCode": 400, "body": "Could not extract mcp_url"}

    print(f"Received event for MCP server: {mcp_url} (account: {account_id})")
    token = get_bearer_token(account_id)
    result = call_tools_list(mcp_url, token)

    tools = result.get("result", {}).get("tools", [])
    print(f"Found {len(tools)} tools from MCP server:")
    for t in tools:
        print(f"  - {t['name']}: {t.get('description', '')}")

    # Compare with registry and update only if changed
    sync_result = sync_registry_if_changed(tools, mcp_url)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "mcp_url": mcp_url,
                "tool_count": len(tools),
                "tools": [t["name"] for t in tools],
                "sync": sync_result,
            },
            default=str,
        ),
    }
