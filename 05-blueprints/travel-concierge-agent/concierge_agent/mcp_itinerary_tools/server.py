"""
Itinerary Tools MCP Server

Exposes itinerary management tools via MCP protocol.
No agent logic - just pure tool implementations.
"""

import os
import logging
from typing import List, Dict, Any
from mcp.server import FastMCP
from dynamodb_manager import DynamoDBManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REGION = os.getenv("AWS_REGION")
if not REGION:
    raise ValueError("AWS_REGION environment variable is required")

# Create MCP server
mcp = FastMCP("Itinerary Tools", host="0.0.0.0", stateless_http=True)  # nosec B104:standard pattern for containerized MCP serverss

# Initialize DynamoDB manager
dynamodb_manager = None


def get_dynamodb_manager():
    """Get or create DynamoDB manager instance."""
    global dynamodb_manager
    if dynamodb_manager is None:
        dynamodb_manager = DynamoDBManager(region_name=REGION)
    return dynamodb_manager


# =============================================================================
# MCP TOOLS - Raw tool exposure
# =============================================================================


@mcp.tool()
def itinerary_get(user_id: str) -> List[Dict[str, Any]]:
    """
    Get the user's saved itinerary.

    Args:
        user_id: User identifier

    Returns:
        List of itinerary items (flights, hotels, activities)
    """
    try:
        manager = get_dynamodb_manager()
        items = manager.get_itinerary_items(user_id)
        return items
    except Exception as e:
        logger.error(f"Error getting itinerary: {e}")
        return []


@mcp.tool()
def itinerary_save(
    user_id: str,
    item_type: str,
    title: str,
    date: str,
    details: str = "",
    location: str = "",
    price: str = "",
    time_of_day: str = "",
) -> Dict[str, Any]:
    """
    Save an item to the user's itinerary.

    Args:
        user_id: User identifier
        item_type: Type of item ('flight', 'hotel', 'activity', 'restaurant')
        title: Item title/name
        date: Date in YYYY-MM-DD format
        details: Additional details
        location: Location/address
        price: Price if applicable
        time_of_day: Time of day ('morning', 'afternoon', 'evening')

    Returns:
        Success status and message
    """
    try:
        manager = get_dynamodb_manager()

        itinerary_item = {
            "item_type": item_type,
            "title": title,
            "date": date,
            "details": details,
            "location": location,
            "price": price,
            "time_of_day": time_of_day,
        }

        manager.add_itinerary_item(user_id, itinerary_item)

        return {"success": True, "message": f"Added {title} to itinerary for {date}"}
    except Exception as e:
        logger.error(f"Error saving itinerary item: {e}")
        return {"success": False, "message": str(e)}


@mcp.tool()
def itinerary_remove(user_id: str, item_id: str) -> Dict[str, Any]:
    """
    Remove an item from the itinerary.

    Args:
        user_id: User identifier
        item_id: ID of the item to remove

    Returns:
        Success status and message
    """
    try:
        manager = get_dynamodb_manager()
        manager.remove_itinerary_item(user_id, item_id)

        return {"success": True, "message": "Item removed from itinerary"}
    except Exception as e:
        logger.error(f"Error removing itinerary item: {e}")
        return {"success": False, "message": str(e)}


@mcp.tool()
def itinerary_clear(user_id: str) -> Dict[str, Any]:
    """
    Clear all items from the user's itinerary.

    Args:
        user_id: User identifier

    Returns:
        Success status and count of removed items
    """
    try:
        manager = get_dynamodb_manager()
        items = manager.get_itinerary_items(user_id)

        if not items:
            return {
                "success": True,
                "items_removed": 0,
                "message": "Itinerary is already empty.",
            }

        for item in items:
            manager.remove_itinerary_item(user_id, item.get("id"))

        return {
            "success": True,
            "items_removed": len(items),
            "message": f"Removed {len(items)} items from itinerary.",
        }
    except Exception as e:
        logger.error(f"Error clearing itinerary: {e}")
        return {"success": False, "message": str(e)}


@mcp.tool()
def itinerary_update_date(user_id: str, item_id: str, new_date: str) -> Dict[str, Any]:
    """
    Update the date for an itinerary item.

    Args:
        user_id: User identifier
        item_id: ID of the item to update
        new_date: New date in YYYY-MM-DD format

    Returns:
        Success status and message
    """
    try:
        from datetime import datetime

        try:
            datetime.strptime(new_date, "%Y-%m-%d")
        except ValueError:
            return {"success": False, "message": "Date must be in YYYY-MM-DD format"}

        manager = get_dynamodb_manager()
        manager.update_itinerary_item(user_id, item_id, {"date": new_date})

        return {"success": True, "message": f"Updated date to {new_date}"}
    except Exception as e:
        logger.error(f"Error updating date: {e}")
        return {"success": False, "message": str(e)}


# =============================================================================
# SERVER STARTUP
# =============================================================================

if __name__ == "__main__":
    logger.info("Starting Itinerary Tools MCP Server...")
    mcp.run(transport="streamable-http")
