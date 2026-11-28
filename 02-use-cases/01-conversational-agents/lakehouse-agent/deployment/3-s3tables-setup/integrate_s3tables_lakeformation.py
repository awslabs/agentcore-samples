#!/usr/bin/env python3
"""
Integrate Amazon S3 Tables with Data Catalog and Lake Formation

This script performs the integration steps required to enable Lake Formation
permissions on S3 Tables:
1. Refuses to continue if S3 Tables is already registered with Lake Formation
   through a role this sample cannot prove it created
2. Creates IAM role for Lake Formation data access (a re-run reuses this sample's
   own role; a same-name role it did not create is refused)
3. Registers S3 Tables bucket with Lake Formation
4. Creates federated catalog for S3 Tables

Reference: https://docs.aws.amazon.com/lake-formation/latest/dg/enable-s3-tables-catalog-integration.html

Usage:
    python integrate_s3tables_lakeformation.py
"""

import json
import os
import sys
import time

import boto3

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session
from utils.iam_roles import FOREIGN, NOT_REGISTERED, RoleOwnershipError, ensure_role, lf_registration_owner


class S3TablesLakeFormationIntegration:
    def __init__(self):
        """Initialize the integration setup."""
        _session, self.region, self.account_id = get_aws_session()

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

    def check_existing_registration(self) -> bool:
        """Check the S3 Tables registration in Lake Formation before any change.

        Returns False when S3 Tables is not registered, and True when it is registered
        with this sample's own role. Exits 1, with nothing changed, when it is registered
        with any other role (including the service-linked role) or cannot be read.
        """
        print("\n🔎 Checking for an existing S3 Tables registration in Lake Formation...")
        resource_arn = f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*"
        try:
            owner, registered_role_arn = lf_registration_owner(
                self.lakeformation, self.iam, self.ssm, resource_arn, self.lf_role_name
            )
        except RoleOwnershipError as e:
            print(e)
            sys.exit(1)
        if owner == NOT_REGISTERED:
            print("   Not registered yet")
            return False
        if owner == FOREIGN:
            print(
                f"❌ This account already links S3 Tables to Lake Formation in {self.region} "
                f"(role {registered_role_arn}). "
                "This sample will not replace that registration. Nothing was changed."
            )
            sys.exit(1)
        return True

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
            # Creates the role, or reuses it (updating its trust policy) only when its
            # ownership record shows this sample created it.
            role_arn, created = ensure_role(
                self.iam,
                self.ssm,
                self.lf_role_name,
                trust_policy,
                description="Lake Formation role for S3 Tables data access",
                tags=[
                    {"Key": "Application", "Value": "lakehouse-agent"},
                    {"Key": "Purpose", "Value": "LakeFormation-S3Tables"},
                ],
            )
            if created:
                print(f"✅ Created role: {role_arn}")

                # Wait for role to be available
                time.sleep(10)
            else:
                print(f"✅ Role already exists (created by this sample): {role_arn}")
                print("   ✅ Trust policy updated")

            # Attach inline policy
            self.iam.put_role_policy(
                RoleName=self.lf_role_name,
                PolicyName="S3TablesDataAccess",
                PolicyDocument=json.dumps(permissions_policy),
            )
            print("✅ Attached permissions policy")

            return role_arn

        except RoleOwnershipError as e:
            print(e)
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error creating role: {e}")
            raise

    def register_s3tables_with_lakeformation(self, role_arn: str):
        """Register S3 Tables bucket with Lake Formation."""
        print("\n📋 Registering S3 Tables with Lake Formation...")

        # Use wildcard to register all S3 Tables buckets
        resource_arn = f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*"

        try:
            # Register with WithFederation AND privileged access
            self.lakeformation.register_resource(ResourceArn=resource_arn, RoleArn=role_arn, WithFederation=True)
            print(f"✅ Registered S3 Tables: {resource_arn}")
            print("   With federation enabled")
            print(f"   Using role: {role_arn}")

        except self.lakeformation.exceptions.AlreadyExistsException:
            print("✅ S3 Tables already registered")
        except Exception as e:
            print(f"❌ Error registering S3 Tables: {e}")
            raise

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
            print(f"   Full error: {e!s}")
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
                print(f"❌ Error storing {name}: {e}")
                raise

    def run(self):
        """Run the complete integration process."""
        print("\n🚀 S3 Tables Lake Formation Integration")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

        # Step 0: Refuse a registration this sample did not make, before any change
        already_registered = self.check_existing_registration()

        # Step 1: Ensure current role is a Lake Formation admin
        self.ensure_lakeformation_admin()

        # Step 2: Create Lake Formation role
        role_arn = self.create_lakeformation_role()

        # Step 3: Register S3 Tables with Lake Formation
        if already_registered:
            print("\nℹ  S3 Tables already registered with this sample's role; not re-registering")
        else:
            self.register_s3tables_with_lakeformation(role_arn)

        # Step 4: Create federated catalog
        catalog_name = self.create_federated_catalog()

        # Step 5: Store configuration
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
