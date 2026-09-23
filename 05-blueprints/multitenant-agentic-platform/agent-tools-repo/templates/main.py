from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
import boto3
import os
from datetime import datetime
import uuid
import json

app = BedrockAgentCoreApp(debug=True)

# Configuration placeholders (replaced during deployment)
QUEUE_URL = "QUEUE_URL_VALUE"
TENANT_ID = "TENANT_ID_VALUE"
AGENT_RUNTIME_ID = "AGENT_RUNTIME_ID_VALUE"
MODEL_ID = "MODEL_ID_VALUE"
SYSTEM_PROMPT = """SYSTEM_PROMPT_VALUE"""

# Initialize AWS clients
region = os.environ.get("AWS_REGION", "us-west-2")
sqs = boto3.client("sqs", region_name=region)
dynamodb = boto3.resource("dynamodb", region_name=region)
config_table = dynamodb.Table("agent-configurations")

# Tools will be injected here by the build system

# Initialize agent with configuration
agent = Agent(model=MODEL_ID, system_prompt=SYSTEM_PROMPT)


def send_usage_to_sqs(input_tokens, output_tokens, total_tokens, user_message, response_message, tenant_id):
    """Send token usage metrics to SQS for tracking and billing"""
    try:
        message_body = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": tenant_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "user_message": user_message,
            "response_message": response_message,
        }

        sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message_body))

        app.logger.info(
            f"Sent usage to SQS for tenant {tenant_id}: {input_tokens} input, {output_tokens} output tokens"
        )
    except Exception as e:
        app.logger.error(f"Failed to send usage to SQS: {str(e)}")


@app.entrypoint
def invoke(payload):
    """
    Agent entrypoint - handles incoming requests and returns responses.

    Supports tool usage when tools are selected during deployment.
    """
    # Extract message from payload (supports both 'message' and 'prompt' keys)
    user_message = payload.get("message") or payload.get("prompt", "Hello!")

    app.logger.info(f"Received request from tenant {TENANT_ID}")
    app.logger.info(f"User message: {user_message}")
    app.logger.info(f"Full payload: {json.dumps(payload)}")

    try:
        # Invoke agent (tools are automatically available if injected)
        result = agent(user_message)

        # Extract response
        response_message = result.message

        # Send usage metrics to SQS
        if hasattr(result, "metrics"):
            input_tokens = result.metrics.accumulated_usage.get("inputTokens", 0)
            output_tokens = result.metrics.accumulated_usage.get("outputTokens", 0)
            total_tokens = result.metrics.accumulated_usage.get("totalTokens", 0)

            app.logger.info(f"Token usage - Input: {input_tokens}, Output: {output_tokens}, Total: {total_tokens}")

            send_usage_to_sqs(
                input_tokens,
                output_tokens,
                total_tokens,
                user_message,
                response_message,
                TENANT_ID,
            )
        else:
            app.logger.warning("No metrics available in result")

        return {"result": response_message}

    except Exception as e:
        error_message = f"Error processing request: {str(e)}"
        app.logger.error(error_message)
        return {"error": error_message}


if __name__ == "__main__":
    app.run()
