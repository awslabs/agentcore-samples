#!/usr/bin/env python3
"""
IAM Roles Setup Script for Lakehouse Agent

This script creates IAM roles for tenant-based access control:
1. Creates lakehouse-policyholders-role
2. Creates lakehouse-adjusters-role
3. Creates lakehouse-administrators-role
4. Configures trust policies for AgentCore Runtime
5. Attaches necessary permissions for Athena and S3 access

Usage:
    python setup_iam_roles.py
    python setup_iam_roles.py --account-id 123456789012

Arguments:
    --account-id: (Optional) AWS Account ID for trust policy. If not provided, uses current account.
"""

import boto3
import json
import argparse
from typing import Dict, Any


class IAMRolesSetup:
    def __init__(self, account_id: str = None):
        """
        Initialize IAM roles setup.

        Args:
            account_id: AWS Account ID for trust policy (optional, defaults to current account)
        """
        # Get region and account from boto3 session
        session = boto3.Session()
        self.region = session.region_name

        # Get account ID from STS if not provided
        sts_client = boto3.client("sts")
        self.account_id = account_id or sts_client.get_caller_identity()["Account"]

        # Initialize AWS clients
        self.iam_client = boto3.client("iam")
        self.ssm_client = boto3.client("ssm", region_name=self.region)

        # Role names
        self.tenant_roles = [
            "lakehouse-policyholders-role",
            "lakehouse-adjusters-role",
            "lakehouse-administrators-role",
        ]

    def get_trust_policy(self) -> Dict[str, Any]:
        """
        Create trust policy for AgentCore Runtime, Lambda role, and current account.

        Returns:
            Trust policy document
        """
        # Get Lambda role ARN from SSM if available
        lambda_role_arn = None
        try:
            response = self.ssm_client.get_parameter(Name="/app/lakehouse-agent/interceptor-lambda-role-arn")
            lambda_role_arn = response["Parameter"]["Value"]
            print(f"   Found Lambda role ARN in SSM: {lambda_role_arn}")

            # Verify the role actually exists in IAM (SSM may have stale value after cleanup)
            role_name = lambda_role_arn.split("/")[-1]
            try:
                self.iam_client.get_role(RoleName=role_name)
                print("   ✅ Verified role exists in IAM")
            except self.iam_client.exceptions.NoSuchEntityException:
                print("   ⚠️  Role not found in IAM (stale SSM parameter), skipping")
                lambda_role_arn = None
        except Exception:
            print("   Lambda role ARN not found in SSM, using account root principal")

        statements = [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": self.account_id}},
            },
            {
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{self.account_id}:root"},
                "Action": "sts:AssumeRole",
            },
        ]

        # Add explicit trust for Lambda role if found and verified
        if lambda_role_arn:
            statements.append(
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": lambda_role_arn},
                    "Action": "sts:AssumeRole",
                }
            )

        return {"Version": "2012-10-17", "Statement": statements}

    def get_athena_s3_policy(self, bucket_name: str) -> Dict[str, Any]:
        """
        Create policy for Athena, S3, and S3 Tables access.

        Args:
            bucket_name: S3 bucket name for data access

        Returns:
            IAM policy document
        """
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AthenaQueryAccess",
                    "Effect": "Allow",
                    "Action": [
                        "athena:StartQueryExecution",
                        "athena:GetQueryExecution",
                        "athena:GetQueryResults",
                        "athena:StopQueryExecution",
                        "athena:GetWorkGroup",
                        "athena:ListWorkGroups",
                        "athena:GetDataCatalog",
                        "athena:GetDatabase",
                        "athena:GetTableMetadata",
                        "athena:ListDatabases",
                        "athena:ListTableMetadata",
                    ],
                    "Resource": [
                        f"arn:aws:athena:{self.region}:{self.account_id}:workgroup/*",
                        f"arn:aws:athena:{self.region}:{self.account_id}:datacatalog/*",
                    ],
                },
                {
                    "Sid": "GlueAccess",
                    "Effect": "Allow",
                    "Action": [
                        "glue:GetDatabase",
                        "glue:GetTable",
                        "glue:GetPartitions",
                        "glue:GetDatabases",
                        "glue:GetTables",
                        "glue:GetCatalog",
                    ],
                    "Resource": [
                        f"arn:aws:glue:{self.region}:{self.account_id}:catalog",
                        f"arn:aws:glue:{self.region}:{self.account_id}:database/*",
                        f"arn:aws:glue:{self.region}:{self.account_id}:table/*/*",
                    ],
                },
                {
                    "Sid": "GlueFederatedCatalogAccess",
                    "Effect": "Allow",
                    "Action": [
                        "glue:GetDatabase",
                        "glue:GetTable",
                        "glue:GetTables",
                        "glue:GetPartitions",
                    ],
                    "Resource": [
                        f"arn:aws:glue:{self.region}:{self.account_id}:catalog",
                        f"arn:aws:glue:{self.region}:{self.account_id}:database/s3tablescatalog/*",
                        f"arn:aws:glue:{self.region}:{self.account_id}:table/s3tablescatalog/*/*",
                    ],
                },
                {
                    "Sid": "S3TablesAccess",
                    "Effect": "Allow",
                    "Action": [
                        "s3tables:GetTableBucket",
                        "s3tables:GetTable",
                        "s3tables:GetTableMetadata",
                        "s3tables:GetTableMetadataLocation",
                        "s3tables:GetTableMaintenanceConfiguration",
                        "s3tables:GetNamespace",
                        "s3tables:ListTables",
                        "s3tables:ListNamespaces",
                    ],
                    "Resource": [
                        f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*",
                        f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*/namespace/*",
                        f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*/namespace/*/table/*",
                    ],
                },
                {
                    "Sid": "LakeFormationAccess",
                    "Effect": "Allow",
                    "Action": [
                        "lakeformation:GetDataAccess",
                        "lakeformation:GetResourceLFTags",
                        "lakeformation:ListLFTags",
                        "lakeformation:GetLFTag",
                        "lakeformation:SearchTablesByLFTags",
                        "lakeformation:SearchDatabasesByLFTags",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "S3DataAccess",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": [
                        f"arn:aws:s3:::{bucket_name}",
                        f"arn:aws:s3:::{bucket_name}/*",
                    ],
                },
                {
                    "Sid": "S3QueryResultsAccess",
                    "Effect": "Allow",
                    "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                    "Resource": [f"arn:aws:s3:::{bucket_name}/athena-results/*"],
                },
                {
                    "Sid": "MarketplaceModelAccess",
                    "Effect": "Allow",
                    "Action": [
                        "aws-marketplace:ViewSubscriptions",
                        "aws-marketplace:Subscribe",
                        "aws-marketplace:Unsubscribe",
                    ],
                    "Resource": "*",
                },
            ],
        }

    def get_admin_policy(self, bucket_name: str) -> Dict[str, Any]:
        """
        Create policy for administrators with additional DynamoDB access for audit logs.

        Args:
            bucket_name: S3 bucket name for data access

        Returns:
            IAM policy document with admin permissions
        """
        # Start with base Athena/S3 policy
        base_policy = self.get_athena_s3_policy(bucket_name)

        # Add DynamoDB access for login audit logs
        base_policy["Statement"].append(
            {
                "Sid": "DynamoDBLoginAuditAccess",
                "Effect": "Allow",
                "Action": ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"],
                "Resource": [f"arn:aws:dynamodb:{self.region}:{self.account_id}:table/lakehouse_user_login_audit"],
            }
        )

        return base_policy

    def create_role(self, role_name: str, trust_policy: Dict[str, Any]) -> str:
        """
        Create IAM role with trust policy.

        Args:
            role_name: Name of the IAM role
            trust_policy: Trust policy document

        Returns:
            Role ARN
        """
        try:
            # Check if role already exists
            try:
                response = self.iam_client.get_role(RoleName=role_name)
                print(f"✅ Role {role_name} already exists")
                return response["Role"]["Arn"]
            except self.iam_client.exceptions.NoSuchEntityException:
                pass

            # Create the role
            response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description=f"IAM role for {role_name} group in lakehouse agent",
                Tags=[
                    {"Key": "Application", "Value": "lakehouse-agent"},
                    {"Key": "Group", "Value": role_name},
                ],
            )

            role_arn = response["Role"]["Arn"]
            print(f"✅ Created role: {role_name}")
            print(f"   ARN: {role_arn}")
            return role_arn

        except Exception as e:
            print(f"❌ Error creating role {role_name}: {e}")
            raise

    def attach_inline_policy(self, role_name: str, policy_name: str, policy_document: Dict[str, Any]):
        """
        Attach inline policy to IAM role.

        Args:
            role_name: Name of the IAM role
            policy_name: Name for the inline policy
            policy_document: Policy document
        """
        try:
            self.iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName=policy_name,
                PolicyDocument=json.dumps(policy_document),
            )
            print(f"✅ Attached inline policy {policy_name} to {role_name}")
        except Exception as e:
            print(f"❌ Error attaching inline policy to {role_name}: {e}")
            raise

    def attach_managed_policy(self, role_name: str, policy_arn: str):
        """
        Attach AWS managed policy to IAM role.

        Args:
            role_name: Name of the IAM role
            policy_arn: ARN of the managed policy
        """
        try:
            self.iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            print(f"✅ Attached managed policy {policy_arn.split('/')[-1]} to {role_name}")
        except Exception as e:
            print(f"❌ Error attaching managed policy to {role_name}: {e}")
            raise

    def get_bucket_name_from_ssm(self) -> str:
        """
        Retrieve S3 bucket name from SSM Parameter Store.

        Returns:
            S3 bucket name
        """
        try:
            response = self.ssm_client.get_parameter(Name="/app/lakehouse-agent/s3-bucket-name")
            bucket_name = response["Parameter"]["Value"]
            print(f"✅ Retrieved bucket name from SSM: {bucket_name}")
            return bucket_name
        except Exception as e:
            print(f"⚠️  Could not retrieve bucket name from SSM: {e}")
            print("   Using default bucket pattern: {account_id}-{region}-lakehouse-agent")
            return f"{self.account_id}-{self.region}-lakehouse-agent"

    def store_role_arns_in_ssm(self, role_arns: Dict[str, str]):
        """
        Store role ARNs in SSM Parameter Store.

        Args:
            role_arns: Dictionary mapping role names to ARNs
        """
        print("\n💾 Storing role ARNs in SSM Parameter Store...")

        for role_name, role_arn in role_arns.items():
            param_name = f"/app/lakehouse-agent/roles/{role_name}"
            try:
                self.ssm_client.put_parameter(
                    Name=param_name,
                    Value=role_arn,
                    Description=f"IAM Role ARN for {role_name}",
                    Type="String",
                    Overwrite=True,
                )
                print(f"✅ Stored parameter: {param_name}")
            except Exception as e:
                print(f"❌ Error storing parameter {param_name}: {e}")

    def setup(self):
        """Run the complete IAM roles setup."""
        print("\n🚀 Starting IAM Roles Setup for Lakehouse Agent")
        print(f"   Region: {self.region}")
        print(f"   Account ID: {self.account_id}")

        # Get S3 bucket name from SSM
        bucket_name = self.get_bucket_name_from_ssm()

        # Create trust policy
        trust_policy = self.get_trust_policy()

        # Create Athena/S3 access policy
        athena_s3_policy = self.get_athena_s3_policy(bucket_name)

        # Create admin policy with additional DynamoDB access
        admin_policy = self.get_admin_policy(bucket_name)

        # Create roles and attach policies
        print("\n👤 Creating group roles...")
        role_arns = {}

        # AWS managed policy ARN for Athena
        athena_full_access_arn = "arn:aws:iam::aws:policy/AmazonAthenaFullAccess"

        for role_name in self.tenant_roles:
            # Create role
            role_arn = self.create_role(role_name, trust_policy)
            role_arns[role_name] = role_arn

            # Attach AWS managed policy for Athena (includes Glue catalog access)
            self.attach_managed_policy(role_name, athena_full_access_arn)

            # Attach appropriate inline policy based on role
            if "administrators" in role_name:
                policy_name = f"{role_name}-admin-access"
                self.attach_inline_policy(role_name, policy_name, admin_policy)
                print("   ✅ Attached admin policy with DynamoDB audit access")
            else:
                policy_name = f"{role_name}-athena-s3-access"
                self.attach_inline_policy(role_name, policy_name, athena_s3_policy)

        # Store role ARNs in SSM
        self.store_role_arns_in_ssm(role_arns)

        print("\n✨ IAM roles setup completed successfully!")
        print("\n🔐 Created roles:")
        for role_name, role_arn in role_arns.items():
            print(f"   - {role_name}")
            print(f"     ARN: {role_arn}")

        print("\n💾 SSM Parameters created:")
        for role_name in self.tenant_roles:
            print(f"   - /app/lakehouse-agent/roles/{role_name}")

        print("\n📋 Permissions granted:")
        print("   All roles:")
        print("     - AmazonAthenaFullAccess (managed policy)")
        print("       • Athena query execution and catalog access")
        print("       • Glue Data Catalog and federated catalog access")
        print("   Policyholders & Adjusters (inline policy):")
        print("     - S3 Tables read access (GetTable, GetTableMetadata, ListTables)")
        print("     - Lake Formation data access (GetDataAccess)")
        print(f"     - S3 data access: s3://{bucket_name}")
        print(f"     - S3 query results: s3://{bucket_name}/athena-results/")
        print("   Administrators (additional inline policy):")
        print("     - DynamoDB login audit access (lakehouse_user_login_audit)")
        print("     - Can query user login history via MCP tool")


def main():
    parser = argparse.ArgumentParser(description="Setup IAM roles for lakehouse agent tenant access control")
    parser.add_argument(
        "--account-id",
        required=False,
        default=None,
        help="AWS Account ID for trust policy (optional, defaults to current account)",
    )

    args = parser.parse_args()

    # Run setup
    setup = IAMRolesSetup(account_id=args.account_id)
    setup.setup()


if __name__ == "__main__":
    main()
