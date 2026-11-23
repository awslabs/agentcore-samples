"""
Customer Query Tool - Mock database query API for Gateway

This tool simulates a database query interface that accepts natural language
queries and returns mock customer data. It demonstrates what would happen if
this were a real database without proper input sanitization.

WARNING: This tool would be vulnerable to SQL injection if not protected by
the Gateway REQUEST interceptor.

Note: This is a MOCK tool - no real database is used. It returns simulated
data to demonstrate the security pattern without requiring infrastructure setup.
"""

import json
import random
from datetime import datetime


def lambda_handler(event, context):
    """
    Lambda handler for customer query tool.

    Expected input:
    {
        "query": "Show me customer with ID 12345"
    }

    Returns mock customer data simulating a database query result.
    """
    print(f"Customer query tool received event: {json.dumps(event)}")

    # Parse input
    body = event if isinstance(event, dict) else json.loads(event)
    query = body.get('query', None)

    if not query:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "tool": "customer_query_tool",
                "error": "query parameter is required",
                "success": False
            })
        }

    print(f"Processing query: {query}")

    # Generate mock customer data
    # In a real implementation, this would execute: SELECT * FROM customers WHERE {query}
    # Without proper sanitization, this would be vulnerable to SQL injection
    
    customer_ids = [12345, 67890, 11111, 22222, 33333]
    first_names = ['Alice', 'Bob', 'Carol', 'David', 'Emma', 'Frank', 'Grace', 'Henry']
    last_names = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis']
    cities = ['Boston', 'Seattle', 'Austin', 'Denver', 'Portland', 'Chicago', 'New York', 'San Francisco']
    
    # Generate 1-3 mock customer records
    num_results = random.randint(1, 3)
    customers = []
    
    for _ in range(num_results):
        customer = {
            "customer_id": random.choice(customer_ids),
            "name": f"{random.choice(first_names)} {random.choice(last_names)}",
            "email": f"{random.choice(first_names).lower()}.{random.choice(last_names).lower()}@example.com",
            "city": random.choice(cities),
            "account_status": random.choice(['Active', 'Inactive', 'Pending']),
            "total_orders": random.randint(0, 50),
            "lifetime_value": round(random.uniform(100, 10000), 2)
        }
        customers.append(customer)

    response = {
        "statusCode": 200,
        "body": {
            "tool": "customer_query_tool",
            "query": query,
            "data_source": "mock_database",
            "results": customers,
            "result_count": len(customers),
            "success": True,
            "note": "This is simulated data. In production, this would query a real database. The Gateway interceptor protects against SQL injection attacks."
        }
    }

    print(f"Returning {len(customers)} customer records")
    return response


# MCP Tool Definition for Gateway registration
TOOL_DEFINITION = {
    "name": "customer_query_tool",
    "description": "Query customer database using natural language. Returns customer records matching the query. Protected by Gateway interceptor against SQL injection attacks. Note: Uses mock data for demonstration.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language query to search customers (e.g., 'Show me customer with ID 12345', 'Find customers in Boston')"
            }
        },
        "required": ["query"]
    }
}


if __name__ == "__main__":
    # Test the tool locally
    test_queries = [
        {"query": "Show me customer with ID 12345"},
        {"query": "Find customers in Boston"},
        {"query": "Get customer email for John Smith"},
        {}  # Test missing query
    ]

    for test_event in test_queries:
        print(f"\n{'='*60}")
        print(f"Testing with: {test_event}")
        print(f"{'='*60}")
        result = lambda_handler(test_event, None)
        print(f"\nTest result:\n{json.dumps(result, indent=2)}")
