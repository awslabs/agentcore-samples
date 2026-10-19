"""Example: Strands agent using RegistryToolProvider.

The agent discovers tools dynamically from AWS Agent Registry
instead of hardcoding them at startup.
"""

import boto3
import json
from strands import Agent
from strands.models import BedrockModel
from registry_tool_provider import RegistryToolProvider

# --- Config ---
REGISTRY_ID = "Vf4gtZ5mreKG"                    # Your registry ID
GATEWAY_URL = "https://gw-xxx.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
REGION = "us-west-2"


def get_gateway_token() -> str:
    """Get a Cognito token for the Gateway. Replace with your auth logic."""
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    creds = json.loads(sm.get_secret_value(SecretId="my-gateway-mcp-cognito-credentials")["SecretString"])
    import httpx
    resp = httpx.post(f"https://{creds['domain']}/oauth2/token", data={
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "scope": creds["scope"],
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    return resp.json()["access_token"]


# --- Agent setup ---
provider = RegistryToolProvider(
    registry_ids=[REGISTRY_ID],
    domains=["weather", "database", "email"],     # Semantic search keywords
    gateway_url=GATEWAY_URL,
    gateway_token_fn=get_gateway_token,
    region=REGION,
    cache_ttl=300,
)

agent = Agent(
    model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-20250514"),
    tool_providers=[provider],
    system_prompt="You are a helpful assistant. Use the available tools to answer questions.",
)

if __name__ == "__main__":
    result = agent("What tools do you have available? List them.")
    print(result)
