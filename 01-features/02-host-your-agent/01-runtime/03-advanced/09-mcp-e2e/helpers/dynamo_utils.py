import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError


class FinanceDB:
    def __init__(
        self,
        table_name: str = "finance_tracker",
        region_name: str = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")),
    ):
        self.dynamodb = boto3.resource("dynamodb", region_name=region_name)
        self.table_name = table_name
        self.table = self.dynamodb.Table(table_name)

    def create_table(self) -> str:
        """Create the finance tracker table if it doesn't exist"""
        try:
            self.table.load()
            return f"Table {self.table_name} already exists"
        except self.dynamodb.meta.client.exceptions.ResourceNotFoundException:
            try:
                table = self.dynamodb.create_table(
                    TableName=self.table_name,
                    KeySchema=[
                        {"AttributeName": "pk", "KeyType": "HASH"},
                        {"AttributeName": "sk", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "pk", "AttributeType": "S"},
                        {"AttributeName": "sk", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
                table.wait_until_exists()
                return f"Table {self.table_name} created successfully"
            except ClientError as e:
                return f"Error creating table: {e!s}"
        except ClientError as e:
            return f"Error checking table: {e!s}"

    def delete_table(self) -> str:
        """Delete the finance tracker table"""
        try:
            self.table.delete()
            self.table.wait_until_not_exists()
            return f"Table {self.table_name} deleted successfully"
        except ClientError as e:
            return f"Error deleting table: {e!s}"

    def add_transaction(
        self,
        user_alias: str,
        transaction_type: str,
        amount: float,
        description: str,
        category: str,
    ) -> str:
        """Add a transaction to DynamoDB"""
        item = {
            "pk": f"USER#{user_alias}",
            "sk": f"TRANSACTION#{datetime.now(tz=timezone.utc).isoformat()}",
            "type": transaction_type,
            "amount": Decimal(str(amount)),
            "description": description,
            "category": category,
            "date": datetime.now(tz=timezone.utc).isoformat(),
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        self.table.put_item(Item=item)
        return f"{transaction_type.title()} of ${abs(amount):.2f} added for {user_alias}"

    def set_budget(self, user_alias: str, category: str, monthly_limit: float) -> str:
        """Set budget for a category"""
        item = {
            "pk": f"USER#{user_alias}",
            "sk": f"BUDGET#{category}",
            "category": category,
            "monthly_limit": Decimal(str(monthly_limit)),
            "set_date": datetime.now(tz=timezone.utc).isoformat(),
        }

        self.table.put_item(Item=item)
        return f"Budget set for {category}: ${monthly_limit:.2f}/month"

    def get_transactions(self, user_alias: str) -> list[dict]:
        """Get all transactions for a user"""
        response = self.table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={
                ":pk": f"USER#{user_alias}",
                ":sk": "TRANSACTION#",
            },
        )
        return response.get("Items", [])

    def get_budgets(self, user_alias: str) -> list[dict]:
        """Get all budgets for a user"""
        response = self.table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{user_alias}", ":sk": "BUDGET#"},
        )
        return response.get("Items", [])

    def get_balance(self, user_alias: str) -> dict:
        """Calculate balance from transactions"""
        transactions = self.get_transactions(user_alias)

        total = sum(float(t["amount"]) for t in transactions)
        income = sum(float(t["amount"]) for t in transactions if t["type"] == "income")
        expenses = sum(abs(float(t["amount"])) for t in transactions if t["type"] == "expense")

        return {"balance": total, "income": income, "expenses": expenses}
