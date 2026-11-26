"""Use a specialist as a tool; keep the illustrative allocation deterministic."""

import os
import re

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

app = BedrockAgentCoreApp()


@tool
def split_budget(monthly_cents: int) -> dict:
    """Split a positive synthetic monthly income (USD cents) using the illustrative 50/30/20 rule."""
    if type(monthly_cents) is not int or not 1 <= monthly_cents <= 100_000_000:
        raise ValueError("Supply 1 to 100000000 integer cents")
    needs = monthly_cents * 50 // 100
    wants = monthly_cents * 30 // 100
    return {"needs_cents": needs, "wants_cents": wants, "savings_cents": monthly_cents - needs - wants}


def make_model():
    version = os.environ["GUARDRAIL_VERSION"]
    if not re.fullmatch(r"[1-9][0-9]*", version) or not os.environ["GUARDRAIL_ID"].strip():
        raise ValueError("An approved, published guardrail is required")
    return BedrockModel(
        model_id=os.environ["BEDROCK_MODEL_ID"],
        guardrail_id=os.environ["GUARDRAIL_ID"],
        guardrail_version=version,
        temperature=0,
    )


def make_coordinator(model_factory=make_model):
    specialist = Agent(
        model=model_factory(),
        tools=[split_budget],
        callback_handler=None,
        system_prompt="Always use split_budget for arithmetic. Explain the illustrative split. Do not recommend investments.",
    )

    @tool
    async def ask_budget_specialist(question: str) -> str:
        """Ask the budget specialist to calculate an example monthly allocation."""
        return str(await specialist.invoke_async(question))

    return Agent(
        model=model_factory(),
        tools=[ask_budget_specialist],
        callback_handler=None,
        system_prompt="For a hypothetical budget, consult ask_budget_specialist and summarize its answer. This is an educational demo, not personalized financial advice.",
    )


@app.entrypoint
async def budget(payload):
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise ValueError("Expected a nonempty prompt up to 2000 characters")
    return {"response": str(await make_coordinator().invoke_async(prompt))}


if __name__ == "__main__":
    app.run()
