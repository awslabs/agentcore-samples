import boto3


def enable_observability_for_resource(
    resource_arn: str, resource_id: str, account_id: str, region: str = "us-east-1"
):
    """
    Enable observability for a Bedrock AgentCore resource (e.g., Memory Store)
    """
    logs_client = boto3.client("logs", region_name=region)

    # Step 0: Create new log group for vended log delivery
    log_group_name = f"/aws/vendedlogs/bedrock-agentcore/{resource_id}"
    logs_client.create_log_group(logGroupName=log_group_name)
    log_group_arn = f"arn:aws:logs:{region}:{account_id}:log-group:{log_group_name}"

    # Step 1: Create delivery source for logs
    logs_source_response = logs_client.put_delivery_source(
        name=f"{resource_id}-logs-source",
        logType="APPLICATION_LOGS",
        resourceArn=resource_arn,
    )

    # Step 2: Create delivery source for traces
    traces_source_response = logs_client.put_delivery_source(
        name=f"{resource_id}-traces-source", logType="TRACES", resourceArn=resource_arn
    )

    # Step 3: Create delivery destinations
    logs_destination_response = logs_client.put_delivery_destination(
        name=f"{resource_id}-logs-destination",
        deliveryDestinationType="CWL",
        deliveryDestinationConfiguration={
            "destinationResourceArn": log_group_arn,
        },
    )

    # Traces required for memory only
    traces_destination_response = logs_client.put_delivery_destination(
        name=f"{resource_id}-traces-destination", deliveryDestinationType="XRAY"
    )

    # Step 4: Create deliveries (connect sources to destinations)
    logs_delivery = logs_client.create_delivery(
        deliverySourceName=logs_source_response["deliverySource"]["name"],
        deliveryDestinationArn=logs_destination_response["deliveryDestination"]["arn"],
    )

    # Traces required for memory only
    traces_delivery = logs_client.create_delivery(
        deliverySourceName=traces_source_response["deliverySource"]["name"],
        deliveryDestinationArn=traces_destination_response["deliveryDestination"][
            "arn"
        ],
    )

    print(f"Observability enabled for {resource_id}")
    return {
        "logs_delivery_id": logs_delivery["id"],
        "traces_delivery_id": traces_delivery["id"],
    }
