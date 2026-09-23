#!/usr/bin/env python3

import logging
import os
from typing import Any, Dict, List, Optional

from .constants import SREConstants
from .llm_utils import create_llm_with_error_handling
from .prompt_loader import prompt_loader

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


class SREOutputFormatter:
    """Simple markdown output formatter for SRE multi-agent responses."""

    def __init__(self, llm_provider: Optional[str] = None):
        # Get provider from parameter, environment, or default to bedrock
        self.llm_provider = llm_provider or os.getenv("LLM_PROVIDER", "bedrock")
        logger.info(
            f"SREOutputFormatter initialized with LLM provider: {self.llm_provider}"
        )

    def _create_llm(self, **kwargs):
        """Create LLM instance with improved error handling."""
        # Get output formatter specific config (with reduced max_tokens)
        formatter_config = SREConstants.get_output_formatter_config(
            self.llm_provider, **kwargs
        )
        logger.info(
            f"Creating LLM for output formatter - Provider: {self.llm_provider}, Max tokens: {formatter_config['max_tokens']}"
        )

        # Use the centralized error handling with formatter-specific config
        return create_llm_with_error_handling(
            self.llm_provider, max_tokens=formatter_config["max_tokens"], **kwargs
        )

    def _extract_steps_from_response(self, response: str) -> List[str]:
        """Extract numbered steps from agent response."""
        if not response:
            return []

        steps = []
        lines = response.split("\n")

        for line in lines:
            line = line.strip()
            # Look for numbered steps (1., 2., etc.) or bullet points
            if line and (
                line[0].isdigit() or line.startswith("-") or line.startswith("•")
            ):
                steps.append(line)

        return steps

    def format_investigation_response(
        self,
        query: str,
        agent_results: Dict[str, Any],
        metadata: Dict[str, Any],
        plan: Optional[Dict[str, Any]] = None,
        user_preferences: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Format a complete investigation response in clean markdown."""

        # Extract key information
        plan_info = plan or metadata.get("investigation_plan", {})
        current_step = metadata.get("plan_step", 0) + 1
        total_steps = len(plan_info.get("steps", []))

        output = []

        # Header
        output.append("# 🔍 Investigation Results")
        output.append("")
        output.append(f"**Query:** {query}")
        output.append("")

        # Executive Summary Section
        executive_summary = self._generate_executive_summary(
            query, agent_results, metadata, user_preferences
        )
        if executive_summary:
            output.append(executive_summary)
            output.append("")

        # Key Findings Section
        if agent_results:
            output.append("## 🎯 Key Findings")
            output.append("")

            for agent_name, result in agent_results.items():
                if not result or result == "No response provided":
                    continue

                agent_display = agent_name.replace("_", " ").title()
                output.append(f"### {agent_display}")

                # Extract steps if this is a runbook response
                if (
                    "runbooks" in agent_name.lower()
                    or "operational" in agent_name.lower()
                ):
                    steps = self._extract_steps_from_response(result)
                    if steps:
                        output.append("")
                        output.append("**Runbook Steps Found:**")
                        for step in steps:
                            # Clean up step formatting
                            clean_step = step.strip()
                            if clean_step.startswith(
                                ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")
                            ):
                                output.append(f"{clean_step}")
                            else:
                                output.append(f"- {clean_step}")
                        output.append("")
                    else:
                        # Show full response if no steps found
                        output.append(f"- {result}")
                        output.append("")
                else:
                    # For non-runbook agents, show full response
                    output.append(f"- {result}")
                    output.append("")

        # Next Steps Section
        if plan_info and current_step < total_steps:
            output.append("## 📋 Next Steps")
            output.append("")
            remaining_steps = plan_info.get("steps", [])[current_step:]
            for i, step in enumerate(remaining_steps, current_step + 1):
                output.append(f"{i}. {step}")
            output.append("")

        # Investigation Complete
        if current_step >= total_steps:
            output.append("## ✅ Investigation Complete")
            output.append("")
            output.append("All planned investigation steps have been executed.")
            output.append("")

        return "\n".join(output)

    def _generate_executive_summary(
        self,
        query: str,
        agent_results: Dict[str, Any],
        metadata: Dict[str, Any],
        user_preferences: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Generate executive summary using LLM analysis of investigation results."""
        if not agent_results:
            return ""

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            # Create LLM instance using configured provider
            llm = self._create_llm()

            # Prepare agent results for analysis
            formatted_results = []
            for agent_name, result in agent_results.items():
                if result and result != "No response provided":
                    formatted_results.append(f"**{agent_name}:**\n{result}\n")

            results_text = "\n".join(formatted_results)

            # Add user preferences to the context if available
            if user_preferences:
                import json

                prefs_text = json.dumps(user_preferences, indent=2, default=str)
                results_text += f"\n\n**User Preferences:**\n{prefs_text}\n"

            # Get prompts from prompt loader
            system_prompt, user_prompt = prompt_loader.get_executive_summary_prompts(
                query=query, results_text=results_text
            )

            # Log the prompts being sent to LLM for debugging
            logger.info("=== EXECUTIVE SUMMARY PROMPT LOGGING ===")
            logger.info(f"System Prompt Length: {len(system_prompt)} characters")
            logger.info(f"User Prompt Length: {len(user_prompt)} characters")
            if user_preferences:
                logger.info(
                    f"User preferences included in context: {len(user_preferences)} preference items"
                )
                logger.info(
                    f"User preferences preview: {str(user_preferences)[:200]}..."
                )
            else:
                logger.info(
                    "No user preferences provided to executive summary generation"
                )
            logger.info(f"User Prompt Content:\n{user_prompt}")
            logger.info("=== END EXECUTIVE SUMMARY PROMPT LOGGING ===")

            # Generate executive summary
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]

            response = llm.invoke(messages)
            return str(response.content).strip()

        except Exception as e:
            logger.error(f"Error generating executive summary with LLM: {e}")
            # Fallback to simple summary if LLM fails
            return self._generate_fallback_summary(query, agent_results)

    def _generate_fallback_summary(
        self, query: str, agent_results: Dict[str, Any]
    ) -> str:
        """Fallback executive summary if LLM generation fails."""
        return """## 📋 Executive Summary

### 🎯 Key Insights
- **Root Cause**: Investigation findings require analysis
- **Impact**: Service performance may be affected
- **Severity**: Medium

### ⚡ Next Steps
1. **Immediate** (< 1 hour): Review detailed findings below
2. **Short-term** (< 24 hours): Execute recommended remediation steps
3. **Long-term** (< 1 week): Monitor system metrics for improvement
4. **Follow-up**: Schedule post-incident review if applicable"""

    def format_plan_approval(self, plan: Dict[str, Any], query: str) -> str:
        """Format plan approval request in clean markdown."""
        output = []

        # Header
        output.append("# 📋 Investigation Plan")
        output.append("")
        output.append(f"**Query:** {query}")
        output.append(f"**Complexity:** {plan.get('complexity', 'unknown').title()}")
        output.append("")

        # Plan Steps
        steps = plan.get("steps", [])
        if steps:
            output.append("## Investigation Steps")
            output.append("")
            for i, step in enumerate(steps, 1):
                output.append(f"{i}. {step}")
            output.append("")

        # Plan Details
        reasoning = plan.get("reasoning", "Standard investigation approach")
        auto_execute = plan.get("auto_execute", False)

        output.append("## Plan Details")
        output.append("")
        output.append(f"**Reasoning:** {reasoning}")
        output.append(f"**Auto-execute:** {'Yes' if auto_execute else 'No'}")
        output.append("")

        # Actions
        output.append("## Available Actions")
        output.append("")
        output.append("- Type `proceed` or `yes` to execute the plan")
        output.append("- Type `modify` to suggest changes")
        output.append("- Ask specific questions about any step")
        output.append("")

        return "\n".join(output)


def create_formatter(llm_provider: Optional[str] = None) -> SREOutputFormatter:
    """Create and return a new SRE output formatter instance."""
    return SREOutputFormatter(llm_provider=llm_provider)
