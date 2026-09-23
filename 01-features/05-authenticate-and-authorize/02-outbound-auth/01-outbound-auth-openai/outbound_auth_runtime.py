"""
Outbound Auth with API Key Credential Provider (OpenAI / Azure OpenAI).

Demonstrates how to configure a Strands agent on AgentCore Runtime to securely
retrieve an API key at runtime using the AgentCore Identity API Key credential
provider, rather than hardcoding secrets in the agent code.

Key concepts:
- API Key credential provider: stores and vends API keys securely
- @requires_api_key decorator: retrieves the key at runtime
- Agent uses the key to call Azure OpenAI via LiteLLM

Usage:
    python outbound_auth_runtime.py

Prerequisites:
    - AWS CLI configured with credentials
    - Azure OpenAI (or OpenAI) API key
    - pip install -r requirements.txt
    - Set environment variables (see Configuration below)
"""

import json
import os
import time

import boto3
from boto3.session import Session

# ── Configuration ─────────────────────────────────────────────────────────────

# Set these environment variables before running:
#   AZURE_OPENAI_API_KEY     - your Azure OpenAI or OpenAI API key
#   AZURE_API_BASE           - Azure OpenAI endpoint URL
#   AZURE_API_VERSION        - e.g. "2024-02-15-preview"
# Optional:
#   OPENAI_PROVIDER          - "openai" or "azure" (default: "azure")

PROVIDER_NAME = "openai-apikey-provider"
AGENT_NAME = f"strands_agents_openai_{int(time.time()) % 100000}"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name or "us-east-1"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")


# ── Agent Code (deployed to AgentCore Runtime) ─────────────────────────────────

AGENT_CODE = '''"""
Strands agent using Azure OpenAI via LiteLLM, retrieving the API key
from AgentCore Identity API Key credential provider at runtime.
"""
import asyncio
import os
from bedrock_agentcore.identity.auth import requires_api_key
from strands import Agent, tool
from strands_tools import calculator
from strands.models.litellm import LiteLLMModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

AZURE_API_KEY_FROM_CREDS_PROVIDER = ""

@requires_api_key(provider_name="openai-apikey-provider")
async def need_api_key(*, api_key: str):
    global AZURE_API_KEY_FROM_CREDS_PROVIDER
    AZURE_API_KEY_FROM_CREDS_PROVIDER = api_key

app = BedrockAgentCoreApp()

os.environ["AZURE_API_BASE"] = os.environ.get("AZURE_API_BASE", "")
os.environ["AZURE_API_VERSION"] = os.environ.get("AZURE_API_VERSION", "2024-02-15-preview")


@tool
def weather():
    """Get current weather."""
    return "sunny"


agent = None


@app.entrypoint
async def strands_agent_open_ai(payload):
    global AZURE_API_KEY_FROM_CREDS_PROVIDER, agent

    if not AZURE_API_KEY_FROM_CREDS_PROVIDER:
        await need_api_key(api_key="")
        os.environ["AZURE_API_KEY"] = AZURE_API_KEY_FROM_CREDS_PROVIDER

    if agent is None:
        litellm_model = LiteLLMModel(
            model_id="azure/gpt-4.1-mini",
            params={"max_tokens": 32000, "temperature": 0.7},
        )
        agent = Agent(
            model=litellm_model,
            tools=[calculator, weather],
            system_prompt="You\'re a helpful assistant. You can do math and tell the weather.",
        )

    user_input = payload.get("prompt")
    response = agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
'''


# ── Step 1: Create API Key Credential Provider ─────────────────────────────────


def create_api_key_credential_provider() -> str:
    """Create (or reuse) an API Key credential provider in AgentCore Identity."""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Set AZURE_OPENAI_API_KEY or OPENAI_API_KEY environment variable before running this script.")

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    try:
        resp = control.create_api_key_credential_provider(
            name=PROVIDER_NAME,
            apiKey=api_key,
        )
        provider_arn = resp["credentialProviderArn"]
        print(f"  Created API key provider: {provider_arn}")
    except control.exceptions.ConflictException:
        resp = control.get_api_key_credential_provider(name=PROVIDER_NAME)
        provider_arn = resp["credentialProviderArn"]
        print(f"  Reusing existing provider: {provider_arn}")

    return provider_arn


# ── Step 2: Demo - show provider creation + usage ──────────────────────────────


def demo_local_usage():
    """Demonstrate local API key retrieval from the credential provider.

    In a deployed agent, this is handled automatically by @requires_api_key.
    This function shows what happens behind the scenes.
    """
    from bedrock_agentcore.services.identity import IdentityClient

    identity_client = IdentityClient(region=REGION)  # noqa: F841
    # In the agent, @requires_api_key calls GetResourceApiKey
    print("  API Key credential provider is ready.")
    print("  The @requires_api_key decorator retrieves the key at agent runtime.")
    print("  The agent then injects it into the AZURE_API_KEY environment variable.")


# ── Step 3: Show required IAM permissions for the runtime role ─────────────────


def show_required_permissions():
    """Print the IAM permissions the agent runtime role needs for outbound auth."""
    print("\n  IAM permissions required on the runtime execution role:")
    print(
        json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "GetResourceAPIKey",
                        "Effect": "Allow",
                        "Action": ["bedrock-agentcore:GetResourceApiKey"],
                        "Resource": "*",
                    },
                    {
                        "Sid": "SecretManager",
                        "Effect": "Allow",
                        "Action": ["secretsmanager:GetSecretValue"],
                        "Resource": "arn:aws:secretsmanager:*:*:secret:bedrock-agentcore*",
                    },
                ],
            },
            indent=4,
        )
    )


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    print("=== Outbound Auth: API Key Credential Provider (OpenAI/Azure) ===\n")

    # ── 1. Create credential provider ────────────────────────────────────────
    print("=== Step 1: Creating API Key Credential Provider ===")
    provider_arn = create_api_key_credential_provider()

    # ── 2. Show local usage example ───────────────────────────────────────────
    print("\n=== Step 2: Credential Provider Usage ===")
    demo_local_usage()

    # ── 3. Show runtime role permissions ──────────────────────────────────────
    print("\n=== Step 3: Required Runtime Role IAM Permissions ===")
    show_required_permissions()

    # ── 4. Print agent code ───────────────────────────────────────────────────
    print("\n=== Step 4: Agent Code (strands_agents_openai.py) ===")
    print("  This code is deployed to AgentCore Runtime.")
    print("  Key pattern: @requires_api_key(provider_name='openai-apikey-provider')")
    print("  The decorator fetches the key from AgentCore Identity at runtime.")
    print("  The LLM (Azure OpenAI GPT-4.1-mini via LiteLLM) is initialized lazily")
    print("  after the key is available.")

    # Save agent code for reference
    with open("strands_agents_openai.py", "w") as f:
        f.write(AGENT_CODE)
    print("\n  Agent code saved to strands_agents_openai.py")
    print("  To deploy this agent to AgentCore Runtime, use deploy.py from")
    print("  the 02-host-your-agent-and-tools/01-runtime/ examples.")

    print("\n=== Summary ===")
    print(f"  Credential provider: {PROVIDER_NAME}")
    print(f"  Provider ARN: {provider_arn}")  # codeql[py/clear-text-logging-sensitive-data]
    print(f"  Region: {REGION}")
    print("\n  The credential provider is now ready for use in AgentCore agents.")
    print("  Use @requires_api_key(provider_name='openai-apikey-provider') in your agent.")

    print("\nTo clean up the credential provider:")
    print(f"  aws bedrock-agentcore delete-api-key-credential-provider --name {PROVIDER_NAME}")


if __name__ == "__main__":
    main()
