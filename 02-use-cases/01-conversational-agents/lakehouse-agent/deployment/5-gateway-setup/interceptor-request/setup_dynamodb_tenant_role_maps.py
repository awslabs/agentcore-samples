#!/usr/bin/env python3
"""
Setup DynamoDB table for tenant-to-role mapping

This script creates a DynamoDB table to store mappings between JWT claims and
assigned IAM roles for tenant-based access control.

Table Schema:
- Table Name: lakehouse_tenant_role_map
- Composite Key: (claim_name, claim_value)
- Attributes: (role_type, role_value)

Usage:
    python setup_dynamodb_tenant_role_maps.py
    python setup_dynamodb_tenant_role_maps.py --table-name custom-table-name
"""

import boto3
import json
import sys
import argparse
from typing import Dict, List
from botocore.exceptions import ClientError


class TenantRoleMappingSetup:
    def __init__(self, table_name: str = "lakehouse_tenant_role_map"):
        """
        Initialize DynamoDB setup.

        Args:all
            table_name: Name of the DynamoDB table
        """
        # Get region from boto3 session
        session = boto3.Session()
        self.region = session.region_name

        # Get account ID from STS
        sts_client = boto3.client("sts")
        self.account_id = sts_client.get_caller_identity()["Account"]

        # Initialize AWS clients
        self.dynamodb = boto3.resource("dynamodb", region_name=self.region)
        self.dynamodb_client = boto3.client("dynamodb", region_name=self.region)
        self.ssm_client = boto3.client("ssm", region_name=self.region)

        self.table_name = table_name

        print("✅ Using AWS configuration")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")
        print(f"   Table Name: {self.table_name}")

    def create_table(self) -> bool:
        """
        Create DynamoDB table for tenant-role mapping.

        Returns:
            True if table was created or already exists
        """
        print(f"\n📦 Creating DynamoDB table: {self.table_name}")

        try:
            # Check if table already exists
            existing_tables = self.dynamodb_client.list_tables()["TableNames"]
            if self.table_name in existing_tables:
                print(f"✅ Table {self.table_name} already exists")

                # Wait for table to be active
                table = self.dynamodb.Table(self.table_name)
                table.wait_until_exists()
                print("✅ Table is active")
                return True

            # Create table with composite key
            table = self.dynamodb.create_table(
                TableName=self.table_name,
                KeySchema=[
                    {
                        "AttributeName": "claim_name",
                        "KeyType": "HASH",  # Partition key
                    },
                    {
                        "AttributeName": "claim_value",
                        "KeyType": "RANGE",  # Sort key
                    },
                ],
                AttributeDefinitions=[
                    {
                        "AttributeName": "claim_name",
                        "AttributeType": "S",  # String
                    },
                    {
                        "AttributeName": "claim_value",
                        "AttributeType": "S",  # String
                    },
                ],
                BillingMode="PAY_PER_REQUEST",  # On-demand billing
                Tags=[
                    {"Key": "Application", "Value": "lakehouse-agent"},
                    {"Key": "Purpose", "Value": "tenant-role-mapping"},
                ],
            )

            print("⏳ Waiting for table to be created...")
            table.wait_until_exists()

            print(f"✅ Table {self.table_name} created successfully")
            print(f"   Table ARN: {table.table_arn}")

            return True

        except ClientError as e:
            print(f"❌ Error creating table: {e}")
            return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return False

    def get_role_arns_from_ssm(self) -> Dict[str, str]:
        """
        Get IAM role ARNs from SSM Parameter Store.

        Returns:
            Dictionary mapping group names to role ARNs
        """
        role_arns = {}
        role_mappings = {
            "adjusters": "lakehouse-adjusters-role",
            "policyholders": "lakehouse-policyholders-role",
            "administrators": "lakehouse-administrators-role",
        }

        for group, role_name in role_mappings.items():
            try:
                response = self.ssm_client.get_parameter(Name=f"/app/lakehouse-agent/roles/{role_name}")
                role_arns[group] = response["Parameter"]["Value"]
                print(f"   ✅ Retrieved {role_name} ARN from SSM")
            except Exception as e:
                print(f"   ⚠️  Could not retrieve {role_name} ARN: {e}")
                # Fallback to constructed ARN
                role_arns[group] = f"arn:aws:iam::{self.account_id}:role/{role_name}"
                print(f"   Using constructed ARN: {role_arns[group]}")

        return role_arns

    def get_seed_data(self, role_arns: Dict[str, str]) -> List[Dict[str, str]]:
        """
        Get seed data for tenant-role mappings.

        Args:
            role_arns: Dictionary mapping group names to role ARNs

        Returns:
            List of tenant-role mappings
        """
        return [
            {
                "claim_name": "cognito:groups",
                "claim_value": '["adjusters"]',
                "role_type": "iam_role",
                "role_value": role_arns.get(
                    "adjusters",
                    f"arn:aws:iam::{self.account_id}:role/lakehouse-adjusters-role",
                ),
                "allowed_tools": [
                    "get_claims_summary",
                    "get_claim_details",
                    "query_claims",
                ],
                "description": "Adjusters group mapping",
                "createdAt": "2024-01-01T00:00:00Z",
            },
            {
                "claim_name": "cognito:groups",
                "claim_value": '["policyholders"]',
                "role_type": "iam_role",
                "role_value": role_arns.get(
                    "policyholders",
                    f"arn:aws:iam::{self.account_id}:role/lakehouse-policyholders-role",
                ),
                "allowed_tools": [
                    "get_claims_summary",
                    "get_claim_details",
                    "query_claims",
                ],
                "description": "Policyholders group mapping",
                "createdAt": "2024-01-01T00:00:00Z",
            },
            {
                "claim_name": "cognito:groups",
                "claim_value": '["administrators"]',
                "role_type": "iam_role",
                "role_value": role_arns.get(
                    "administrators",
                    f"arn:aws:iam::{self.account_id}:role/lakehouse-administrators-role",
                ),
                "allowed_tools": ["query_login_audit", "text_to_sql"],
                "description": "Administrators group mapping with audit access and text-to-SQL",
                "createdAt": "2024-01-01T00:00:00Z",
            },
        ]

    def populate_seed_data(self) -> bool:
        """
        Populate table with seed data.
        Always overwrites existing entries to ensure latest configuration.

        Returns:
            True if data was populated successfully
        """
        print("\n📝 Populating seed data (overwriting existing entries)...")

        try:
            # Get role ARNs from SSM
            role_arns = self.get_role_arns_from_ssm()

            table = self.dynamodb.Table(self.table_name)
            seed_data = self.get_seed_data(role_arns)

            for item in seed_data:
                # Always overwrite - use put_item to replace existing entries
                table.put_item(Item=item)

                allowed_tools_str = ", ".join(item.get("allowed_tools", [])) if "allowed_tools" in item else "None"
                print(
                    f"   ✅ Updated mapping: ({item['claim_name']}, {item['claim_value']}) → {item['role_type']}={item['role_value'].split('/')[-1]}"
                )
                print(f"      Allowed tools: {allowed_tools_str}")

            print(f"✅ Seed data populated successfully ({len(seed_data)} entries)")
            return True

        except ClientError as e:
            print(f"❌ Error populating seed data: {e}")
            return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return False

    def verify_data(self):
        """Verify the data in the table."""
        print("\n🔍 Verifying table data...")

        try:
            table = self.dynamodb.Table(self.table_name)

            # Scan table to get all items
            response = table.scan()
            items = response.get("Items", [])

            print(f"✅ Found {len(items)} items in table:")
            for item in items:
                print(f"   - Key: ({item['claim_name']}, {item['claim_value']})")
                print(f"     Target: {item['role_type']} = {item['role_value']}")
                if "allowed_tools" in item:
                    print(f"     Allowed Tools: {', '.join(item['allowed_tools'])}")
                if "description" in item:
                    print(f"     Description: {item['description']}")

        except Exception as e:
            print(f"❌ Error verifying data: {e}")

    def store_table_info_in_ssm(self):
        """Store table information in SSM Parameter Store."""
        print("\n💾 Storing table information in SSM Parameter Store...")

        try:
            table_arn = f"arn:aws:dynamodb:{self.region}:{self.account_id}:table/{self.table_name}"

            parameters = [
                {
                    "name": "/app/lakehouse-agent/tenant-role-mapping-table",
                    "value": self.table_name,
                    "description": "DynamoDB table name for tenant-role mapping",
                },
                {
                    "name": "/app/lakehouse-agent/tenant-role-mapping-table-arn",
                    "value": table_arn,
                    "description": "DynamoDB table ARN for tenant-role mapping",
                },
            ]

            for param in parameters:
                self.ssm_client.put_parameter(
                    Name=param["name"],
                    Value=param["value"],
                    Description=param["description"],
                    Type="String",
                    Overwrite=True,
                )
                print(f"✅ Stored parameter: {param['name']}")

        except Exception as e:
            print(f"❌ Error storing SSM parameters: {e}")

    def update_lambda_role_permissions(self):
        """Add DynamoDB read permissions to Lambda role."""
        print("\n🔑 Updating Lambda role permissions for DynamoDB access...")

        try:
            iam = boto3.client("iam", region_name=self.region)
            lambda_role_name = "InsuranceClaimsGatewayInterceptorRole"

            # Create DynamoDB read policy
            dynamodb_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "DynamoDBReadTenantRoleMapping",
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:GetItem",
                            "dynamodb:Query",
                            "dynamodb:Scan",
                        ],
                        "Resource": f"arn:aws:dynamodb:{self.region}:{self.account_id}:table/{self.table_name}",
                    }
                ],
            }

            # Add policy to Lambda role
            iam.put_role_policy(
                RoleName=lambda_role_name,
                PolicyName="DynamoDBTenantRoleMappingPolicy",
                PolicyDocument=json.dumps(dynamodb_policy),
            )

            print("✅ Added DynamoDB read permissions to Lambda role")

        except Exception as e:
            print(f"⚠️  Could not update Lambda role: {e}")
            print("   You may need to add DynamoDB permissions manually")

    def setup(self):
        """Run the complete setup."""
        print("\n🚀 Starting Tenant-Role Mapping Setup")
        print("=" * 70)

        # Create table
        if not self.create_table():
            print("\n❌ Failed to create table")
            sys.exit(1)

        # Populate seed data
        if not self.populate_seed_data():
            print("\n❌ Failed to populate seed data")
            sys.exit(1)

        # Verify data
        self.verify_data()

        # Store table info in SSM
        self.store_table_info_in_ssm()

        # Update Lambda role permissions
        self.update_lambda_role_permissions()

        print("\n" + "=" * 70)
        print("✨ Setup completed successfully!")
        print("=" * 70)
        print("\n📋 Summary:")
        print(f"   Table Name: {self.table_name}")
        print(f"   Region: {self.region}")
        print("   Billing Mode: PAY_PER_REQUEST (on-demand)")
        print("   Seed Data: 3 group mappings populated")
        print("\n🔑 Key Schema:")
        print("   - Partition Key: claim_name (String)")
        print("   - Sort Key: claim_value (String)")
        print("\n📊 Attributes:")
        print("   - role_type (String)")
        print("   - role_value (String)")
        print("   - allowed_tools (List of Strings)")
        print("\n🔐 Tool Authorization:")
        print(
            "   - administrators: query_login_audit, text_to_sql, get_claims_summary, get_claim_details, query_claims"
        )
        print("   - adjusters: get_claims_summary, get_claim_details, query_claims")
        print("   - policyholders: get_claims_summary, get_claim_details, query_claims")
        print("\n💾 SSM Parameters:")
        print("   - /app/lakehouse-agent/tenant-role-mapping-table")
        print("   - /app/lakehouse-agent/tenant-role-mapping-table-arn")
        print("\n🔐 Lambda Role:")
        print("   - Added DynamoDB read permissions")
        print("\n📋 Next Steps:")
        print("   1. Update lambda_function.py to use check_tool_authorization()")
        print("   2. Query using composite key: (claim_name, claim_value)")
        print("   3. Redeploy Lambda function: ./deploy.sh")
        print("   4. Test with adjusters, policyholders, and administrators groups")


def main():
    parser = argparse.ArgumentParser(description="Setup DynamoDB table for tenant-role mapping")
    parser.add_argument(
        "--table-name",
        required=False,
        default="lakehouse_tenant_role_map",
        help="Name of the DynamoDB table (default: lakehouse_tenant_role_map)",
    )

    args = parser.parse_args()

    # Run setup
    setup = TenantRoleMappingSetup(table_name=args.table_name)
    setup.setup()


if __name__ == "__main__":
    main()
