"""
Shared utilities for Lambda functions
"""

import json
from typing import Dict, Any

# Standard CORS headers for all API responses
CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
}


def create_response(status_code: int, body: Dict[str, Any], headers: Dict[str, str] = None) -> Dict[str, Any]:
    """
    Create a standardized API Gateway response

    Args:
        status_code: HTTP status code
        body: Response body (will be JSON serialized)
        headers: Optional additional headers (will be merged with CORS headers)

    Returns:
        API Gateway response dict
    """
    response_headers = CORS_HEADERS.copy()
    if headers:
        response_headers.update(headers)

    return {
        "statusCode": status_code,
        "headers": response_headers,
        "body": json.dumps(body, default=str),
    }


def create_error_response(status_code: int, error_message: str) -> Dict[str, Any]:
    """
    Create a standardized error response

    Args:
        status_code: HTTP error status code
        error_message: Error message

    Returns:
        API Gateway error response dict
    """
    return create_response(status_code, {"error": error_message})


def create_success_response(data: Any, message: str = None) -> Dict[str, Any]:
    """
    Create a standardized success response

    Args:
        data: Response data
        message: Optional success message

    Returns:
        API Gateway success response dict
    """
    body = {"data": data}
    if message:
        body["message"] = message

    return create_response(200, body)
