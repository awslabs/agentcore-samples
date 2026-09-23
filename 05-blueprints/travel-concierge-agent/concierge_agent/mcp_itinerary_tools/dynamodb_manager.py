import os
import uuid
import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class DynamoDBManager:
    """DynamoDB client for itinerary operations."""

    def __init__(self, region_name: str = None):
        self.region_name = region_name or os.environ.get("AWS_REGION")

        # Initialize DynamoDB resource
        self.dynamodb = boto3.resource("dynamodb", region_name=self.region_name)

        # Table names from environment variables
        self.user_profile_table_name = os.environ.get("USER_PROFILE_TABLE_NAME")
        self.itinerary_table_name = os.environ.get("ITINERARY_TABLE_NAME")

        # Get table references
        self.user_profile_table = self.dynamodb.Table(self.user_profile_table_name)
        self.itinerary_table = self.dynamodb.Table(self.itinerary_table_name)

        logger.info(f"DynamoDB Manager initialized with region: {self.region_name}")
        logger.info(f"Itinerary table: {self.itinerary_table_name}")

    def get_itinerary_items(self, user_id: str):
        """Get all itinerary items for a user using GSI."""
        try:
            response = self.itinerary_table.query(
                IndexName="itinerariesByUser_id",
                KeyConditionExpression="user_id = :user_id",
                ExpressionAttributeValues={":user_id": user_id},
            )

            items = response.get("Items", [])
            logger.info(f"Retrieved {len(items)} itinerary items for user {user_id}")
            return items

        except ClientError as e:
            logger.error(f"Error getting itinerary items: {e}")
            raise

    def add_itinerary_item(self, user_id: str, item: dict):
        """Add an item to the itinerary."""
        try:
            now = datetime.now(timezone.utc).isoformat()

            itinerary_item = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "type": item.get("item_type", item.get("type", "activity")),
                "title": item.get("title", ""),
                "date": item.get("date", ""),
                "time_of_day": item.get("time_of_day", ""),
                "details": item.get("details", ""),
                "location": item.get("location", ""),
                "price": item.get("price", ""),
                "createdAt": now,
                "updatedAt": now,
            }

            self.itinerary_table.put_item(Item=itinerary_item)
            logger.info(f"Added itinerary item '{item.get('title')}' for user {user_id}")
            return itinerary_item

        except ClientError as e:
            logger.error(f"Error adding itinerary item: {e}")
            raise

    def remove_itinerary_item(self, user_id: str, item_id: str):
        """Remove an item from the itinerary."""
        try:
            self.itinerary_table.delete_item(Key={"id": item_id})
            logger.info(f"Removed itinerary item {item_id} for user {user_id}")

        except ClientError as e:
            logger.error(f"Error removing itinerary item: {e}")
            raise

    def update_itinerary_item(self, user_id: str, item_id: str, updates: dict):
        """Update an itinerary item."""
        try:
            now = datetime.now(timezone.utc).isoformat()

            # Build update expression
            update_parts = []
            expr_values = {":updatedAt": now}
            expr_names = {}

            for key, value in updates.items():
                safe_key = f"#{key}"
                expr_names[safe_key] = key
                expr_values[f":{key}"] = value
                update_parts.append(f"{safe_key} = :{key}")

            update_parts.append("updatedAt = :updatedAt")
            update_expression = "SET " + ", ".join(update_parts)

            self.itinerary_table.update_item(
                Key={"id": item_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expr_values,
                ExpressionAttributeNames=expr_names if expr_names else None,
            )
            logger.info(f"Updated itinerary item {item_id} for user {user_id}")

        except ClientError as e:
            logger.error(f"Error updating itinerary item: {e}")
            raise

    def get_user_profile(self, user_id: str):
        """Get user profile from the UserProfile table."""
        try:
            response = self.user_profile_table.get_item(Key={"id": user_id})

            if "Item" in response:
                return response["Item"]
            return None

        except ClientError as e:
            logger.error(f"Error getting user profile: {e}")
            raise
