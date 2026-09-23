#!/usr/bin/env python3

# ============================================================================
# IMPORTS
# ============================================================================

import boto3
import time
import sys
import os

# ============================================================================
# CONFIGURATION
# ============================================================================

# Add project root to path for shared config manager
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from shared.config_manager import AgentCoreConfigManager

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def update_config_with_arns(config_manager, runtime_arn, endpoint_arn):
    """Update dynamic configuration with new ARNs"""
    print("\n📝 Updating dynamic configuration with new SDK runtime ARN...")
    try:
        # Update dynamic configuration
        updates = {"runtime": {"sdk_agent": {"arn": runtime_arn}}}

        if endpoint_arn:
            updates["runtime"]["sdk_agent"]["endpoint_arn"] = endpoint_arn

        config_manager.update_dynamic_config(updates)
        print("   ✅ Dynamic config updated with new SDK runtime ARN")

    except Exception as config_error:
        print(f"   ⚠️  Error updating config: {config_error}")


# Initialize configuration manager
config_manager = AgentCoreConfigManager()

# Get configuration values
base_config = config_manager.get_base_settings()
merged_config = config_manager.get_merged_config()  # For runtime values that may be dynamic
oauth_config = config_manager.get_oauth_settings()

# Extract configuration values
REGION = base_config["aws"]["region"]
ROLE_ARN = base_config["runtime"]["role_arn"]
AGENT_RUNTIME_NAME = base_config["runtime"]["sdk_agent"]["name"]
ECR_URI = merged_config["runtime"]["sdk_agent"]["ecr_uri"]  # ECR URI is dynamic

# Okta configuration
OKTA_DOMAIN = oauth_config["domain"]
OKTA_AUDIENCE = oauth_config["jwt"]["audience"]

print("🚀 Creating AgentCore Runtime for SDK agent...")
print(f"   📝 Name: {AGENT_RUNTIME_NAME}")
print(f"   📦 Container: {ECR_URI}")
print(f"   🔐 Role: {ROLE_ARN}")

control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)

try:
    response = control_client.create_agent_runtime(
        agentRuntimeName=AGENT_RUNTIME_NAME,
        agentRuntimeArtifact={"containerConfiguration": {"containerUri": ECR_URI}},
        networkConfiguration={"networkMode": "PUBLIC"},
        roleArn=ROLE_ARN,
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": oauth_config["jwt"]["discovery_url"],
                "allowedAudience": [OKTA_AUDIENCE],
            }
        },
    )

    runtime_arn = response["agentRuntimeArn"]
    runtime_id = runtime_arn.split("/")[-1]

    print("✅ SDK AgentCore Runtime created!")
    print(f"🏷️  ARN: {runtime_arn}")
    print(f"🆔 Runtime ID: {runtime_id}")

    print("\n⏳ Waiting for runtime to be READY...")
    max_wait = 600  # 10 minutes
    wait_time = 0

    while wait_time < max_wait:
        try:
            status_response = control_client.get_agent_runtime(agentRuntimeId=runtime_id)
            status = status_response.get("status")
            print(f"   📊 Status: {status} ({wait_time}s)")

            if status == "READY":
                print("✅ SDK Runtime is READY!")

                # Create DEFAULT endpoint
                print("\n🔗 Creating DEFAULT endpoint...")
                try:
                    endpoint_response = control_client.create_agent_runtime_endpoint(
                        agentRuntimeId=runtime_id, name="DEFAULT"
                    )
                    print("✅ DEFAULT endpoint created!")
                    print(f"🏷️  Endpoint ARN: {endpoint_response['agentRuntimeEndpointArn']}")

                    # Update config with new ARNs
                    update_config_with_arns(
                        config_manager,
                        runtime_arn,
                        endpoint_response["agentRuntimeEndpointArn"],
                    )

                except Exception as ep_error:
                    if "already exists" in str(ep_error):
                        print("ℹ️  DEFAULT endpoint already exists")
                        # Fetch existing endpoint ARN
                        try:
                            endpoints_response = control_client.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
                            default_endpoint = next(
                                (ep for ep in endpoints_response["runtimeEndpoints"] if ep["name"] == "DEFAULT"),
                                None,
                            )
                            if default_endpoint:
                                existing_endpoint_arn = default_endpoint["agentRuntimeEndpointArn"]
                                print(f"🏷️  Found existing endpoint ARN: {existing_endpoint_arn}")
                                update_config_with_arns(config_manager, runtime_arn, existing_endpoint_arn)
                            else:
                                print("⚠️  Could not find DEFAULT endpoint")
                                update_config_with_arns(config_manager, runtime_arn, "")
                        except Exception as fetch_error:
                            print(f"⚠️  Error fetching existing endpoint: {fetch_error}")
                            update_config_with_arns(config_manager, runtime_arn, "")
                    else:
                        print(f"❌ Error creating endpoint: {ep_error}")

                break
            elif status in ["FAILED", "DELETING"]:
                print(f"❌ Runtime creation failed with status: {status}")
                break

            time.sleep(15)
            wait_time += 15

        except Exception as e:
            print(f"❌ Error checking status: {e}")
            break

    if wait_time >= max_wait:
        print("⚠️  Runtime creation taking longer than expected")

    print("\n🧪 Test with:")
    print(f"   ARN: {runtime_arn}")
    print(f"   ID: {runtime_id}")

except Exception as e:
    print(f"❌ Error creating SDK runtime: {e}")
