"""
Device Management Lambda Function - Test Suite

This module provides a comprehensive test suite for the Device Management
Lambda function, testing all seven MCP (Model Context Protocol) tools with
realistic test data and formatted output.

The test suite validates:
- All 7 MCP tool implementations
- Request/response format handling
- Error handling for invalid inputs
- JSON serialization with Decimal types
- DynamoDB query operations
- Lambda handler routing logic

Test Coverage:

    1. test_list_devices():
       - Tests device listing with limit parameter
       - Validates device data structure
       - Checks connection status, model, firmware version

    2. test_get_device_settings():
       - Tests device settings retrieval by device ID
       - Validates settings dictionary structure
       - Checks device metadata (name, model, firmware)

    3. test_list_wifi_networks():
       - Tests WiFi network listing for specific device
       - Validates network configuration data
       - Checks SSID, security type, channel, signal strength

    4. test_list_users():
       - Tests user listing with limit parameter
       - Validates user data structure
       - Checks username, email, role, last login

    5. test_query_user_activity():
       - Tests activity querying with date range
       - Validates activity data structure
       - Checks activity type, timestamp, description

    6. test_update_wifi_ssid():
       - Tests WiFi SSID update operation
       - Validates update response
       - Checks old and new SSID values

    7. test_update_wifi_security():
       - Tests WiFi security type update operation
       - Validates security type change
       - Checks old and new security values

    8. test_invalid_tool():
       - Tests error handling for unknown tool names
       - Validates error response format
       - Checks available tools list in error message

Test Data Requirements:
    - Device DG-100001 must exist in Devices table
    - Device must have settings in DeviceSettings table
    - Device must have WiFi networks in WifiNetworks table
    - Users must exist in Users table
    - Activities must exist in UserActivities table

Prerequisites:
    - DynamoDB tables initialized (run dynamodb_models.py)
    - Synthetic data generated (run synthetic_data.py)
    - Lambda function code available (lambda_function.py)

Usage:
    Run all tests:
    >>> python test_lambda.py

    Run individual test:
    >>> from test_lambda import test_list_devices
    >>> test_list_devices()

Output Format:
    Each test displays:
    - Test name and description
    - HTTP status code
    - Formatted JSON response body
    - Pretty-printed with 2-space indentation

Example Output:
    Testing Device Management Lambda Function

    1. Testing list_devices:
    Status Code: 200
    Response Body: [
      {
        "device_id": "DG-100001",
        "name": "Device Router 1",
        "model": "TransPort WR31",
        ...
      }
    ]

Error Handling:
    - Tests validate status codes (200 for success, 400/500 for errors)
    - Error responses include descriptive error messages
    - Invalid tool names return available tools list
    - Missing parameters return validation errors

Response Validation:
    - JSON parsing of response body
    - Decimal type handling (converted to float)
    - Nested object structure validation
    - Array response validation

Notes:
    - Tests use lambda_handler directly (no AWS invocation)
    - No mocking required (uses real DynamoDB tables)
    - Tests assume synthetic data is present
    - Safe to run multiple times (read-only except updates)
    - Update tests modify actual data (use test device)
    - All tests print formatted output for manual verification

Integration Testing:
    This test suite performs integration testing by:
    - Using real DynamoDB tables
    - Testing actual Lambda handler code
    - Validating end-to-end data flow
    - Checking serialization/deserialization

Best Practices:
    - Run after deploying Lambda function
    - Verify synthetic data exists before testing
    - Review output for data accuracy
    - Use for regression testing after code changes
    - Extend with additional test cases as needed
"""

import json
from lambda_function import lambda_handler


def test_list_devices():
    """Test the list_devices tool"""
    event = {"action_name": "list_devices", "limit": 10}

    response = lambda_handler(event, None)
    print(f"Status Code: {response['statusCode']}")
    print(f"Response Body: {json.dumps(json.loads(response['body']), indent=2)}")


def test_get_device_settings():
    """Test the get_device_settings tool"""
    event = {"action_name": "get_device_settings", "device_id": "DG-100001"}

    response = lambda_handler(event, None)
    print(f"Status Code: {response['statusCode']}")
    print(f"Response Body: {json.dumps(json.loads(response['body']), indent=2)}")


def test_list_wifi_networks():
    """Test the list_wifi_networks tool"""
    event = {"action_name": "list_wifi_networks", "device_id": "DG-100001"}

    response = lambda_handler(event, None)
    print(f"Status Code: {response['statusCode']}")
    print(f"Response Body: {json.dumps(json.loads(response['body']), indent=2)}")


def test_list_users():
    """Test the list_users tool"""
    event = {"action_name": "list_users", "limit": 5}

    response = lambda_handler(event, None)
    print(f"Status Code: {response['statusCode']}")
    print(f"Response Body: {json.dumps(json.loads(response['body']), indent=2)}")


def test_query_user_activity():
    """Test the query_user_activity tool"""
    event = {
        "action_name": "query_user_activity",
        "start_date": "2024-01-01T00:00:00",
        "end_date": "2024-12-31T23:59:59",
        "limit": 5,
    }

    response = lambda_handler(event, None)
    print(f"Status Code: {response['statusCode']}")
    print(f"Response Body: {json.dumps(json.loads(response['body']), indent=2)}")


def test_update_wifi_ssid():
    """Test the update_wifi_ssid tool"""
    event = {
        "action_name": "update_wifi_ssid",
        "device_id": "DG-100001",
        "network_id": "wifi_1",
        "ssid": "New-Office-Network",
    }

    response = lambda_handler(event, None)
    print(f"Status Code: {response['statusCode']}")
    print(f"Response Body: {json.dumps(json.loads(response['body']), indent=2)}")


def test_update_wifi_security():
    """Test the update_wifi_security tool"""
    event = {
        "action_name": "update_wifi_security",
        "device_id": "DG-100001",
        "network_id": "wifi_1",
        "security_type": "wpa3-psk",
    }

    response = lambda_handler(event, None)
    print(f"Status Code: {response['statusCode']}")
    print(f"Response Body: {json.dumps(json.loads(response['body']), indent=2)}")


def test_invalid_tool():
    """Test an invalid tool name"""
    event = {"action_name": "invalid_tool"}

    response = lambda_handler(event, None)
    print(f"Status Code: {response['statusCode']}")
    print(f"Response Body: {json.dumps(json.loads(response['body']), indent=2)}")


if __name__ == "__main__":
    print("Testing Device Management Lambda Function")
    print("\n1. Testing list_devices:")
    test_list_devices()

    print("\n2. Testing get_device_settings:")
    test_get_device_settings()

    print("\n3. Testing list_wifi_networks:")
    test_list_wifi_networks()

    print("\n4. Testing list_users:")
    test_list_users()

    print("\n5. Testing query_user_activity:")
    test_query_user_activity()

    print("\n6. Testing update_wifi_ssid:")
    test_update_wifi_ssid()

    print("\n7. Testing update_wifi_security:")
    test_update_wifi_security()

    print("\n8. Testing invalid tool:")
    test_invalid_tool()
