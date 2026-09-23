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
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "asin": item.get("asin", ""),
                "title": item["title"],
                "price": item["price"],
                "reviews": item.get("reviews", ""),
                "url": item.get("url", ""),
                "item_type": item.get("item_type", "product"),
                "createdAt": now,
                "updatedAt": now,
            }

            self.wishlist_table.put_item(Item=wishlist_item)
            logger.info(f"Added {wishlist_item['item_type']} item to wishlist for user {user_id}")

        except ClientError as e:
            logger.error(f"Error adding wishlist item: {e}")
            raise

    def get_user_profile(self, user_id: str):
        """Get user profile from the UserProfile table."""
        try:
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
                profile = items[0]
                logger.info(f"Retrieved user profile via userId scan for: {user_id}")
                return profile
            else:
                logger.info(f"No user profile found for: {user_id}")
                return None

        except ClientError as e:
            logger.error(f"Error getting user profile: {e}")
            raise
