#!/usr/bin/env python3
"""
S3 Tables Setup - Replaces Athena with S3 Tables (Apache Iceberg)

This script creates S3 Tables infrastructure using the MCP tools available.
It provides better performance, ACID transactions, and native Iceberg support.

Usage:
    python setup_s3tables.py --table-bucket-name my-lakehouse
"""

import boto3
import sys
import os
import argparse
import random
import string

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session


def generate_random_suffix(length=6):
    """Generate a random alphanumeric suffix."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def main():
    parser = argparse.ArgumentParser(description="Setup S3 Tables for lakehouse")
    parser.add_argument(
        "--table-bucket-name",
        required=False,
        default=None,
        help="S3 table bucket name (default: lakehouse-{account_id}-{random})",
    )
    args = parser.parse_args()

    # Get AWS session
    session, region, account_id = get_aws_session()

    # Use default table bucket name with random suffix if not provided
    if args.table_bucket_name:
        table_bucket_name = args.table_bucket_name
    else:
        random_suffix = generate_random_suffix()
        table_bucket_name = f"lakehouse-{account_id}-{random_suffix}"

    print("\n🚀 S3 Tables Setup")
    print(f"   Region: {region}")
    print(f"   Account ID: {account_id}")
    print(f"   Table Bucket: {table_bucket_name}")

    # Initialize clients
    s3tables = boto3.client("s3tables", region_name=region)
    ssm = boto3.client("ssm", region_name=region)

    # Step 1: Create table bucket
    print("\n📦 Creating S3 table bucket...")

    table_bucket_arn = None

    try:
        response = s3tables.create_table_bucket(name=table_bucket_name)
        table_bucket_arn = response.get("arn")
        if table_bucket_arn:
            print(f"✅ Created table bucket: {table_bucket_arn}")
        else:
            print(f"⚠️  Bucket created but no ARN returned. Response: {response}")
            table_bucket_arn = f"arn:aws:s3tables:{region}:{account_id}:bucket/{table_bucket_name}"
            print(f"   Using constructed ARN: {table_bucket_arn}")
    except s3tables.exceptions.ConflictException:
        # Get existing bucket ARN
        print("   Bucket already exists, retrieving ARN...")
        try:
            response = s3tables.get_table_bucket(name=table_bucket_name)
            table_bucket_arn = response.get("arn")
            if table_bucket_arn:
                print(f"✅ Table bucket exists: {table_bucket_arn}")
            else:
                print(f"⚠️  Could not get ARN from response: {response}")
                table_bucket_arn = f"arn:aws:s3tables:{region}:{account_id}:bucket/{table_bucket_name}"
                print(f"   Using constructed ARN: {table_bucket_arn}")
        except Exception as e:
            print(f"⚠️  Error getting bucket: {e}")
            # Fallback: construct ARN manually
            table_bucket_arn = f"arn:aws:s3tables:{region}:{account_id}:bucket/{table_bucket_name}"
            print(f"   Using constructed ARN: {table_bucket_arn}")
    except Exception as e:
        print(f"❌ Error creating table bucket: {e}")
        print(f"   Error type: {type(e).__name__}")
        raise

    if not table_bucket_arn:
        raise Exception("Failed to create or retrieve table bucket ARN")

    # Step 2: Create namespace
    namespace = "lakehouse_data"
    print(f"\n📁 Creating namespace: {namespace}")

    try:
        s3tables.create_namespace(tableBucketARN=table_bucket_arn, namespace=[namespace])
        print("✅ Created namespace")
    except s3tables.exceptions.ConflictException:
        print("✅ Namespace exists")

    # Step 3: Create claims table
    print("\n📊 Creating claims table...")

    claims_metadata = {
        "iceberg": {
            "schema": {
                "fields": [
                    {"name": "claim_id", "type": "string", "required": True},
                    {"name": "user_id", "type": "string", "required": True},
                    {"name": "policyholder_name", "type": "string", "required": False},
                    {"name": "policyholder_dob", "type": "date", "required": False},
                    {"name": "claim_date", "type": "date", "required": False},
                    {
                        "name": "claim_amount",
                        "type": "decimal(10,2)",
                        "required": False,
                    },
                    {"name": "claim_type", "type": "string", "required": False},
                    {"name": "claim_status", "type": "string", "required": False},
                    {"name": "provider_name", "type": "string", "required": False},
                    {"name": "provider_npi", "type": "string", "required": False},
                    {"name": "diagnosis_code", "type": "string", "required": False},
                    {"name": "procedure_code", "type": "string", "required": False},
                    {"name": "submitted_date", "type": "timestamp", "required": False},
                    {"name": "processed_date", "type": "timestamp", "required": False},
                    {
                        "name": "approved_amount",
                        "type": "decimal(10,2)",
                        "required": False,
                    },
                    {"name": "denial_reason", "type": "string", "required": False},
                    {"name": "notes", "type": "string", "required": False},
                    {"name": "created_by", "type": "string", "required": False},
                    {"name": "last_modified_by", "type": "string", "required": False},
                    {
                        "name": "last_modified_date",
                        "type": "timestamp",
                        "required": False,
                    },
                    {"name": "adjuster_user_id", "type": "string", "required": False},
                ]
            }
        }
    }

    try:
        s3tables.create_table(
            tableBucketARN=table_bucket_arn,
            namespace=namespace,
            name="claims",
            format="ICEBERG",
            metadata=claims_metadata,
        )
        print("✅ Created claims table")
    except s3tables.exceptions.ConflictException:
        print("✅ Claims table exists")

    # Step 4: Create users table
    print("\n👥 Creating users table...")

    users_metadata = {
        "iceberg": {
            "schema": {
                "fields": [
                    {"name": "user_id", "type": "string", "required": True},
                    {"name": "user_name", "type": "string", "required": False},
                    {"name": "user_role", "type": "string", "required": False},
                    {"name": "department", "type": "string", "required": False},
                    {"name": "created_date", "type": "timestamp", "required": False},
                ]
            }
        }
    }

    try:
        s3tables.create_table(
            tableBucketARN=table_bucket_arn,
            namespace=namespace,
            name="users",
            format="ICEBERG",
            metadata=users_metadata,
        )
        print("✅ Created users table")
    except s3tables.exceptions.ConflictException:
        print("✅ Users table exists")

    # Step 5: Create or get S3 bucket for Athena query results
    print("\n📦 Setting up S3 bucket for query results...")
    s3 = boto3.client("s3", region_name=region)

    # Use a consistent bucket name format
    s3_bucket_name = f"{account_id}-{region}-lakehouse-agent"

    try:
        create_params = {"Bucket": s3_bucket_name}
        if region != "us-east-1":
            create_params["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**create_params)
        print(f"✅ Created S3 bucket: {s3_bucket_name}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"✅ S3 bucket exists: {s3_bucket_name}")
    except Exception as e:
        print(f"⚠️  S3 bucket creation note: {e}")

    # Step 6: Store configuration in SSM
    print("\n💾 Storing configuration in SSM...")

    # Extract table bucket name from ARN
    table_bucket_name = table_bucket_arn.split("/")[-1]

    # S3 Tables structure in Glue:
    # - Catalog: s3tablescatalog/{table_bucket_name}
    # - Database: {namespace}
    catalog_name = f"s3tablescatalog/{table_bucket_name}"

    params = {
        "/app/lakehouse-agent/table-bucket-name": table_bucket_name,
        "/app/lakehouse-agent/table-bucket-arn": table_bucket_arn,
        "/app/lakehouse-agent/namespace": namespace,
        "/app/lakehouse-agent/database-name": namespace,  # Database name for compatibility
        "/app/lakehouse-agent/warehouse-location": table_bucket_arn,
        "/app/lakehouse-agent/catalog-uri": f"https://s3tables.{region}.amazonaws.com/iceberg",
        "/app/lakehouse-agent/catalog-name": catalog_name,  # For Athena queries
        "/app/lakehouse-agent/s3-bucket-name": s3_bucket_name,  # For query results
    }

    for name, value in params.items():
        ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
        print(f"✅ {name}")

    print("\n✨ S3 Tables setup complete!")
    print("\n📋 Configuration:")
    print(f"   Table Bucket: {table_bucket_name}")
    print(f"   Namespace/Database: {namespace}")
    print(f"   Catalog: {catalog_name}")
    print(f"   S3 Bucket: {s3_bucket_name}")
    print("\n💾 SSM Parameters:")
    print("   /app/lakehouse-agent/table-bucket-name")
    print("   /app/lakehouse-agent/table-bucket-arn")
    print("   /app/lakehouse-agent/namespace")
    print("   /app/lakehouse-agent/database-name")
    print("   /app/lakehouse-agent/catalog-name")
    print("   /app/lakehouse-agent/s3-bucket-name")
    print("\n📋 Query via Athena:")
    print(f"   SELECT * FROM {catalog_name}.{namespace}.claims;")
    print("\n📋 Next Steps:")
    print("   1. Run: python setup_lakeformation_permissions.py")
    print("   2. Run: python load_sample_data.py")
    print("   3. Test queries with Athena")


if __name__ == "__main__":
    main()
