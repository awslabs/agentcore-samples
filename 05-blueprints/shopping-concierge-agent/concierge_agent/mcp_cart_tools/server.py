"""
Cart Tools MCP Server

Exposes cart management tools via MCP protocol.
No agent logic - just pure tool implementations.
"""

import os
import time
import boto3
import json
import logging
from datetime import datetime
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
mcp = FastMCP("Cart Tools", host="0.0.0.0", stateless_http=True)  # nosec B104:standard pattern for containerized MCP servers

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
def get_cart(user_id: str) -> List[Dict[str, Any]]:
    """
    Retrieves the current shopping cart items for a specified user.

    Args:
        user_id: The user ID to retrieve cart for

    Returns:
        list: A list of dictionaries, where each dictionary represents a cart item
    """
    try:
        manager = get_dynamodb_manager()
        items = manager.get_wishlist_items(user_id)

        # Group items by product ID (stored in asin field)
        item_groups = {}
        for item in items:
            key = item.get("asin", "")

            if key not in item_groups:
                item_groups[key] = []
            item_groups[key].append(item)

        # Build cart items
        cart_items = []
        for key, group in item_groups.items():
            latest = max(group, key=lambda x: x.get("createdAt", ""))

            # Ensure URL is never empty - use fallback if needed
            url = latest.get("url", "")
            if not url:
                product_title = latest.get("title", "").replace(" ", "+")
                url = f"https://www.google.com/search?q={product_title}&tbm=shop"

            cart_item = {
                "id": latest.get("id"),
                "asin": latest.get("asin", ""),
                "title": latest.get("title", ""),
                "price": latest.get("price", ""),
                "quantity": len(group),
                "reviews": latest.get("reviews", ""),
                "url": url,
            }

            cart_items.append(cart_item)

        cart_items.sort(key=lambda x: x.get("asin", ""))
        return cart_items

    except Exception as e:
        raise Exception(f"Error getting cart: {str(e)}")


@mcp.tool()
def add_to_cart(user_id: str, items: List[Dict[str, Any]]) -> None:
    """Adds multiple items to the user's shopping cart."""
    try:
        if not isinstance(items, list):
            raise TypeError("items must be a list")

        if not items:
            raise ValueError("items list cannot be empty")

        required_fields = ["asin", "title", "price"]
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise TypeError(f"Item at index {i} must be a dictionary")

            missing_fields = [field for field in required_fields if field not in item]
            if missing_fields:
                raise ValueError(f"Item at index {i} is missing required fields: {missing_fields}")

        manager = get_dynamodb_manager()

        for item in items:
            # Ensure URL is always set - prioritize: url > link > fallback
            if "url" not in item or not item["url"]:
                if "link" in item and item["link"]:
                    item["url"] = item["link"]
                else:
                    # Fallback: create a Google search URL for the product
                    product_title = item.get("title", "").replace(" ", "+")
                    item["url"] = f"https://www.google.com/search?q={product_title}&tbm=shop"

            item_with_type = {**item, "item_type": "product"}
            manager.add_wishlist_item(user_id, item_with_type)

    except Exception as e:
        raise Exception(f"Error adding items to cart: {str(e)}")


@mcp.tool()
def remove_from_cart(user_id: str, identifiers: List[str], item_type: str = "product") -> None:
    """
    Removes specific items from the user's shopping cart by identifier.

    Use the 'identifier' field from get_cart() results.
    For products: use product ID (stored in asin field)
    For hotels: use hotel_id
    For flights: use flight_id

    Args:
        user_id: The user ID
        identifiers: List of identifiers (product IDs, hotel_ids, or flight_ids)
        item_type: Type of item - 'product', 'hotel', or 'flight'
    """
    try:
        if not isinstance(identifiers, list):
            raise TypeError("identifiers must be a list")

        if not identifiers:
            raise ValueError("identifiers list cannot be empty")

        manager = get_dynamodb_manager()
        total_removed = 0

        all_items = manager.get_wishlist_items(user_id)

        for identifier in identifiers:
            items_to_remove = [item for item in all_items if item.get("asin") == identifier.strip()]

            for item in items_to_remove:
                manager.wishlist_table.delete_item(Key={"id": item["id"]})
                total_removed += 1

    except Exception as e:
        raise Exception(f"Error removing items from cart: {str(e)}")


@mcp.tool()
def clear_cart(user_id: str) -> Dict[str, Any]:
    """Clears all items from the user's shopping cart."""
    try:
        manager = get_dynamodb_manager()
        cart_items = manager.get_wishlist_items(user_id)

        if not cart_items:
            return {
                "success": True,
                "items_removed": 0,
                "message": "Cart is already empty.",
            }

        # Delete all items
        for item in cart_items:
            manager.wishlist_table.delete_item(Key={"id": item["id"]})

        return {
            "success": True,
            "items_removed": len(cart_items),
            "message": f"Successfully removed {len(cart_items)} items from cart.",
        }

    except Exception as e:
        return {"success": False, "message": f"Error clearing cart: {str(e)}"}


@mcp.tool()
def request_purchase_confirmation(user_id: str) -> Dict[str, Any]:
    """Prepares purchase summary and requests user confirmation."""
    try:
        manager = get_dynamodb_manager()
        cart_items = manager.get_wishlist_items(user_id)

        if not cart_items:
            return {
                "requires_confirmation": False,
                "success": False,
                "message": "Your cart is empty. Add items before purchasing.",
            }

        total_amount = 0.0
        for item in cart_items:
            price_str = item.get("price", "0")
            qty = item.get("qty", 1)

            # Remove currency symbols and commas
            price_str = price_str.replace("$", "").replace(",", "").strip()

            # Handle "per night" or other rate descriptions (e.g., "$120/night")
            # Take only the numeric part before any slash
            if "/" in price_str:
                price_str = price_str.split("/")[0].strip()

            try:
                item_price = float(price_str)
                # Multiply by quantity (for products) or number of duplicate entries
                total_amount += item_price * qty
            except ValueError:
                # If price parsing fails, log it but continue
                logger.warning(
                    f"Could not parse price '{item.get('price', '0')}' for item {item.get('title', 'unknown')}"
                )

        profile = manager.get_user_profile(user_id)
        if not profile or not profile.get("preferences"):
            return {
                "requires_confirmation": False,
                "success": False,
                "message": "No payment method found. Please add a payment card first.",
            }

        preferences = profile.get("preferences", {})
        if isinstance(preferences, str):
            preferences = json.loads(preferences)

        primary_card = preferences.get("payment", {}).get("primaryCard", {})
        if not primary_card or not primary_card.get("vProvisionedTokenId"):
            return {
                "requires_confirmation": False,
                "success": False,
                "message": "No payment method found. Please add a payment card first.",
            }

        card_type = primary_card.get("type", "Card")
        last_four = primary_card.get("cardNumber", "****")

        return {
            "requires_confirmation": True,
            "total_amount": total_amount,
            "total_items": len(cart_items),
            "payment_method": f"{card_type} ending in {last_four}",
            "message": f"Ready to purchase {len(cart_items)} items for ${total_amount:.2f} using {card_type} ending in {last_four}. Please confirm to proceed.",
        }

    except Exception as e:
        return {
            "requires_confirmation": False,
            "success": False,
            "message": f"Error preparing purchase: {str(e)}",
        }


@mcp.tool()
def confirm_purchase(user_id: str) -> Dict[str, Any]:
    """Executes the purchase after user has confirmed."""
    try:
        manager = get_dynamodb_manager()
        cart_items = manager.get_wishlist_items(user_id)

        if not cart_items:
            return {"success": False, "message": "Your cart is empty."}

        total_amount = 0.0
        for item in cart_items:
            price_str = item.get("price", "0")
            qty = item.get("qty", 1)

            # Remove currency symbols and commas
            price_str = price_str.replace("$", "").replace(",", "").strip()

            # Handle "per night" or other rate descriptions (e.g., "$120/night")
            # Take only the numeric part before any slash
            if "/" in price_str:
                price_str = price_str.split("/")[0].strip()

            try:
                item_price = float(price_str)
                # Multiply by quantity (for products) or number of duplicate entries
                total_amount += item_price * qty
            except ValueError:
                # If price parsing fails, log it but continue
                logger.warning(
                    f"Could not parse price '{item.get('price', '0')}' for item {item.get('title', 'unknown')}"
                )

        profile = manager.get_user_profile(user_id)
        preferences = profile.get("preferences", {})
        if isinstance(preferences, str):
            preferences = json.loads(preferences)

        primary_card = preferences.get("payment", {}).get("primaryCard", {})
        card_type = primary_card.get("type", "Card")
        last_four = primary_card.get("cardNumber", "****")

        # Generate order ID
        order_id = f"ORD-{datetime.now().strftime('%Y%m%d')}-{user_id[:8].upper()}"

        # Clear cart after successful purchase
        for item in cart_items:
            manager.wishlist_table.delete_item(Key={"id": item["id"]})

        return {
            "success": True,
            "order_id": order_id,
            "total_amount": total_amount,
            "items_count": len(cart_items),
            "payment_method": f"{card_type} ending in {last_four}",
            "message": f"Purchase completed successfully! Order ID: {order_id}.",
        }

    except Exception as e:
        return {"success": False, "message": f"Purchase failed: {str(e)}"}


@mcp.tool()
def send_purchase_confirmation_email(
    order_id: str,
    recipient_email: str,
    total_amount: str,
    items_count: int,
    payment_method: str,
) -> Dict[str, Any]:
    """Sends purchase confirmation email via AWS SES."""
    try:
        ses = boto3.client("ses", region_name=REGION)

        subject = f"Order Confirmation - {order_id}"
        body_html = f"""
        <html>
        <body>
            <h2>Thank you for your purchase!</h2>
            <p>Your order has been confirmed.</p>
            <p><strong>Order ID:</strong> {order_id}</p>
            <p><strong>Total Amount:</strong> ${total_amount}</p>
            <p><strong>Items:</strong> {items_count}</p>
            <p><strong>Payment Method:</strong> {payment_method}</p>
        </body>
        </html>
        """

        response = ses.send_email(
            Source="noreply@example.com",
            Destination={"ToAddresses": [recipient_email]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Html": {"Data": body_html}},
            },
        )

        return {
            "success": True,
            "message_id": response["MessageId"],
            "message": f"Confirmation email sent to {recipient_email}",
        }

    except Exception as e:
        return {"success": False, "message": f"Failed to send email: {str(e)}"}


@mcp.tool()
def onboard_card(
    user_id: str,
    card_number: str,
    expiration_date: str,
    cvv: str,
    card_type: str = "Visa",
    is_primary: bool = True,
) -> Dict[str, Any]:
    """Onboard new card and store details to user profile."""
    try:
        manager = get_dynamodb_manager()

        # Mock tokenization (replace with real Visa integration)
        token_id = f"vptoken_{user_id}_{int(time.time())}"
        last_four = card_number[-4:]

        profile = manager.get_user_profile(user_id)
        preferences = profile.get("preferences", {})
        if isinstance(preferences, str):
            preferences = json.loads(preferences)

        if "payment" not in preferences:
            preferences["payment"] = {}

        card_data = {
            "vProvisionedTokenId": token_id,
            "type": card_type,
            "cardNumber": last_four,
            "expiryMonth": (expiration_date.split("/")[0] if "/" in expiration_date else ""),
            "expiryYear": (expiration_date.split("/")[1] if "/" in expiration_date else ""),
            "cvv": "***",
        }

        if is_primary:
            preferences["payment"]["primaryCard"] = card_data
        else:
            preferences["payment"]["backupCard"] = card_data

        manager.update_user_profile(user_id, {"preferences": preferences})

        return {
            "success": True,
            "vProvisionedTokenId": token_id,
            "message": f"{card_type} ending in {last_four} added successfully",
            "card_type": card_type,
            "last_four": last_four,
        }

    except Exception as e:
        return {"success": False, "message": f"Error onboarding card: {str(e)}"}


@mcp.tool()
def get_visa_iframe_config(user_id: str) -> Dict[str, Any]:
    """Get Visa iframe configuration for secure card onboarding."""
    try:
        return {
            "success": True,
            "iframe_url": "",
            "config": {"user_id": user_id, "environment": "sandbox"},
        }
    except Exception as e:
        return {"success": False, "message": f"Error: {str(e)}"}


@mcp.tool()
def check_user_has_payment_card(user_id: str) -> Dict[str, Any]:
    """
    Check if user has a payment card (primary or backup) on file.

    Args:
        user_id: The user ID to check

    Returns:
        dict: {
            'has_card': bool,
            'has_primary': bool,
            'has_backup': bool,
            'card_info': dict or None (last 4 digits, type, etc)
        }
    """
    try:
        manager = get_dynamodb_manager()
        user_profile = manager.get_user_profile(user_id)

        if not user_profile:
            return {
                "has_card": False,
                "has_primary": False,
                "has_backup": False,
                "card_info": None,
                "message": "User profile not found",
            }

        preferences = user_profile.get("preferences")
        if isinstance(preferences, str):
            preferences = json.loads(preferences)

        payment = preferences.get("payment", {}) if preferences else {}
        primary_card = payment.get("primaryCard")
        backup_card = payment.get("backupCard")

        has_primary = primary_card is not None and primary_card.get("vProvisionedTokenId")
        has_backup = backup_card is not None and backup_card.get("vProvisionedTokenId")

        card_info = None
        if has_primary:
            card_info = {
                "type": primary_card.get("type", "Card"),
                "last_four": primary_card.get("lastFour") or primary_card.get("cardNumber", "****"),
                "is_primary": True,
            }
        elif has_backup:
            card_info = {
                "type": backup_card.get("type", "Card"),
                "last_four": backup_card.get("lastFour") or backup_card.get("cardNumber", "****"),
                "is_primary": False,
            }

        result = {
            "has_card": has_primary or has_backup,
            "has_primary": has_primary,
            "has_backup": has_backup,
            "card_info": card_info,
        }

        # Automatically include ui_actions based on card status
        # This ensures UI always gets the right button, regardless of agent behavior
        if not result["has_card"]:
            # No card → Show ADD_CARD button
            result["ui_actions"] = [
                {
                    "type": "show_button",
                    "action": "ADD_CARD",
                    "label": "💳 Add Payment Card",
                }
            ]
        else:
            # Has card → Show CONFIRM_PURCHASE button (agent can proceed with purchase)
            result["ui_actions"] = [
                {
                    "type": "show_button",
                    "action": "CONFIRM_PURCHASE",
                    "label": "✅ Confirm Purchase",
                }
            ]

        return result

    except Exception as e:
        logger.error(f"Error checking payment card: {e}")
        return {
            "has_card": False,
            "has_primary": False,
            "has_backup": False,
            "card_info": None,
            "error": str(e),
            # Show ADD_CARD button on error (assume no card)
            "ui_actions": [
                {
                    "type": "show_button",
                    "action": "ADD_CARD",
                    "label": "💳 Add Payment Card",
                }
            ],
        }


# =============================================================================
# SERVER STARTUP
# =============================================================================

if __name__ == "__main__":
    logger.info("Starting Cart Tools MCP Server...")
    mcp.run(transport="streamable-http")
