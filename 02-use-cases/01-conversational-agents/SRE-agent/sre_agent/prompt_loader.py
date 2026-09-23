#!/usr/bin/env python3

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


class PromptLoader:
    """Utility class for loading and managing prompt templates."""

    def __init__(self, prompts_dir: Optional[str] = None):
        """Initialize the prompt loader.

        Args:
            prompts_dir: Directory containing prompt files. If None, uses default relative path.
        """
        if prompts_dir:
            self.prompts_dir = Path(prompts_dir)
        else:
            # Default to config/prompts relative to this file
            self.prompts_dir = Path(__file__).parent / "config" / "prompts"

        logger.debug(f"PromptLoader initialized with prompts_dir: {self.prompts_dir}")

    @lru_cache(maxsize=32)
    def _load_prompt_file(self, filename: str) -> str:
        """Load a prompt file with caching.

        Args:
            filename: Name of the prompt file to load

        Returns:
            Content of the prompt file

        Raises:
            FileNotFoundError: If the prompt file doesn't exist
            IOError: If there's an error reading the file
        """
        filepath = self.prompts_dir / filename

        if not filepath.exists():
            raise FileNotFoundError(f"Prompt file not found: {filepath}")

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()

            logger.debug(f"Loaded prompt file: {filename}")
            return content
        except Exception as e:
            logger.error(f"Error loading prompt file {filename}: {e}")
            raise IOError(f"Failed to read prompt file {filename}: {e}")

    def load_prompt(self, prompt_name: str) -> str:
        """Load a prompt by name.

        Args:
            prompt_name: Name of the prompt (without .txt extension)

        Returns:
            Content of the prompt file
        """
        filename = f"{prompt_name}.txt"
        return self._load_prompt_file(filename)

    def load_template(self, template_name: str, **kwargs) -> str:
        """Load a prompt template and substitute variables.

        Args:
            template_name: Name of the template (without .txt extension)
            **kwargs: Variables to substitute in the template

        Returns:
            Template content with variables substituted
        """
        template_content = self.load_prompt(template_name)

        try:
            return template_content.format(**kwargs)
        except KeyError as e:
            logger.error(f"Missing template variable {e} in template {template_name}")
            raise ValueError(f"Missing required template variable: {e}")
        except Exception as e:
            logger.error(f"Error formatting template {template_name}: {e}")
            raise ValueError(f"Error formatting template {template_name}: {e}")

    def get_agent_prompt(
        self,
        agent_type: str,
        agent_name: str,
        agent_description: str,
        memory_context: str = "",
    ) -> str:
        """Combine base agent prompt with agent-specific prompt and memory context.

        Args:
            agent_type: Type of agent (kubernetes, logs, metrics, runbooks)
            agent_name: Display name of the agent
            agent_description: Description of the agent's capabilities
            memory_context: Relevant memory context for this agent

        Returns:
            Complete system prompt for the agent with memory context
        """
        try:
            # Load base prompt template
            base_prompt = self.load_template(
                "agent_base_prompt",
                agent_name=agent_name,
                agent_description=agent_description,
            )

            # Load agent-specific prompt if it exists
            try:
                agent_specific_prompt = self.load_prompt(f"{agent_type}_agent_prompt")
                combined_prompt = f"{base_prompt}\n\n{agent_specific_prompt}"
            except FileNotFoundError:
                logger.warning(f"No specific prompt found for agent type: {agent_type}")
                combined_prompt = base_prompt

            # Add memory context if provided
            if memory_context:
                combined_prompt += f"\n\n## Relevant Memory Context\n\n{memory_context}\n\nUse this context to inform your responses and avoid repeating work that has already been done."
                logger.debug(
                    f"Added memory context to {agent_type} agent prompt ({len(memory_context)} characters)"
                )

            return combined_prompt

        except Exception as e:
            logger.error(f"Error building agent prompt for {agent_type}: {e}")
            raise

    def get_supervisor_aggregation_prompt(
        self,
        is_plan_based: bool,
        query: str,
        agent_results: str,
        auto_approve_plan: bool = False,
        user_preferences: str = "",
        **kwargs,
    ) -> str:
        """Get supervisor aggregation prompt based on context.

        Args:
            is_plan_based: Whether this is a plan-based aggregation
            query: Original user query
            agent_results: JSON string of agent results
            auto_approve_plan: Whether to include auto-approve instruction
            **kwargs: Additional template variables (e.g., current_step, total_steps, plan)

        Returns:
            Formatted aggregation prompt
        """
        try:
            # Determine auto-approve instruction
            auto_approve_instruction = ""
            if auto_approve_plan:
                auto_approve_instruction = "\n\nIMPORTANT: Do not ask any follow-up questions or suggest that the user can ask for more details. Provide a complete, conclusive response."

            template_vars = {
                "query": query,
                "agent_results": agent_results,
                "auto_approve_instruction": auto_approve_instruction,
                "user_preferences": user_preferences,
                **kwargs,
            }

            if is_plan_based:
                return self.load_template(
                    "supervisor_plan_aggregation", **template_vars
                )
            else:
                return self.load_template(
                    "supervisor_standard_aggregation", **template_vars
                )

        except Exception as e:
            logger.error(f"Error building supervisor aggregation prompt: {e}")
            raise

    def get_executive_summary_prompts(
        self, query: str, results_text: str
    ) -> tuple[str, str]:
        """Get system and user prompts for executive summary generation.

        Args:
            query: Original user query
            results_text: Formatted investigation results

        Returns:
            Tuple of (system_prompt, user_prompt)
        """
        try:
            system_prompt = self.load_prompt("executive_summary_system")
            user_prompt = self.load_template(
                "executive_summary_user_template",
                query=query,
                results_text=results_text,
            )

            return system_prompt, user_prompt

        except Exception as e:
            logger.error(f"Error building executive summary prompts: {e}")
            raise

    def list_available_prompts(self) -> list[str]:
        """List all available prompt files.

        Returns:
            List of prompt names (without .txt extension)
        """
        try:
            prompt_files = list(self.prompts_dir.glob("*.txt"))
            return [f.stem for f in prompt_files]
        except Exception as e:
            logger.error(f"Error listing prompt files: {e}")
            return []


# Convenience instance for easy import
prompt_loader = PromptLoader()


# Convenience functions for backward compatibility
def load_prompt(prompt_name: str) -> str:
    """Load a prompt by name using the default loader."""
    return prompt_loader.load_prompt(prompt_name)


def load_template(template_name: str, **kwargs) -> str:
    """Load and format a template using the default loader."""
    return prompt_loader.load_template(template_name, **kwargs)


def get_agent_prompt(
    agent_type: str, agent_name: str, agent_description: str, memory_context: str = ""
) -> str:
    """Get complete agent prompt using the default loader."""
    return prompt_loader.get_agent_prompt(
        agent_type, agent_name, agent_description, memory_context
    )
