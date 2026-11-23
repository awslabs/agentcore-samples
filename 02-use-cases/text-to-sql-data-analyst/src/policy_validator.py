# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Policy Validator — SQL Security Validation

Ensures only SELECT queries are executed.
Validates against dangerous commands and auto-applies LIMIT.
"""

from dataclasses import dataclass
from typing import Optional

import sqlparse
from sqlparse.sql import Statement


@dataclass
class PolicyValidationResult:
    """SQL validation result."""

    valid: bool
    reason: Optional[str] = None
    modified_sql: Optional[str] = None


class PolicyValidator:
    """
    Validates SQL queries against security policies.

    Ensures that:
    - Only SELECT commands are allowed
    - No dangerous DDL/DML keywords are present
    - LIMIT is added if missing
    """

    DANGEROUS_KEYWORDS = [
        "DROP", "DELETE", "INSERT", "UPDATE", "ALTER",
        "CREATE", "TRUNCATE", "GRANT", "REVOKE", "EXEC", "EXECUTE",
    ]

    def __init__(self, default_limit: int = 1000):
        self.default_limit = default_limit

    def validate(self, sql: str) -> PolicyValidationResult:
        """Validate a SQL query against security policies."""
        if not sql or not sql.strip():
            return PolicyValidationResult(valid=False, reason="SQL query is empty")

        try:
            parsed = sqlparse.parse(sql)
        except Exception as e:
            return PolicyValidationResult(
                valid=False, reason=f"SQL could not be parsed: {e}"
            )

        if not parsed:
            return PolicyValidationResult(
                valid=False, reason="SQL could not be parsed"
            )

        for statement in parsed:
            if not self._is_select_statement(statement):
                stmt_type = statement.get_type()
                return PolicyValidationResult(
                    valid=False,
                    reason=f"Command {stmt_type} not allowed. Only SELECT is valid.",
                )

        sql_upper = sql.upper()
        for keyword in self.DANGEROUS_KEYWORDS:
            if keyword in sql_upper:
                return PolicyValidationResult(
                    valid=False,
                    reason=f"Dangerous keyword detected: {keyword}",
                )

        modified_sql = self._ensure_limit(sql)
        return PolicyValidationResult(valid=True, modified_sql=modified_sql)

    def _is_select_statement(self, statement: Statement) -> bool:
        return statement.get_type() == "SELECT"

    def _ensure_limit(self, sql: str) -> str:
        if "LIMIT" in sql.upper():
            return sql
        sql_stripped = sql.rstrip().rstrip(";")
        return f"{sql_stripped} LIMIT {self.default_limit}"
