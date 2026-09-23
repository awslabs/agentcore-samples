import os
from typing import Any, Dict
from mcp.server import FastMCP
from serp_tools import search_products, generate_packing_list

REGION = os.getenv("AWS_REGION")
if not REGION:
    raise ValueError("AWS_REGION environment variable is required")

# Create MCP server
mcp = FastMCP("Shopping Assistant Agent", host="0.0.0.0", stateless_http=True)  # nosec B104:standard pattern for containerized MCP servers


@mcp.tool()
def search_products_tool(user_id: str, question: str) -> Dict[str, Any]:
    """
    Search Amazon for products matching the user's query.

    Args:
        user_id: The unique identifier of the user
        question: User's query text requesting product information

    Returns:
        Dict with 'answer', 'asins', and 'products' keys
    """
    return search_products(user_id, question)


@mcp.tool()
def generate_packing_list_tool(user_id: str, question: str) -> Dict[str, Any]:
    """
    Generate a packing list with product recommendations for a trip.

    Args:
        user_id: The unique identifier of the user
        question: Trip description (e.g., "I'm going to Hawaii for a week")

    Returns:
        Dict with 'answer', 'asins', and 'items' keys
    """
    return generate_packing_list(user_id, question)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
