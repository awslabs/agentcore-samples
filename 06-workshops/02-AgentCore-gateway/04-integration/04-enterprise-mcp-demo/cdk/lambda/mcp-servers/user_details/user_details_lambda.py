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
    target_id = context.client_context.custom["bedrockAgentCoreTargetId"]

    # Process the request based on the tool name
    if tool_name == "get_user_email":
        # Handle get_user_email tool
        print("Processing get_user_email tool")
        user_id = event.get("userId", target_id)
        print(f"User ID: {user_id}")
        return f"User email for user {user_id} retrieved! Email: john.doe@example.com"
    elif tool_name == "get_user_cc_number":
        # Handle get_user_cc_number tool
        print("Processing get_user_cc_number tool")
        user_id = event.get("userId", target_id)
        print(f"User ID: {user_id}")
        return f"User credit card number for user {user_id} retrieved! CC Number: 1234-5678-9012-3456"
    else:
        # Handle unknown tool
        pass
