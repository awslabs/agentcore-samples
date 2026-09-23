"""Invoke the deployed travel agent. Reads runtime_config.json written by deploy.py."""

import json
import boto3

with open("runtime_config.json") as f:
    config = json.load(f)

client = boto3.client("bedrock-agentcore", region_name=config["region"])

prompts = [
    "I'm planning a weekend trip to Kyoto in spring. What are the must-visit places?",
    "What are the best beaches in Thailand for a budget traveler?",
    "Suggest a 5-day itinerary for first-time visitors to Paris.",
]

for prompt in prompts:
    print(f"\nPrompt: {prompt}")
    print("-" * 60)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=config["runtime_arn"],
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": prompt}).encode(),
    )
    body = response["response"].read().decode()
    try:
        print(json.loads(body))
    except json.JSONDecodeError:
        print(body)
