from strands import tool
import boto3
import os


@tool
def database_query(sql: str, database: str = "default") -> str:
    """
    Query a database using SQL via AWS RDS Data API.

    This tool allows the agent to execute SQL queries against
    configured databases using AWS RDS Data API.

    Args:
        sql: SQL query to execute
        database: Database name (default: "default")

    Returns:
        Query results as formatted text

    Example:
        result = database_query("SELECT * FROM users LIMIT 10")
        result = database_query("SELECT COUNT(*) FROM orders", database="analytics")

    Note:
        Requires AWS RDS Data API to be configured. Set these environment variables:
        - RDS_RESOURCE_ARN: ARN of your RDS cluster
        - RDS_SECRET_ARN: ARN of your Secrets Manager secret
        - AWS_REGION: AWS region (optional, defaults to us-west-2)
    """
    try:
        # Get configuration from environment variables
        resource_arn = os.environ.get("RDS_RESOURCE_ARN")
        secret_arn = os.environ.get("RDS_SECRET_ARN")
        region = os.environ.get("AWS_REGION", "us-west-2")

        # Validate configuration
        if not resource_arn or not secret_arn:
            return (
                "Error: Database not configured. Please set the following environment variables:\n"
                "- RDS_RESOURCE_ARN: ARN of your RDS cluster (e.g., arn:aws:rds:region:account:cluster:name)\n"
                "- RDS_SECRET_ARN: ARN of your Secrets Manager secret (e.g., arn:aws:secretsmanager:region:account:secret:name)\n"
                "- AWS_REGION: AWS region (optional, defaults to us-west-2)"
            )

        # Initialize RDS Data API client
        rds_client = boto3.client("rds-data", region_name=region)

        # Execute SQL statement
        response = rds_client.execute_statement(
            resourceArn=resource_arn, secretArn=secret_arn, database=database, sql=sql
        )

        # Check if query returned records
        records = response.get("records", [])
        column_metadata = response.get("columnMetadata", [])

        if not records:
            rows_updated = response.get("numberOfRecordsUpdated", 0)
            if rows_updated > 0:
                return f"Query executed successfully. {rows_updated} row(s) affected."
            else:
                return "Query executed successfully. No results returned."

        # Format results as table
        result_text = f"Query returned {len(records)} row(s):\n\n"

        # Add column headers
        if column_metadata:
            headers = [col.get("label", col.get("name", f"col_{i}")) for i, col in enumerate(column_metadata)]
            result_text += " | ".join(headers) + "\n"
            result_text += "-" * (len(" | ".join(headers))) + "\n"

        # Add rows (limit to first 20 for readability)
        for i, record in enumerate(records[:20], 1):
            row_values = []
            for field in record:
                # Extract value from field (handles different data types)
                if "stringValue" in field:
                    row_values.append(field["stringValue"])
                elif "longValue" in field:
                    row_values.append(str(field["longValue"]))
                elif "doubleValue" in field:
                    row_values.append(str(field["doubleValue"]))
                elif "booleanValue" in field:
                    row_values.append(str(field["booleanValue"]))
                elif "isNull" in field and field["isNull"]:
                    row_values.append("NULL")
                else:
                    row_values.append(str(field))

            result_text += " | ".join(row_values) + "\n"

        if len(records) > 20:
            result_text += f"\n... and {len(records) - 20} more row(s)"

        return result_text

    except rds_client.exceptions.BadRequestException as e:
        return f"Invalid SQL query: {str(e)}"
    except rds_client.exceptions.StatementTimeoutException:
        return "Query timed out. Try simplifying your query or adding appropriate indexes."
    except Exception as e:
        return f"Error executing database query: {str(e)}"
