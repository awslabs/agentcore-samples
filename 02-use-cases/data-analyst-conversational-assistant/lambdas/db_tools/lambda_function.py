"""
AgentCore Gateway Lambda Target — Database Tools

This Lambda is registered as a Gateway target and provides two tools:
  - get_tables_information: Returns database schema metadata
  - execute_sql_query: Executes read-only SQL queries via RDS Data API

The Gateway routes tool calls to this Lambda. The tool name is extracted from
context.client_context.custom['bedrockAgentCoreToolName'] in the format:
  {targetId}___{toolName}

Environment variables (set via CDK/deploy.py):
  - READONLY_SECRET_ARN: Secrets Manager ARN for read-only DB user
  - AURORA_RESOURCE_ARN: Aurora cluster ARN
  - DATABASE_NAME: PostgreSQL database name
  - MAX_RESPONSE_SIZE_BYTES: Response size limit (default 1MB)
"""

import json
import os
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

TABLES_INFORMATION = """<db_tables_available>
<tables>
    <table>
    <table_name>video_games_sales_units</table_name>
    <table_description>This is a table for units sold of video games globally; the information is for 64,016 titles released from 1971 to 2024. Each record in the table contains a video game title (unique) with the total units sold for each region (1 North America, 2 Japan, 3 European Union (EU), and 4 the rest of the world), critics' scores, genres, consoles, and more.</table_description>
    <table_schema>
    video_games_sales_units (
        title TEXT, -- Only include this column in queries to search for a specific title of video game name
        console TEXT,
        genre TEXT,
        publisher TEXT,
        developer TEXT,
        critic_score NUMERIC(3,1),
        na_sales NUMERIC(4,2),
        jp_sales NUMERIC(4,2),
        pal_sales NUMERIC(4,2),
        other_sales NUMERIC(4,2),
        release_date DATE
    )
    </table_schema>
    <data_dictionary>The Video Games Sales Units table has the following structure/schema:
    title: Game title
    console: Console the game was released for
    genre: Genre of the game
    publisher: Publisher of the game
    developer: Developer of the game
    critic_score: Metacritic score (out of 10)
    na_sales: North American sales of copies in millions (units)
    jp_sales: Japanese sales of copies in millions (units)
    pal_sales: European & African sales of copies in millions (units)
    other_sales: Rest of world sales of copies in millions (units)
    release_date: Date the game was released on
    </data_dictionary>
    </table>
</tables>
<business_rules></business_rules>
</db_tables_available>"""


def lambda_handler(event, context):
    """Route tool calls from AgentCore Gateway to the appropriate handler."""
    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    delimiter = "___"
    tool_name = extended_tool_name[extended_tool_name.index(delimiter) + len(delimiter) :]

    if tool_name == "get_tables_information":
        return handle_get_tables_information()
    elif tool_name == "execute_sql_query":
        return handle_execute_sql_query(event)
    else:
        return {"error": f"Unknown tool: {tool_name}"}


def handle_get_tables_information():
    """Return database schema metadata for the configured database table."""
    return {
        "toolUsed": "get_tables_information",
        "information": TABLES_INFORMATION,
    }


BLOCKED_PII_PATTERNS = ["email", "phone", "ssn", "address", "credit_card"]
BLOCKED_COST_PATTERNS = ["cost_per_unit", "profit_margin", "wholesale_price", "procurement"]


def handle_execute_sql_query(event):
    """Execute a read-only SQL query against Aurora PostgreSQL via RDS Data API."""
    sql_query = event.get("sql_query", "")
    description = event.get("description", "")

    if not sql_query:
        return {"error": "sql_query parameter is required"}

    sql_lower = sql_query.lower()
    for pattern in BLOCKED_PII_PATTERNS:
        if pattern in sql_lower:
            return {"error": f"Query blocked: access to PII-related fields ({pattern}) is not permitted"}
    for pattern in BLOCKED_COST_PATTERNS:
        if pattern in sql_lower:
            return {"error": f"Query blocked: access to internal cost data ({pattern}) is not permitted"}

    readonly_secret_arn = os.environ.get("READONLY_SECRET_ARN", "")
    aurora_resource_arn = os.environ.get("AURORA_RESOURCE_ARN", "")
    database_name = os.environ.get("DATABASE_NAME", "")
    max_response_size = int(os.environ.get("MAX_RESPONSE_SIZE_BYTES", "1048576"))

    if not aurora_resource_arn or not readonly_secret_arn or not database_name:
        return {"error": "Missing database configuration (READONLY_SECRET_ARN, AURORA_RESOURCE_ARN, DATABASE_NAME)"}

    try:
        client = boto3.client("rds-data")
        response = client.execute_statement(
            resourceArn=aurora_resource_arn,
            secretArn=readonly_secret_arn,
            database=database_name,
            sql=sql_query,
            includeResultMetadata=True,
        )
    except ClientError as e:
        return {"error": f"Query execution failed: {str(e)}"}

    if "error" in response:
        return {"error": f"Query execution failed: {response['error']}"}

    records = []
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

        serialized = json.dumps(records)
        if len(serialized.encode("utf-8")) > max_response_size:
            records_to_return = []
            for item in records:
                if len(json.dumps(records_to_return).encode("utf-8")) <= max_response_size:
                    records_to_return.append(item)
            message = f"Data truncated from {len(records)} to {len(records_to_return)} rows."
            records = records_to_return

    result = {"result": records}
    if message:
        result["message"] = message

    return result
