"""
Market Trends Agent Tools

This package contains all the tools used by the market trends agent:
- browser_tool: AgentCore Browser Tool integration for web scraping
- broker_card_tools: Broker card parsing and market summary generation
- memory_tools: AgentCore Memory integration for broker profiles and conversation history
"""

from .browser_tool import get_stock_data, search_news
from .broker_card_tools import (
    parse_broker_profile_from_message,
    generate_market_summary_for_broker,
    get_broker_card_template,
    collect_broker_preferences_interactively,
)
from .memory_tools import get_memory_from_ssm, extract_actor_id, create_memory_tools

__all__ = [
    "get_stock_data",
    "search_news",
    "parse_broker_profile_from_message",
    "generate_market_summary_for_broker",
    "get_broker_card_template",
    "collect_broker_preferences_interactively",
    "get_memory_from_ssm",
    "extract_actor_id",
    "create_memory_tools",
]
