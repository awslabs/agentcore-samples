"""
Shared Strands browser agent using AgentCore Browser Tool.

Used as the common demo agent across browser sub-demos:
  - 04-strands/demo.py

The agent uses the official strands_tools.browser.AgentCoreBrowser
integration to provide a browser tool to the Strands agent.
"""

import os

from strands import Agent
from strands_tools.browser import AgentCoreBrowser

# ── Configuration ─────────────────────────────────────────────────────────────

REGION = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

SYSTEM_PROMPT = """You are an intelligent web analyst that specializes in browsing websites and extracting useful information. When given a URL or a question about a website:

1. Use the browser tool to visit and interact with the website efficiently.
2. Focus on extracting key information quickly — complete your task within 2-3 browser interactions.
3. Provide specific, actionable insights with actual data points.
4. Be concise but comprehensive in your findings.

Always present your findings clearly and include the source URL."""


# ── Factory ────────────────────────────────────────────────────────────────────


def create_agent() -> Agent:
    """Create and return the shared browser agent."""
    agent_core_browser = AgentCoreBrowser(region=REGION)
    return Agent(
        tools=[agent_core_browser.browser],
        model=MODEL_ID,
        system_prompt=SYSTEM_PROMPT,
    )
