"""
Infrastructure Costs Lambda Handler

Retrieves infrastructure costs from AWS Cost Explorer filtered by tenant ID tags.
Only queries for tenants that currently exist in the aggregation table.
"""

import json
import os
from datetime import datetime, timedelta
import boto3

dynamodb = boto3.resource("dynamodb")
ce_client = boto3.client("ce")

# Require AGGREGATION_TABLE_NAME to be set - fail fast if misconfigured
if "AGGREGATION_TABLE_NAME" not in os.environ:
    raise ValueError(
        "AGGREGATION_TABLE_NAME environment variable is required but not set. "
        "This Lambda function cannot operate without proper configuration."
    )

aggregation_table = dynamodb.Table(os.environ["AGGREGATION_TABLE_NAME"])


def get_current_tenant_ids():
    """
    Retrieve list of tenant IDs from the aggregation table.
    Only returns tenants that have aggregation records.
    """
    try:
        # Scan with pagination and server-side filtering
        from boto3.dynamodb.conditions import Attr

        tenant_ids = set()
        last_evaluated_key = None

        while True:
            scan_kwargs = {
                "FilterExpression": Attr("aggregation_key").begins_with("tenant:"),
                # Only retrieve the fields we need
                "ProjectionExpression": "tenant_id",
            }

            if last_evaluated_key:
                scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

            response = aggregation_table.scan(**scan_kwargs)

            # Extract tenant IDs from the filtered results
            for item in response.get("Items", []):
                tenant_id = item.get("tenant_id")
                if tenant_id:
                    tenant_ids.add(tenant_id)

            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

        return list(tenant_ids)
    except Exception as e:
        print(f"Error fetching tenant IDs: {str(e)}")
        return []


def build_cost_explorer_query(tenant_ids, start_date, end_date):
    """
    Build the Cost Explorer query parameters.

    Args:
        tenant_ids: List of tenant IDs to filter by
        start_date: Start date string (YYYY-MM-DD)
        end_date: End date string (YYYY-MM-DD)

    Returns:
        Dictionary of query parameters for get_cost_and_usage
    """
    if not tenant_ids:
        return None

    return {
        "TimePeriod": {"Start": start_date, "End": end_date},
        "Granularity": "MONTHLY",
        "Filter": {
            "Tags": {
                "Key": "tenantId",
                "Values": tenant_ids,
                "MatchOptions": ["EQUALS"],
            }
        },
        "GroupBy": [{"Type": "TAG", "Key": "tenantId"}],
        "Metrics": ["UnblendedCost"],
    }


def query_infrastructure_costs(tenant_ids):
    """
    Query AWS Cost Explorer for infrastructure costs by tenant ID.

    Args:
        tenant_ids: List of tenant IDs to query

    Returns:
        Dictionary mapping tenant_id to infrastructure_cost
    """
    if not tenant_ids:
        return {}

    # Calculate time period (first day of current month to today)
    today = datetime.utcnow()
    start_date = today.replace(day=1).strftime("%Y-%m-%d")
    end_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")  # End date is exclusive

    query_params = build_cost_explorer_query(tenant_ids, start_date, end_date)
    if not query_params:
        return {}

    try:
        response = ce_client.get_cost_and_usage(**query_params)

        # Parse response and aggregate costs by tenant
        costs_by_tenant = {}

        for result in response.get("ResultsByTime", []):
            for group in result.get("Groups", []):
                # Extract tenant ID from group key
                keys = group.get("Keys", [])
                if keys:
                    # Key format is "tenantId$value" or just the value
                    key = keys[0]
                    tenant_id = key.replace("tenantId$", "") if key.startswith("tenantId$") else key

                    # Get cost amount
                    metrics = group.get("Metrics", {})
                    unblended_cost = metrics.get("UnblendedCost", {})
                    amount = float(unblended_cost.get("Amount", 0))

                    # Aggregate costs (in case of multiple time periods)
                    if tenant_id in costs_by_tenant:
                        costs_by_tenant[tenant_id] += amount
                    else:
                        costs_by_tenant[tenant_id] = amount

        return costs_by_tenant

    except Exception as e:
        print(f"Error querying Cost Explorer: {str(e)}")
        return {}


def format_infrastructure_costs(tenant_ids, costs_by_tenant):
    """
    Format infrastructure costs response, ensuring all tenants are included.
    Tenants without Cost Explorer data get zero cost.

    Args:
        tenant_ids: List of all tenant IDs
        costs_by_tenant: Dictionary of costs from Cost Explorer

    Returns:
        List of dictionaries with tenant_id and infrastructure_cost
    """
    result = []

    for tenant_id in tenant_ids:
        cost = costs_by_tenant.get(tenant_id, 0.0)
        result.append(
            {
                "tenant_id": tenant_id,
                "infrastructure_cost": round(cost, 6),  # 6 decimal places precision
            }
        )

    return result


def lambda_handler(event, context):
    """
    GET /infrastructure-costs

    Returns infrastructure costs for all configured tenants.
    """
    try:
        # Get list of current tenant IDs from aggregation table
        tenant_ids = get_current_tenant_ids()

        if not tenant_ids:
            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                    "Access-Control-Allow-Methods": "GET,OPTIONS",
                },
                "body": json.dumps([]),
            }

        # Query Cost Explorer for infrastructure costs
        costs_by_tenant = query_infrastructure_costs(tenant_ids)

        # Format response with all tenants (zero cost for missing)
        result = format_infrastructure_costs(tenant_ids, costs_by_tenant)

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "GET,OPTIONS",
            },
            "body": json.dumps(result),
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": "Failed to retrieve infrastructure costs"}),
        }
