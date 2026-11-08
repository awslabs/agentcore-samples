"""
Multi-Gateway Tool Deduplication Sample

When an agent connects to multiple AgentCore Gateways, both gateways might return 
overlapping tools (e.g., built-in AgentCore tools or shared utilities).
This script demonstrates how to deduplicate the combined list of MCP tools 
by tool name to prevent conflicts when initializing the agent.
"""

import logging
from typing import List, Any, Dict

# Configure logger
logging.basicConfig(format="%(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def deduplicate_tools(all_tools_raw: List[Any]) -> List[Any]:
    """
    Deduplicates a list of MCP tools based on their names.
    If duplicates are found, logs a warning and keeps the first occurrence.
    """
    unique_tools: List[Any] = []
    seen_tools_map: Dict[str, Any] = {}

    for t in all_tools_raw:
        # Safely extract the tool name (handles different MCP client implementations)
        t_name = getattr(t, "name", getattr(t, "tool_name", str(t)))

        if t_name not in seen_tools_map:
            seen_tools_map[t_name] = t
            unique_tools.append(t)
        else:
            # Duplicate found: Perform deep inspection and log
            t_original = seen_tools_map[t_name]

            logger.warning("\n" + "!" * 60)
            logger.warning(f"🚨 DUPLICATE TOOL DETECTED: {t_name}")
            logger.warning("!" * 60)

            logger.warning("🔵 KEPT TOOL (Already registered):")
            logger.warning(f" - Attributes: {vars(t_original) if hasattr(t_original, '__dict__') else dir(t_original)}")
            
            logger.warning("\n🔴 DISCARDED TOOL (Duplicate):")
            logger.warning(f" - Attributes: {vars(t) if hasattr(t, '__dict__') else dir(t)}")
            logger.warning("!" * 60 + "\n")

    return unique_tools

# ==========================================
# Example Usage
# ==========================================
if __name__ == "__main__":
    # Mocking MCP tools for demonstration purposes
    class MockTool:
        def __init__(self, name: str, description: str):
            self.name = name
            self.description = description

    # Tools fetched from Gateway A (e.g. Domain Specific Gateway)
    gateway_a_tools = [
        MockTool("calculate_sum", "Calculates the sum of numbers"),
        MockTool("x_amz_bedrock_agentcore_search", "A special tool that returns a trimmed down list of tools...") # Auto-injected tool
    ]

    # Tools fetched from Gateway B (e.g. Shared Utility Gateway)
    gateway_b_tools = [
        MockTool("save_memory", "Saves information to memory"),
        MockTool("x_amz_bedrock_agentcore_search", "A special tool that returns a trimmed down list of tools...") # Auto-injected tool
    ]

    all_tools = gateway_a_tools + gateway_b_tools
    print(f"Raw Tools Count: {len(all_tools)}")

    # Apply Deduplication
    safe_tools = deduplicate_tools(all_tools)

    print(f"\n📋 Safe tools to pass to the agent: {[t.name for t in safe_tools]}")
