#!/usr/bin/env python3
"""
Runtime Manager - CRUD operations for AgentCore Runtimes
"""

import boto3
import json
import sys
import os

# Add project root to path for shared config manager
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from shared.config_manager import AgentCoreConfigManager


class RuntimeManager:
    def __init__(self, region=None):
        # Initialize configuration manager
        config_manager = AgentCoreConfigManager()
        base_config = config_manager.get_base_settings()

        self.region = region or base_config["aws"]["region"]
        self.control_client = boto3.client("bedrock-agentcore-control", region_name=self.region)

    def list_runtimes(self):
        """List all agent runtimes"""
        try:
            print("🔍 Listing Agent Runtimes...")
            response = self.control_client.list_agent_runtimes()
            runtimes = response.get("agentRuntimes", [])

            if not runtimes:
                print("   📋 No runtimes found")
                return []

            print(f"   📋 Found {len(runtimes)} runtime(s):")
            for runtime in runtimes:
                print(f"      • Name: {runtime.get('agentRuntimeName')}")
                print(f"        ARN: {runtime.get('agentRuntimeArn')}")
                print(f"        Status: {runtime.get('status')}")
                print(f"        Created: {runtime.get('createdTime', 'Unknown')}")
                print()

            return runtimes

        except Exception as e:
            print(f"❌ Error listing runtimes: {e}")
            return []

    def get_runtime(self, runtime_id):
        """Get details of a specific runtime"""
        try:
            print(f"🔍 Getting runtime details: {runtime_id}")
            response = self.control_client.get_agent_runtime(agentRuntimeId=runtime_id)

            runtime = response
            print("   📋 Runtime Details:")
            print(f"      • Name: {runtime.get('agentRuntimeName')}")
            print(f"      • ARN: {runtime.get('agentRuntimeArn')}")
            print(f"      • Status: {runtime.get('status')}")
            print(f"      • Role ARN: {runtime.get('roleArn')}")
            print(f"      • Network Mode: {runtime.get('networkConfiguration', {}).get('networkMode')}")
            print(
                f"      • Container URI: {runtime.get('agentRuntimeArtifact', {}).get('containerConfiguration', {}).get('containerUri')}"
            )

            # Check for authorizer configuration
            auth_config = runtime.get("authorizerConfiguration")
            if auth_config:
                print(f"      • Auth Config: {json.dumps(auth_config, indent=8)}")

            return runtime

        except Exception as e:
            print(f"❌ Error getting runtime: {e}")
            return None

    def delete_runtime(self, runtime_id):
        """Delete a runtime"""
        try:
            print(f"🗑️  Deleting runtime: {runtime_id}")

            # First delete endpoints
            print("   🔗 Checking for endpoints...")
            try:
                endpoints_response = self.control_client.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
                endpoints = endpoints_response.get("agentRuntimeEndpointSummaries", [])

                for endpoint in endpoints:
                    endpoint_id = endpoint.get("agentRuntimeEndpointId")
                    print(f"      🗑️  Deleting endpoint: {endpoint_id}")
                    self.control_client.delete_agent_runtime_endpoint(
                        agentRuntimeId=runtime_id, agentRuntimeEndpointId=endpoint_id
                    )
                    print(f"      ✅ Endpoint deleted: {endpoint_id}")

            except Exception as ep_error:
                print(f"      ⚠️  Error handling endpoints: {ep_error}")

            # Delete the runtime
            self.control_client.delete_agent_runtime(agentRuntimeId=runtime_id)
            print(f"   ✅ Runtime deletion initiated: {runtime_id}")

            return True

        except Exception as e:
            print(f"❌ Error deleting runtime: {e}")
            return False

    def delete_all_runtimes(self, confirm=False):
        """Delete all agent runtimes"""
        try:
            print("🔍 Discovering all Agent Runtimes...")
            runtimes = self.list_runtimes()

            if not runtimes:
                print("✅ No runtimes found to delete")
                return True

            print(f"\n⚠️  WARNING: This will delete ALL {len(runtimes)} runtime(s)!")

            if not confirm:
                print("🛑 Use --confirm flag to proceed with deletion")
                print("   Example: python3 runtime_manager.py delete-all --confirm")
                return False

            # Confirm deletion
            print(f"\n🗑️  Proceeding to delete {len(runtimes)} runtime(s)...")

            deleted_count = 0
            failed_count = 0

            for i, runtime in enumerate(runtimes, 1):
                runtime_name = runtime.get("agentRuntimeName", "Unknown")
                runtime_id = runtime.get("agentRuntimeId")

                if not runtime_id:
                    # Extract ID from ARN if not directly available
                    arn = runtime.get("agentRuntimeArn", "")
                    if "/runtime/" in arn:
                        runtime_id = arn.split("/runtime/")[-1]

                print(f"\n[{i}/{len(runtimes)}] Deleting: {runtime_name} ({runtime_id})")

                if self.delete_runtime(runtime_id):
                    deleted_count += 1
                    print(f"   ✅ Successfully deleted: {runtime_name}")
                else:
                    failed_count += 1
                    print(f"   ❌ Failed to delete: {runtime_name}")

            print("\n📊 Deletion Summary:")
            print(f"   ✅ Successfully deleted: {deleted_count}")
            print(f"   ❌ Failed to delete: {failed_count}")
            print(f"   📋 Total processed: {len(runtimes)}")

            if failed_count == 0:
                print("🎉 All runtimes deleted successfully!")
            else:
                print(f"⚠️  {failed_count} runtime(s) failed to delete - check logs above")

            return failed_count == 0

        except Exception as e:
            print(f"❌ Error in delete-all operation: {e}")
            return False

    def list_endpoints(self, runtime_id):
        """List endpoints for a runtime"""
        try:
            print(f"🔍 Listing endpoints for runtime: {runtime_id}")
            response = self.control_client.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
            endpoints = response.get("runtimeEndpoints", [])

            if not endpoints:
                print("   📋 No endpoints found")
                return []

            print(f"   📋 Found {len(endpoints)} endpoint(s):")
            for endpoint in endpoints:
                print(f"      • Name: {endpoint.get('name')}")
                print(f"        ID: {endpoint.get('id')}")
                print(f"        ARN: {endpoint.get('agentRuntimeEndpointArn')}")
                print(f"        Status: {endpoint.get('status')}")
                print()

            return endpoints

        except Exception as e:
            print(f"❌ Error listing endpoints: {e}")
            return []


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 runtime_manager.py list")
        print("  python3 runtime_manager.py get <runtime_id>")
        print("  python3 runtime_manager.py delete <runtime_id>")
        print("  python3 runtime_manager.py delete-all [--confirm]")
        print("  python3 runtime_manager.py endpoints <runtime_id>")
        sys.exit(1)

    manager = RuntimeManager()
    command = sys.argv[1]

    if command == "list":
        manager.list_runtimes()
    elif command == "get" and len(sys.argv) > 2:
        manager.get_runtime(sys.argv[2])
    elif command == "delete" and len(sys.argv) > 2:
        manager.delete_runtime(sys.argv[2])
    elif command == "delete-all":
        # Check for --confirm flag
        confirm = "--confirm" in sys.argv
        manager.delete_all_runtimes(confirm=confirm)
    elif command == "endpoints" and len(sys.argv) > 2:
        manager.list_endpoints(sys.argv[2])
    else:
        print("Invalid command or missing arguments")
        sys.exit(1)


if __name__ == "__main__":
    main()
