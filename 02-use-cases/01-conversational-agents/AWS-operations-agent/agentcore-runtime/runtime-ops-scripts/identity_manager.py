#!/usr/bin/env python3
"""
Identity Manager - CRUD operations for AgentCore Workload Identities
"""

import boto3
import json
import sys
import os

# Add config directory to path
config_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
)
sys.path.append(config_path)


class IdentityManager:
    def __init__(self, region="us-east-1"):
        self.region = region
        self.control_client = boto3.client("bedrock-agentcore-control", region_name=region)

    def list_identities(self):
        """List all workload identities with pagination support"""
        try:
            print("🔍 Listing Workload Identities...")

            all_identities = []
            next_token = None
            page_count = 0

            while True:
                page_count += 1

                # Use maximum allowed page size (20)
                if next_token:
                    response = self.control_client.list_workload_identities(maxResults=20, nextToken=next_token)
                else:
                    response = self.control_client.list_workload_identities(maxResults=20)

                page_identities = response.get("workloadIdentities", [])
                all_identities.extend(page_identities)

                if page_count <= 5 or page_count % 100 == 0:  # Show progress for first 5 pages and every 100th page
                    print(f"   📄 Page {page_count}: {len(page_identities)} identities (Total: {len(all_identities)})")

                next_token = response.get("nextToken")
                if not next_token:
                    break

                # Safety limit to prevent infinite loops
                if page_count > 2000:
                    print("      ⚠️  Stopping after 2000 pages for safety")
                    break

            if page_count > 5:
                print(f"   📊 Completed pagination: {page_count} pages, {len(all_identities)} total identities")

            if not all_identities:
                print("   📋 No workload identities found")
                return []

            print(f"   📋 Found {len(all_identities)} identity/identities:")
            # Show only first 10 for readability
            for i, identity in enumerate(all_identities[:10]):
                print(f"      • Name: {identity.get('name')}")
                print(f"        ARN: {identity.get('workloadIdentityArn')}")
                print(f"        Status: {identity.get('status')}")
                print(f"        Principal: {identity.get('principalArn')}")
                print(f"        Created: {identity.get('createdTime', 'Unknown')}")
                print()

            if len(all_identities) > 10:
                print(f"      ... and {len(all_identities) - 10} more identities")
                print()

            return all_identities

        except Exception as e:
            print(f"❌ Error listing identities: {e}")
            return []

    def get_identity(self, identity_name):
        """Get details of a specific workload identity"""
        try:
            print(f"🔍 Getting identity details: {identity_name}")
            response = self.control_client.get_workload_identity(name=identity_name)

            identity = response
            print("   📋 Identity Details:")
            print(f"      • Name: {identity.get('name')}")
            print(f"      • ARN: {identity.get('workloadIdentityArn')}")
            print(f"      • Status: {identity.get('status')}")
            print(f"      • Principal ARN: {identity.get('principalArn')}")
            print(f"      • Agent Runtime ARN: {identity.get('agentRuntimeArn')}")
            print(f"      • Created: {identity.get('createdTime')}")
            print(f"      • Updated: {identity.get('updatedTime')}")

            # Show configuration if available
            config = identity.get("workloadIdentityConfiguration", {})
            if config:
                print("      • Configuration:")
                print(f"        - Callback URLs: {config.get('callbackUrls', [])}")
                print(f"        - Allowed Audiences: {config.get('allowedAudiences', [])}")

            return identity

        except Exception as e:
            print(f"❌ Error getting identity: {e}")
            return None

    def create_identity(self, name, principal_arn, callback_urls=None, allowed_audiences=None):
        """Create a new workload identity"""
        try:
            print(f"🆕 Creating workload identity: {name}")

            # Build configuration
            config = {}
            if callback_urls:
                config["callbackUrls"] = callback_urls
            if allowed_audiences:
                config["allowedAudiences"] = allowed_audiences

            request = {"workloadIdentityName": name, "principalArn": principal_arn}

            if config:
                request["workloadIdentityConfiguration"] = config

            response = self.control_client.create_workload_identity(**request)

            print("   ✅ Identity created successfully!")
            print(f"      • ARN: {response.get('workloadIdentityArn')}")

            return response

        except Exception as e:
            print(f"❌ Error creating identity: {e}")
            return None

    def delete_identity(self, identity_name):
        """Delete a workload identity"""
        try:
            print(f"🗑️  Deleting workload identity: {identity_name}")

            self.control_client.delete_workload_identity(name=identity_name)
            print(f"   ✅ Identity deletion initiated: {identity_name}")

            return True

        except Exception as e:
            print(f"❌ Error deleting identity: {e}")
            return False

    def delete_all_identities(self, confirm=False):
        """Delete all workload identities with proper pagination support (dangerous operation)"""
        if not confirm:
            print("⚠️  WARNING: This will delete ALL workload identities!")
            print("⚠️  This operation will process ALL pages of identities, which could be 20,000+ identities!")
            response = input("Type 'DELETE ALL' to confirm: ")
            if response != "DELETE ALL":
                print("❌ Operation cancelled")
                return False

        print("🔍 Getting complete list of ALL identities (this may take a while)...")
        identities = self.list_identities()

        if not identities:
            print("✅ No identities to delete")
            return True

        print(f"\n🗑️  Starting bulk deletion of {len(identities)} identities...")
        print("📊 This will be processed in batches with progress updates...")

        deleted_count = 0
        failed_count = 0
        batch_size = 100  # Process in batches for better progress tracking

        for i in range(0, len(identities), batch_size):
            batch = identities[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (len(identities) + batch_size - 1) // batch_size

            print(f"\n📦 Processing batch {batch_num}/{total_batches} ({len(batch)} identities)...")

            batch_deleted = 0
            batch_failed = 0

            for identity in batch:
                identity_name = identity.get("name")
                if identity_name:
                    if self.delete_identity(identity_name):
                        deleted_count += 1
                        batch_deleted += 1
                    else:
                        failed_count += 1
                        batch_failed += 1
                else:
                    print(f"⚠️  Skipping identity with no name: {identity}")
                    failed_count += 1
                    batch_failed += 1

            print(f"   📊 Batch {batch_num} results: {batch_deleted} deleted, {batch_failed} failed")
            print(
                f"   📈 Overall progress: {deleted_count}/{len(identities)} ({(deleted_count / len(identities) * 100):.1f}%)"
            )

            # Add a small delay between batches to avoid rate limiting
            if batch_num < total_batches:
                import time

                time.sleep(1)

        print("\n📊 Final bulk deletion results:")
        print(f"   ✅ Successfully deleted: {deleted_count}")
        print(f"   ❌ Failed deletions: {failed_count}")
        print(f"   📋 Total processed: {len(identities)}")
        print(f"   📈 Success rate: {(deleted_count / len(identities) * 100):.1f}%")

        # Verify deletion by checking remaining count
        print("\n🔍 Verifying deletion (checking first page only for speed)...")
        try:
            response = self.control_client.list_workload_identities(maxResults=20)
            remaining_identities = response.get("workloadIdentities", [])
            has_more = "nextToken" in response

            print(f"   📊 First page shows: {len(remaining_identities)} identities")
            if has_more:
                print("   📄 More pages exist - some identities may still remain")
                print("   💡 You may need to run the script again to delete remaining identities")
            elif len(remaining_identities) == 0:
                print("   🎉 First page is empty - deletion appears successful!")
            else:
                print(f"   ⚠️  {len(remaining_identities)} identities still remain on first page")

        except Exception as e:
            print(f"   ❌ Error verifying deletion: {e}")

        return failed_count == 0

    def update_identity(self, identity_name, callback_urls=None, allowed_audiences=None):
        """Update workload identity configuration"""
        try:
            print(f"📝 Updating workload identity: {identity_name}")

            # Build configuration
            config = {}
            if callback_urls:
                config["callbackUrls"] = callback_urls
            if allowed_audiences:
                config["allowedAudiences"] = allowed_audiences

            if not config:
                print("   ⚠️  No configuration updates provided")
                return None

            response = self.control_client.update_workload_identity(
                workloadIdentityName=identity_name, workloadIdentityConfiguration=config
            )

            print("   ✅ Identity updated successfully!")
            print(f"      • Updated configuration: {json.dumps(config, indent=8)}")

            return response

        except Exception as e:
            print(f"❌ Error updating identity: {e}")
            return None


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 identity_manager.py list")
        print("  python3 identity_manager.py get <identity_name>")
        print("  python3 identity_manager.py create <name> <principal_arn> [callback_urls] [allowed_audiences]")
        print("  python3 identity_manager.py delete <identity_name>")
        print("  python3 identity_manager.py delete-all [--confirm]")
        print("  python3 identity_manager.py update <identity_name> [callback_urls] [allowed_audiences]")
        print("")
        print("Examples:")
        print("  python3 identity_manager.py create my-identity arn:aws:iam::123456789012:role/my-role")
        print("  python3 identity_manager.py update my-identity 'http://localhost:8080/callback' 'my-audience'")
        print("  python3 identity_manager.py delete-all  # Interactive confirmation")
        print("  python3 identity_manager.py delete-all --confirm  # Skip confirmation")
        print("")
        print("⚠️  WARNING: delete-all now processes ALL pages and may delete 20,000+ identities!")
        sys.exit(1)

    manager = IdentityManager()
    command = sys.argv[1]

    if command == "list":
        manager.list_identities()
    elif command == "get" and len(sys.argv) > 2:
        manager.get_identity(sys.argv[2])
    elif command == "create" and len(sys.argv) > 3:
        name = sys.argv[2]
        principal_arn = sys.argv[3]
        callback_urls = [sys.argv[4]] if len(sys.argv) > 4 else None
        allowed_audiences = [sys.argv[5]] if len(sys.argv) > 5 else None
        manager.create_identity(name, principal_arn, callback_urls, allowed_audiences)
    elif command == "delete" and len(sys.argv) > 2:
        manager.delete_identity(sys.argv[2])
    elif command == "delete-all":
        confirm = "--confirm" in sys.argv
        manager.delete_all_identities(confirm=confirm)
    elif command == "update" and len(sys.argv) > 2:
        name = sys.argv[2]
        callback_urls = [sys.argv[3]] if len(sys.argv) > 3 else None
        allowed_audiences = [sys.argv[4]] if len(sys.argv) > 4 else None
        manager.update_identity(name, callback_urls, allowed_audiences)
    else:
        print("Invalid command or missing arguments")
        sys.exit(1)


if __name__ == "__main__":
    main()
