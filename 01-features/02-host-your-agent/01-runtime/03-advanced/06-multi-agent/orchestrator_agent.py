"""
Orchestrator Agent — routes questions to specialist agents.

This agent uses the tech and HR agent ARNs (passed as environment variables)
to invoke specialist agents as tools via invoke_agent_runtime.
"""

import json
import os

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

# Read specialist agent ARNs from environment variables
# These are set by deploy.py when creating the orchestrator runtime
TECH_AGENT_ARN = os.environ.get("TECH_AGENT_ARN", "")
HR_AGENT_ARN = os.environ.get("HR_AGENT_ARN", "")


def _invoke_specialist(arn: str, question: str) -> str:
    """Invoke a specialist agent and return its response."""
    if not arn:
        return "Error: specialist agent ARN not configured."
    client = boto3.client("bedrock-agentcore")
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=json.dumps({"prompt": question}).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    return response["response"].read().decode("utf-8")


@tool
def ask_tech_agent(question: str) -> str:
    """Route technical questions to the tech support specialist.

    Use this for programming questions, debugging, error codes,
    and technical troubleshooting.

    Args:
        question: The technical question to ask.
    """
    return _invoke_specialist(TECH_AGENT_ARN, question)


@tool
def ask_hr_agent(question: str) -> str:
    """Route HR questions to the HR specialist.

    Use this for questions about company benefits, policies,
    PTO, health insurance, 401k, and other HR topics.

    Args:
        question: The HR question to ask.
    """
    return _invoke_specialist(HR_AGENT_ARN, question)


model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

agent = Agent(
    model=model,
    tools=[ask_tech_agent, ask_hr_agent],
    system_prompt=(
        "You are a helpful assistant that routes questions to specialist agents.\n\n"
        "- For technical questions (programming, debugging, errors): use ask_tech_agent\n"
        "- For HR questions (benefits, policies, PTO, insurance): use ask_hr_agent\n"
        "- For general questions: answer directly\n\n"
        "Always route to the appropriate specialist rather than guessing."
    ),
)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke_agent(payload: dict) -> str:
    prompt = payload.get("prompt", "Hello!")
    response = agent(prompt)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
