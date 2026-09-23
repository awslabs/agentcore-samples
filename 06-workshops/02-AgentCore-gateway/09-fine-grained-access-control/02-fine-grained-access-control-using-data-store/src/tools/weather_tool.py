"""
Weather Tool - Mock weather API for Gateway

This tool provides mock weather information for different locations.
"""

import json
import random
from datetime import datetime


def lambda_handler(event, context):
    """
    Lambda handler for weather tool.

    Expected input:
    {
        "location": "New York",
        "units": "celsius" | "fahrenheit" (optional)
    }

    Returns mock weather data.
    """
    print(f"Weather tool received event: {json.dumps(event)}")

    # Parse input
    body = event if isinstance(event, dict) else json.loads(event)
    location = body.get("location", "Unknown")
    units = body.get("units", "celsius")

    # Generate mock weather data
    temp_celsius = random.randint(-10, 35)
    temp_fahrenheit = (temp_celsius * 9 / 5) + 32

    conditions = ["Sunny", "Cloudy", "Rainy", "Snowy", "Partly Cloudy", "Overcast"]
    condition = random.choice(conditions)

    humidity = random.randint(30, 90)
    wind_speed = random.randint(0, 30)

    weather_data = {
        "location": location,
        "timestamp": datetime.utcnow().isoformat(),
        "condition": condition,
        "temperature": {
            "celsius": temp_celsius,
            "fahrenheit": round(temp_fahrenheit, 1),
            "units_requested": units,
        },
        "humidity_percent": humidity,
        "wind_speed_kmh": wind_speed,
        "forecast": f"{condition} with {humidity}% humidity",
    }

    response = {
        "statusCode": 200,
        "body": json.dumps({"tool": "weather_tool", "result": weather_data, "success": True}),
    }

    print(f"Weather tool response: {json.dumps(response)}")
    return response


# MCP Tool Definition for Gateway registration
TOOL_DEFINITION = {
    "name": "weather_tool",
    "description": "Get current weather information for a specific location. Returns temperature, conditions, humidity, and wind speed.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "The location to get weather for (e.g., 'New York', 'London', 'Tokyo')",
            },
            "units": {
                "type": "string",
                "description": "Temperature units to use: 'celsius' or 'fahrenheit' (default: celsius)",
            },
        },
        "required": ["location"],
    },
}


if __name__ == "__main__":
    # Test the tool locally
    test_event = {"location": "San Francisco", "units": "fahrenheit"}

    result = lambda_handler(test_event, None)
    print(f"\nTest result:\n{json.dumps(result, indent=2)}")
