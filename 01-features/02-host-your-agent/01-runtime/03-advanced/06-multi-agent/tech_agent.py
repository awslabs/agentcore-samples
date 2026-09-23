"""
Tech Support Specialist Agent.

Handles programming questions and technical troubleshooting.
Deployed as its own AgentCore Runtime.
"""

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.bedrock import BedrockModel


@tool
def search_docs(query: str) -> str:
    """Search technical documentation.

    Args:
        query: The technical topic to search for.
    """
    return (
        f"Documentation results for '{query}':\n"
        f"- Getting started guide for {query}\n"
        f"- API reference and best practices\n"
        f"- Common troubleshooting steps\n"
        f"- Example code and tutorials"
    )


@tool
def check_error_code(error_code: str) -> str:
    """Look up an error code and return possible solutions.

    Args:
        error_code: The error code to look up.
    """
    solutions = {
        "404": "Resource not found. Check the URL or resource identifier.",
        "500": "Internal server error. Check server logs and retry.",
        "403": "Access denied. Verify IAM permissions and credentials.",
        "timeout": "Request timed out. Increase timeout or check network.",
    }
    return solutions.get(
        error_code.lower(),
        f"Unknown error code: {error_code}. Check the documentation.",
    )


model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

agent = Agent(
    model=model,
    tools=[search_docs, check_error_code],
    system_prompt=(
        "You are a technical support specialist. You help with programming questions, "
        "debugging, and technical troubleshooting. Use search_docs to find relevant "
        "documentation and check_error_code to diagnose error codes."
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
