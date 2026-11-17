"""DynamoDB storage for the finance tracker. deploy.py copies this file into each zip."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3


def now() -> str:
    return datetime.now(UTC).isoformat()


class FinanceDB:
    """One item per transaction or budget, keyed by user. The table name comes from FINANCE_TABLE."""

    def __init__(self, table_name: str | None = None) -> None:
        self.table_name = table_name or os.getenv("FINANCE_TABLE", "finance_tracker")
        # Built when the server imports this module, as the Runtime V2 guide recommends: the snapshot keeps the
        # loaded service model and the credential provider, which refreshes the role's credentials itself.
        # Only the connection is opened again after a restore.
        self.table: Any = boto3.resource("dynamodb").Table(self.table_name)

    def add_transaction(self, user_alias: str, kind: str, amount: float, description: str, category: str) -> dict:
        item = {
            "pk": f"USER#{user_alias}",
            "sk": f"TRANSACTION#{now()}",
            "type": kind,
            "amount": Decimal(str(amount)),
            "description": description,
            "category": category,
            "date": now(),
        }
        self.table.put_item(Item=item)
        return {"type": kind, "amount": float(amount), "description": description, "category": category}

    def set_budget(self, user_alias: str, category: str, monthly_limit: float) -> dict:
        self.table.put_item(
            Item={
                "pk": f"USER#{user_alias}",
                "sk": f"BUDGET#{category}",
                "category": category,
                "monthly_limit": Decimal(str(monthly_limit)),
                "set_date": now(),
            }
        )
        return {"category": category, "monthly_limit": monthly_limit}

    def _query(self, user_alias: str, prefix: str) -> list[dict]:
        response = self.table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{user_alias}", ":sk": prefix},
        )
        return response.get("Items", [])

    def get_transactions(self, user_alias: str) -> list[dict]:
        return self._query(user_alias, "TRANSACTION#")

    def get_budgets(self, user_alias: str) -> list[dict]:
        return self._query(user_alias, "BUDGET#")

    def clear_user(self, user_alias: str) -> None:
        """Delete every item of one user. Each invoke.py calls it first, so every run starts from the same data."""
        query: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": {":pk": f"USER#{user_alias}"},
            "ProjectionExpression": "pk, sk",
        }
        with self.table.batch_writer() as batch:
            while True:
                page = self.table.query(**query)
                for item in page.get("Items", []):
                    batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
                if "LastEvaluatedKey" not in page:
                    break
                query["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def get_balance(self, user_alias: str) -> dict:
        transactions = self.get_transactions(user_alias)
        income = sum(float(t["amount"]) for t in transactions if t["type"] == "income")
        expenses = sum(-float(t["amount"]) for t in transactions if t["type"] == "expense")
        return {"balance": income - expenses, "income": income, "expenses": expenses}

    def spending_by_category(self, user_alias: str, since: str | None = None) -> dict[str, float]:
        spending: dict[str, float] = {}
        for t in self.get_transactions(user_alias):
            if t["type"] == "expense" and (since is None or t["date"] >= since):
                spending[t["category"]] = spending.get(t["category"], 0.0) - float(t["amount"])
        return spending
