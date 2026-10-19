"""
Creates a read-only PostgreSQL user in Aurora Serverless v2 via the RDS Data API.

Required environment variables:
  - SECRET_ARN: Admin secret ARN (for RDS Data API authentication)
  - READONLY_SECRET_ARN: Read-only user secret ARN (contains the password)
  - AURORA_SERVERLESS_DB_CLUSTER_ARN: Aurora cluster ARN
  - TABLE_NAME: Target table name (from CDK stack parameter PostgreSQLTableName)
  - DATABASE_NAME: Target database name (from CDK stack parameter DatabaseName)
"""

import json
import os

import boto3

# Environment variables
aurora_serverless_db_cluster_arn = os.environ["AURORA_SERVERLESS_DB_CLUSTER_ARN"]
secret_arn = os.environ["SECRET_ARN"]
readonly_secret_arn = os.environ["READONLY_SECRET_ARN"]
table_name = os.environ["TABLE_NAME"]
database_name = os.environ["DATABASE_NAME"]

print(f"Configuring read-only user for table: {table_name} in database: {database_name}")

secrets_client = boto3.client("secretsmanager")
rds_data = boto3.client("rds-data")


def execute_sql(sql):
    """Execute a SQL statement via RDS Data API using admin credentials."""
    return rds_data.execute_statement(
        resourceArn=aurora_serverless_db_cluster_arn,
        secretArn=secret_arn,
        database=database_name,
        sql=sql,
    )


try:
    # Retrieve the read-only user password from Secrets Manager
    resp = secrets_client.get_secret_value(SecretId=readonly_secret_arn)
    readonly_password = json.loads(resp["SecretString"])["password"]

    # Check if user already exists
    check_query = "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'readonly_user';"
    result = execute_sql(check_query)

    if result.get("records"):
        # User exists, update password
        execute_sql(f"ALTER USER readonly_user WITH PASSWORD '{readonly_password}';")
        print("Updated readonly_user password")
    else:
        # Create user
        execute_sql(f"CREATE USER readonly_user WITH PASSWORD '{readonly_password}';")
        print("Created readonly_user")

    # Grant schema usage
    query2 = "GRANT USAGE ON SCHEMA public TO readonly_user;"
    execute_sql(query2)
    print("Granted USAGE on schema public")

    # Grant SELECT on the specific table
    query3 = f"GRANT SELECT ON TABLE {table_name} TO readonly_user;"
    execute_sql(query3)
    print(f"Granted SELECT on table {table_name}")

    # Grant SELECT on future tables
    query4 = "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO readonly_user;"
    execute_sql(query4)
    print("Granted default SELECT privileges on future tables")

    print("-----------------------------------------")
    print("Read-only user created successfully!")

except Exception as e:
    print(f"Error: {e}")
