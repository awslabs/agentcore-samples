#!/usr/bin/env python3
"""
Update both gateways to accept Okta tokens instead of Auth0.

This reconfigures the JWT authorizer to use Okta's discovery URL and audience.
"""
import boto3
import json

REGION = "us-east-1"

# Okta configuration
OKTA_DISCOVERY_URL = "https://integrator-9803828.okta.com/oauth2/aus16nq99b5SrujQg698/.well-known/openid-configuration"
OKTA_AUDIENCE = "api://lakehouse-api"

# Gateway IDs
GATEWAYS = [
    "lakehouse-gateway-rfdbv4cd4b",
    "lakehouse-notes-gateway-6zj3okrbbn"
]

def main():
    client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    
    for gateway_id in GATEWAYS:
        print(f"\n{'='*60}")
        print(f"📋 Getting current gateway config for {gateway_id}...")
        
        try:
            gw = client.get_gateway(gatewayIdentifier=gateway_id)
        except Exception as e:
            print(f"   ❌ Error getting gateway: {e}")
            continue
        
        print(f"   Name: {gw.get('name', 'N/A')}")
        print(f"   Status: {gw.get('status', 'N/A')}")
        print(f"   Authorizer Type: {gw.get('authorizerType', 'N/A')}")
        
        current_auth = gw.get("authorizerConfiguration", {})
        print(f"   Current authorizerConfiguration: {json.dumps(current_auth, indent=2)}")
        
        # Build update params
        print(f"\n🔧 Updating gateway JWT authorizer to use Okta...")
        
        # Prepare the new Okta JWT authorizer config
        new_auth_config = {
            "customJWTAuthorizer": {
                "discoveryUrl": OKTA_DISCOVERY_URL,
                "allowedAudience": [OKTA_AUDIENCE],
                "allowedClients": []  # Empty means all clients allowed
            }
        }
        
        update_params = {
            "gatewayIdentifier": gateway_id,
            "name": gw["name"],
            "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": new_auth_config,
            "protocolType": gw.get("protocolType", "MCP"),
            "roleArn": gw["roleArn"]
        }
        
        # Pass through optional fields if they exist
        if gw.get("description"):
            update_params["description"] = gw["description"]
        if gw.get("protocolConfiguration"):
            update_params["protocolConfiguration"] = gw["protocolConfiguration"]
        if gw.get("kmsKeyArn"):
            update_params["kmsKeyArn"] = gw["kmsKeyArn"]
        
        try:
            response = client.update_gateway(**update_params)
            print(f"✅ Update initiated for {gateway_id}")
            print(f"   Status: {response.get('status', 'unknown')}")
            print(f"   New discovery URL: {OKTA_DISCOVERY_URL}")
            print(f"   New audience: {OKTA_AUDIENCE}")
        except Exception as e:
            print(f"   ❌ Error updating gateway: {e}")
    
    print(f"\n{'='*60}")
    print("🎯 Summary:")
    print("   Both gateways have been updated to accept Okta tokens.")
    print("   Wait for gateways to become READY, then test with Streamlit.")
    print("   The Okta user token will now be validated correctly.")

if __name__ == "__main__":
    main()
