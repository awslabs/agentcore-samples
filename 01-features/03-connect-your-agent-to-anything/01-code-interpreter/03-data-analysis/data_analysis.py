"""
Advanced Data Analysis with AgentCore Code Interpreter (Strands).

Demonstrates a Strands agent that performs multi-step data analysis using the
AgentCore Code Interpreter sandbox:
  1. Start a Code Interpreter session (with extended timeout for large datasets)
  2. Upload a CSV file into the sandbox via writeFiles
  3. Ask the agent to perform exploratory data analysis (EDA)
  4. Ask the agent to answer a specific data query
  5. Stop the session

The CSV contains ~300 000 records with columns:
  Name, Preferred_City, Preferred_Animal, Preferred_Thing

The shared agent is defined in utils/code_interpreter_agent.py.

Prerequisites:
    pip install -r ../requirements.txt
    Access to Claude Haiku 4.5 in us-west-2 (or AWS_DEFAULT_REGION).

IAM permissions required:
    bedrock-agentcore:CreateCodeInterpreter
    bedrock-agentcore:StartCodeInterpreterSession
    bedrock-agentcore:InvokeCodeInterpreter
    bedrock-agentcore:StopCodeInterpreterSession
    bedrock-agentcore:DeleteCodeInterpreter
    bedrock-agentcore:ListCodeInterpreters
    bedrock-agentcore:GetCodeInterpreter

Usage:
    python data_analysis.py
    python data_analysis.py --query "How many rows have 'Goat' as Preferred_Animal?"
"""

import argparse
import json
import os
import sys
from typing import Any, Dict

from strands import Agent, tool
from strands.models import BedrockModel

from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.code_interpreter_agent import REGION, SYSTEM_PROMPT, MODEL_ID

# ── Configuration ──────────────────────────────────────────────────────────────

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "samples")
SESSION_TIMEOUT = 1200  # 20 min — large dataset analysis can be slow

DEFAULT_EDA_QUERY = (
    "Load the file 'data.csv' from the sandbox and perform exploratory data analysis. "
    "Report row count, column stats, top values, and any notable distributions."
)
DEFAULT_DETAIL_QUERY = (
    "Within 'data.csv', how many individuals with the first name 'Kimberly' have 'Crocodile' as their favourite animal?"
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def call_tool(client: CodeInterpreter, tool_name: str, arguments: Dict[str, Any]) -> str:
    response = client.invoke(tool_name, arguments)
    for event in response["stream"]:
        return json.dumps(event["result"])
    return json.dumps({"isError": True})


def run_agent_query(agent, query: str):
    print(f"\n{'=' * 60}")
    print(f"Query: {query}")
    print("=" * 60)
    response = agent(query)
    print("\n[Agent Response]")
    print("-" * 60)
    if hasattr(response, "message"):
        for block in response.message.get("content", []):
            if block.get("text"):
                print(block["text"])
    else:
        print(str(response))


# ── Main ───────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Data analysis agent demo")
    parser.add_argument("--query", default=None, help="Custom data query")
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("AgentCore Code Interpreter — Advanced Data Analysis")
    print("=" * 60)

    # 1. Start a persistent session for file upload
    data_csv_path = os.path.join(SAMPLES_DIR, "data.csv")
    if not os.path.exists(data_csv_path):
        print(f"ERROR: {data_csv_path} not found. Make sure samples/data.csv exists.")
        sys.exit(1)

    print(f"\n[1] Starting Code Interpreter session (timeout={SESSION_TIMEOUT}s)...")
    code_client = CodeInterpreter(REGION)
    code_client.start(session_timeout_seconds=SESSION_TIMEOUT)
    print("  Session started.")

    try:
        # 2. Upload data file
        print("\n[2] Uploading data.csv to sandbox...")
        data_content = read_file(data_csv_path)
        write_result = call_tool(
            code_client,
            "writeFiles",
            {"content": [{"path": "data.csv", "text": data_content}]},
        )
        print(f"  Result: {json.loads(write_result)['content'][0]['text']}")

        # 3. Create an agent that reuses the same session (so it can see uploaded files)
        def make_session_agent(client: CodeInterpreter) -> Agent:
            @tool
            def execute_python(code: str, description: str = "") -> str:
                """Execute Python code in the AgentCore Code Interpreter sandbox.

                Args:
                    code: Python source code to execute.
                    description: Optional one-line description prepended as a comment.

                Returns:
                    JSON string with execution results including stdout, stderr, and exit code.
                """
                if description:
                    code = f"# {description}\n{code}"
                print(f"\n[Sandbox] Executing:\n{'-' * 40}\n{code}\n{'-' * 40}")
                response = client.invoke(
                    "executeCode",
                    {"code": code, "language": "python", "clearContext": False},
                )
                for event in response["stream"]:
                    return json.dumps(event["result"])
                return json.dumps(
                    {
                        "isError": True,
                        "content": [{"type": "text", "text": "No result"}],
                    }
                )

            return Agent(
                model=BedrockModel(model_id=MODEL_ID),
                tools=[execute_python],
                system_prompt=SYSTEM_PROMPT,
                callback_handler=None,
            )

        agent = make_session_agent(code_client)

        eda_query = args.query or DEFAULT_EDA_QUERY
        run_agent_query(agent, eda_query)

        if not args.query:
            run_agent_query(agent, DEFAULT_DETAIL_QUERY)

    finally:
        print("\n[3] Stopping Code Interpreter session...")
        code_client.stop()
        print("  Session stopped.")

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
