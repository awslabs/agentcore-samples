"""
Cognito Post-Authentication Lambda Trigger
Logs user login events to DynamoDB table for audit and analytics purposes.
"""

import json
import boto3
import os
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
table_name = os.environ.get("LOGIN_AUDIT_TABLE", "lakehouse_user_login_audit")
table = dynamodb.Table(table_name)


def lambda_handler(event, context):
    """
    Cognito Post-Authentication trigger handler.

    This function is invoked after a user successfully authenticates.
    It logs the login event to DynamoDB for audit purposes.

    Args:
        event: Cognito trigger event containing user attributes and request context
        context: Lambda context object

    Returns:
        The original event (required by Cognito triggers)
    """

    try:
        # Extract user information from the event
        user_attributes = event.get("request", {}).get("userAttributes", {})
        username = event.get("userName", "unknown")
        user_pool_id = event.get("userPoolId", "unknown")

        # Extract request context
        request_context = event.get("request", {})

        # Get current timestamp
        timestamp = datetime.utcnow().isoformat()

        # Prepare login record
        login_record = {
            "user_id": username,  # Primary key
            "login_timestamp": timestamp,  # Sort key
            "user_pool_id": user_pool_id,
            "email": user_attributes.get("email", ""),
            "email_verified": user_attributes.get("email_verified", "false"),
            "cognito_username": user_attributes.get("cognito:username", username),
            "groups": user_attributes.get("cognito:groups", "[]"),
            "client_id": request_context.get("clientId", ""),
            "source_ip": request_context.get("sourceIp", ""),
            "user_agent": request_context.get("userAgent", ""),
            "event_type": "post_authentication",
            "ttl": int(datetime.utcnow().timestamp()) + (90 * 24 * 60 * 60),  # 90 days TTL
        }

        # Write to DynamoDB
        table.put_item(Item=login_record)

        print(f"Successfully logged authentication for user: {username}")

    except Exception as e:
        # Log error but don't fail authentication
        print(f"Error logging authentication: {str(e)}")
        print(f"Event: {json.dumps(event)}")

    # Always return the event to allow authentication to proceed
    return event
