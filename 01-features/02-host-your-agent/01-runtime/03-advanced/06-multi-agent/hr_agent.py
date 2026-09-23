"""
HR Specialist Agent.

Handles questions about company benefits, policies, and HR topics.
Deployed as its own AgentCore Runtime.
"""

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.bedrock import BedrockModel


@tool
def lookup_benefit(benefit_name: str) -> str:
    """Look up information about a company benefit.

    Args:
        benefit_name: The benefit to look up (e.g., 'health insurance', '401k', 'pto').
    """
    benefits = {
        "health insurance": "PPO and HMO plans available. Company covers 80% of premiums. Open enrollment in November.",
        "401k": "Company matches 50% of contributions up to 6% of salary. Vesting after 2 years.",
        "pto": "20 days PTO per year, plus 10 company holidays. Unused PTO rolls over up to 5 days.",
        "parental leave": "16 weeks paid parental leave for all new parents.",
        "remote work": "Hybrid policy: 3 days in office, 2 days remote. Full remote requires VP approval.",
    }
    return benefits.get(
        benefit_name.lower(),
        f"No information found for '{benefit_name}'. Contact HR at hr@company.com.",
    )


@tool
def lookup_policy(policy_name: str) -> str:
    """Look up a company policy.

    Args:
        policy_name: The policy to look up (e.g., 'expense', 'travel', 'dress code').
    """
    policies = {
        "expense": "Submit expenses within 30 days via Concur. Manager approval required for amounts over $500.",
        "travel": "Book through corporate travel portal. Economy class for flights under 6 hours.",
        "dress code": "Business casual Monday-Thursday. Casual Friday.",
    }
    return policies.get(
        policy_name.lower(),
        f"No policy found for '{policy_name}'. Check the employee handbook.",
    )


model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

agent = Agent(
    model=model,
    tools=[lookup_benefit, lookup_policy],
    system_prompt=(
        "You are an HR specialist. You help employees with questions about "
        "company benefits, policies, and HR topics. Use lookup_benefit and "
        "lookup_policy to find accurate information."
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
