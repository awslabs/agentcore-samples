"""Use the AgentCore Web Answer Tool with a Strands agent."""

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
    parser = argparse.ArgumentParser(
        description="Strands agent with Web Answer Tool via AgentCore Gateway"
    )
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
