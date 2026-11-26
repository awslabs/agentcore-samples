"""
Knowledge Base RAG Agent - Agent Definition

This module defines the Strands agent with tools for RAG-based knowledge retrieval.
"""

import logging
import os

from strands import Agent
from strands.models import BedrockModel
from src.tools.knowledge_base import search_knowledge_base

logger = logging.getLogger(__name__)


def create_agent() -> Agent:
    """
    Create and configure the Strands agent.

    Returns:
        Configured Strands Agent instance
    """
    # Get model configuration from environment
    model_id = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    region = os.environ.get("AWS_REGION", "us-east-1")

    logger.info(f"Creating agent with model: {model_id} in region: {region}")

    # Configure the Bedrock model
    model = BedrockModel(
        model_id=model_id,
        region_name=region,
    )

    # Define the system prompt
    system_prompt = """You are a helpful AI assistant powered by Amazon Bedrock AgentCore.

You have access to a knowledge base containing documents uploaded by the user. ALWAYS use the search_knowledge_base tool first when:
- The user asks about specific topics, products, or documentation
- The user asks questions that might be answered by uploaded documents
- You're unsure if your general knowledge is sufficient

Your capabilities include:
1. Searching the knowledge base for relevant information
2. Providing helpful and accurate information with source citations
3. Maintaining context across conversations

When answering questions:
- Search the knowledge base first for any factual or domain-specific questions
- Be concise and accurate
- Cite sources when providing information from the knowledge base
- If the knowledge base doesn't have relevant information, say so and provide general guidance

Always be helpful, harmless, and honest."""

    # Create the agent with tools
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[search_knowledge_base],
    )

    logger.info("Agent created successfully")
    return agent
