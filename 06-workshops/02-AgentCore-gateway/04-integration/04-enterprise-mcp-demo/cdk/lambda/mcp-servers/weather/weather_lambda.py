# Access context properties in your Lambda function
def lambda_handler(event, context):
    print(event)
    print(context)
    # Since the visible tool name includes the target name as a prefix, we can use this delimiter to strip the prefix
    delimiter = "___"

    # Get the tool name from the context
    originalToolName = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = originalToolName[originalToolName.index(delimiter) + len(delimiter) :]

    # Get other context properties
    _message_version = context.client_context.custom["bedrockAgentCoreMessageVersion"]
    _aws_request_id = context.client_context.custom["bedrockAgentCoreAwsRequestId"]
    _mcp_message_id = context.client_context.custom["bedrockAgentCoreMcpMessageId"]
    _gateway_id = context.client_context.custom["bedrockAgentCoreGatewayId"]
    _target_id = context.client_context.custom["bedrockAgentCoreTargetId"]

    # Process the request based on the tool name
    if tool_name == "get_weather":
        # Handle searchProducts tool
        print("Processing get_weather tool")
        return f"It is vey sunny today in {event.get('timezone', 'unknown')}!"
    else:
        # Handle unknown tool
        pass
