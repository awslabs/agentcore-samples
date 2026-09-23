"""
Knowledge Base RAG Agent - Knowledge Base Tool

This module provides a tool for searching the Bedrock Knowledge Base using the
Retrieve API for RAG (Retrieval Augmented Generation).
"""

import logging
import os

import boto3
from strands.tools import tool

logger = logging.getLogger(__name__)

# Global Bedrock Agent Runtime client (lazy initialization)
_bedrock_agent_client = None


def get_bedrock_agent_client():
    """
    Get or create the Bedrock Agent Runtime client.

    Returns:
        Bedrock Agent Runtime client instance
    """
    global _bedrock_agent_client

    if _bedrock_agent_client is not None:
        return _bedrock_agent_client

    region = os.environ.get("AWS_REGION", "us-east-1")
    _bedrock_agent_client = boto3.client("bedrock-agent-runtime", region_name=region)

    return _bedrock_agent_client


@tool
def search_knowledge_base(query: str, max_results: int = 5) -> str:
    """
    Search the knowledge base for relevant information using Bedrock KB Retrieve API.

    Use this tool when you need to find specific information from the knowledge base
    to answer user questions about uploaded documents.

    Args:
        query: The search query to find relevant documents
        max_results: Maximum number of results to return (default: 5)

    Returns:
        A formatted string containing the search results with source citations
    """
    knowledge_base_id = os.environ.get("KNOWLEDGE_BASE_ID")

    if not knowledge_base_id:
        logger.warning("KNOWLEDGE_BASE_ID not configured - knowledge base search disabled")
        return "Knowledge base search is not available. The KNOWLEDGE_BASE_ID environment variable is not set."

    client = get_bedrock_agent_client()

    try:
        # Use Bedrock KB Retrieve API for semantic search
        response = client.retrieve(
            knowledgeBaseId=knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": max_results}},
        )

        results = response.get("retrievalResults", [])

        if not results:
            return f"No results found for query: {query}"

        # Format results with source citations
        formatted_results = []
        for i, result in enumerate(results, 1):
            content = result.get("content", {}).get("text", "No content available")
            score = result.get("score", 0)

            # Extract source location
            location = result.get("location", {})
            source_type = location.get("type", "UNKNOWN")

            if source_type == "S3":
                s3_location = location.get("s3Location", {})
                source = s3_location.get("uri", "Unknown S3 source")
            else:
                source = f"Source type: {source_type}"

            result_text = f"**Result {i}** (Relevance: {score:.2f})\n"
            result_text += f"Source: {source}\n"

            # Truncate long content for readability
            if len(content) > 800:
                content = content[:800] + "..."
            result_text += f"Content: {content}\n"

            formatted_results.append(result_text)

        return "\n---\n".join(formatted_results)

    except client.exceptions.ResourceNotFoundException:
        logger.error(f"Knowledge base not found: {knowledge_base_id}")
        return f"Error: Knowledge base '{knowledge_base_id}' not found. Please verify the knowledge base exists."

    except client.exceptions.ValidationException as e:
        logger.error(f"Validation error searching knowledge base: {e}")
        return f"Error: Invalid search request - {str(e)}"

    except Exception as e:
        logger.error(f"Error searching knowledge base: {e}")
        return f"Error searching knowledge base: {str(e)}"
