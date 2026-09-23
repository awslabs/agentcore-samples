import json
import os
import boto3
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")
aggregation_table = dynamodb.Table(os.environ["AGGREGATION_TABLE_NAME"])


def lambda_handler(event, context):
    print(f"Received {len(event['Records'])} DynamoDB stream records")

    # Dictionary to accumulate tokens per tenant
    tenant_aggregations = {}

    for record in event["Records"]:
        if record["eventName"] == "INSERT":
            # New item was added
            new_item = record["dynamodb"]["NewImage"]

            # Extract data from DynamoDB format
            item_id = new_item.get("id", {}).get("S", "unknown")
            timestamp = new_item.get("timestamp", {}).get("S", "unknown")
            tenant_id = new_item.get("tenant_id", {}).get("S", "default")
            input_tokens = int(new_item.get("input_tokens", {}).get("N", "0"))
            output_tokens = int(new_item.get("output_tokens", {}).get("N", "0"))
            total_tokens = int(new_item.get("total_tokens", {}).get("N", "0"))
            user_message = new_item.get("user_message", {}).get("S", "")

            print("New token usage record:")
            print(f"  ID: {item_id}")
            print(f"  Tenant ID: {tenant_id}")
            print(f"  Timestamp: {timestamp}")
            print(f"  Input Tokens: {input_tokens}")
            print(f"  Output Tokens: {output_tokens}")
            print(f"  Total Tokens: {total_tokens}")
            print(f"  User Message: {user_message[:50]}...")

            # Accumulate tokens per tenant
            if tenant_id not in tenant_aggregations:
                tenant_aggregations[tenant_id] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "request_count": 0,
                }

            tenant_aggregations[tenant_id]["input_tokens"] += input_tokens
            tenant_aggregations[tenant_id]["output_tokens"] += output_tokens
            tenant_aggregations[tenant_id]["total_tokens"] += total_tokens
            tenant_aggregations[tenant_id]["request_count"] += 1

        elif record["eventName"] == "MODIFY":
            print(f"Item modified: {record['dynamodb']['Keys']}")
        elif record["eventName"] == "REMOVE":
            print(f"Item removed: {record['dynamodb']['Keys']}")

    # Update aggregation table with running totals per tenant
    for tenant_id, aggregation in tenant_aggregations.items():
        try:
            aggregation_key = f"tenant:{tenant_id}"

            # Calculate cost increments
            # Pricing: $0.003 per 1000 input tokens, $0.015 per 1000 output tokens
            input_cost_increment = Decimal(aggregation["input_tokens"]) * Decimal("0.003") / Decimal("1000")
            output_cost_increment = Decimal(aggregation["output_tokens"]) * Decimal("0.015") / Decimal("1000")
            total_cost_increment = input_cost_increment + output_cost_increment

            # Use atomic counter to increment the totals for this tenant
            response = aggregation_table.update_item(
                Key={"aggregation_key": aggregation_key},
                UpdateExpression="ADD input_tokens :input, output_tokens :output, total_tokens :total, request_count :count, input_cost :input_cost, output_cost :output_cost, total_cost :total_cost SET tenant_id = :tenant_id",
                ExpressionAttributeValues={
                    ":input": Decimal(aggregation["input_tokens"]),
                    ":output": Decimal(aggregation["output_tokens"]),
                    ":total": Decimal(aggregation["total_tokens"]),
                    ":count": Decimal(aggregation["request_count"]),
                    ":input_cost": input_cost_increment,
                    ":output_cost": output_cost_increment,
                    ":total_cost": total_cost_increment,
                    ":tenant_id": tenant_id,
                },
                ReturnValues="ALL_NEW",
            )

            updated_item = response["Attributes"]
            print(f"\nUpdated aggregation for tenant {tenant_id}:")
            print(f"  Total Input Tokens: {updated_item['input_tokens']}")
            print(f"  Total Output Tokens: {updated_item['output_tokens']}")
            print(f"  Total Tokens: {updated_item['total_tokens']}")
            print(f"  Total Requests: {updated_item['request_count']}")
            print(f"  Total Input Cost: ${float(updated_item.get('input_cost', 0)):.6f}")
            print(f"  Total Output Cost: ${float(updated_item.get('output_cost', 0)):.6f}")
            print(f"  Total Cost: ${float(updated_item.get('total_cost', 0)):.6f}")

        except Exception as e:
            print(f"Error updating aggregation table for tenant {tenant_id}: {str(e)}")
            raise

    total_records = len(event["Records"])
    total_tenants = len(tenant_aggregations)

    return {
        "statusCode": 200,
        "body": json.dumps(f"Processed {total_records} records for {total_tenants} tenant(s)"),
    }
