"""
CDK Custom Resource Lambda — Initializes the Sample Database

Runs during 'cdk deploy' to:
  1. Create the video_games_sales_units table
  2. Install aws_s3 extension
  3. Import CSV data from S3 into Aurora
  4. Create read-only user with SELECT-only permissions

This eliminates the need for manual 'python create-sales-database.py' steps.
"""

import json
import os
import re

import boto3


def handler(event, context):
    """CDK Provider onEvent contract; the Provider sends the CFN response."""
    physical_id = event.get("PhysicalResourceId", "video-games-data-v1")
    if event["RequestType"] in ("Create", "Update"):
        setup_database(event)
    return {"PhysicalResourceId": physical_id}


def setup_database(event):
    """Create table, import data, and set up read-only user."""
    rds_data = boto3.client("rds-data")
    secrets_client = boto3.client("secretsmanager")
    props = event.get("ResourceProperties", {})
    cluster_arn = props["ClusterArn"]
    admin_secret_arn = props["AdminSecretArn"]
    readonly_secret_arn = props["ReadOnlySecretArn"]
    database_name = props["DatabaseName"]
    bucket_name = props["BucketName"]
    s3_file_key = props["S3FileKey"]
    table_name = props.get("TableName", "video_games_sales_units")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", table_name):
        raise ValueError("Invalid table name")

    def execute_sql(sql):
        return rds_data.execute_statement(
            resourceArn=cluster_arn,
            secretArn=admin_secret_arn,
            database=database_name,
            sql=sql,
        )

    # Step 1: Create table
    print("Creating table...")
    execute_sql(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            title TEXT,
            console TEXT,
            genre TEXT,
            publisher TEXT,
            developer TEXT,
            critic_score NUMERIC(3,1),
            total_sales NUMERIC(4,2),
            na_sales NUMERIC(4,2),
            jp_sales NUMERIC(4,2),
            pal_sales NUMERIC(4,2),
            other_sales NUMERIC(4,2),
            release_date DATE
        );
    """)

    # Step 2: Install aws_s3 extension
    print("Installing aws_s3 extension...")
    execute_sql("CREATE EXTENSION IF NOT EXISTS aws_s3 CASCADE;")

    # Step 3: Check if data already exists (idempotent)
    result = execute_sql(f"SELECT COUNT(*) as cnt FROM {table_name};")
    count = 0
    if result.get("records"):
        count = result["records"][0][0].get("longValue", 0)

    if count == 0:
        # Import data from S3
        print(f"Importing data from s3://{bucket_name}/{s3_file_key}...")
        execute_sql(f"""
            SELECT aws_s3.table_import_from_s3(
                '{table_name}',
                'title,console,genre,publisher,developer,critic_score,total_sales,na_sales,jp_sales,pal_sales,other_sales,release_date',
                '(FORMAT csv, DELIMITER ''|'', HEADER false)',
                aws_commons.create_s3_uri('{bucket_name}', '{s3_file_key}', '{region}')
            );
        """)
        print("Data imported successfully")
    else:
        print(f"Table already has {count} rows, skipping import")

    # Step 4: Create read-only user
    print("Setting up read-only user...")
    resp = secrets_client.get_secret_value(SecretId=readonly_secret_arn)
    readonly_password = json.loads(resp["SecretString"])["password"].replace("'", "''")

    # Check if user exists
    result = execute_sql("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'readonly_user';")
    if result.get("records"):
        execute_sql(f"ALTER USER readonly_user WITH PASSWORD '{readonly_password}';")
    else:
        execute_sql(f"CREATE USER readonly_user WITH PASSWORD '{readonly_password}';")

    execute_sql("GRANT USAGE ON SCHEMA public TO readonly_user;")
    execute_sql(f"GRANT SELECT ON TABLE {table_name} TO readonly_user;")
    execute_sql("ALTER USER readonly_user SET default_transaction_read_only = on;")
    execute_sql("ALTER USER readonly_user SET statement_timeout = '30000';")
    print("Read-only user configured successfully")
