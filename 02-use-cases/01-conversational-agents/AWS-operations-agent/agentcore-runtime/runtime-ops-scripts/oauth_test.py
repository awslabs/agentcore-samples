#!/usr/bin/env python3
"""
OAuth Test Script - Test OAuth token generation using AgentCore Identity service
"""

import boto3
import sys
import os

# Add project root to path for shared config manager
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from shared.config_manager import AgentCoreConfigManager


class OAuthTester:
    def __init__(self, region=None):
        # Initialize configuration manager
        config_manager = AgentCoreConfigManager()
        base_config = config_manager.get_base_settings()

        self.region = region or base_config["aws"]["region"]
        self.agentcore_client = boto3.client("bedrock-agentcore", region_name=self.region)
        self.control_client = boto3.client("bedrock-agentcore-control", region_name=self.region)

    def get_workload_token(self, workload_name):
        """Get workload access token for a given workload"""
        try:
            print(f"🔐 Getting workload access token for: {workload_name}")

            response = self.agentcore_client.get_workload_access_token(workloadName=workload_name)

            token = response.get("workloadAccessToken")
            print(f"   ✅ Workload token obtained (length: {len(token) if token else 0})")
            print(f"   🔑 Token preview: {token[:30]}..." if token else "   ❌ No token returned")

            return token

        except Exception as e:
            print(f"❌ Error getting workload token: {e}")
            return None

    def get_oauth_token(self, workload_token, provider_name, scopes=None, auth_flow="M2M"):
        """Get OAuth2 token using workload token"""
        try:
            print("🎫 Getting OAuth2 token from provider")

            if scopes is None:
                scopes = ["api"]

            print(f"   📋 Scopes: {scopes}")
            print(f"   🔄 Auth Flow: {auth_flow}")

            response = self.agentcore_client.get_resource_oauth2_token(
                workloadIdentityToken=workload_token,
                resourceCredentialProviderName=provider_name,
                scopes=scopes,
                oauth2Flow=auth_flow,
                forceAuthentication=False,
            )

            access_token = response.get("accessToken")
            auth_url = response.get("authorizationUrl")

            if access_token:
                print("   ✅ OAuth2 token obtained successfully!")
                print(f"   🔑 Token preview: {access_token[:30]}...")
                print(f"   📏 Token length: {len(access_token)}")
                return access_token
            elif auth_url:
                print(f"   🔗 Authorization required: {auth_url}")
                return None
            else:
                print("   ❌ No token or authorization URL returned")
                return None

        except Exception as e:
            print(f"❌ Error getting OAuth token: {e}")
            return None

    def test_full_flow(self, workload_name, provider_name, scopes=None):
        """Test the complete OAuth flow: workload token -> OAuth token"""
        try:
            print("🚀 Testing Complete OAuth Flow")
            print("=" * 50)

            # Step 1: Get workload token
            print("\n📍 Step 1: Get Workload Access Token")
            workload_token = self.get_workload_token(workload_name)
            if not workload_token:
                print("❌ Failed to get workload token. Cannot continue.")
                return False

            # Step 2: Get OAuth token
            print("\n📍 Step 2: Get OAuth2 Token")
            oauth_token = self.get_oauth_token(workload_token, provider_name, scopes)
            if not oauth_token:
                print("❌ Failed to get OAuth token.")
                return False

            print("\n🎉 SUCCESS! Complete OAuth flow working!")
            print("=" * 50)
            print(f"✅ Workload: {workload_name}")
            print(f"✅ Scopes: {scopes or ['api']}")
            print("✅ Token obtained and ready for use")

            return True

        except Exception as e:
            print(f"❌ Error in OAuth flow test: {e}")
            return False

    def test_with_config(self, workload_name=None, provider_name=None):
        """Test OAuth using configuration from files"""
        try:
            print("🔧 Testing OAuth with configuration files")

            # Initialize configuration manager
            config_manager = AgentCoreConfigManager()
            dynamic_config = config_manager.get_dynamic_config()
            base_config = config_manager.get_base_settings()

            # Get OAuth provider config from dynamic configuration
            oauth_provider_config = dynamic_config.get("oauth_provider", {})

            if oauth_provider_config:
                if not provider_name:
                    provider_name = oauth_provider_config.get("provider_name", "bac-identity-provider-okta")

                scopes = ["api"]  # Default scopes

                print(f"   📋 Using scopes: {scopes}")
            else:
                print("   ⚠️  OAuth provider config not found, using defaults")
                provider_name = provider_name or "bac-identity-provider-okta"
                scopes = ["api"]

            # Get workload name from base config
            if not workload_name:
                workload_name = base_config.get("runtime", {}).get("diy_agent", {}).get("name", "bac-diy")
                print(f"   📋 Using workload from config: {workload_name}")

            return self.test_full_flow(workload_name, provider_name, scopes)

        except Exception as e:
            print(f"❌ Error testing with config: {e}")
            return False

    def list_available_resources(self):
        """List available workload identities and OAuth providers for reference"""
        try:
            print("📋 Available Resources for Testing")
            print("=" * 40)

            # List workload identities
            print("\n🆔 Workload Identities:")
            try:
                identities = self.control_client.list_workload_identities()
                identity_list = identities.get("workloadIdentities", [])
                if identity_list:
                    for identity in identity_list:
                        print(f"   • {identity.get('name')} ({identity.get('status')})")
                else:
                    print("   📭 No workload identities found")
            except Exception as e:
                print(f"   ❌ Error listing identities: {e}")

            # List OAuth providers
            print("\n🔐 OAuth2 Credential Providers:")
            try:
                providers = self.control_client.list_oauth2_credential_providers()
                provider_list = providers.get("credentialProviders", [])
                if provider_list:
                    for provider in provider_list:
                        print(f"   • {provider.get('name')}")
                        print(f"     ARN: {provider.get('credentialProviderArn')}")
                        print(f"     Vendor: {provider.get('credentialProviderVendor')}")
                else:
                    print("   📭 No OAuth2 providers found")
            except Exception as e:
                print(f"   ❌ Error listing providers: {e}")

            return True

        except Exception as e:
            print(f"❌ Error listing resources: {e}")
            return False


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 oauth_test.py list                           # List available resources")
        print("  python3 oauth_test.py test-config                    # Test using config files")
        print("  python3 oauth_test.py test <workload> <provider>     # Test specific workload/provider")
        print("  python3 oauth_test.py workload-token <workload>      # Get workload token only")
        print("  python3 oauth_test.py oauth-token <workload> <provider> [scopes]  # Get OAuth token")
        print("")
        print("Examples:")
        print("  python3 oauth_test.py test-config")
        print("  python3 oauth_test.py test bac-diy bac-identity-provider-okta")
        print("  python3 oauth_test.py oauth-token bac-diy bac-identity-provider-okta api,read")
        sys.exit(1)

    tester = OAuthTester()
    command = sys.argv[1]

    if command == "list":
        tester.list_available_resources()
    elif command == "test-config":
        tester.test_with_config()
    elif command == "test" and len(sys.argv) > 3:
        workload = sys.argv[2]
        provider = sys.argv[3]
        tester.test_full_flow(workload, provider)
    elif command == "workload-token" and len(sys.argv) > 2:
        workload = sys.argv[2]
        tester.get_workload_token(workload)
    elif command == "oauth-token" and len(sys.argv) > 3:
        workload = sys.argv[2]
        provider = sys.argv[3]
        scopes = sys.argv[4].split(",") if len(sys.argv) > 4 else None

        # First get workload token
        workload_token = tester.get_workload_token(workload)
        if workload_token:
            tester.get_oauth_token(workload_token, provider, scopes)
    else:
        print("Invalid command or missing arguments")
        sys.exit(1)


if __name__ == "__main__":
    main()
