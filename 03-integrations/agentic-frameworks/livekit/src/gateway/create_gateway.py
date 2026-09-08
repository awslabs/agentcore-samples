#!/usr/bin/env python3

import boto3

client = boto3.client("bedrock-agent", region_name="us-east-1")

# Create gateway
response = client.create_agent(
    agentName="interview-evaluation-gateway",
    description="Gateway for interview evaluation with Lambda tools",
    foundationModel="anthropic.claude-3-5-sonnet-20241022-v2:0",
    instruction="You are a gateway that provides tools to evaluate interview transcripts.",
    agentResourceRoleArn="arn:aws:iam::123456789012:role/service-role/AmazonBedrockExecutionRoleForAgents_INTERVIEW",
)

agent_id = response["agent"]["agentId"]
print(f"Gateway created: {agent_id}")

# Wait for agent to be ready
import time

print("Waiting for agent to be ready...")
for i in range(30):
    agent_status = client.get_agent(agentId=agent_id)["agent"]["agentStatus"]
    if agent_status in ["NOT_PREPARED", "PREPARED"]:
        print(f"Agent ready: {agent_status}")
        break
    time.sleep(2)

# Create action group with Lambda tool
action_group = client.create_agent_action_group(
    agentId=agent_id,
    agentVersion="DRAFT",
    actionGroupName="evaluation-tools",
    description="Tools for evaluating interviews",
    actionGroupExecutor={
        "lambda": "arn:aws:lambda:us-east-1:123456789012:function:EvaluateInterview"
    },
    functionSchema={
        "functions": [
            {
                "name": "evaluate_interview",
                "description": "Evaluates interview transcripts using Claude Sonnet and generates feedback report with PDF",
                "parameters": {
                    "session_id": {
                        "type": "string",
                        "description": "The session ID of the interview to evaluate",
                        "required": True,
                    }
                },
            }
        ]
    },
)

print(f"Action group created: {action_group['agentActionGroup']['actionGroupId']}")
print(f"\nGateway ARN: arn:aws:bedrock-agent:us-east-1:123456789012:agent/{agent_id}")
