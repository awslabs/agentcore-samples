#!/usr/bin/env python3
"""
Cleanup S3 Tables Resources

This script removes S3 Tables resources to allow for a fresh deployment:
- Deletes S3 Tables (claims, users)
- Deletes S3 Tables namespace
- Deletes S3 Tables bucket
- Removes federated catalog from Glue
- Unregisters S3 Tables from Lake Formation
- Optionally removes SSM parameters

Usage:
    python cleanup_s3tables.py [--keep-ssm]
"""

import boto3
import sys
import os
import argparse
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session


class S3TablesCleanup:
    def __init__(self, keep_ssm=False):
        """Initialize the cleanup."""
        session, self.region, self.account_id = get_aws_session()

        self.s3tables = boto3.client("s3tables", region_name=self.region)
        self.glue = boto3.client("glue", region_name=self.region)
        self.lakeformation = boto3.client("lakeformation", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)

        self.keep_ssm = keep_ssm

    def _get_ssm_param(self, name: str, default=None) -> str:
        """Get parameter from SSM."""
        try:
            response = self.ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}")
            return response["Parameter"]["Value"]
        except Exception:
            return default

    def delete_tables(self):
        """Delete S3 Tables."""
        print("\n🗑️  Deleting S3 Tables...")

        table_bucket_name = self._get_ssm_param("table-bucket-name")
        namespace = self._get_ssm_param("namespace")

        if not table_bucket_name or not namespace:
            print("   ⚠️  No table bucket or namespace found in SSM, skipping")
            return

        tables = ["claims", "users"]

        for table_name in tables:
            try:
                self.s3tables.delete_table(
                    tableBucketARN=f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/{table_bucket_name}",
                    namespace=namespace,
                    name=table_name,
                )
                print(f"   ✅ Deleted table: {table_name}")
            except self.s3tables.exceptions.NotFoundException:
                print(f"   ⚠️  Table not found: {table_name}")
            except Exception as e:
                print(f"   ❌ Error deleting table {table_name}: {e}")

        # Wait a bit for tables to be deleted
        time.sleep(2)

    def delete_namespace(self):
        """Delete S3 Tables namespace."""
        print("\n🗑️  Deleting namespace...")

        table_bucket_name = self._get_ssm_param("table-bucket-name")
        namespace = self._get_ssm_param("namespace")

        if not table_bucket_name or not namespace:
            print("   ⚠️  No table bucket or namespace found in SSM, skipping")
            return

        try:
            self.s3tables.delete_namespace(
                tableBucketARN=f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/{table_bucket_name}",
                namespace=namespace,
            )
            print(f"   ✅ Deleted namespace: {namespace}")
        except self.s3tables.exceptions.NotFoundException:
            print(f"   ⚠️  Namespace not found: {namespace}")
        except Exception as e:
            print(f"   ❌ Error deleting namespace: {e}")

        # Wait a bit for namespace to be deleted
        time.sleep(2)

    def delete_table_bucket(self):
        """Delete S3 Tables bucket."""
        print("\n🗑️  Deleting S3 Tables bucket...")

        table_bucket_name = self._get_ssm_param("table-bucket-name")

        if not table_bucket_name:
            print("   ⚠️  No table bucket found in SSM, skipping")
            return

        try:
            self.s3tables.delete_table_bucket(
                tableBucketARN=f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/{table_bucket_name}"
            )
            print(f"   ✅ Deleted S3 Tables bucket: {table_bucket_name}")
        except self.s3tables.exceptions.NotFoundException:
            print(f"   ⚠️  S3 Tables bucket not found: {table_bucket_name}")
        except Exception as e:
            print(f"   ❌ Error deleting S3 Tables bucket: {e}")

    def delete_federated_catalog(self):
        """Delete federated catalog from Glue."""
        print("\n🗑️  Deleting federated catalog...")

        catalog_name = "s3tablescatalog"

        try:
            self.glue.delete_catalog(CatalogId=catalog_name)
            print(f"   ✅ Deleted catalog: {catalog_name}")
        except self.glue.exceptions.EntityNotFoundException:
            print(f"   ⚠️  Catalog not found: {catalog_name}")
        except Exception as e:
            print(f"   ❌ Error deleting catalog: {e}")

    def unregister_from_lakeformation(self):
        """Unregister S3 Tables from Lake Formation."""
        print("\n🗑️  Unregistering from Lake Formation...")

        resource_arn = f"arn:aws:s3tables:{self.region}:{self.account_id}:bucket/*"

        try:
            self.lakeformation.deregister_resource(ResourceArn=resource_arn)
            print(f"   ✅ Unregistered: {resource_arn}")
        except self.lakeformation.exceptions.EntityNotFoundException:
            print(f"   ⚠️  Resource not registered: {resource_arn}")
        except Exception as e:
            print(f"   ⚠️  Note: {e}")

    def delete_ssm_parameters(self):
        """Delete SSM parameters."""
        if self.keep_ssm:
            print("\n⏭️  Keeping SSM parameters (--keep-ssm flag)")
            return

        print("\n🗑️  Deleting SSM parameters...")

        params_to_delete = [
            "table-bucket-name",
            "table-bucket-arn",
            "namespace",
            "catalog-name",
            "s3tables-catalog-name",
            "lakeformation-role-arn",
        ]

        for param in params_to_delete:
            try:
                self.ssm.delete_parameter(Name=f"/app/lakehouse-agent/{param}")
                print(f"   ✅ Deleted: /app/lakehouse-agent/{param}")
            except self.ssm.exceptions.ParameterNotFound:
                print(f"   ⚠️  Not found: /app/lakehouse-agent/{param}")
            except Exception as e:
                print(f"   ❌ Error deleting {param}: {e}")

    def run(self):
        """Run the complete cleanup process."""
        print("\n🧹 S3 Tables Cleanup")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

        # Step 1: Delete tables
        self.delete_tables()

        # Step 2: Delete namespace
        self.delete_namespace()

        # Step 3: Delete S3 Tables bucket
        self.delete_table_bucket()

        # Step 4: Delete federated catalog (OPTIONAL)
        # self.delete_federated_catalog()

        # Step 5: Unregister from Lake Formation
        self.unregister_from_lakeformation()

        # Step 6: Delete SSM parameters (optional)
        self.delete_ssm_parameters()

        print("\n✨ Cleanup complete!")
        print("\n📋 Next Steps:")
        print("   1. Verify Lake Formation admin permissions are still set")
        print("   2. Run: python integrate_s3tables_lakeformation.py")
        print("   3. Run: python setup_s3tables.py")
        print("   4. Run: python load_sample_data.py")
        print("   5. Run: python setup_lakeformation_permissions.py")


def main():
    parser = argparse.ArgumentParser(description="Cleanup S3 Tables resources")
    parser.add_argument(
        "--keep-ssm",
        action="store_true",
        help="Keep SSM parameters (useful for re-deployment)",
    )

    args = parser.parse_args()

    cleanup = S3TablesCleanup(keep_ssm=args.keep_ssm)
    cleanup.run()


if __name__ == "__main__":
    main()
