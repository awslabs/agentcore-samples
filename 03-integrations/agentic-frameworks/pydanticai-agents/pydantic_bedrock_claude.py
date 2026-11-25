"""A request-local Pydantic AI agent using Bedrock Converse and two small tools."""

import os
from datetime import UTC, datetime

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent
from pydantic_ai.models.bedrock import BedrockConverseModel
from starlette.exceptions import HTTPException

app = BedrockAgentCoreApp()


class Question(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


def build_agent(model=None):
    if model is None:
        model = BedrockConverseModel(os.environ["BEDROCK_MODEL_ID"])
    agent = Agent(
        model, instructions="Answer concisely. Use the UTC clock for time and label the weather as simulated."
    )

    @agent.tool_plain
    def get_current_date() -> str:
        """Get the current date and time in UTC."""
        return datetime.now(UTC).isoformat(timespec="seconds")

    @agent.tool_plain
    def get_weather() -> str:
        """Return the simulated weather, not a live forecast."""
        return "Sunny (simulated)"

    return agent


@app.entrypoint
async def pydantic_bedrock_claude_main(payload):
    try:
        question = Question.model_validate(payload)
    except ValidationError:
        raise HTTPException(400, "Send a JSON object with a nonempty prompt up to 8000 characters.") from None
    return (await build_agent().run(question.prompt)).output


if __name__ == "__main__":
    app.run()
