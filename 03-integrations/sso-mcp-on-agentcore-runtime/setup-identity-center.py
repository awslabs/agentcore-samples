#!/usr/bin/env python3
"""
Identity Center Discovery and Configuration Script
Automatically discovers Identity Center details and configures the MCP server
"""

import boto3
import json
import sys
from botocore.exceptions import ClientError, NoCredentialsError

def discover_identity_center():
    """Discover Identity Center instance details"""
    print("🔍 Discovering Identity Center configuration...")
    
    try:
        # Try to get current AWS identity
        sts = boto3.client('sts')
        identity = sts.get_caller_identity()
        account_id = identity['Account']
        print(f"✅ Current AWS Account: {account_id}")
        
        # Try to find Identity Center instances
        sso_admin = boto3.client('sso-admin')
        
        try:
            instances = sso_admin.list_instances()
            if instances['Instances']:
                instance = instances['Instances'][0]
                instance_arn = instance['InstanceArn']
                identity_store_id = instance['IdentityStoreId']
                
                print(f"✅ Identity Center Instance Found:")
                print(f"   ARN: {instance_arn}")
                print(f"   Identity Store ID: {identity_store_id}")
                
                return {
                    'instance_arn': instance_arn,
                    'identity_store_id': identity_store_id,
                    'account_id': account_id,
                    'region': sso_admin.meta.region_name
                }
            else:
                print("❌ No Identity Center instances found in this account")
                return None
                
        except ClientError as e:
            if 'AccessDenied' in str(e):
                print("❌ Access denied to Identity Center. Please ensure you have the necessary permissions:")
                print("   - sso:ListInstances")
                print("   - sso-admin:ListInstances")
            else:
                print(f"❌ Error accessing Identity Center: {e}")
            return None
            
    except NoCredentialsError:
        print("❌ AWS credentials not configured. Please run 'aws configure' first.")
        return None
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return None

def get_user_input():
    """Get user configuration input"""
    print("\n📝 Please provide the following information:")
    
    user_email = input("Your Identity Center email address: ").strip()
    if not user_email or '@' not in user_email:
        print("❌ Invalid email address")
        return None
    
    target_accounts = []
    print("\nTarget AWS accounts for cross-account access (press Enter when done):")
    while True:
        account = input(f"Target account #{len(target_accounts) + 1} (or Enter to finish): ").strip()
        if not account:
            break
        if len(account) == 12 and account.isdigit():
            target_accounts.append(account)
            print(f"✅ Added account: {account}")
        else:
            print("❌ Invalid account ID (must be 12 digits)")
    
    return {
        'user_email': user_email,
        'target_accounts': target_accounts
    }

def update_config(identity_center_info, user_info):
    """Update the configuration template with discovered values"""
    try:
        # Load the template
        with open('config-template.json', 'r') as f:
            config = json.load(f)
        
        # Update with discovered values
        config['identity_center']['instance_arn'] = identity_center_info['instance_arn']
        config['identity_center']['region'] = identity_center_info['region']
        config['identity_center']['account_id'] = identity_center_info['account_id']
        
        config['user_config']['default_user_email'] = user_info['user_email']
        config['cross_account']['target_accounts'] = user_info['target_accounts']
        
        # Save the updated configuration
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"\n✅ Configuration saved to config.json")
        print(f"✅ Identity Center ARN: {identity_center_info['instance_arn']}")
        print(f"✅ User Email: {user_info['user_email']}")
        print(f"✅ Target Accounts: {', '.join(user_info['target_accounts'])}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error updating configuration: {e}")
        return False

def main():
    print("🚀 Enterprise CloudWatch MCP Server - Identity Center Setup")
    print("=" * 60)
    
    # Discover Identity Center
    identity_center_info = discover_identity_center()
    if not identity_center_info:
        print("\n❌ Could not discover Identity Center configuration.")
        print("Please ensure:")
        print("1. AWS CLI is configured with valid credentials")
        print("2. You have access to AWS Identity Center")
        print("3. Identity Center is set up in your account")
        sys.exit(1)
    
    # Get user input
    user_info = get_user_input()
    if not user_info:
        print("❌ Invalid user configuration")
        sys.exit(1)
    
    # Update configuration
    if update_config(identity_center_info, user_info):
        print("\n🎉 Setup completed successfully!")
        print("\nNext steps:")
        print("1. Run: python deploy-server.py")
        print("2. Run: python setup-kiro.py")
        print("3. Run: python test-connection.py")
    else:
        print("❌ Setup failed")
        sys.exit(1)

if __name__ == "__main__":
    main()