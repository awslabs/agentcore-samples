"""
RDS Data API Utilities

Executes SQL queries against Aurora Serverless PostgreSQL via the RDS Data API.
Configuration is read directly from environment variables:
- READONLY_SECRET_ARN, AURORA_RESOURCE_ARN, DATABASE_NAME, MAX_RESPONSE_SIZE_BYTES
"""

import os
import boto3
import json
from botocore.exceptions import ClientError
from decimal import Decimal


def get_rds_data_client():
    return boto3.client("rds-data")


def execute_statement(sql_query, aurora_resource_arn, secret_arn, database_name):
    client = get_rds_data_client()
    try:
        response = client.execute_statement(
            resourceArn=aurora_resource_arn,
            secretArn=secret_arn,
            database=database_name,
            sql=sql_query,
            includeResultMetadata=True,
        )
        return response
    except ClientError as e:
        print(f"❌ SQL execution error: {e}")
        return {"error": str(e)}


def get_size(string: str) -> int:
    return len(string.encode("utf-8"))


def run_sql_query(sql_query: str) -> str:
    """
    Execute a SQL query via RDS Data API and return results as JSON.

    Uses READONLY_SECRET_ARN for least-privilege database access.
    Truncates results if they exceed MAX_RESPONSE_SIZE_BYTES.
    """
    try:
        readonly_secret_arn = os.environ.get("READONLY_SECRET_ARN", "")
        aurora_resource_arn = os.environ.get("AURORA_RESOURCE_ARN", "")
        database_name = os.environ.get("DATABASE_NAME", "")
        max_response_size = int(os.environ.get("MAX_RESPONSE_SIZE_BYTES", "1048576"))

        if not aurora_resource_arn or not readonly_secret_arn or not database_name:
            return json.dumps(
                {
                    "error": "Missing required database configuration (READONLY_SECRET_ARN, AURORA_RESOURCE_ARN, DATABASE_NAME)"
                }
            )

        response = execute_statement(sql_query, aurora_resource_arn, readonly_secret_arn, database_name)

        if "error" in response:
            return json.dumps({"error": f"Query execution failed: {response['error']}"})

        records = []
        records_to_return = []
        message = ""

        if "records" in response:
            column_metadata = response.get("columnMetadata", [])
            column_names = [col.get("name") for col in column_metadata]

            for row in response["records"]:
                record = {}
                for i, value in enumerate(row):
                    for value_type, actual_value in value.items():
                        if value_type == "numberValue" and isinstance(actual_value, Decimal):
                            record[column_names[i]] = float(actual_value)
                        else:
                            record[column_names[i]] = actual_value
                records.append(record)

            if get_size(json.dumps(records)) > max_response_size:
                for item in records:
                    if get_size(json.dumps(records_to_return)) <= max_response_size:
                        records_to_return.append(item)
                message = f"Data truncated from {len(records)} to {len(records_to_return)} rows."
            else:
                records_to_return = records

        if message:
            return json.dumps({"result": records_to_return, "message": message})
        return json.dumps({"result": records_to_return})

    except Exception as e:
        return json.dumps({"error": f"Unexpected error: {str(e)}"})
