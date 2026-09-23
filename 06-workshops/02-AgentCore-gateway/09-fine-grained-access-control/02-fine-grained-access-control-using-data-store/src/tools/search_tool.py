"""
Search Tool - Mock search functionality for Gateway

This tool simulates a search engine with mock results.
"""

import json
from datetime import datetime


# Mock search index
MOCK_SEARCH_INDEX = {
    "documents": [
        {
            "id": "doc1",
            "title": "Introduction to Amazon Bedrock",
            "content": "Amazon Bedrock is a fully managed service that offers foundation models...",
            "url": "https://aws.amazon.com/bedrock",
            "keywords": ["bedrock", "aws", "ai", "foundation models"],
        },
        {
            "id": "doc2",
            "title": "AgentCore Runtime Guide",
            "content": "AgentCore Runtime provides serverless execution for AI agents...",
            "url": "https://docs.aws.amazon.com/agentcore",
            "keywords": ["agentcore", "runtime", "agents", "serverless"],
        },
        {
            "id": "doc3",
            "title": "MCP Gateway Documentation",
            "content": "The Model Context Protocol Gateway enables tool integration...",
            "url": "https://docs.aws.amazon.com/gateway",
            "keywords": ["mcp", "gateway", "tools", "protocol"],
        },
        {
            "id": "doc4",
            "title": "Lambda Interceptors Best Practices",
            "content": "Lambda interceptors allow you to transform requests and responses...",
            "url": "https://docs.aws.amazon.com/lambda",
            "keywords": ["lambda", "interceptor", "aws", "serverless"],
        },
        {
            "id": "doc5",
            "title": "DynamoDB Query Patterns",
            "content": "DynamoDB provides fast and flexible NoSQL database services...",
            "url": "https://aws.amazon.com/dynamodb",
            "keywords": ["dynamodb", "database", "nosql", "aws"],
        },
        {
            "id": "doc6",
            "title": "Strands Agent Framework",
            "content": "Strands is a powerful framework for building AI agents with tools...",
            "url": "https://strands.dev",
            "keywords": ["strands", "agents", "framework", "ai"],
        },
        {
            "id": "doc7",
            "title": "IAM Permissions for AgentCore",
            "content": "Configure IAM roles and policies for AgentCore resources...",
            "url": "https://docs.aws.amazon.com/iam",
            "keywords": ["iam", "permissions", "security", "aws"],
        },
        {
            "id": "doc8",
            "title": "Tool Invocation in Agents",
            "content": "Agents can invoke tools through the MCP protocol...",
            "url": "https://docs.tools.dev",
            "keywords": ["tools", "invocation", "mcp", "agents"],
        },
    ]
}


def search_documents(query, max_results=10):
    """
    Search mock documents by query string.

    Args:
        query: Search query string
        max_results: Maximum number of results to return

    Returns:
        List of matching documents with relevance scores
    """
    query_lower = query.lower()
    query_terms = query_lower.split()

    results = []

    for doc in MOCK_SEARCH_INDEX["documents"]:
        score = 0

        # Check title
        if query_lower in doc["title"].lower():
            score += 10

        # Check content
        if query_lower in doc["content"].lower():
            score += 5

        # Check keywords
        for keyword in doc["keywords"]:
            if keyword in query_lower:
                score += 3

        # Check individual terms
        for term in query_terms:
            if term in doc["title"].lower():
                score += 2
            if term in doc["content"].lower():
                score += 1
            if term in doc["keywords"]:
                score += 2

        if score > 0:
            results.append({"document": doc, "relevance_score": score})

    # Sort by relevance score
    results.sort(key=lambda x: x["relevance_score"], reverse=True)

    return results[:max_results]


def lambda_handler(event, context):
    """
    Lambda handler for search tool.

    Expected input:
    {
        "query": "search terms",
        "max_results": 10 (optional),
        "filter_keywords": ["keyword1", "keyword2"] (optional)
    }

    Returns search results with relevance scores.
    """
    print(f"Search tool received event: {json.dumps(event)}")

    # Parse input
    body = event if isinstance(event, dict) else json.loads(event)
    query = body.get("query", "")
    max_results = body.get("max_results", 10)
    filter_keywords = body.get("filter_keywords", [])

    # Validate query
    if not query:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {
                    "tool": "search_tool",
                    "error": "Query parameter is required",
                    "success": False,
                }
            ),
        }

    # Perform search
    results = search_documents(query, max_results)

    # Apply keyword filter if provided
    if filter_keywords:
        results = [r for r in results if any(kw in r["document"]["keywords"] for kw in filter_keywords)]

    # Format results
    formatted_results = []
    for item in results:
        doc = item["document"]
        formatted_results.append(
            {
                "id": doc["id"],
                "title": doc["title"],
                "snippet": doc["content"][:200] + "...",
                "url": doc["url"],
                "keywords": doc["keywords"],
                "relevance_score": item["relevance_score"],
            }
        )

    search_result = {
        "query": query,
        "result_count": len(formatted_results),
        "max_results": max_results,
        "filter_keywords": filter_keywords,
        "results": formatted_results,
        "search_timestamp": datetime.utcnow().isoformat(),
    }

    response = {
        "statusCode": 200,
        "body": json.dumps({"tool": "search_tool", "result": search_result, "success": True}),
    }

    print(f"Search tool response: {len(formatted_results)} results for query '{query}'")
    return response


# MCP Tool Definition for Gateway registration
TOOL_DEFINITION = {
    "name": "search_tool",
    "description": "Search for documents and information using keywords. Returns relevant results with snippets and URLs.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string (keywords or phrases)",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return, between 1 and 100 (default: 10)",
            },
            "filter_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of keywords to filter results",
            },
        },
        "required": ["query"],
    },
}


if __name__ == "__main__":
    # Test the tool locally
    test_cases = [
        {"query": "bedrock"},
        {"query": "lambda interceptor", "max_results": 5},
        {"query": "aws", "filter_keywords": ["aws", "lambda"]},
        {"query": "agent tools", "max_results": 3},
    ]

    for i, test_event in enumerate(test_cases, 1):
        print(f"\n{'=' * 80}")
        print(f"Test Case {i}: {test_event}")
        print(f"{'=' * 80}")
        result = lambda_handler(test_event, None)
        print(f"{json.dumps(result, indent=2)}")
