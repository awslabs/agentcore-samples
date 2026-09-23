#!/usr/bin/env python3
"""
Load Sample Data into S3 Tables

This script loads sample claims and users data into S3 Tables using Athena INSERT statements.
"""

import boto3
import sys
import os
import time

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session


def wait_for_query(athena, query_id, max_wait=120):
    """Wait for Athena query to complete."""
    for _ in range(max_wait):
        response = athena.get_query_execution(QueryExecutionId=query_id)
        status = response["QueryExecution"]["Status"]["State"]

        if status in ["SUCCEEDED", "FAILED", "CANCELLED"]:
            return response

        time.sleep(1)

    return None


def execute_query(athena, query, catalog, database, output_location, retries=2):
    """Execute an Athena query and wait for completion. Retries on transient failures."""
    for attempt in range(retries + 1):
        response = athena.start_query_execution(
            QueryString=query,
            QueryExecutionContext={"Catalog": catalog, "Database": database},
            ResultConfiguration={"OutputLocation": output_location},
        )

        query_id = response["QueryExecutionId"]
        print(f"      Query ID: {query_id}")

        result = wait_for_query(athena, query_id)

        if not result:
            try:
                current = athena.get_query_execution(QueryExecutionId=query_id)
                print(f"      Status: {current['QueryExecution']['Status']}")
            except Exception:
                pass
            raise Exception("Query timeout after 120 seconds")

        status = result["QueryExecution"]["Status"]["State"]

        if status == "SUCCEEDED":
            return query_id

        error_msg = result["QueryExecution"]["Status"].get("StateChangeReason", "Unknown error")
        athena_error = result["QueryExecution"]["Status"].get("AthenaError", {})
        if athena_error:
            error_category = athena_error.get("ErrorCategory", "")
            error_message = athena_error.get("ErrorMessage", error_msg)
            error_type = athena_error.get("ErrorType", "")
            error_msg = f"{error_category} ({error_type}): {error_message}"

        if attempt < retries:
            print(f"      ⚠️  Attempt {attempt + 1} failed: {error_msg}")
            print("      🔄 Retrying in 5 seconds...")
            time.sleep(5)
        else:
            print(f"      ❌ Query execution failed after {retries + 1} attempts:")
            print(f"         Status: {status}")
            print(f"         Error: {error_msg}")
            raise Exception(f"Query failed: {error_msg}")


def main():
    print("\n📤 Loading sample data into S3 Tables...")

    session, region, account_id = get_aws_session()
    ssm = boto3.client("ssm", region_name=region)

    # Assume the administrators role for Lake Formation permissions
    admin_role_arn = ssm.get_parameter(Name="/app/lakehouse-agent/roles/lakehouse-administrators-role")["Parameter"][
        "Value"
    ]

    print(f"   Assuming admin role: {admin_role_arn}")

    sts = boto3.client("sts", region_name=region)
    assumed = sts.assume_role(RoleArn=admin_role_arn, RoleSessionName="load-sample-data")
    creds = assumed["Credentials"]

    # Create Athena client with assumed admin role credentials
    athena = boto3.client(
        "athena",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )

    print("   ✅ Assumed admin role successfully")

    # Get configuration from SSM
    ssm.get_parameter(Name="/app/lakehouse-agent/table-bucket-name")["Parameter"]["Value"]
    namespace = ssm.get_parameter(Name="/app/lakehouse-agent/namespace")["Parameter"]["Value"]
    catalog_name = ssm.get_parameter(Name="/app/lakehouse-agent/catalog-name")["Parameter"]["Value"]

    # Get S3 output location
    output_bucket = ssm.get_parameter(Name="/app/lakehouse-agent/s3-bucket-name")["Parameter"]["Value"]

    output_location = f"s3://{output_bucket}/athena-results/"

    print(f"   Catalog: {catalog_name}")
    print(f"   Database: {namespace}")
    print(f"   Output: {output_location}")

    # Insert claims data
    print("\n📊 Loading claims data...")

    claims_inserts = [
        # Claims for policyholder001@example.com (John Doe)
        """
        INSERT INTO claims VALUES (
            'CLM-2024-001', 'policyholder001@example.com', 'John Doe', 
            DATE '1985-03-15', DATE '2024-01-10', 1250.00, 'medical', 'approved',
            'City Medical Center', '1234567890', 'J06.9', '99213',
            TIMESTAMP '2024-01-11 09:30:00', TIMESTAMP '2024-01-15 14:20:00',
            1000.00, '', 'Annual physical examination and lab work',
            'policyholder001@example.com', 'adjuster001@example.com',
            TIMESTAMP '2024-01-15 14:20:00', 'adjuster001@example.com'
        )
        """,
        """
        INSERT INTO claims VALUES (
            'CLM-2024-002', 'policyholder001@example.com', 'John Doe',
            DATE '1985-03-15', DATE '2024-02-05', 85.50, 'prescription', 'approved',
            'CVS Pharmacy', '9876543210', 'E11.9', '90670',
            TIMESTAMP '2024-02-05 16:45:00', TIMESTAMP '2024-02-06 10:15:00',
            85.50, '', 'Diabetes medication - monthly refill',
            'policyholder001@example.com', 'adjuster001@example.com',
            TIMESTAMP '2024-02-06 10:15:00', 'adjuster001@example.com'
        )
        """,
        """
        INSERT INTO claims VALUES (
            'CLM-2024-003', 'policyholder001@example.com', 'John Doe',
            DATE '1985-03-15', DATE '2024-02-20', 3500.00, 'hospital', 'in_review',
            'General Hospital', '1122334455', 'M54.5', '22612',
            TIMESTAMP '2024-02-21 08:00:00', NULL,
            NULL, '', 'Emergency room visit for back pain including X-rays',
            'policyholder001@example.com', 'adjuster002@example.com',
            TIMESTAMP '2024-02-21 08:00:00', 'adjuster002@example.com'
        )
        """,
        """
        INSERT INTO claims VALUES (
            'CLM-2024-004', 'policyholder001@example.com', 'John Doe',
            DATE '1985-03-15', DATE '2024-03-10', 450.00, 'medical', 'pending',
            'Downtown Dental Clinic', '2233445566', 'K02.9', 'D0150',
            TIMESTAMP '2024-03-11 11:20:00', NULL,
            NULL, '', 'Dental examination and cleaning',
            'policyholder001@example.com', 'adjuster001@example.com',
            TIMESTAMP '2024-03-11 11:20:00', 'adjuster001@example.com'
        )
        """,
        # Claims for policyholder002@example.com (Jane Smith)
        """
        INSERT INTO claims VALUES (
            'CLM-2024-005', 'policyholder002@example.com', 'Jane Smith',
            DATE '1990-07-22', DATE '2024-01-15', 850.00, 'medical', 'approved',
            'Womens Health Center', '5544332211', 'Z00.00', '99395',
            TIMESTAMP '2024-01-16 10:00:00', TIMESTAMP '2024-01-18 15:30:00',
            680.00, '', 'Annual gynecological exam and preventive care',
            'policyholder002@example.com', 'adjuster002@example.com',
            TIMESTAMP '2024-01-18 15:30:00', 'adjuster002@example.com'
        )
        """,
        """
        INSERT INTO claims VALUES (
            'CLM-2024-006', 'policyholder002@example.com', 'Jane Smith',
            DATE '1990-07-22', DATE '2024-02-10', 125.00, 'prescription', 'approved',
            'Walgreens Pharmacy', '6655443322', 'H10.9', '90680',
            TIMESTAMP '2024-02-10 13:15:00', TIMESTAMP '2024-02-11 09:00:00',
            125.00, '', 'Antibiotic prescription for eye infection',
            'policyholder002@example.com', 'adjuster001@example.com',
            TIMESTAMP '2024-02-11 09:00:00', 'adjuster001@example.com'
        )
        """,
        """
        INSERT INTO claims VALUES (
            'CLM-2024-007', 'policyholder002@example.com', 'Jane Smith',
            DATE '1990-07-22', DATE '2024-02-25', 12500.00, 'hospital', 'approved',
            'St. Marys Hospital', '7766554433', 'O80', '59400',
            TIMESTAMP '2024-02-26 07:30:00', TIMESTAMP '2024-03-05 16:00:00',
            10000.00, '', 'Childbirth and postpartum care',
            'policyholder002@example.com', 'adjuster002@example.com',
            TIMESTAMP '2024-03-05 16:00:00', 'adjuster002@example.com'
        )
        """,
        """
        INSERT INTO claims VALUES (
            'CLM-2024-008', 'policyholder002@example.com', 'Jane Smith',
            DATE '1990-07-22', DATE '2024-03-15', 200.00, 'medical', 'denied',
            'Cosmetic Surgery Center', '8877665544', 'Z41.1', '15780',
            TIMESTAMP '2024-03-16 14:00:00', TIMESTAMP '2024-03-20 11:00:00',
            0.00, 'Cosmetic procedures not covered by policy', 'Facial cosmetic procedure',
            'policyholder002@example.com', 'adjuster001@example.com',
            TIMESTAMP '2024-03-20 11:00:00', 'adjuster001@example.com'
        )
        """,
        """
        INSERT INTO claims VALUES (
            'CLM-2024-009', 'policyholder002@example.com', 'Jane Smith',
            DATE '1990-07-22', DATE '2024-03-25', 75.00, 'prescription', 'pending',
            'Target Pharmacy', '9988776655', 'Z79.890', '90715',
            TIMESTAMP '2024-03-26 09:45:00', NULL,
            NULL, '', 'Vitamin supplements and prenatal care',
            'policyholder002@example.com', 'adjuster002@example.com',
            TIMESTAMP '2024-03-26 09:45:00', 'adjuster002@example.com'
        )
        """,
    ]

    for i, insert_query in enumerate(claims_inserts, 1):
        try:
            execute_query(athena, insert_query, catalog_name, namespace, output_location)
            print(f"   ✅ Inserted claim {i}/{len(claims_inserts)}")
        except Exception as e:
            print(f"   ❌ Failed to insert claim {i}: {e}")
        time.sleep(1)  # Small delay between Iceberg writes

    # Insert users data
    print("\n👥 Loading users data...")

    users_inserts = [
        """
        INSERT INTO users VALUES (
            'policyholder001@example.com', 'John Doe', 'policyholder', 
            'Individual', TIMESTAMP '2023-01-15 00:00:00'
        )
        """,
        """
        INSERT INTO users VALUES (
            'policyholder002@example.com', 'Jane Smith', 'policyholder',
            'Individual', TIMESTAMP '2023-02-20 00:00:00'
        )
        """,
        """
        INSERT INTO users VALUES (
            'adjuster001@example.com', 'Michael Johnson', 'adjuster',
            'Claims Department', TIMESTAMP '2022-06-01 00:00:00'
        )
        """,
        """
        INSERT INTO users VALUES (
            'adjuster002@example.com', 'Sarah Williams', 'adjuster',
            'Claims Department', TIMESTAMP '2022-08-15 00:00:00'
        )
        """,
        """
        INSERT INTO users VALUES (
            'admin@example.com', 'Admin User', 'admin',
            'IT Department', TIMESTAMP '2022-01-01 00:00:00'
        )
        """,
    ]

    for i, insert_query in enumerate(users_inserts, 1):
        try:
            execute_query(athena, insert_query, catalog_name, namespace, output_location)
            print(f"   ✅ Inserted user {i}/{len(users_inserts)}")
        except Exception as e:
            print(f"   ❌ Failed to insert user {i}: {e}")
        time.sleep(1)  # Small delay between Iceberg writes

    print("\n✨ Data loading complete!")
    print("\n📊 Verify data with:")
    print(f"   SELECT COUNT(*) FROM {catalog_name}.{namespace}.claims")
    print(f"   SELECT COUNT(*) FROM {catalog_name}.{namespace}.users")


if __name__ == "__main__":
    main()
