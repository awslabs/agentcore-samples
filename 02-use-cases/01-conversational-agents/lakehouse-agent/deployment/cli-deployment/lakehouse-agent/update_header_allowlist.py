#!/usr/bin/env python3
"""
Update the agent runtime to forward the Authorization header to the handler.

Uses bedrock-agentcore-control (control plane) to add requestHeaderAllowlist.
"""
import boto3
import json

REGION = "us-east-1"
RUNTIME_ID = "LakehouseAgent_lakehouse_agent-zX1PuL84KN"

def main():
    client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    
    # Get current runtime config
    print(f"📋 Getting current runtime config for {RUNTIME_ID}...")
    runtime = client.get_agent_runtime(agentRuntimeId=RUNTIME_ID)
    
    print(f"   Name: {runtime.get('agentRuntimeName', 'N/A')}")
    print(f"   Status: {runtime.get('status', 'N/A')}")
    print(f"   Role ARN: {runtime.get('roleArn', 'N/A')}")
    
    # Check current header config
    current_headers = runtime.get("requestHeaderConfiguration", {})
    print(f"   Current requestHeaderConfiguration: {json.dumps(current_headers)}")
    
    if current_headers.get("requestHeaderAllowlist") == ["Authorization"]:
        print("\n✅ Authorization header already in allowlist!")
        return
    
    # Build update params - pass through existing required fields
    print(f"\n🔧 Updating runtime with requestHeaderAllowlist: ['Authorization']...")
    
    update_params = {
        "agentRuntimeId": RUNTIME_ID,
        "roleArn": runtime["roleArn"],
        "agentRuntimeArtifact": runtime["agentRuntimeArtifact"],
        "requestHeaderConfiguration": {
            "requestHeaderAllowlist": ["Authorization"]
        }
    }
    
    # Pass through optional fields if they exist
    if runtime.get("networkConfiguration"):
        update_params["networkConfiguration"] = runtime["networkConfiguration"]
    if runtime.get("protocolConfiguration"):
        update_params["protocolConfiguration"] = runtime["protocolConfiguration"]
    if runtime.get("authorizerConfiguration"):
        update_params["authorizerConfiguration"] = runtime["authorizerConfiguration"]
    if runtime.get("environmentVariables"):
        update_params["environmentVariables"] = runtime["environmentVariables"]
    
    response = client.update_agent_runtime(**update_params)
    
    print(f"✅ Update initiated. Status: {response.get('status', 'unknown')}")
    print(f"   The runtime will update. After it becomes READY again,")
    print(f"   the handler will receive the Authorization header from Streamlit.")
    
if __name__ == "__main__":
    main()
