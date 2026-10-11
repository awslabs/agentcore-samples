# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for PolicyValidator."""

import pytest
from src.policy_validator import PolicyValidator, PolicyValidationResult


@pytest.fixture
def validator():
    return PolicyValidator(default_limit=1000)


class TestPolicyValidator:
    """Test SQL policy validation."""

    def test_valid_select(self, validator):
        result = validator.validate("SELECT * FROM customers")
        assert result.valid is True

    def test_valid_select_with_where(self, validator):
        result = validator.validate(
            "SELECT name, email FROM customers WHERE segment = 'premium'"
        )
        assert result.valid is True

    def test_valid_select_with_join(self, validator):
        sql = """
        SELECT c.name, SUM(s.total_amount) as total
        FROM sales s
        JOIN customers c ON s.customer_id = c.customer_id
        GROUP BY c.name
        ORDER BY total DESC
        """
        result = validator.validate(sql)
        assert result.valid is True

    def test_valid_with_cte(self, validator):
        sql = """
        WITH top_customers AS (
            SELECT customer_id, SUM(total_amount) as total
            FROM sales GROUP BY customer_id
        )
        SELECT c.name, t.total
        FROM top_customers t
        JOIN customers c ON t.customer_id = c.customer_id
        """
        result = validator.validate(sql)
        assert result.valid is True

    def test_rejects_drop(self, validator):
        result = validator.validate("DROP TABLE customers")
        assert result.valid is False
        assert "DROP" in result.reason

    def test_rejects_delete(self, validator):
        result = validator.validate("DELETE FROM customers WHERE id = 1")
        assert result.valid is False

    def test_rejects_insert(self, validator):
        result = validator.validate(
            "INSERT INTO customers (name) VALUES ('test')"
        )
        assert result.valid is False

    def test_rejects_update(self, validator):
        result = validator.validate(
            "UPDATE customers SET name = 'test' WHERE id = 1"
        )
        assert result.valid is False

    def test_rejects_truncate(self, validator):
        result = validator.validate("TRUNCATE TABLE customers")
        assert result.valid is False

    def test_rejects_create(self, validator):
        result = validator.validate("CREATE TABLE test (id INT)")
        assert result.valid is False

    def test_rejects_empty(self, validator):
        result = validator.validate("")
        assert result.valid is False

    def test_rejects_none(self, validator):
        result = validator.validate(None)
        assert result.valid is False

    def test_adds_limit_when_missing(self, validator):
        result = validator.validate("SELECT * FROM customers")
        assert result.valid is True
        assert "LIMIT 1000" in result.modified_sql

    def test_preserves_existing_limit(self, validator):
        result = validator.validate("SELECT * FROM customers LIMIT 10")
        assert result.valid is True
        assert result.modified_sql == "SELECT * FROM customers LIMIT 10"

    def test_custom_default_limit(self):
        v = PolicyValidator(default_limit=500)
        result = v.validate("SELECT * FROM customers")
        assert "LIMIT 500" in result.modified_sql
