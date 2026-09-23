"""
Database Query Tool - Mock database query functionality for Gateway

This tool simulates database query operations.
"""

import json
from datetime import datetime


# Mock database of users
MOCK_DATABASE = {
    "users": [
        {
            "id": 1,
            "name": "Alice Johnson",
            "email": "alice@example.com",
            "role": "admin",
            "created": "2023-01-15",
        },
        {
            "id": 2,
            "name": "Bob Smith",
            "email": "bob@example.com",
            "role": "user",
            "created": "2023-03-22",
        },
        {
            "id": 3,
            "name": "Charlie Brown",
            "email": "charlie@example.com",
            "role": "user",
            "created": "2023-06-10",
        },
        {
            "id": 4,
            "name": "Diana Prince",
            "email": "diana@example.com",
            "role": "moderator",
            "created": "2023-08-05",
        },
        {
            "id": 5,
            "name": "Eve Davis",
            "email": "eve@example.com",
            "role": "user",
            "created": "2023-11-01",
        },
    ],
    "products": [
        {
            "id": 101,
            "name": "Laptop",
            "price": 1200,
            "stock": 15,
            "category": "Electronics",
        },
        {
            "id": 102,
            "name": "Mouse",
            "price": 25,
            "stock": 150,
            "category": "Electronics",
        },
        {
            "id": 103,
            "name": "Keyboard",
            "price": 75,
            "stock": 80,
            "category": "Electronics",
        },
        {
            "id": 104,
            "name": "Monitor",
            "price": 350,
            "stock": 45,
            "category": "Electronics",
        },
        {
            "id": 105,
            "name": "Desk Chair",
            "price": 200,
            "stock": 30,
            "category": "Furniture",
        },
    ],
}


def lambda_handler(event, context):
    """
    Lambda handler for database query tool.

    Expected input:
    {
        "table": "users" | "products",
        "filter": {
            "field": "role",
            "value": "admin"
        } (optional),
        "limit": 10 (optional)
    }

    Returns mock query results.
    """
    print(f"Database query tool received event: {json.dumps(event)}")

    # Parse input
    body = event if isinstance(event, dict) else json.loads(event)
    table = body.get("table", "users")
    filter_criteria = body.get("filter", {})
    limit = body.get("limit", 100)

    # Validate table
    if table not in MOCK_DATABASE:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {
                    "tool": "database_query_tool",
                    "error": f"Unknown table: {table}. Available tables: {list(MOCK_DATABASE.keys())}",
                    "success": False,
                }
            ),
        }

    # Get data from mock database
    data = MOCK_DATABASE[table]

    # Apply filter if provided
    if filter_criteria:
        field = filter_criteria.get("field")
        value = filter_criteria.get("value")

        if field:
            data = [item for item in data if item.get(field) == value]

    # Apply limit
    data = data[:limit]

    query_result = {
        "table": table,
        "filter_applied": filter_criteria,
        "result_count": len(data),
        "results": data,
        "query_timestamp": datetime.utcnow().isoformat(),
    }

    response = {
        "statusCode": 200,
        "body": json.dumps({"tool": "database_query_tool", "result": query_result, "success": True}),
    }

    print(f"Database query tool response: {len(data)} results")
    return response


# MCP Tool Definition for Gateway registration
TOOL_DEFINITION = {
    "name": "database_query_tool",
    "description": "Query a database table with optional filtering. Available tables: users, products. Returns matching records.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "table": {
                "type": "string",
                "description": "The database table to query: 'users' or 'products'",
            },
            "filter": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "description": "Field name to filter by",
                    },
                    "value": {"type": "string", "description": "Value to match"},
                },
                "description": "Optional filter criteria",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return, between 1 and 1000 (default: 100)",
            },
        },
        "required": ["table"],
    },
}


if __name__ == "__main__":
    # Test the tool locally
    test_cases = [
        {"table": "users"},
        {"table": "users", "filter": {"field": "role", "value": "admin"}},
        {
            "table": "products",
            "filter": {"field": "category", "value": "Electronics"},
            "limit": 3,
        },
    ]

    for i, test_event in enumerate(test_cases, 1):
        print(f"\n{'=' * 80}")
        print(f"Test Case {i}: {test_event}")
        print(f"{'=' * 80}")
        result = lambda_handler(test_event, None)
        print(f"{json.dumps(result, indent=2)}")
