"""A single web-search agent using the same Runtime contract as the handoff example."""

from agents import Agent, WebSearchTool
from runtime_app import runtime_app

app = runtime_app(Agent(name="Assistant", tools=[WebSearchTool()]), "How can I help you today?")

if __name__ == "__main__":
    app.run()
