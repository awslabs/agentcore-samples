"""
Use the Amazon Bedrock AgentCore Web Answer Tool with a Strands agent.

Documentation:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html

This example demonstrates a Strands agent that uses Web Answer through an
AgentCore Gateway to answer questions requiring current public-web information:
  1. Connect to the Gateway using MCP Streamable HTTP
  2. Discover WebAnswer through tools/list
  3. Create a Strands agent with the discovered MCP tools
  4. Let the model invoke WebAnswer when a question requires current facts
  5. Present the synthesized answer while preserving its source URLs

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

Usage:
    python web_answer_strands.py
    python web_answer_strands.py --question "What is the current Node.js LTS release?"
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.web_answer_agent import create_agent, create_mcp_client

DEFAULT_QUESTIONS = [
    "What is the latest stable release of Python, and when was it released?",
    "What is the current long-term support release of Node.js?",
]


def run_question(agent, question: str) -> None:
    """Run one question through the Strands agent and print its final response.

    Args:
        agent: Configured Strands agent with the WebAnswer MCP tool available.
        question: Natural-language question that may require current web facts.
    """
    print(f"\n{'=' * 60}")
    print(f"Question: {question}")
    print("=" * 60)

    response = agent(question)
    print("\n[Agent Response]")
    print("-" * 60)
    if hasattr(response, "message"):
        for block in response.message.get("content", []):
            if block.get("text"):
                print(block["text"])
    else:
        print(str(response))


def parse_args():
    parser = argparse.ArgumentParser(description="Strands agent with Web Answer Tool via AgentCore Gateway")
    parser.add_argument(
        "--question",
        help="Question to answer (runs built-in examples if omitted)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("AgentCore Web Answer Tool — Strands Agent")
    print("=" * 60)

    mcp_client = create_mcp_client()
    with mcp_client:
        tools = mcp_client.list_tools_sync()
        print(f"\nDiscovered {len(tools)} tool(s) from Gateway")
        agent = create_agent(mcp_client)
        questions = [args.question] if args.question else DEFAULT_QUESTIONS
        for question in questions:
            run_question(agent, question)

    print("\n" + "=" * 60)
    print("Web Answer with a Strands agent demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
