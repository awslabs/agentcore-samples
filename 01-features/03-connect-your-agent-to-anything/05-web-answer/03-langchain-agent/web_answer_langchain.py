"""Use the AgentCore Web Answer Tool with a LangChain agent."""

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
