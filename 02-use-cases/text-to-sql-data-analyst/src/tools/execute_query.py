# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Query Execution Tool

Executes SQL queries on Amazon Athena or Amazon Redshift.
Applies security policies: timeouts and row limits.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Literal
import time

import boto3
from botocore.exceptions import ClientError


@dataclass
class ColumnMetadata:
    name: str
    type: str
    nullable: bool = True


@dataclass
class QueryResult:
    columns: List[ColumnMetadata]
    rows: List[Dict[str, Any]]
    row_count: int
    truncated: bool
    execution_time: int  # milliseconds
    query_id: str = ""
    data_scanned_bytes: int = 0


class QueryExecutionError(Exception):
    def __init__(self, message: str, user_message: str = None):
        super().__init__(message)
        self.user_message = user_message or message


class PolicyViolationError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def execute_query(
    sql: str,
    engine_type: Literal["athena", "redshift"],
    database_name: str,
    aws_region: str = "us-east-1",
    timeout_seconds: int = 30,
    max_rows: int = 1000,
    athena_output_location: str = None,
    athena_workgroup: str = "primary",
    redshift_cluster_id: str = None,
    redshift_db_user: str = None,
) -> QueryResult:
    """Execute a SQL query on Athena or Redshift with security policies."""
    if not sql or not sql.strip():
        raise QueryExecutionError("SQL query is empty", "The SQL query is empty")
    if not database_name:
        raise QueryExecutionError("Database name is required")
    if not _is_select_only(sql):
        raise PolicyViolationError("Only SELECT commands are allowed")

    start_time = time.time()

    if engine_type == "athena":
        if not athena_output_location:
            raise ValueError("athena_output_location is required for Athena queries")
        result = _execute_athena_query(
            sql, database_name, athena_output_location,
            athena_workgroup, aws_region, timeout_seconds,
        )
    elif engine_type == "redshift":
        if not redshift_cluster_id or not redshift_db_user:
            raise ValueError(
                "redshift_cluster_id and redshift_db_user are required"
            )
        result = _execute_redshift_query(
            sql, database_name, redshift_cluster_id,
            redshift_db_user, aws_region, timeout_seconds,
        )
    else:
        raise ValueError(f"Unsupported engine type: {engine_type}")

    result.execution_time = int((time.time() - start_time) * 1000)

    if len(result.rows) > max_rows:
        result.rows = result.rows[:max_rows]
        result.row_count = max_rows
        result.truncated = True

    return result


def _is_select_only(sql: str) -> bool:
    sql_upper = sql.upper().strip()
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return False
    dangerous = [
        "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "TRUNCATE",
    ]
    return not any(kw in sql_upper for kw in dangerous)


def _execute_athena_query(
    sql, database_name, output_location, workgroup, aws_region, timeout_seconds
):
    try:
        athena = boto3.client("athena", region_name=aws_region)
    except Exception as e:
        raise QueryExecutionError(
            f"Failed to create Athena client: {e}",
            "Could not connect to Athena",
        )

    try:
        response = athena.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": database_name},
            ResultConfiguration={"OutputLocation": output_location},
            WorkGroup=workgroup,
        )
        qid = response["QueryExecutionId"]
    except ClientError as e:
        msg = e.response.get("Error", {}).get("Message", str(e))
        raise QueryExecutionError(
            f"Failed to start query: {msg}",
            f"Error starting query: {msg}",
        )

    status = _wait_for_athena(athena, qid, timeout_seconds)

    if status == "FAILED":
        try:
            ex = athena.get_query_execution(QueryExecutionId=qid)
            reason = ex["QueryExecution"]["Status"].get(
                "StateChangeReason", "Unknown"
            )
        except Exception:
            reason = "Unknown"
        raise QueryExecutionError(
            f"Query failed: {reason}", f"Query failed: {reason}"
        )
    elif status == "TIMEOUT":
        raise QueryExecutionError(
            "Query timeout", f"Timeout of {timeout_seconds}s exceeded"
        )
    elif status != "SUCCEEDED":
        raise QueryExecutionError(f"Unexpected status: {status}")

    data_scanned = 0
    try:
        ex = athena.get_query_execution(QueryExecutionId=qid)
        data_scanned = (
            ex.get("QueryExecution", {})
            .get("Statistics", {})
            .get("DataScannedInBytes", 0)
        )
    except Exception:
        pass

    try:
        results = athena.get_query_results(QueryExecutionId=qid)
        result = _parse_athena_results(results, qid)
        result.data_scanned_bytes = data_scanned
        return result
    except ClientError as e:
        msg = e.response.get("Error", {}).get("Message", str(e))
        raise QueryExecutionError(
            f"Failed to get results: {msg}",
            f"Error retrieving results: {msg}",
        )


def _wait_for_athena(athena, qid, timeout_seconds, poll=0.5):
    start = time.time()
    while True:
        if time.time() - start > timeout_seconds:
            try:
                athena.stop_query_execution(QueryExecutionId=qid)
            except Exception:
                pass
            return "TIMEOUT"
        try:
            resp = athena.get_query_execution(QueryExecutionId=qid)
            status = resp["QueryExecution"]["Status"]["State"]
            if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
                return status
        except ClientError:
            pass
        time.sleep(poll)


def _parse_athena_results(results, query_id):
    result_set = results.get("ResultSet", {})
    rows_data = result_set.get("Rows", [])
    if not rows_data:
        return QueryResult(
            columns=[], rows=[], row_count=0, truncated=False,
            execution_time=0, query_id=query_id,
        )

    column_info = result_set.get("ResultSetMetadata", {}).get("ColumnInfo", [])
    columns = [
        ColumnMetadata(
            name=c.get("Name", ""),
            type=c.get("Type", "VARCHAR"),
            nullable=c.get("Nullable", "NULLABLE") != "NOT_NULL",
        )
        for c in column_info
    ]

    rows = []
    for row_data in rows_data[1:]:
        row_dict = {}
        for i, col in enumerate(columns):
            cell = (
                row_data.get("Data", [])[i]
                if i < len(row_data.get("Data", []))
                else {}
            )
            value = cell.get("VarCharValue")
            row_dict[col.name] = (
                _convert_value(value, col.type) if value is not None else None
            )
        rows.append(row_dict)

    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows), truncated=False,
        execution_time=0, query_id=query_id,
    )


def _execute_redshift_query(
    sql, database_name, cluster_id, db_user, aws_region, timeout_seconds
):
    try:
        client = boto3.client("redshift-data", region_name=aws_region)
    except Exception as e:
        raise QueryExecutionError(f"Failed to create Redshift client: {e}")

    try:
        response = client.execute_statement(
            ClusterIdentifier=cluster_id,
            Database=database_name,
            DbUser=db_user,
            Sql=sql,
        )
        sid = response["Id"]
    except ClientError as e:
        msg = e.response.get("Error", {}).get("Message", str(e))
        raise QueryExecutionError(f"Redshift query failed: {msg}")

    start = time.time()
    while True:
        if time.time() - start > timeout_seconds:
            try:
                client.cancel_statement(Id=sid)
            except Exception:
                pass
            raise QueryExecutionError("Redshift query timeout")
        try:
            desc = client.describe_statement(Id=sid)
            status = desc["Status"]
            if status == "FINISHED":
                break
            elif status in ("FAILED", "ABORTED"):
                raise QueryExecutionError(
                    f"Redshift query {status}: {desc.get('Error', 'Unknown')}"
                )
        except ClientError:
            pass
        time.sleep(0.5)

    results = client.get_statement_result(Id=sid)
    col_meta = results.get("ColumnMetadata", [])
    columns = [
        ColumnMetadata(name=c.get("name", ""), type=c.get("typeName", "VARCHAR"))
        for c in col_meta
    ]

    rows = []
    for record in results.get("Records", []):
        row = {}
        for i, col in enumerate(columns):
            if i < len(record):
                field = record[i]
                value = (
                    field.get("stringValue")
                    or field.get("longValue")
                    or field.get("doubleValue")
                    or field.get("booleanValue")
                )
                if field.get("isNull"):
                    value = None
                row[col.name] = value
            else:
                row[col.name] = None
        rows.append(row)

    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows), truncated=False,
        execution_time=0, query_id=sid,
    )


def _convert_value(value, col_type):
    if value is None or value == "":
        return None
    ct = col_type.upper()
    try:
        if ct in ("INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT"):
            return int(value)
        elif ct in ("DOUBLE", "FLOAT", "DECIMAL", "REAL"):
            return float(value)
        elif ct in ("BOOLEAN", "BOOL"):
            return value.lower() in ("true", "1", "t", "yes")
    except (ValueError, AttributeError):
        pass
    return value
