"""Route a travel question to a specialist using an OpenAI Agents SDK handoff."""

from agents import Agent, WebSearchTool
from runtime_app import runtime_app

travel_agent = Agent(
    name="Travel Expert",
    instructions="Help plan destinations, flights, accommodation, and travel requirements. Use web search for current facts.",
    tools=[WebSearchTool()],
)
food_agent = Agent(
    name="Food Expert",
    instructions="Find restaurants, local cuisine and food tours. Use web search and respect dietary preferences.",
    tools=[WebSearchTool()],
)
triage_agent = Agent(
    name="Travel Assistant",
    instructions="Hand off travel planning to the Travel Expert and dining questions to the Food Expert. Answer general questions directly.",
    handoffs=[travel_agent, food_agent],
)
app = runtime_app(triage_agent, "How can I help you with your travel plans?")

if __name__ == "__main__":
    app.run()
