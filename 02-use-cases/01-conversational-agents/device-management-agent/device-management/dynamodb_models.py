"""
Device Management System - DynamoDB Table Models and Initialization

This module defines the DynamoDB table schemas and provides initialization
functions for the Device Management System. It creates and manages five
DynamoDB tables with appropriate key schemas, indexes, and provisioned
throughput settings.

The module manages:
- Table schema definitions for all five DynamoDB tables
- Primary key and sort key configurations
- Global Secondary Indexes (GSI) for efficient querying
- Provisioned throughput settings
- Table creation and initialization
- Helper functions for datetime and Decimal handling

DynamoDB Tables:

    1. Devices Table:
       - Primary Key: device_id (String, HASH)
       - Attributes: name, model, firmware_version, connection_status,
                    ip_address, mac_address, last_connected
       - Purpose: Store device inventory and status information

    2. DeviceSettings Table:
       - Primary Key: device_id (String, HASH)
       - Sort Key: setting_key (String, RANGE)
       - Attributes: setting_value, last_updated
       - Purpose: Store device configuration settings (key-value pairs)

    3. WifiNetworks Table:
       - Primary Key: device_id (String, HASH)
       - Sort Key: network_id (String, RANGE)
       - Attributes: ssid, security_type, enabled, channel,
                    signal_strength, last_updated
       - Purpose: Store WiFi network configurations per device

    4. Users Table:
       - Primary Key: user_id (String, HASH)
       - GSI 1: EmailIndex (email as HASH)
       - GSI 2: UsernameIndex (username as HASH)
       - Attributes: username, email, first_name, last_name, role,
                    created_at, last_login
       - Purpose: Store user accounts and profiles

    5. UserActivities Table:
       - Primary Key: user_id (String, HASH)
       - Sort Key: timestamp (String, RANGE)
       - GSI: ActivityTypeIndex (activity_type as HASH, timestamp as RANGE)
       - Attributes: activity_type, description, ip_address
       - Purpose: Store user activity logs and audit trail

Key Features:
    - Composite primary keys for efficient queries
    - Global Secondary Indexes for alternate access patterns
    - Provisioned throughput (5 RCU/5 WCU for tables, 2 RCU/2 WCU for GSIs)
    - Automatic table creation with wait for active state
    - Idempotent initialization (skips existing tables)
    - Helper functions for data type conversions

Global Secondary Indexes:

    EmailIndex (Users table):
    - Partition Key: email
    - Purpose: Query users by email address
    - Use case: Login, user lookup by email

    UsernameIndex (Users table):
    - Partition Key: username
    - Purpose: Query users by username
    - Use case: User search, username validation

    ActivityTypeIndex (UserActivities table):
    - Partition Key: activity_type
    - Sort Key: timestamp
    - Purpose: Query activities by type and time range
    - Use case: Activity reports, security audits

Helper Functions:

    datetime_to_iso(dt):
    - Converts datetime objects to ISO 8601 strings
    - Handles DynamoDB datetime storage requirements
    - Returns original value if not datetime

    json_dumps(obj):
    - JSON serialization with Decimal handling
    - Uses DecimalEncoder for DynamoDB Decimal types
    - Converts Decimal to float for JSON compatibility

    get_dynamodb_resource():
    - Creates boto3 DynamoDB resource
    - Configured for us-west-2 region
    - Returns resource for table operations

Table Creation Functions:

    create_devices_table():
    - Creates Devices table with device_id as primary key
    - Returns table object

    create_device_settings_table():
    - Creates DeviceSettings table with composite key
    - device_id (HASH) + setting_key (RANGE)
    - Returns table object

    create_wifi_networks_table():
    - Creates WifiNetworks table with composite key
    - device_id (HASH) + network_id (RANGE)
    - Returns table object

    create_users_table():
    - Creates Users table with user_id as primary key
    - Includes EmailIndex and UsernameIndex GSIs
    - Returns table object

    create_user_activities_table():
    - Creates UserActivities table with composite key
    - user_id (HASH) + timestamp (RANGE)
    - Includes ActivityTypeIndex GSI
    - Returns table object

    init_db():
    - Initializes all tables if they don't exist
    - Waits for tables to become active
    - Returns True on success, False on failure
    - Idempotent (safe to run multiple times)

Environment Variables:
    AWS_REGION: AWS region for DynamoDB (defaults to us-west-2)

Usage:
    Initialize tables:
    >>> from dynamodb_models import init_db
    >>> init_db()

    Run as script:
    >>> python dynamodb_models.py

    Use helper functions:
    >>> from dynamodb_models import datetime_to_iso, json_dumps
    >>> iso_string = datetime_to_iso(datetime.now())
    >>> json_string = json_dumps({"value": Decimal("123.45")})

Output:
    Creating table Devices...
    Creating table DeviceSettings...
    Creating table WifiNetworks...
    Creating table Users...
    Creating table UserActivities...
    Created tables: Devices, DeviceSettings, WifiNetworks, Users, UserActivities
    DynamoDB tables initialized successfully.

Provisioned Throughput:
    - Tables: 5 Read Capacity Units, 5 Write Capacity Units
    - GSIs: 2 Read Capacity Units, 2 Write Capacity Units
    - Suitable for development and testing
    - Consider on-demand billing for production

Data Types:
    - Strings: device_id, setting_key, ssid, email, etc.
    - Numbers: Stored as Decimal (DynamoDB requirement)
    - Booleans: enabled status
    - Timestamps: ISO 8601 strings (YYYY-MM-DDTHH:MM:SS)

Notes:
    - Always uses us-west-2 region for consistency
    - Tables are created with provisioned capacity mode
    - GSIs enable efficient querying by alternate keys
    - Waits for table creation to complete before returning
    - Skips tables that already exist (no error)
    - Requires appropriate IAM permissions for DynamoDB
"""

import boto3
import datetime
from decimal import Decimal
import json

# Configure DynamoDB connection
# Always use AWS DynamoDB in us-west-2
aws_region = "us-west-2"


# Initialize DynamoDB resource
def get_dynamodb_resource():
    """Get DynamoDB resource based on environment"""
    return boto3.resource("dynamodb", region_name=aws_region)


# Define table names
DEVICES_TABLE = "Devices"
DEVICE_SETTINGS_TABLE = "DeviceSettings"
WIFI_NETWORKS_TABLE = "WifiNetworks"
USERS_TABLE = "Users"
USER_ACTIVITIES_TABLE = "UserActivities"


# Helper function to convert datetime to ISO format string
def datetime_to_iso(dt):
    if isinstance(dt, datetime.datetime):
        return dt.isoformat()
    return dt


# Helper function to handle decimal serialization for DynamoDB
class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super(DecimalEncoder, self).default(o)


def json_dumps(obj):
    return json.dumps(obj, cls=DecimalEncoder)


# Table creation functions
def create_devices_table():
    dynamodb = get_dynamodb_resource()
    table = dynamodb.create_table(
        TableName=DEVICES_TABLE,
        KeySchema=[
            {"AttributeName": "device_id", "KeyType": "HASH"}  # Partition key
        ],
        AttributeDefinitions=[{"AttributeName": "device_id", "AttributeType": "S"}],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    return table


def create_device_settings_table():
    dynamodb = get_dynamodb_resource()
    table = dynamodb.create_table(
        TableName=DEVICE_SETTINGS_TABLE,
        KeySchema=[
            {"AttributeName": "device_id", "KeyType": "HASH"},  # Partition key
            {"AttributeName": "setting_key", "KeyType": "RANGE"},  # Sort key
        ],
        AttributeDefinitions=[
            {"AttributeName": "device_id", "AttributeType": "S"},
            {"AttributeName": "setting_key", "AttributeType": "S"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    return table


def create_wifi_networks_table():
    dynamodb = get_dynamodb_resource()
    table = dynamodb.create_table(
        TableName=WIFI_NETWORKS_TABLE,
        KeySchema=[
            {"AttributeName": "device_id", "KeyType": "HASH"},  # Partition key
            {"AttributeName": "network_id", "KeyType": "RANGE"},  # Sort key
        ],
        AttributeDefinitions=[
            {"AttributeName": "device_id", "AttributeType": "S"},
            {"AttributeName": "network_id", "AttributeType": "S"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    return table


def create_users_table():
    dynamodb = get_dynamodb_resource()
    table = dynamodb.create_table(
        TableName=USERS_TABLE,
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"}  # Partition key
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "email", "AttributeType": "S"},
            {"AttributeName": "username", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "EmailIndex",
                "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 2,
                    "WriteCapacityUnits": 2,
                },
            },
            {
                "IndexName": "UsernameIndex",
                "KeySchema": [{"AttributeName": "username", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 2,
                    "WriteCapacityUnits": 2,
                },
            },
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    return table


def create_user_activities_table():
    dynamodb = get_dynamodb_resource()
    table = dynamodb.create_table(
        TableName=USER_ACTIVITIES_TABLE,
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},  # Partition key
            {"AttributeName": "timestamp", "KeyType": "RANGE"},  # Sort key
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
            {"AttributeName": "activity_type", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "ActivityTypeIndex",
                "KeySchema": [
                    {"AttributeName": "activity_type", "KeyType": "HASH"},
                    {"AttributeName": "timestamp", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 2,
                    "WriteCapacityUnits": 2,
                },
            }
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    return table


# Initialize all tables
def init_db():
    """Initialize DynamoDB tables if they don't exist"""
    try:
        dynamodb = get_dynamodb_resource()
        # Check if tables exist
        existing_tables = [table.name for table in dynamodb.tables.all()]

        tables_to_create = [
            (DEVICES_TABLE, create_devices_table),
            (DEVICE_SETTINGS_TABLE, create_device_settings_table),
            (WIFI_NETWORKS_TABLE, create_wifi_networks_table),
            (USERS_TABLE, create_users_table),
            (USER_ACTIVITIES_TABLE, create_user_activities_table),
        ]

        created_tables = []
        for table_name, create_func in tables_to_create:
            if table_name not in existing_tables:
                table = create_func()
                print(f"Creating table {table_name}...")
                table.meta.client.get_waiter("table_exists").wait(TableName=table_name)
                created_tables.append(table_name)

        if created_tables:
            print(f"Created tables: {', '.join(created_tables)}")
        else:
            print("All tables already exist")

        return True
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        return False


if __name__ == "__main__":
    init_db()
    print("DynamoDB tables initialized successfully.")
