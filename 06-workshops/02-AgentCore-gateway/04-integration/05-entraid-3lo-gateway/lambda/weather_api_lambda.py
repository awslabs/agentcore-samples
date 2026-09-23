# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Weather REST API Lambda — EntraID variant.

Simple HTTP endpoint that returns weather data. Called by AgentCore Gateway
via the OpenAPI target. The Gateway handles token validation (EntraID 3LO),
so this Lambda receives pre-authorized requests.

The Gateway passes the user's EntraID access token in the Authorization header.
For this demo, we trust the Gateway's auth and return mock weather data.
In production, you'd validate the token against EntraID.
"""

import json
import random


def lambda_handler(event, context):
    """Handle GET /weather?location=..."""
    # Log request metadata only (exclude headers which may contain tokens)
    print(f"Method: {event.get('httpMethod', 'unknown')}, Path: {event.get('path', '/')}")

    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")

    if method != "GET":
        return json_response(405, {"error": "Method not allowed"})

    params = event.get("queryStringParameters", {}) or {}
    location = params.get("location", "")

    if not location:
        return json_response(400, {"error": "Missing required parameter: location"})

    # Mock weather data
    weather = {
        "location": location,
        "temperature": round(random.uniform(20, 95), 1),
        "conditions": random.choice(
            [
                "Sunny",
                "Partly Cloudy",
                "Cloudy",
                "Rainy",
                "Thunderstorms",
                "Snowy",
                "Windy",
            ]
        ),
        "humidity": random.randint(20, 95),
    }

    return json_response(200, weather)


def json_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
