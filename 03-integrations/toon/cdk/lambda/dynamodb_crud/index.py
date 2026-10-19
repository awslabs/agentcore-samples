"""
DynamoDB CRUD Lambda Handler for Bedrock Agent Core

Supports read and batch read operations on the Customers table.
"""

import json
import os
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("TABLE_NAME", "Customers")
table = dynamodb.Table(TABLE_NAME)


def get_named_parameter(event, name):
    if name not in event:
        return None
    return event.get(name)


def get_customer(customer_id: str, email: str = None) -> dict:
    """Get a customer by customer_id and optionally email."""
    if email:
        response = table.get_item(
            Key={
                "customer_id": customer_id,
                "email": email,
            }
        )
        if "Item" not in response:
            raise ValueError(
                f"Customer not found with customer_id={customer_id} and email={email}"
            )
        return response["Item"]
    else:
        response = table.query(
            KeyConditionExpression=Key("customer_id").eq(customer_id)
        )
        items = response.get("Items", [])
        if not items:
            raise ValueError(f"No customer found with customer_id={customer_id}")
        return items[0] if len(items) == 1 else items


def query_by_region(region: str, limit: int = 50) -> dict:
    """Query customers by region using the RegionIndex GSI."""
    valid_regions = [
        "Northeast",
        "Southeast",
        "Midwest",
        "South",
        "Southwest",
        "West",
        "Northwest",
        "Mountain",
    ]
    if region not in valid_regions:
        raise ValueError(f"Invalid region: {region}. Must be one of: {valid_regions}")

    response = table.query(
        IndexName="RegionIndex",
        KeyConditionExpression=Key("region").eq(region),
        Limit=limit,
    )
    return {
        "customers": response.get("Items", []),
        "count": response.get("Count", 0),
        "region": region,
    }


def query_by_tier(subscription_tier: str, limit: int = 50) -> dict:
    """Query customers by subscription tier using the SubscriptionTierIndex GSI."""
    valid_tiers = ["Free", "Basic", "Standard", "Premium", "Enterprise"]
    if subscription_tier not in valid_tiers:
        raise ValueError(
            f"Invalid subscription tier: {subscription_tier}. Must be one of: {valid_tiers}"
        )

    response = table.query(
        IndexName="SubscriptionTierIndex",
        KeyConditionExpression=Key("subscription_tier").eq(subscription_tier),
        Limit=limit,
        ScanIndexForward=False,
    )
    return {
        "customers": response.get("Items", []),
        "count": response.get("Count", 0),
        "subscription_tier": subscription_tier,
    }


def batch_get_customers() -> dict:
    """Get all customers from the DynamoDB table."""
    all_customers = []
    last_evaluated_key = None

    while True:
        scan_kwargs = {}
        if last_evaluated_key:
            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

        response = table.scan(**scan_kwargs)
        all_customers.extend(response.get("Items", []))

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    return {
        "customers": all_customers,
        "count": len(all_customers),
    }


def scan_customers(limit: int = 50, last_evaluated_key: dict = None) -> dict:
    """Scan the customers table with pagination support."""
    scan_kwargs = {"Limit": min(limit, 100)}
    if last_evaluated_key:
        scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

    response = table.scan(**scan_kwargs)
    result = {
        "customers": response.get("Items", []),
        "count": response.get("Count", 0),
        "scanned_count": response.get("ScannedCount", 0),
    }
    if "LastEvaluatedKey" in response:
        result["last_evaluated_key"] = response["LastEvaluatedKey"]
        result["has_more"] = True
    else:
        result["has_more"] = False
    return result


def convert_decimals(obj):
    """Convert Decimal types from DynamoDB to native Python types."""
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    return obj


def lambda_handler(event, context):
    """Main Lambda handler for Bedrock Agent Core."""
    print(f"Event: {json.dumps(event, default=str)}")

    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    resource = extended_tool_name.split("___")[1]

    print(f"Resource: {resource}")

    if resource == "get_customer":
        customer_id = get_named_parameter(event, "customer_id")
        email = get_named_parameter(event, "email")

        if not customer_id:
            return {"error": "Please provide customer_id"}

        try:
            customer = get_customer(customer_id=customer_id, email=email)
        except Exception as e:
            print(e)
            return {"error": str(e)}

        return convert_decimals(customer)

    elif resource == "query_by_region":
        region = get_named_parameter(event, "region")
        limit = get_named_parameter(event, "limit") or 50

        if not region:
            return {"error": "Please provide region"}

        try:
            result = query_by_region(region=region, limit=int(limit))
        except Exception as e:
            print(e)
            return {"error": str(e)}

        return convert_decimals(result)

    elif resource == "query_by_tier":
        subscription_tier = get_named_parameter(event, "subscription_tier")
        limit = get_named_parameter(event, "limit") or 50

        if not subscription_tier:
            return {"error": "Please provide subscription_tier"}

        try:
            result = query_by_tier(
                subscription_tier=subscription_tier, limit=int(limit)
            )
        except Exception as e:
            print(e)
            return {"error": str(e)}

        return convert_decimals(result)

    elif resource == "batch_get_customers":
        try:
            result = batch_get_customers()
        except Exception as e:
            print(e)
            return {"error": str(e)}

        return convert_decimals(result)

    elif resource == "scan_customers":
        limit = get_named_parameter(event, "limit") or 50
        last_evaluated_key = get_named_parameter(event, "last_evaluated_key")

        try:
            result = scan_customers(
                limit=int(limit), last_evaluated_key=last_evaluated_key
            )
        except Exception as e:
            print(e)
            return {"error": str(e)}

        return convert_decimals(result)

    return {"error": f"Unknown toolname: {resource}"}
