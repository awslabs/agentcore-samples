#!/usr/bin/env python3
"""
Update all lakehouse-agent runtimes from Cognito to Okta JWT auth.

This script updates the authorizerConfiguration for all three runtimes:
- lakehouse_mcp_server
- opensearch_mcp_server  
- lakehouse_agent

Prerequisites:
- AWS credentials configured
- SSM parameters for Okta configured (/app/lakehouse-agent/okta-*)
- idp-provider set to 'okta' in SSM

Usage:
    python update_runtimes_to_okta.py
"""

import json
import sys
import os

import boto3

# Add parent path for utils
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.idp_config import get_idp_provider


def get_ssm_parameter(ssm_client, name: str, required: bool = True) -> str:
    """Get parameter from SSM."""
    try:
        response = ssm_client.get_parameter(Name=name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except ssm_client.exceptions.ParameterNotFound:
        if required:
            print(f"❌ SSM parameter not found: {name}")
            sys.exit(1)
        return None


def get_runtime_ids(agentcore_client) -> dict:
    """Get runtime IDs for all lakehouse-agent runtimes."""
    runtimes = {}
    paginator = agentcore_client.get_paginator("list_agent_runtimes")
    
    target_names = ["lakehouse_mcp_server", "opensearch_mcp_server", "lakehouse_agent"]
    
    for page in paginator.paginate():
        for runtime in page.get("agentRuntimes", []):
            name = runtime.get("agentRuntimeName")
            if name in target_names:
                runtimes[name] = {
                    "id": runtime.get("agentRuntimeId"),
                    "arn": runtime.get("agentRuntimeArn"),
                }
    
    return runtimes


def get_current_runtime_config(agentcore_client, runtime_id: str) -> dict:
    """Get current runtime configuration."""
    response = agentcore_client.get_agent_runtime(agentRuntimeId=runtime_id)
    return response


def update_runtime_auth(agentcore_client, runtime_id: str, runtime_name: str, 
                        okta_discovery_url: str, okta_audience: str) -> bool:
    """Update runtime to use Okta JWT auth."""
    try:
        # Get current config to preserve other settings
        current = get_current_runtime_config(agentcore_client, runtime_id)
        
        print(f"\n📝 Updating {runtime_name} ({runtime_id})")
        print(f"   Current discovery URL: {current.get('authorizerConfiguration', {}).get('customJWTAuthorizer', {}).get('discoveryUrl', 'N/A')}")
        print(f"   New discovery URL: {okta_discovery_url}")
        print(f"   New audience: {okta_audience}")
        
        # Build update request - must include roleArn
        update_params = {
            "agentRuntimeId": runtime_id,
            "roleArn": current["roleArn"],
            "authorizerConfiguration": {
                "customJWTAuthorizer": {
                    "discoveryUrl": okta_discovery_url,
                    "allowedAudience": [okta_audience],
                }
            },
        }
        
        # Preserve requestHeaderConfiguration if it exists
        if "requestHeaderConfiguration" in current:
            update_params["requestHeaderConfiguration"] = current["requestHeaderConfiguration"]
        
        # Preserve environment variables if they exist
        if "environmentVariables" in current:
            update_params["environmentVariables"] = current["environmentVariables"]
        
        response = agentcore_client.update_agent_runtime(**update_params)
        
        print(f"   ✅ Updated successfully! New version: {response.get('agentRuntimeVersion', 'N/A')}")
        return True
        
    except Exception as e:
        print(f"   ❌ Failed to update: {e}")
        return False


def main():
    print("=" * 70)
    print("Update Lakehouse-Agent Runtimes: Cognito → Okta")
    print("=" * 70)
    
    # Initialize clients
    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)
    agentcore = boto3.client("bedrock-agentcore-control", region_name=region)
    
    print(f"\n🔧 Region: {region}")
    
    # Verify IdP is set to Okta
    idp_provider = get_idp_provider(ssm)
    if idp_provider != "okta":
        print(f"❌ IdP provider is '{idp_provider}', expected 'okta'")
        print("   Set /app/lakehouse-agent/idp-provider to 'okta' first")
        sys.exit(1)
    print(f"✅ IdP provider: {idp_provider}")
    
    # Get Okta configuration from SSM
    print("\n📋 Loading Okta configuration from SSM...")
    okta_discovery_url = get_ssm_parameter(ssm, "/app/lakehouse-agent/okta-discovery-url")
    okta_audience = get_ssm_parameter(ssm, "/app/lakehouse-agent/okta-resource-server-audience")
    
    print(f"   Discovery URL: {okta_discovery_url}")
    print(f"   Audience: {okta_audience}")
    
    # Get runtime IDs
    print("\n🔍 Finding runtimes...")
    runtimes = get_runtime_ids(agentcore)
    
    if not runtimes:
        print("❌ No lakehouse-agent runtimes found")
        sys.exit(1)
    
    for name, info in runtimes.items():
        print(f"   Found: {name} ({info['id']})")
    
    # Update each runtime
    print("\n" + "=" * 70)
    print("Updating Runtimes")
    print("=" * 70)
    
    success_count = 0
    for name, info in runtimes.items():
        if update_runtime_auth(agentcore, info["id"], name, okta_discovery_url, okta_audience):
            success_count += 1
    
    # Summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"✅ Updated {success_count}/{len(runtimes)} runtimes")
    
    if success_count == len(runtimes):
        print("\n🎉 All runtimes updated to Okta JWT auth!")
        print("\nNext steps:")
        print("1. Delete and recreate the gateway target (it's in FAILED state)")
        print("2. Test the Streamlit UI with Okta login")
    else:
        print("\n⚠️  Some runtimes failed to update. Check errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
