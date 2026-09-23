"""
Agent-Based Code Execution with AgentCore Code Interpreter (Strands).

Demonstrates a Strands agent that answers questions by generating and executing
Python code in an AgentCore Code Interpreter sandbox:
  1. The agent receives a natural language query
  2. It generates Python code to answer the question
  3. The code is executed in a sandboxed session via execute_python tool
  4. The agent synthesises the results into a natural-language answer

The shared agent is defined in utils/code_interpreter_agent.py.

Prerequisites:
    pip install -r ../requirements.txt
    Access to Claude Haiku 4.5 in us-west-2 (default) or the region set in
    AWS_DEFAULT_REGION.

IAM permissions required (in addition to Bedrock InvokeModel):
    bedrock-agentcore:CreateCodeInterpreter
    bedrock-agentcore:StartCodeInterpreterSession
    bedrock-agentcore:InvokeCodeInterpreter
    bedrock-agentcore:StopCodeInterpreterSession
    bedrock-agentcore:DeleteCodeInterpreter
    bedrock-agentcore:ListCodeInterpreters
    bedrock-agentcore:GetCodeInterpreter

Usage:
    python code_execution.py
    python code_execution.py --query "Compute the first 15 Fibonacci numbers"
"""

import argparse
import sys
import os

# Allow running from this sub-directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.code_interpreter_agent import create_agent

# ── Sample queries ─────────────────────────────────────────────────────────────

DEFAULT_QUERIES = [
    "Tell me the largest prime number between 1 and 100 that is less than 84 and greater than 9.",
    "Compute the sum of all even numbers from 1 to 200 and verify your answer with code.",
]


# ── Main ───────────────────────────────────────────────────────────────────────


def run_query(agent, query: str):
    print(f"\n{'=' * 60}")
    print(f"Query: {query}")
    print("=" * 60)

    response = agent(query)

    print("\n[Agent Response]")
    print("-" * 60)
    if hasattr(response, "message"):
        content = response.message.get("content", [])
        for block in content:
            if block.get("text"):
                print(block["text"])
    else:
        print(str(response))


def parse_args():
    parser = argparse.ArgumentParser(description="Run the Strands code-execution agent demo")
    parser.add_argument(
        "--query",
        default=None,
        help="Custom query to run (runs default queries if omitted)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("AgentCore Code Interpreter — Strands Agent Code Execution")
    print("=" * 60)

    agent = create_agent()

    queries = [args.query] if args.query else DEFAULT_QUERIES
    for q in queries:
        run_query(agent, q)

    print("\nDemo complete!")


if __name__ == "__main__":
    main()
