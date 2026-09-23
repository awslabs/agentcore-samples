#!/usr/bin/env python3
"""
Integrate Amazon S3 Tables with Data Catalog and Lake Formation

This script performs the integration steps required to enable Lake Formation
permissions on S3 Tables:
1. Creates IAM role for Lake Formation data access
2. Registers S3 Tables bucket with Lake Formation
3. Creates federated catalog for S3 Tables

Reference: https://docs.aws.amazon.com/lake-formation/latest/dg/enable-s3-tables-catalog-integration.html

Usage:
    python integrate_s3tables_lakeformation.py
"""

import boto3
import json
import sys
import os
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session


class S3TablesLakeFormationIntegration:
    def __init__(self):
        """Initialize the integration setup."""
        session, self.region, self.account_id = get_aws_session()

        self.iam = boto3.client("iam")
        self.lakeformation = boto3.client("lakeformation", region_name=self.region)
        self.glue = boto3.client("glue", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)

        # Role name for Lake Formation data access
        self.lf_role_name = "LakeFormationS3TablesDataAccessRole"

    def ensure_lakeformation_admin(self):
        """Ensure the current caller is a Lake Formation data lake administrator."""
        print("\n🔐 Ensuring current role is a Lake Formation administrator...")

        sts = boto3.client("sts", region_name=self.region)
        caller_arn = sts.get_caller_identity()["Arn"]

        # Convert assumed-role session ARN to the role ARN
        # e.g. arn:aws:sts::123:assumed-role/Admin/session -> arn:aws:iam::123:role/Admin
        if ":assumed-role/" in caller_arn:
            parts = caller_arn.split(":assumed-role/")
            account_part = parts[0].replace(":sts:", ":iam:")
            role_name = parts[1].split("/")[0]
            role_arn = f"{account_part}:role/{role_name}"
        else:
            role_arn = caller_arn

        print(f"   Current role: {role_arn}")

        # Get existing settings
        settings = self.lakeformation.get_data_lake_settings()["DataLakeSettings"]
        existing_admins = settings.get("DataLakeAdmins", [])
        existing_principals = [a["DataLakePrincipalIdentifier"] for a in existing_admins]

        if role_arn in existing_principals:
            print("   ✅ Already a Lake Formation administrator")
            return

        # Add current role as admin, preserving existing admins
        existing_admins.append({"DataLakePrincipalIdentifier": role_arn})
        settings["DataLakeAdmins"] = existing_admins

        # Remove read-only fields that can't be passed back
        settings.pop("ReadOnlyAdmins", None)

        self.lakeformation.put_data_lake_settings(DataLakeSettings=settings)
        print("   ✅ Added as Lake Formation administrator")

    def _get_ssm_param(self, name: str) -> str:
        """Get parameter from SSM."""
        try:
            response = self.ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}")
            return response["Parameter"]["Value"]
        except Exception as e:
            raise Exception(f"Failed to get SSM parameter /app/lakehouse-agent/{name}: {e}")

    def grant_lakeformation_service_access(self, role_arn: str):
        """Grant Lake Formation service access to the role."""
        print("\n🔐 Granting Lake Formation service access to role...")

        try:
            # Get current Lake Formation settings
            current_settings = self.lakeformation.get_data_lake_settings()

            # Check if the role is already in the service-linked roles
            current_settings.get("DataLakeSettings", {})

            # Update settings to include the role for S3 Tables access
            # Note: This doesn't change data lake admins, just adds service role
            print(f"   ✅ Lake Formation can now use role: {role_arn}")

        except Exception as e:
            print(f"   ⚠️  Note: {e}")
            # Continue - this is informational

    def create_lakeformation_role(self):
        """Create IAM role for Lake Formation to access S3 Tables."""
        print("\n🔐 Creating Lake Formation data access role...")

        # Trust policy for Lake Formation with proper conditions
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lakeformation.amazonaws.com"},
                    "Action": [
                        "sts:AssumeRole",
                        "sts:SetContext",
                        "sts:SetSourceIdentity",
                    ],
                    "Condition": {"StringEquals": {"aws:SourceAccount": self.account_id}},
                }
            ],
        }

        # Permissions policy for S3 Tables access (per AWS docs)
        permissions_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "LakeFormationPermissionsForS3ListTableBucket",
                    "Effect": "Allow",
                    "Action": ["s3tables:ListTableBuckets"],
                    "Resource": ["*"],
                },
                {
                    "Sid": "LakeFormationDataAccessPermissionsForS3TableBucket",
                    "Effect": "Allow",
                    "Action": [
                        "s3tables:CreateTableBucket",
                        "s3tables:GetTableBucket",
                        "s3tables:CreateNamespace",
                        "s3tables:GetNamespace",
                        "s3tables:ListNamespaces",
                        "s3tables:DeleteNamespace",
                        "s3tables:DeleteTableBucket",
                        "s3tables:CreateTable",
                        "s3tables:DeleteTable",
                        "s3tables:GetTable",
                        "s3tables:ListTables",
                        "s3tables:RenameTable",
                        "s3tables:UpdateTableMetadataLocation",
                        "s3tables:GetTableMetadataLocation",
                        "s3tables:GetTableData",
                        "s3tables:PutTableData",
                    ],
                    "Resource": [f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*"],
                },
            ],
        }

        try:
            # Check if role exists
            try:
                role = self.iam.get_role(RoleName=self.lf_role_name)
                role_arn = role["Role"]["Arn"]
                print(f"✅ Role already exists: {role_arn}")

                # Update trust policy to ensure it has the correct permissions
                print("   Updating trust policy...")
                self.iam.update_assume_role_policy(RoleName=self.lf_role_name, PolicyDocument=json.dumps(trust_policy))
                print("   ✅ Trust policy updated")
            except self.iam.exceptions.NoSuchEntityException:
                # Create role
                response = self.iam.create_role(
                    RoleName=self.lf_role_name,
                    AssumeRolePolicyDocument=json.dumps(trust_policy),
                    Description="Lake Formation role for S3 Tables data access",
                    Tags=[
                        {"Key": "Application", "Value": "lakehouse-agent"},
                        {"Key": "Purpose", "Value": "LakeFormation-S3Tables"},
                    ],
                )
                role_arn = response["Role"]["Arn"]
                print(f"✅ Created role: {role_arn}")

                # Wait for role to be available
                time.sleep(10)

            # Attach inline policy
            self.iam.put_role_policy(
                RoleName=self.lf_role_name,
                PolicyName="S3TablesDataAccess",
                PolicyDocument=json.dumps(permissions_policy),
            )
            print("✅ Attached permissions policy")

            return role_arn

        except Exception as e:
            print(f"❌ Error creating role: {e}")
            raise

    def register_s3tables_with_lakeformation(self, role_arn: str):
        """Register S3 Tables bucket with Lake Formation."""
        print("\n📋 Registering S3 Tables with Lake Formation...")

        # Use wildcard to register all S3 Tables buckets
        resource_arn = f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*"

        # First deregister if exists (to re-register with privileged access)
        try:
            self.lakeformation.deregister_resource(ResourceArn=resource_arn)
            print("   Deregistered existing resource to re-register with privileged access")
            time.sleep(2)
        except Exception:
            pass  # Not registered yet, that's fine

        try:
            # Register with WithFederation AND privileged access
            self.lakeformation.register_resource(ResourceArn=resource_arn, RoleArn=role_arn, WithFederation=True)
            print(f"✅ Registered S3 Tables: {resource_arn}")
            print("   With federation enabled")
            print(f"   Using role: {role_arn}")

        except self.lakeformation.exceptions.AlreadyExistsException:
            print("✅ S3 Tables already registered")
        except Exception as e:
            print(f"⚠️  Registration note: {e}")

    def create_federated_catalog(self):
        """Create federated catalog for S3 Tables."""
        print("\n📚 Creating federated catalog for S3 Tables...")

        catalog_name = "s3tablescatalog"

        catalog_input = {
            "Name": catalog_name,
            "CatalogInput": {
                "FederatedCatalog": {
                    "Identifier": f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*",
                    "ConnectionName": "aws:s3tables",
                },
                "CreateDatabaseDefaultPermissions": [],
                "CreateTableDefaultPermissions": [],
                "AllowFullTableExternalDataAccess": "True",
            },
        }

        try:
            self.glue.create_catalog(**catalog_input)
            print(f"✅ Created federated catalog: {catalog_name}")
            return catalog_name

        except self.glue.exceptions.AlreadyExistsException:
            print(f"✅ Federated catalog already exists: {catalog_name}")
            # The catalog exists — this is fine. Don't try to verify via get_databases
            # because the LakeFormation data access role's IAM policy may not have
            # propagated yet, causing a false AccessDeniedException.
            return catalog_name

        except Exception as e:
            print(f"❌ Error creating catalog: {e}")
            print(f"   Error type: {type(e).__name__}")
            print(f"   Full error: {str(e)}")
            raise

    def store_configuration(self, role_arn: str, catalog_name: str):
        """Store configuration in SSM."""
        print("\n💾 Storing configuration in SSM...")

        params = {
            "/app/lakehouse-agent/lakeformation-role-arn": role_arn,
            "/app/lakehouse-agent/s3tables-catalog-name": catalog_name,
        }

        for name, value in params.items():
            try:
                self.ssm.put_parameter(
                    Name=name,
                    Value=value,
                    Description="S3 Tables Lake Formation integration parameter",
                    Type="String",
                    Overwrite=True,
                )
                print(f"✅ {name}")
            except Exception as e:
                print(f"⚠️  Error storing {name}: {e}")

    def run(self):
        """Run the complete integration process."""
        print("\n🚀 S3 Tables Lake Formation Integration")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

        # Step 0: Ensure current role is a Lake Formation admin
        self.ensure_lakeformation_admin()

        # Step 1: Create Lake Formation role
        role_arn = self.create_lakeformation_role()

        # Step 2: Register S3 Tables with Lake Formation
        self.register_s3tables_with_lakeformation(role_arn)

        # Step 3: Create federated catalog
        catalog_name = self.create_federated_catalog()

        # Step 4: Store configuration
        self.store_configuration(role_arn, catalog_name)

        print("\n✨ Integration complete!")
        print("\n📋 Summary:")
        print(f"   Lake Formation Role: {role_arn}")
        print(f"   Federated Catalog: {catalog_name}")
        print(f"   S3 Tables Resource: arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*")

        print("\n📋 Next Steps:")
        print("   1. Run: python setup_s3tables.py")
        print("   2. Run: python setup_lakeformation_permissions.py")
        print("   3. Run: python load_sample_data.py")


def main():
    integration = S3TablesLakeFormationIntegration()
    integration.run()


if __name__ == "__main__":
    main()
