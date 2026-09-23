"""
Device Management System - AWS Lambda Function

This module implements the core backend functionality for the Device Management System
as an AWS Lambda function. It provides MCP (Model Context Protocol) tools for managing
IoT devices, WiFi networks, users, and activities through Amazon DynamoDB operations.

The Lambda function serves as the execution engine for all device management operations,
receiving requests from the Amazon Bedrock AgentCore Gateway and returning formatted
responses according to the MCP protocol specification.

MCP Tools Implemented:
    - list_devices: Retrieve all devices with their status and configuration
    - get_device_settings: Get detailed settings for a specific device
    - list_wifi_networks: List WiFi networks configured on a device
    - list_users: Retrieve all users in the system
    - query_user_activity: Query user activities within a time range
    - update_wifi_ssid: Update the SSID of a WiFi network
    - update_wifi_security: Update the security type of a WiFi network

DynamoDB Tables:
    - Devices: Device inventory and status information
    - DeviceSettings: Device configuration settings
    - WifiNetworks: WiFi network configurations per device
    - Users: User accounts and profiles
    - UserActivities: User activity logs and audit trail

Environment Variables:
    AWS_REGION: AWS region for DynamoDB access (defaults to us-west-2)

Error Handling:
    All functions return standardized error responses with appropriate HTTP status codes
    and descriptive error messages for debugging and user feedback.

Example Lambda Event:
    {
        "tool_name": "list_devices",
        "parameters": {}
    }

Example Response:
    {
        "statusCode": 200,
        "body": [{"device_id": "DG-001", "name": "Device 1", ...}]
    }
"""

import json
import datetime
import logging
import boto3
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def datetime_to_iso(dt):
    """
    Convert datetime object to ISO format string.

    Args:
        dt: datetime object or other value

    Returns:
        str: ISO format datetime string if input is datetime, otherwise original value
    """
    if isinstance(dt, datetime.datetime):
        return dt.isoformat()
    return dt


class DecimalEncoder(json.JSONEncoder):
    """
    Custom JSON encoder for handling DynamoDB Decimal types.

    DynamoDB returns numeric values as Decimal objects which are not
    JSON serializable by default. This encoder converts Decimal objects
    to float for JSON serialization.
    """

    def default(self, o):
        """
        Override default JSON encoding for Decimal objects.

        Args:
            o: Object to encode

        Returns:
            float: If object is Decimal, return as float
            Any: Otherwise use default JSON encoding
        """
        if isinstance(o, Decimal):
            return float(o)
        return super(DecimalEncoder, self).default(o)


def json_dumps(obj):
    """
    JSON serialize object with Decimal handling.

    Args:
        obj: Object to serialize

    Returns:
        str: JSON string with Decimal objects converted to float
    """
    return json.dumps(obj, cls=DecimalEncoder)


def get_dynamodb_resource():
    """
    Initialize and return DynamoDB resource for database operations.

    Returns:
        boto3.resource: DynamoDB resource configured for us-west-2 region

    Note:
        Always uses us-west-2 region for consistency across deployments.
        Assumes Lambda execution role has appropriate DynamoDB permissions.
    """
    aws_region = "us-west-2"
    return boto3.resource("dynamodb", region_name=aws_region)


# Define table names
DEVICES_TABLE = "Devices"
DEVICE_SETTINGS_TABLE = "DeviceSettings"
WIFI_NETWORKS_TABLE = "WifiNetworks"
USERS_TABLE = "Users"
USER_ACTIVITIES_TABLE = "UserActivities"


# Device operations
def get_device(device_id):
    """Get a device by ID"""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(DEVICES_TABLE)
    response = table.get_item(Key={"device_id": device_id})
    return response.get("Item")


def list_devices(limit=100):
    """List all devices"""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(DEVICES_TABLE)
    response = table.scan(Limit=limit)
    return response.get("Items", [])


# Device Settings operations
def get_device_setting(device_id, setting_key):
    """Get a specific device setting"""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(DEVICE_SETTINGS_TABLE)
    response = table.get_item(Key={"device_id": device_id, "setting_key": setting_key})
    return response.get("Item")


def list_device_settings(device_id):
    """List all settings for a device"""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(DEVICE_SETTINGS_TABLE)
    response = table.query(KeyConditionExpression=Key("device_id").eq(device_id))
    return response.get("Items", [])


# WiFi Network operations
def list_wifi_networks(device_id):
    """List all WiFi networks for a device"""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(WIFI_NETWORKS_TABLE)
    response = table.query(KeyConditionExpression=Key("device_id").eq(device_id))
    return response.get("Items", [])


def update_wifi_network(device_id, network_id, update_data):
    """Update a WiFi network"""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(WIFI_NETWORKS_TABLE)

    # Convert datetime objects to ISO format strings
    if "last_updated" in update_data and update_data["last_updated"]:
        update_data["last_updated"] = datetime_to_iso(update_data["last_updated"])
    else:
        update_data["last_updated"] = datetime_to_iso(datetime.datetime.utcnow())

    # Convert float to Decimal for DynamoDB
    if "signal_strength" in update_data and update_data["signal_strength"] is not None:
        update_data["signal_strength"] = Decimal(str(update_data["signal_strength"]))

    # Build update expression
    update_expression = "SET "
    expression_attribute_values = {}
    expression_attribute_names = {}

    for key, value in update_data.items():
        if key not in ["device_id", "network_id"]:  # Skip the primary keys
            update_expression += f"#{key} = :{key}, "
            expression_attribute_values[f":{key}"] = value
            expression_attribute_names[f"#{key}"] = key

    # Remove trailing comma and space
    update_expression = update_expression[:-2]

    response = table.update_item(
        Key={"device_id": device_id, "network_id": network_id},
        UpdateExpression=update_expression,
        ExpressionAttributeValues=expression_attribute_values,
        ExpressionAttributeNames=expression_attribute_names,
        ReturnValues="ALL_NEW",
    )

    return response.get("Attributes")


def update_wifi_ssid(device_id, network_id, ssid):
    """Update the SSID of a WiFi network"""
    return update_wifi_network(device_id, network_id, {"ssid": ssid})


def update_wifi_security(device_id, network_id, security_type):
    """Update the security type of a WiFi network"""
    return update_wifi_network(device_id, network_id, {"security_type": security_type})


# User operations
def list_users(limit=100):
    """List all users"""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(USERS_TABLE)
    response = table.scan(Limit=limit)
    return response.get("Items", [])


# User Activity operations
def query_user_activity(start_date, end_date, user_id=None, activity_type=None, limit=100):
    """Query user activities within a time period"""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(USER_ACTIVITIES_TABLE)

    # Convert datetime objects to ISO format strings
    if isinstance(start_date, datetime.datetime):
        start_date = datetime_to_iso(start_date)

    if isinstance(end_date, datetime.datetime):
        end_date = datetime_to_iso(end_date)

    if user_id:
        # Query by user_id and time range
        key_condition = Key("user_id").eq(user_id) & Key("timestamp").between(start_date, end_date)

        filter_expression = None
        if activity_type:
            filter_expression = Attr("activity_type").eq(activity_type)

        if filter_expression:
            response = table.query(
                KeyConditionExpression=key_condition,
                FilterExpression=filter_expression,
                Limit=limit,
            )
        else:
            response = table.query(KeyConditionExpression=key_condition, Limit=limit)
    elif activity_type:
        # Query by activity_type and time range using GSI
        response = table.query(
            IndexName="ActivityTypeIndex",
            KeyConditionExpression=Key("activity_type").eq(activity_type)
            & Key("timestamp").between(start_date, end_date),
            Limit=limit,
        )
    else:
        # Scan with time range filter
        response = table.scan(
            FilterExpression=Attr("timestamp").between(start_date, end_date),
            Limit=limit,
        )

    return response.get("Items", [])


# MCP Tool implementations
def tool_get_device_settings(device_id):
    """
    Get the settings of a device from the Device API

    Args:
        device_id: The ID of the device to get settings for

    Returns:
        Device settings information
    """
    try:
        device = get_device(device_id)

        if not device:
            return {"error": f"Device not found: {device_id}"}

        settings = list_device_settings(device_id)

        result = {
            "device_id": device["device_id"],
            "device_name": device["name"],
            "model": device["model"],
            "firmware_version": device["firmware_version"],
            "connection_status": device["connection_status"],
            "settings": {},
        }

        for setting in settings:
            result["settings"][setting["setting_key"]] = setting["setting_value"]

        return result
    except Exception as e:
        logger.error(f"Error in get_device_settings: {str(e)}")
        return {"error": str(e)}


def tool_list_devices(limit=25):
    """
    List devices in the Device Remote Management system

    Args:
        limit: Maximum number of devices to return (default: 25)

    Returns:
        List of devices with their details
    """
    try:
        devices = list_devices(limit)
        return devices
    except Exception as e:
        logger.error(f"Error in list_devices: {str(e)}")
        return {"error": str(e)}


def tool_list_wifi_networks(device_id):
    """
    List all WiFi networks for a specific device

    Args:
        device_id: The ID of the device to get WiFi networks for

    Returns:
        List of WiFi networks with their details
    """
    try:
        device = get_device(device_id)

        if not device:
            return {"error": f"Device not found: {device_id}"}

        networks = list_wifi_networks(device_id)

        return {
            "device_id": device["device_id"],
            "device_name": device["name"],
            "wifi_networks": networks,
        }
    except Exception as e:
        logger.error(f"Error in list_wifi_networks: {str(e)}")
        return {"error": str(e)}


def tool_list_users(limit=100):
    """
    List users within an account from the Device API

    Args:
        limit: Maximum number of users to return (default: 100)

    Returns:
        List of users
    """
    try:
        users = list_users(limit)
        return users
    except Exception as e:
        logger.error(f"Error in list_users: {str(e)}")
        return {"error": str(e)}


def tool_query_user_activity(start_date, end_date, user_id=None, activity_type=None, limit=100):
    """
    Query user activity within a time period

    Args:
        start_date: Start date in ISO format (YYYY-MM-DDTHH:MM:SS)
        end_date: End date in ISO format (YYYY-MM-DDTHH:MM:SS)
        user_id: Optional user ID to filter activities
        activity_type: Optional activity type to filter
        limit: Maximum number of activities to return (default: 100)

    Returns:
        List of user activities
    """
    try:
        activities = query_user_activity(start_date, end_date, user_id, activity_type, limit)
        return activities
    except Exception as e:
        logger.error(f"Error in query_user_activity: {str(e)}")
        return {"error": str(e)}


def tool_update_wifi_ssid(device_id, network_id, ssid):
    """
    Update the SSID of a Wi-Fi network on a device

    Args:
        device_id: The ID of the device
        network_id: The ID of the Wi-Fi network
        ssid: The new SSID for the Wi-Fi network

    Returns:
        Result of the SSID update operation
    """
    try:
        # Validate SSID length (1-32 characters)
        if len(ssid) < 1 or len(ssid) > 32:
            return {"error": "SSID must be between 1 and 32 characters"}

        result = update_wifi_ssid(device_id, network_id, ssid)
        return result
    except Exception as e:
        logger.error(f"Error in update_wifi_ssid: {str(e)}")
        return {"error": str(e)}


def tool_update_wifi_security(device_id, network_id, security_type):
    """
    Update the security type of a Wi-Fi network on a device

    Args:
        device_id: The ID of the device
        network_id: The ID of the Wi-Fi network
        security_type: The new security type for the Wi-Fi network (wpa2-psk, wpa3-psk, open, wpa-psk, wep, enterprise)

    Returns:
        Result of the security type update operation
    """
    try:
        # Validate security type
        valid_security_types = [
            "wpa2-psk",
            "wpa3-psk",
            "open",
            "wpa-psk",
            "wep",
            "enterprise",
        ]
        if security_type not in valid_security_types:
            return {"error": f"Invalid security type. Must be one of: {', '.join(valid_security_types)}"}

        result = update_wifi_security(device_id, network_id, security_type)
        return result
    except Exception as e:
        logger.error(f"Error in update_wifi_security: {str(e)}")
        return {"error": str(e)}


# Lambda handler
def lambda_handler(event, context):
    """
    AWS Lambda handler function

    Args:
        event: Lambda event data
        context: Lambda context

    Returns:
        Lambda response
    """
    try:
        # Parse the incoming request
        logger.info(f"Received event: {json.dumps(event)}")

        # Extract the tool name from the event
        tool_name = event["action_name"]
        result = None

        # Call the appropriate function based on tool_name
        if tool_name == "get_device_settings":
            device_id = event["device_id"]
            result = tool_get_device_settings(device_id)

        elif tool_name == "list_devices":
            limit = event.get("limit", 25)
            result = tool_list_devices(limit)

        elif tool_name == "list_wifi_networks":
            device_id = event["device_id"]
            result = tool_list_wifi_networks(device_id)

        elif tool_name == "list_users":
            limit = event.get("limit", 100)
            result = tool_list_users(limit)

        elif tool_name == "query_user_activity":
            start_date = event["start_date"]
            end_date = event["end_date"]
            user_id = event.get("user_id")
            activity_type = event.get("activity_type")
            limit = event.get("limit", 50)
            result = tool_query_user_activity(start_date, end_date, user_id, activity_type, limit)

        elif tool_name == "update_wifi_ssid":
            device_id = event["device_id"]
            network_id = event["network_id"]
            ssid = event["ssid"]
            result = tool_update_wifi_ssid(device_id, network_id, ssid)

        elif tool_name == "update_wifi_security":
            device_id = event["device_id"]
            network_id = event["network_id"]
            security_type = event["security_type"]
            result = tool_update_wifi_security(device_id, network_id, security_type)

        else:
            available_tools = [
                "get_device_settings",
                "list_devices",
                "list_wifi_networks",
                "list_users",
                "query_user_activity",
                "update_wifi_ssid",
                "update_wifi_security",
            ]
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": f"Unknown tool: {tool_name}",
                        "available_tools": available_tools,
                    }
                ),
            }

        # Return the result
        return {"statusCode": 200, "body": json_dumps(result)}

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Internal server error: {str(e)}"}),
        }
