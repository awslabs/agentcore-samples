import os
import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class DynamoDBManager:
    """DynamoDB client for cart/wishlist operations and user management."""

    def __init__(self, region_name: str = None):
        self.region_name = region_name or os.environ.get("AWS_REGION")

        # Initialize DynamoDB resource and client
        self.dynamodb = boto3.resource("dynamodb", region_name=self.region_name)
        self.dynamodb_client = boto3.client("dynamodb", region_name=self.region_name)

        # Table names - can be overridden via environment variables
        # Updated to use UserProfile table name to match AppSync schema
        self.user_profile_table_name = os.environ.get("USER_PROFILE_TABLE_NAME")
        self.wishlist_table_name = os.environ.get("WISHLIST_TABLE_NAME")

        # Get table references
        self.user_profile_table = self.dynamodb.Table(self.user_profile_table_name)
        self.wishlist_table = self.dynamodb.Table(self.wishlist_table_name)

        logger.info(f"DynamoDB Manager initialized with region: {self.region_name}")
        logger.info(f"UserProfile table: {self.user_profile_table_name}, Wishlist table: {self.wishlist_table_name}")

    def get_wishlist_items(self, user_id: str):
        """Get all individual wishlist items for a user using GSI."""
        try:
            # Use query with GSI for better performance
            response = self.wishlist_table.query(
                IndexName="wishlistsByUser_id",
                KeyConditionExpression="user_id = :user_id",
                ExpressionAttributeValues={":user_id": user_id},
            )

            items = response.get("Items", [])
            logger.info(f"Retrieved {len(items)} wishlist items for user {user_id}")
            return items

        except ClientError as e:
            logger.error(f"Error getting wishlist items: {e}")
            raise

    def add_wishlist_item(self, user_id: str, item: dict):
        """Add a single item to the wishlist with auto-generated ID."""
        try:
            import uuid

            now = datetime.now(timezone.utc).isoformat()

            wishlist_item = {
                "id": str(uuid.uuid4()),  # Auto-generate UUID for primary key
                "user_id": user_id,  # User identifier (attribute)
                "asin": item.get("asin", ""),
                "title": item["title"],
                "price": item["price"],
                "reviews": item.get("reviews", ""),
                "url": item.get("url", ""),
                "item_type": item.get("item_type", "product"),
                "createdAt": now,  # Use Amplify standard field name
                "updatedAt": now,  # Use Amplify standard field name
            }

            # Add type-specific fields
            if item.get("item_type") == "hotel":
                wishlist_item.update(
                    {
                        "hotel_id": item.get("hotel_id", ""),
                        "city_code": item.get("city_code", ""),
                        "rating": item.get("rating", ""),
                        "amenities": item.get("amenities", ""),
                    }
                )
            elif item.get("item_type") == "flight":
                wishlist_item.update(
                    {
                        "flight_id": item.get("flight_id", ""),
                        "origin": item.get("origin", ""),
                        "destination": item.get("destination", ""),
                        "departure_date": item.get("departure_date", ""),
                        "airline": item.get("airline", ""),
                    }
                )

            self.wishlist_table.put_item(Item=wishlist_item)
            logger.info(f"Added {wishlist_item['item_type']} item to wishlist for user {user_id}")

        except ClientError as e:
            logger.error(f"Error adding wishlist item: {e}")
            raise

    def remove_wishlist_items_by_asin(self, user_id: str, asin: str):
        """Remove all items with specific ASIN for a user."""
        try:
            # Use query with GSI to find items with this ASIN for the user
            response = self.wishlist_table.query(
                IndexName="wishlistsByUser_id",
                KeyConditionExpression="user_id = :user_id",
                FilterExpression="asin = :asin",
                ExpressionAttributeValues={":user_id": user_id, ":asin": asin},
            )

            items_to_delete = response.get("Items", [])

            # Delete each item using the id (primary key)
            for item in items_to_delete:
                self.wishlist_table.delete_item(Key={"id": item["id"]})

            logger.info(f"Removed {len(items_to_delete)} items with ASIN {asin} for user {user_id}")
            return len(items_to_delete)

        except ClientError as e:
            logger.error(f"Error removing wishlist items: {e}")
            raise

    # User Profile Management Methods

    def get_user_profile(self, user_id: str):
        """Get user profile from the UserProfile table."""
        try:
            # First try to get by id (primary key)
            response = self.user_profile_table.get_item(Key={"id": user_id})

            if "Item" in response:
                profile = response["Item"]
                logger.info(f"Retrieved user profile for: {user_id}")
                return profile

            # If not found by id, try scanning for userId field
            response = self.user_profile_table.scan(
                FilterExpression="userId = :user_id",
                ExpressionAttributeValues={":user_id": user_id},
            )

            items = response.get("Items", [])
            if items:
                profile = items[0]  # Get the first match
                logger.info(f"Retrieved user profile via userId scan for: {user_id}")
                return profile
            else:
                logger.info(f"No user profile found for: {user_id}")
                return None

        except ClientError as e:
            logger.error(f"Error getting user profile: {e}")
            raise

    def update_itinerary_item_date(self, user_id: str, identifier: str, item_type: str, new_date: str):
        """
        Update the date field for itinerary items (flights/hotels).

        Args:
            user_id: User ID
            identifier: flight_id for flights, hotel_id for hotels
            item_type: Type of item ('flight' or 'hotel')
            new_date: New date in YYYY-MM-DD format

        Returns:
            int: Number of items updated
        """
        try:
            # Get all items for the user
            response = self.wishlist_table.query(
                IndexName="wishlistsByUser_id",
                KeyConditionExpression="user_id = :user_id",
                ExpressionAttributeValues={":user_id": user_id},
            )

            items = response.get("Items", [])
            updated_count = 0

            # Filter items by type and identifier
            for item in items:
                should_update = False

                if item.get("item_type") == item_type:
                    if item_type == "flight" and item.get("flight_id") == identifier:
                        should_update = True
                    elif item_type == "hotel" and item.get("hotel_id") == identifier:
                        should_update = True

                if should_update:
                    # Update the item
                    update_expression = "SET updatedAt = :updated"
                    expression_values = {":updated": datetime.now(timezone.utc).isoformat()}

                    # Add date field based on item type
                    if item_type == "flight":
                        update_expression += ", departure_date = :date"
                        expression_values[":date"] = new_date
                    elif item_type == "hotel":
                        # For hotels, we'll use check_in_date field
                        update_expression += ", check_in_date = :date"
                        expression_values[":date"] = new_date

                    self.wishlist_table.update_item(
                        Key={"id": item["id"]},
                        UpdateExpression=update_expression,
                        ExpressionAttributeValues=expression_values,
                    )
                    updated_count += 1
                    logger.info(f"Updated {item_type} date for item {item['id']}")

            return updated_count

        except ClientError as e:
            logger.error(f"Error updating itinerary item date: {e}")
            raise
