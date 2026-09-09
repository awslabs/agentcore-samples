#!/usr/bin/env python3
"""
Verify the OpenSearch MCP runtime deploy.

Thin orchestrator. The actual runtime deploy is owned by
deployment/4b-mcp-opensearch-server/deploy_runtime.py — notebook 05b runs
that script directly. This verifier:

  1. Confirms the SSM key /app/lakehouse-agent/opensearch-mcp-runtime-arn
     and -id are present (deploy_runtime.py wrote them).
  2. Calls bedrock-agentcore-control:GetAgentRuntime as a healthcheck and
     reports the runtime's READY status + protocol + authorizer summary.

This script does NOT redeploy the runtime — it is read-only on AWS state
(GetAgentRuntime + SSM read). Re-running is safe and idempotent.

Per design §10 reconciliation: notebook 05b's cell order is
  5b/01_deploy_opensearch_collection.py  (creates AOSS substrate)
  4b/deploy_runtime.py                   (deploys the runtime)
  5b/02_verify_opensearch_mcp.py         (this verifier)
  5b/03..05                              (oauth provider + OBO gateway + agent IAM)

Usage:
    python 02_verify_opensearch_mcp.py
"""

import sys

import boto3

SSM_PREFIX = "/app/lakehouse-agent/"
RUNTIME_ARN_KEY = f"{SSM_PREFIX}opensearch-mcp-runtime-arn"
RUNTIME_ID_KEY = f"{SSM_PREFIX}opensearch-mcp-runtime-id"


def get_ssm_param(ssm, name: str):
    """Read an SSM parameter; return value or None."""
    try:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None


def main():
    print("=" * 70)
    print("OpenSearch MCP Runtime Verifier")
    print("=" * 70)

    session = boto3.Session()
    region = session.region_name
    if not region:
        print("❌ Could not detect AWS region from boto3 session")
        sys.exit(1)

    ssm = boto3.client("ssm", region_name=region)
    bedrock = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"\n📋 Region: {region}")

    # Step 1: SSM keys present?
    print("\n🔎 Step 1: SSM key presence check")
    runtime_arn = get_ssm_param(ssm, RUNTIME_ARN_KEY)
    runtime_id = get_ssm_param(ssm, RUNTIME_ID_KEY)

    if not runtime_arn:
        print(f"❌ {RUNTIME_ARN_KEY} not found in SSM")
        print("   Run deployment/4b-mcp-opensearch-server/deploy_runtime.py first")
        sys.exit(1)
    print(f"   ✅ {RUNTIME_ARN_KEY} = {runtime_arn}")

    if not runtime_id:
        print(f"❌ {RUNTIME_ID_KEY} not found in SSM")
        print("   Run deployment/4b-mcp-opensearch-server/deploy_runtime.py first")
        sys.exit(1)
    print(f"   ✅ {RUNTIME_ID_KEY} = {runtime_id}")

    # Step 2: GetAgentRuntime healthcheck
    print("\n🩺 Step 2: GetAgentRuntime healthcheck")
    try:
        resp = bedrock.get_agent_runtime(agentRuntimeId=runtime_id)
    except bedrock.exceptions.ResourceNotFoundException:
        print(f"❌ Runtime {runtime_id} not found by AgentCore control plane")
        print("   The SSM key references a runtime that no longer exists.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ GetAgentRuntime failed: {e}")
        sys.exit(1)

    status = resp.get("status", "UNKNOWN")
    name = resp.get("agentRuntimeName", "")
    protocol = (resp.get("protocolConfiguration") or {}).get("serverProtocol", "UNKNOWN")

    auth_cfg = resp.get("authorizerConfiguration") or {}
    custom_jwt = auth_cfg.get("customJWTAuthorizer") or {}
    discovery_url = custom_jwt.get("discoveryUrl", "<not present>")
    allowed_aud = custom_jwt.get("allowedAudience") or []

    print(f"   Name:             {name}")
    print(f"   Status:           {status}")
    print(f"   Protocol:         {protocol}")
    print(f"   Discovery URL:    {discovery_url}")
    print(f"   Allowed audience: {allowed_aud}")

    # Note: requestHeaderConfiguration intentionally NOT printed here — the
    # field does not surface via GetAgentRuntime even when applied. The
    # data-path verification gate is the authoritative check, not
    # control-plane API output.

    if status != "READY":
        print(f"\n❌ Runtime is not READY (status = {status})")
        sys.exit(1)

    print("\n✅ Runtime is READY")
    print("\n📋 Next Steps:")
    print("   1. Issue a tools/call against the runtime to confirm it reads the")
    print("      forwarded Authorization header: look for")
    print("      '✅ Extracted user sub from Authorization header' in CloudWatch logs.")
    print("   2. Continue notebook 05b cells (03_create_oauth_provider, ...)")
    print("=" * 70)


if __name__ == "__main__":
    main()
