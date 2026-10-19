# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Text-to-SQL Agent — Amazon Bedrock AgentCore

Converts natural language questions to SQL and executes them on Athena.

CONFIGURATION:
- Edit config/tables.yaml to define your tables
- Edit config/system_prompt.yaml to customize the prompt and examples
- Set environment variables in .env
"""

import os
import time
import uuid
import yaml
from datetime import datetime
from pathlib import Path

from strands import tool
from bedrock_agentcore import BedrockAgentCoreApp

# --- Configuration (from environment variables or .env) ---
GLUE_DATABASE = os.environ.get("GLUE_DATABASE_NAME", "my_company_demo")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ATHENA_OUTPUT = os.environ.get(
    "ATHENA_OUTPUT_LOCATION", "s3://my-company-text-to-sql-athena/results/"
)
MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")
PROJECT_NAME = os.environ.get("PROJECT_NAME", "my-company")

app = BedrockAgentCoreApp()

# Lazy-initialized clients
_glue = None
_athena = None


def _get_glue():
    global _glue
    if _glue is None:
        import boto3

        _glue = boto3.client("glue", region_name=AWS_REGION)
    return _glue


def _get_athena():
    global _athena
    if _athena is None:
        import boto3

        _athena = boto3.client("athena", region_name=AWS_REGION)
    return _athena


def _load_system_prompt():
    """Load and build the system prompt from config/system_prompt.yaml."""
    config_path = Path(__file__).parent / "config" / "system_prompt.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception:
        config = {}

    tables_info = config.get("naming_conventions", {}).get("tables", [])
    tables_str = "\n".join(f"  - {t}" for t in tables_info)

    relationships = config.get("naming_conventions", {}).get("relationships", [])
    rels_str = "\n".join(f"  - {r}" for r in relationships)

    guidelines = config.get("sql_guidelines", [])
    guidelines_str = "\n".join(f"  - {g}" for g in guidelines)

    business_dict = config.get("business_dictionary", {})
    biz_str = "\n".join(f"  - {k}: {v}" for k, v in business_dict.items())

    return f"""You are an expert SQL assistant for {PROJECT_NAME}.

CONTEXT:
- Database: {GLUE_DATABASE} in AWS Glue Data Catalog
- Available tables:
{tables_str}
- Relationships:
{rels_str}
- Engine: Amazon Athena (Presto SQL dialect)

BUSINESS DICTIONARY:
{biz_str}

CAPABILITIES:
1. Convert natural language questions to SQL queries
2. Use discover_schema() to get table metadata from Glue
3. Generate optimized and safe SQL
4. Execute queries with execute_query()
5. Format results clearly

WORKFLOW:
1. When you receive a question, first use discover_schema() with relevant keywords
2. Analyze the returned schema to understand available columns
3. Generate an appropriate SQL query
4. Execute with execute_query()
5. Present results clearly and concisely

SQL RULES:
{guidelines_str}

IMPORTANT SQL SYNTAX (Presto/Athena):
- Date columns are STRING type in 'YYYY-MM-DD' format
- To extract year: year(date_parse(date_col, '%Y-%m-%d'))
- To extract month: month(date_parse(date_col, '%Y-%m-%d'))

RESPONSE FORMAT:
- Answer the question directly with the data obtained
- Do NOT include the SQL in your response (it is shown automatically in the frontend)
- Be concise, direct, and friendly"""


@tool
def discover_schema(keywords=None):
    """
    Discover the schema of available tables in the database.

    Args:
        keywords: Optional list of keywords to filter tables

    Returns:
        Dictionary with table and column information
    """
    try:
        response = _get_glue().get_tables(DatabaseName=GLUE_DATABASE)
        tables_info = []
        for table in response.get("TableList", []):
            name = table["Name"]
            if keywords:
                kw_lower = [k.lower() for k in keywords]
                if not any(kw in name.lower() for kw in kw_lower):
                    continue
            columns = [
                {
                    "name": c["Name"],
                    "type": c["Type"],
                    "comment": c.get("Comment", ""),
                }
                for c in table.get("StorageDescriptor", {}).get("Columns", [])
            ]
            tables_info.append(
                {
                    "name": name,
                    "columns": columns,
                    "location": table.get("StorageDescriptor", {}).get("Location", ""),
                    "row_count": table.get("Parameters", {}).get(
                        "numRows", "unknown"
                    ),
                }
            )
        return {
            "database": GLUE_DATABASE,
            "tables": tables_info,
            "total_tables": len(tables_info),
        }
    except Exception as e:
        return {"database": GLUE_DATABASE, "tables": [], "error": str(e)}


@tool
def execute_query(sql: str):
    """
    Execute a SQL SELECT query on Amazon Athena.
    Only SELECT queries are allowed.

    Args:
        sql: SQL query to execute (must be SELECT)

    Returns:
        Dictionary with query results
    """
    try:
        sql_upper = sql.strip().upper()
        if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
            return {"success": False, "error": "Only SELECT queries are allowed"}

        forbidden = [
            "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "TRUNCATE",
        ]
        for word in forbidden:
            if word in sql_upper:
                return {"success": False, "error": f"Operation not allowed: {word}"}

        athena = _get_athena()
        response = athena.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": GLUE_DATABASE},
            ResultConfiguration={"OutputLocation": ATHENA_OUTPUT},
        )
        qid = response["QueryExecutionId"]

        for _ in range(60):
            status_resp = athena.get_query_execution(QueryExecutionId=qid)
            state = status_resp["QueryExecution"]["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.5)

        if state != "SUCCEEDED":
            err = status_resp["QueryExecution"]["Status"].get(
                "StateChangeReason", "Query failed"
            )
            return {"success": False, "error": err}

        results = athena.get_query_results(QueryExecutionId=qid, MaxResults=1000)
        cols = [
            {"name": c["Name"], "type": c["Type"]}
            for c in results["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
        ]
        rows = []
        for row in results["ResultSet"]["Rows"][1:]:
            rows.append(
                {
                    cols[i]["name"]: row["Data"][i].get("VarCharValue")
                    for i in range(len(cols))
                }
            )

        stats = status_resp["QueryExecution"]["Statistics"]
        return {
            "success": True,
            "sql": sql,
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "execution_time_ms": stats.get("TotalExecutionTimeInMillis", 0),
            "data_scanned_bytes": stats.get("DataScannedInBytes", 0),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# Load system prompt at module level
SYSTEM_PROMPT = _load_system_prompt()


@app.entrypoint
def invoke(payload, context=None):
    """AgentCore Runtime entrypoint."""
    try:
        query = (
            payload.get("query", payload.get("prompt", ""))
            if isinstance(payload, dict)
            else str(payload)
        )
        session_id = (
            payload.get("session_id", str(uuid.uuid4()))
            if isinstance(payload, dict)
            else str(uuid.uuid4())
        )
        user_id = (
            payload.get("user_id", "demo_user")
            if isinstance(payload, dict)
            else "demo_user"
        )

        if not query:
            return {
                "success": False,
                "error": "No query provided",
                "session_id": session_id,
            }

        from strands import Agent

        session_manager = None

        # Configure Memory if available
        if MEMORY_ID:
            try:
                from bedrock_agentcore.memory.integrations.strands.config import (
                    AgentCoreMemoryConfig,
                )
                from bedrock_agentcore.memory.integrations.strands.session_manager import (
                    AgentCoreMemorySessionManager,
                )

                memory_config = AgentCoreMemoryConfig(
                    memory_id=MEMORY_ID,
                    session_id=session_id,
                    actor_id=user_id,
                )
                session_manager = AgentCoreMemorySessionManager(
                    agentcore_memory_config=memory_config,
                    region_name=AWS_REGION,
                )
            except Exception:
                pass  # Memory is optional

        agent = Agent(
            name=f"{PROJECT_NAME}TextToSQLAgent",
            model="us.anthropic.claude-sonnet-4-20250514-v1:0",
            system_prompt=SYSTEM_PROMPT,
            tools=[discover_schema, execute_query],
            session_manager=session_manager,
        )

        start = time.time()
        response = agent(query)
        latency = int((time.time() - start) * 1000)

        response_text = str(response)
        if hasattr(response, "message") and isinstance(response.message, dict):
            content = response.message.get("content", [])
            if content and isinstance(content, list) and len(content) > 0:
                response_text = content[0].get("text", str(response))

        return {
            "success": True,
            "response": response_text,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "latency_ms": latency,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "session_id": session_id if "session_id" in dir() else str(uuid.uuid4()),
        }


if __name__ == "__main__":
    app.run()
