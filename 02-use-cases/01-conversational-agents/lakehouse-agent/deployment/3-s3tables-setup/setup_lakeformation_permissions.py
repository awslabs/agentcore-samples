#!/usr/bin/env python3
"""
Lake Formation Permissions Setup for S3 Tables

This script grants Lake Formation permissions to tenant roles for accessing S3 Tables.
It configures column-level and row-level security by granting appropriate permissions to:
- lakehouse-policyholders-role
- lakehouse-adjusters-role
- lakehouse-administrators-role

Usage:
    python setup_lakeformation_permissions.py
"""

import boto3
import sys
import os
import argparse
from typing import List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session


class LakeFormationSetup:
    def __init__(self):
        """Initialize Lake Formation setup."""
        session, self.region, self.account_id = get_aws_session()

        self.lakeformation = boto3.client("lakeformation", region_name=self.region)
        self.glue = boto3.client("glue", region_name=self.region)
        self.s3tables = boto3.client("s3tables", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)

        # Get configuration from SSM
        self.namespace = self._get_ssm_param("namespace")
        self.table_bucket_arn = self._get_ssm_param("table-bucket-arn")
        self.table_bucket_name = self._get_ssm_param("table-bucket-name")

        # For S3 Tables in Lake Formation (from AWS documentation):
        # CatalogId format: {account_id}:s3tablescatalog/{table_bucket_name}
        # Example from docs: 111122223333:s3tablescatalog/amzn-s3-demo-bucket1
        # DatabaseName: The namespace (e.g., lakehouse_data)
        # Name: The table name (e.g., claims, users)
        self.catalog_id = f"{self.account_id}:s3tablescatalog/{self.table_bucket_name}"

        print(f"   Catalog ID: {self.catalog_id}")
        print(f"   Database (namespace): {self.namespace}")
        print(f"   Table Bucket: {self.table_bucket_name}")

        # Get tenant role ARNs
        self.tenant_roles = {
            "policyholders": self._get_ssm_param("roles/lakehouse-policyholders-role"),
            "adjusters": self._get_ssm_param("roles/lakehouse-adjusters-role"),
            "administrators": self._get_ssm_param("roles/lakehouse-administrators-role"),
        }

    def _get_ssm_param(self, name: str) -> str:
        """Get parameter from SSM."""
        try:
            response = self.ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}")
            return response["Parameter"]["Value"]
        except Exception as e:
            raise Exception(f"Failed to get SSM parameter /app/lakehouse-agent/{name}: {e}")

    def register_s3tables_resource(self):
        """Register S3 Tables bucket as a Lake Formation resource."""
        print("\n📋 Registering S3 Tables bucket with Lake Formation...")

        try:
            self.lakeformation.register_resource(ResourceArn=self.table_bucket_arn, UseServiceLinkedRole=True)
            print(f"✅ Registered S3 Tables bucket: {self.table_bucket_arn}")
        except self.lakeformation.exceptions.AlreadyExistsException:
            print("✅ S3 Tables bucket already registered")
        except Exception as e:
            print(f"ℹ️  Registration note: {e}")

    def create_glue_database(self):
        """Create Glue database for S3 Tables namespace."""
        print("\n📚 Creating Glue database for namespace...")

        try:
            self.glue.create_database(
                DatabaseInput={
                    "Name": self.namespace,
                    "Description": f"Glue database for S3 Tables namespace {self.namespace}",
                    "LocationUri": self.table_bucket_arn,
                }
            )
            print(f"✅ Created Glue database: {self.namespace}")
        except self.glue.exceptions.AlreadyExistsException:
            print(f"✅ Glue database already exists: {self.namespace}")
        except Exception as e:
            print(f"⚠️  Error creating Glue database: {e}")

    def create_glue_table(self, table_name: str, columns: List[Dict[str, str]]):
        """Create Glue table pointing to S3 Tables."""
        print(f"   Creating Glue table: {table_name}...")

        try:
            self.glue.create_table(
                DatabaseName=self.namespace,
                TableInput={
                    "Name": table_name,
                    "StorageDescriptor": {
                        "Columns": columns,
                        "Location": f"{self.table_bucket_arn}/{self.namespace}/{table_name}",
                        "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                        "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                        "SerdeInfo": {"SerializationLibrary": "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"},
                    },
                    "TableType": "EXTERNAL_TABLE",
                    "Parameters": {"EXTERNAL": "TRUE", "table_type": "ICEBERG"},
                },
            )
            print(f"   ✅ Created Glue table: {table_name}")
        except self.glue.exceptions.AlreadyExistsException:
            print(f"   ✅ Glue table already exists: {table_name}")
        except Exception as e:
            print(f"   ⚠️  Error creating Glue table: {e}")

    def grant_database_permissions(self, role_arn: str, role_name: str):
        """Grant database-level permissions to a role."""
        print(f"\n🔐 Granting database permissions to {role_name}...")

        try:
            self.lakeformation.grant_permissions(
                CatalogId=self.account_id,  # Top-level CatalogId is just the account ID
                Principal={"DataLakePrincipalIdentifier": role_arn},
                Resource={
                    "Database": {
                        "CatalogId": self.catalog_id,  # Resource CatalogId uses full format
                        "Name": self.namespace,
                    }
                },
                Permissions=["DESCRIBE"],
                PermissionsWithGrantOption=[],
            )
            print(f"   ✅ Granted DESCRIBE on database {self.namespace}")
        except self.lakeformation.exceptions.AlreadyExistsException:
            print("   ✅ Database permissions already exist")
        except Exception as e:
            print(f"   ⚠️  Error granting database permissions: {e}")

    def grant_table_permissions(self, role_arn: str, role_name: str, table_name: str, permissions: List[str]):
        """Grant table-level permissions to a role."""
        print(f"   Granting {', '.join(permissions)} on table {table_name}...")

        try:
            self.lakeformation.grant_permissions(
                CatalogId=self.account_id,  # Top-level CatalogId is just the account ID
                Principal={"DataLakePrincipalIdentifier": role_arn},
                Resource={
                    "Table": {
                        "CatalogId": self.catalog_id,  # Resource CatalogId uses full format
                        "DatabaseName": self.namespace,
                        "Name": table_name,
                    }
                },
                Permissions=permissions,
                PermissionsWithGrantOption=[],
            )
            print(f"   ✅ Granted permissions on {table_name}")
        except self.lakeformation.exceptions.AlreadyExistsException:
            print(f"   ✅ Permissions already exist for {table_name}")
        except Exception as e:
            print(f"   ⚠️  Error granting table permissions: {e}")

    def grant_column_permissions_with_filter(
        self,
        role_arn: str,
        role_name: str,
        table_name: str,
        columns: List[str],
        row_filter: Dict[str, Any] = None,
    ):
        """Grant column-level permissions with optional row filter."""
        print(f"   Granting column permissions on {table_name} with row filter...")

        resource = {
            "TableWithColumns": {
                "CatalogId": self.catalog_id,  # Resource CatalogId uses full format
                "DatabaseName": self.namespace,
                "Name": table_name,
                "ColumnNames": columns,
            }
        }

        grant_params = {
            "CatalogId": self.account_id,  # Top-level CatalogId is just the account ID
            "Principal": {"DataLakePrincipalIdentifier": role_arn},
            "Resource": resource,
            "Permissions": ["SELECT"],
            "PermissionsWithGrantOption": [],
        }

        try:
            self.lakeformation.grant_permissions(**grant_params)
            print(f"   ✅ Granted column permissions on {table_name}")

            # Apply row filter if provided
            if row_filter:
                self._apply_row_filter(role_arn, table_name, row_filter)

        except self.lakeformation.exceptions.AlreadyExistsException:
            print(f"   ✅ Column permissions already exist for {table_name}")
        except Exception as e:
            print(f"   ⚠️  Error granting column permissions: {e}")

    def grant_column_wildcard_permissions(
        self,
        role_arn: str,
        role_name: str,
        table_name: str,
        excluded_columns: List[str],
        row_filter: Dict[str, Any] = None,
    ):
        """Grant SELECT on all columns except the excluded ones using ColumnWildcard.

        This allows SELECT * to work transparently — Lake Formation silently
        omits the excluded columns from the result set instead of raising
        COLUMN_NOT_FOUND.
        """
        print(f"   Granting wildcard column permissions on {table_name} (excluding {excluded_columns})...")

        resource = {
            "TableWithColumns": {
                "CatalogId": self.catalog_id,
                "DatabaseName": self.namespace,
                "Name": table_name,
                "ColumnWildcard": {"ExcludedColumnNames": excluded_columns},
            }
        }

        grant_params = {
            "CatalogId": self.account_id,
            "Principal": {"DataLakePrincipalIdentifier": role_arn},
            "Resource": resource,
            "Permissions": ["SELECT"],
            "PermissionsWithGrantOption": [],
        }

        try:
            self.lakeformation.grant_permissions(**grant_params)
            print(f"   ✅ Granted wildcard column permissions on {table_name}")

            if row_filter:
                self._apply_row_filter(role_arn, table_name, row_filter)

        except self.lakeformation.exceptions.AlreadyExistsException:
            print(f"   ✅ Wildcard column permissions already exist for {table_name}")
        except Exception as e:
            print(f"   ⚠️  Error granting wildcard column permissions: {e}")

    def _apply_row_filter(self, role_arn: str, table_name: str, row_filter: Dict[str, Any]):
        """Apply row-level filter to a table."""
        try:
            self.lakeformation.create_data_cells_filter(
                TableData={
                    "DatabaseName": self.namespace,
                    "TableName": table_name,
                    "Name": f"{table_name}_filter_{role_arn.split('/')[-1]}",
                    "RowFilter": row_filter,
                }
            )
            print(f"   ✅ Applied row filter to {table_name}")
        except self.lakeformation.exceptions.AlreadyExistsException:
            print(f"   ✅ Row filter already exists for {table_name}")
        except Exception as e:
            print(f"   ⚠️  Note on row filter: {e}")

    def setup_permissions(self):
        """Setup Lake Formation permissions for all tenant roles."""
        print("\n🚀 Setting up Lake Formation permissions for S3 Tables")
        print(f"   Region: {self.region}")
        print(f"   Database: {self.namespace}")
        print(f"   Table Bucket: {self.table_bucket_arn}")

        # Register S3 Tables resource
        self.register_s3tables_resource()

        # Define column sets for claims table

        # Restricted columns for policyholders (excludes sensitive internal columns)
        # Excluded: adjuster_user_id, created_by, last_modified_by, last_modified_date, notes, denial_reason
        claims_columns_policyholder = [
            "claim_id",
            "user_id",
            "policyholder_name",
            "policyholder_dob",
            "claim_date",
            "claim_amount",
            "claim_type",
            "claim_status",
            "provider_name",
            "provider_npi",
            "diagnosis_code",
            "procedure_code",
            "submitted_date",
            "processed_date",
            "approved_amount",
        ]

        # Users table columns (same for all roles)
        users_columns = [
            "user_id",
            "user_name",
            "user_role",
            "department",
            "created_date",
        ]

        # Setup permissions for each role
        for role_type, role_arn in self.tenant_roles.items():
            print(f"\n👤 Setting up permissions for {role_type}...")

            # Grant database permissions
            self.grant_database_permissions(role_arn, role_type)

            # Grant table permissions
            if role_type == "administrators":
                # Admins get full access on all tables (including INSERT for data loading)
                print("   📊 Granting admin permissions...")
                self.grant_table_permissions(
                    role_arn,
                    role_type,
                    "claims",
                    ["SELECT", "DESCRIBE", "INSERT", "ALTER", "DELETE"],
                )
                self.grant_table_permissions(
                    role_arn,
                    role_type,
                    "users",
                    ["SELECT", "DESCRIBE", "INSERT", "ALTER", "DELETE"],
                )
            elif role_type == "policyholders":
                # Policyholders get restricted column access (no sensitive internal columns)
                print("   📊 Granting column-level permissions (restricted)...")
                print(f"      Columns: {len(claims_columns_policyholder)}/21 (excluding internal operational data)")
                self.grant_column_permissions_with_filter(role_arn, role_type, "claims", claims_columns_policyholder)
                self.grant_column_permissions_with_filter(role_arn, role_type, "users", users_columns)
            elif role_type == "adjusters":
                # Adjusters get all columns except policyholder_dob (PII protection)
                # Using ColumnWildcard so SELECT * works transparently
                print("   📊 Granting column-level permissions (excluding policyholder_dob)...")
                self.grant_column_wildcard_permissions(
                    role_arn, role_type, "claims", excluded_columns=["policyholder_dob"]
                )
                self.grant_column_permissions_with_filter(role_arn, role_type, "users", users_columns)

        print("\n✨ Lake Formation permissions setup complete!")
        print("\n📋 Permissions granted:")
        print("   Policyholders: SELECT on claims (15 columns - excludes internal data), SELECT on users")
        print(
            "      Excluded columns: adjuster_user_id, created_by, last_modified_by, last_modified_date, notes, denial_reason"
        )
        print("   Adjusters: SELECT on claims (20 columns - excludes policyholder_dob), SELECT on users")
        print("   Administrators: Full access on all tables (SELECT, INSERT, ALTER, DELETE)")

        print("\n⚠️  Note: Row-level security filters need to be configured manually")
        print("   in Lake Formation console for policyholders and adjusters roles.")
        print("   Filter expression example: user_id = '{{SESSION_CONTEXT:user_id}}'")


def main():
    parser = argparse.ArgumentParser(description="Setup Lake Formation permissions for S3 Tables tenant access")

    parser.parse_args()

    # Run setup
    setup = LakeFormationSetup()
    setup.setup_permissions()


if __name__ == "__main__":
    main()
