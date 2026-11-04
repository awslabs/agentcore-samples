"""
Use the AgentCore Web Answer Tool with a LangChain agent.

This example demonstrates a LangChain agent that uses Web Answer through an
AgentCore Gateway to answer questions requiring current public-web information:
  1. Obtain a Cognito OAuth token for Gateway authentication
  2. Connect with langchain-mcp-adapters and discover WebAnswer
  3. Create an agent backed by ChatBedrockConverse
  4. Let the model invoke WebAnswer when it needs a current fact
  5. Present the synthesized answer while preserving its citations

Prerequisites:
    pip install -r ../requirements.txt
    Run setup_gateway.py and load the generated .env.web-answer file
    Enable access to the configured Bedrock model

Environment variables required:
    AGENTCORE_GATEWAY_URL  — Gateway MCP endpoint
    COGNITO_DOMAIN         — Cognito domain prefix
    COGNITO_CLIENT_ID      — Cognito machine-to-machine app client ID
    COGNITO_CLIENT_SECRET  — Cognito app client secret
    COGNITO_SCOPE          — OAuth scope string
    BEDROCK_MODEL_ID       — Optional Bedrock inference profile ID or ARN
    AWS_DEFAULT_REGION     — AWS region, defaulting to us-east-1

Usage:
    python web_answer_langchain.py
    python web_answer_langchain.py --question "What is the current Node.js LTS release?"
"""

import argparse
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from langchain.agents import create_agent
from langchain_aws import ChatBedrockConverse
from langchain_mcp_adapters.client import MultiServerMCPClient
from utils.gateway_auth import get_oauth_token

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
GATEWAY_URL = os.getenv("AGENTCORE_GATEWAY_URL", "")
DEFAULT_QUESTION = "What is the latest stable release of Python, and when was it released?"

SYSTEM_PROMPT = f"""You are a helpful assistant. Today's date is {date.today().isoformat()}.
Use the WebAnswer tool for direct factual questions that require current web
information. Preserve the citations and source URLs returned by the tool. If it
cannot produce a well-supported answer, say so rather than guessing.
"""


async def run_agent(question: str) -> None:
    """Create a LangChain agent, submit one question, and print its answer.

    The function authenticates to AgentCore Gateway, discovers its MCP tools,
    and gives those tools to a Bedrock-backed LangChain agent. Web Answer
    performs retrieval and synthesis; the agent presents the returned answer
    and citations.

    Args:
        question: Natural-language question that may require current web facts.

    Raises:
        ValueError: If ``AGENTCORE_GATEWAY_URL`` is not configured.
        requests.HTTPError: If Cognito rejects the OAuth token request.
    """
    if not GATEWAY_URL:
        raise ValueError("AGENTCORE_GATEWAY_URL is not set. Run setup_gateway.py and source .env.web-answer first.")

    token = get_oauth_token()
    model = ChatBedrockConverse(
        model=MODEL_ID,
        region_name=REGION,
        temperature=0,
        max_tokens=1024,
    )
    client = MultiServerMCPClient(
        {
            "web-answer": {
                "transport": "streamable_http",
                "url": GATEWAY_URL,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    )
    tools = await client.get_tools()
    print(f"  Discovered {len(tools)} tool(s)")

    agent = create_agent(model, tools=tools, system_prompt=SYSTEM_PROMPT)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]})

    print("\n[Agent Response]")
    print("-" * 60)
    final_message = result["messages"][-1]
    print(final_message.content if hasattr(final_message, "content") else final_message)


def parse_args():
    parser = argparse.ArgumentParser(description="LangChain agent with Web Answer Tool via AgentCore Gateway")
    parser.add_argument(
        "--question",
        default=DEFAULT_QUESTION,
        help=f"Question to answer (default: '{DEFAULT_QUESTION}')",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print("=" * 60)
    print("AgentCore Web Answer Tool — LangChain Agent")
    print("=" * 60)
    print(f"\nQuestion: {args.question}\n")
    asyncio.run(run_agent(args.question))
    print("\n" + "=" * 60)
    print("Web Answer with a LangChain agent demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
