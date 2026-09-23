import boto3
from datetime import datetime
from typing import Dict, List
from decimal import Decimal


class FinanceDB:
    def __init__(self, table_name: str = "finance_tracker", region_name: str = "us-east-1"):
        self.dynamodb = boto3.resource("dynamodb", region_name=region_name)
        self.table_name = table_name
        self.table = self.dynamodb.Table(table_name)

    def create_table(self) -> str:
        """Create the finance tracker table if it doesn't exist"""
        try:
            # Check if table already exists
            self.table.load()
            return f"Table {self.table_name} already exists"
        except self.dynamodb.meta.client.exceptions.ResourceNotFoundException:
            # Table doesn't exist, create it
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
            except Exception as e:
                return f"Error creating table: {str(e)}"
        except Exception as e:
            return f"Error checking table: {str(e)}"

    def delete_table(self) -> str:
        """Delete the finance tracker table"""
        try:
            self.table.delete()
            self.table.wait_until_not_exists()
            return f"Table {self.table_name} deleted successfully"
        except Exception as e:
            return f"Error deleting table: {str(e)}"

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
            "sk": f"TRANSACTION#{datetime.now().isoformat()}",
            "type": transaction_type,
            "amount": Decimal(str(amount)),  # Convert float to Decimal
            "description": description,
            "category": category,
            "date": datetime.now().isoformat(),
            "created_at": datetime.now().isoformat(),
        }

        self.table.put_item(Item=item)
        return f"{transaction_type.title()} of ${abs(amount):.2f} added for {user_alias}"

    def set_budget(self, user_alias: str, category: str, monthly_limit: float) -> str:
        """Set budget for a category"""
        item = {
            "pk": f"USER#{user_alias}",
            "sk": f"BUDGET#{category}",
            "category": category,
            "monthly_limit": Decimal(str(monthly_limit)),  # Convert float to Decimal
            "set_date": datetime.now().isoformat(),
        }

        self.table.put_item(Item=item)
        return f"Budget set for {category}: ${monthly_limit:.2f}/month"

    def get_transactions(self, user_alias: str) -> List[Dict]:
        """Get all transactions for a user"""
        response = self.table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={
                ":pk": f"USER#{user_alias}",
                ":sk": "TRANSACTION#",
            },
        )
        return response.get("Items", [])

    def get_budgets(self, user_alias: str) -> List[Dict]:
        """Get all budgets for a user"""
        response = self.table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{user_alias}", ":sk": "BUDGET#"},
        )
        return response.get("Items", [])

    def get_balance(self, user_alias: str) -> Dict:
        """Calculate balance from transactions"""
        transactions = self.get_transactions(user_alias)

        total = sum(float(t["amount"]) for t in transactions)  # Convert Decimal to float
        income = sum(float(t["amount"]) for t in transactions if t["type"] == "income")
        expenses = sum(abs(float(t["amount"])) for t in transactions if t["type"] == "expense")

        return {"balance": total, "income": income, "expenses": expenses}
