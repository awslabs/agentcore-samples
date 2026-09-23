#!/usr/bin/env python3
"""
AnyBank FAQ Knowledge Base MCP Server

Provides FAQ retrieval from AnyBank knowledge base via Model Context Protocol.
The knowledge base contains comprehensive FAQ information from anybank-faq.md covering:
- General Banking (accounts, safety, closures)
- Online & Mobile Banking (security, deposits, bill pay)
- Fees & Charges (monthly fees, overdrafts, ATMs, wires)
- Debit & Credit Cards (delivery, fraud, international use)
- Mortgages (affordability, pre-approval, documents, timeline)
- Security & Fraud (protection, suspicious activity, liability)
- Customer Service (contact methods, hours, complaints)
- Account Access (authorization, beneficiaries, international)

Supports both retrieve (search only) and retrieve_and_generate (RAG with LLM).
"""

import asyncio
import json
import logging
import boto3
import os
from typing import Optional
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server
server = Server("anybank-faq-kb")

# Configuration from environment
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID")
DEFAULT_MODEL_ARN = os.getenv(
    "KB_MODEL_ARN",
    f"arn:aws:bedrock:{AWS_REGION}::foundation-model/anthropic.claude-3-sonnet-20240229-v1:0",
)

# Initialize Bedrock Agent Runtime client
bedrock_agent_runtime = None


def get_bedrock_client():
    """Lazy initialization of Bedrock client"""
    global bedrock_agent_runtime
    if bedrock_agent_runtime is None:
        bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
    return bedrock_agent_runtime


# ============================================================================
# Tool Functions
# ============================================================================


def retrieve_from_knowledge_base(
    query: str,
    max_results: int = 5,
    min_score: float = 0.0,
    knowledge_base_id: Optional[str] = None,
) -> str:
    """
    Retrieve relevant information from Bedrock Knowledge Base using semantic search.

    Args:
        query: The search query
        max_results: Maximum number of results to return (default: 5)
        min_score: Minimum relevance score threshold (0.0-1.0, default: 0.0)
        knowledge_base_id: Optional KB ID (uses env var if not provided)

    Returns:
        Retrieved documents with content, scores, and source metadata
    """
    kb_id = knowledge_base_id or KNOWLEDGE_BASE_ID

    if not kb_id:
        return json.dumps(
            {
                "status": "error",
                "message": "KNOWLEDGE_BASE_ID environment variable not set and no knowledge_base_id provided",
            },
            indent=2,
        )

    try:
        client = get_bedrock_client()
        response = client.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": max_results,
                    "overrideSearchType": "SEMANTIC",
                }
            },
        )

        results = []
        for result in response.get("retrievalResults", []):
            score = result.get("score", 0)

            # Filter by minimum score
            if score < min_score:
                continue

            content = result.get("content", {})
            location = result.get("location", {})
            metadata = result.get("metadata", {})

            # Extract S3 location if available
            s3_location = location.get("s3Location", {})
            uri = s3_location.get("uri") or metadata.get("x-amz-bedrock-kb-source-uri", "")

            # Extract page number if available
            page_number = metadata.get("x-amz-bedrock-kb-document-page-number")
            if isinstance(page_number, float) and page_number.is_integer():
                page_number = int(page_number)

            results.append(
                {
                    "content": content.get("text", ""),
                    "content_type": content.get("type", "TEXT"),
                    "score": score,
                    "uri": uri,
                    "page": page_number,
                    "chunk_id": metadata.get("x-amz-bedrock-kb-chunk-id"),
                    "data_source_id": metadata.get("x-amz-bedrock-kb-data-source-id"),
                }
            )

        return json.dumps(
            {
                "status": "success",
                "query": query,
                "knowledge_base_id": kb_id,
                "results_count": len(results),
                "min_score_filter": min_score,
                "results": results,
                "message": f"Retrieved {len(results)} relevant documents (score >= {min_score})",
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Error retrieving from knowledge base: {e}")
        return json.dumps({"status": "error", "query": query, "message": str(e)}, indent=2)


def retrieve_and_generate(
    query: str,
    max_results: int = 5,
    knowledge_base_id: Optional[str] = None,
    model_arn: Optional[str] = None,
) -> str:
    """
    Retrieve from Knowledge Base and generate a comprehensive answer using RAG.

    Args:
        query: The user's question
        max_results: Maximum number of documents to retrieve (default: 5)
        knowledge_base_id: Optional KB ID (uses env var if not provided)
        model_arn: Optional model ARN (uses default if not provided)

    Returns:
        Generated response with citations and source documents
    """
    kb_id = knowledge_base_id or KNOWLEDGE_BASE_ID

    if not kb_id:
        return json.dumps(
            {
                "status": "error",
                "message": "KNOWLEDGE_BASE_ID environment variable not set and no knowledge_base_id provided",
            },
            indent=2,
        )

    model = model_arn or DEFAULT_MODEL_ARN

    try:
        client = get_bedrock_client()
        response = client.retrieve_and_generate(
            input={"text": query},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": kb_id,
                    "modelArn": model,
                    "retrievalConfiguration": {
                        "vectorSearchConfiguration": {
                            "numberOfResults": max_results,
                            "overrideSearchType": "SEMANTIC",
                        }
                    },
                },
            },
        )

        output = response.get("output", {}).get("text", "")
        citations = response.get("citations", [])

        citation_details = []
        for citation in citations:
            for reference in citation.get("retrievedReferences", []):
                content = reference.get("content", {})
                location = reference.get("location", {})
                metadata = reference.get("metadata", {})

                # Extract S3 location
                s3_location = location.get("s3Location", {})
                uri = s3_location.get("uri") or metadata.get("x-amz-bedrock-kb-source-uri", "")

                # Extract page number
                page_number = metadata.get("x-amz-bedrock-kb-document-page-number")
                if isinstance(page_number, float) and page_number.is_integer():
                    page_number = int(page_number)

                citation_details.append(
                    {
                        "content": content.get("text", ""),
                        "uri": uri,
                        "page": page_number,
                        "chunk_id": metadata.get("x-amz-bedrock-kb-chunk-id"),
                    }
                )

        return json.dumps(
            {
                "status": "success",
                "query": query,
                "knowledge_base_id": kb_id,
                "model_used": model,
                "answer": output,
                "citations_count": len(citation_details),
                "citations": citation_details,
                "message": "Generated response with citations from knowledge base",
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Error in retrieve and generate: {e}")
        return json.dumps({"status": "error", "query": query, "message": str(e)}, indent=2)


# ============================================================================
# MCP Server Configuration
# ============================================================================

TOOLS = [
    Tool(
        name="search_anybank_faq",
        description="""Search AnyBank FAQ knowledge base for customer questions. Returns relevant FAQ sections with scores and metadata. 
        
        The knowledge base contains comprehensive information about:
        - Account opening, types, and management
        - Online/mobile banking features and security
        - Fees, charges, and how to avoid them
        - Debit/credit cards, fraud protection, international use
        - Mortgage information, pre-approval, and requirements
        - Security best practices and fraud prevention
        - Customer service contact methods and hours
        - Account access, beneficiaries, and international banking
        
        Use this when you need to see the exact FAQ content or want to review multiple relevant sections.""",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Customer question or search query (e.g., 'how to open account', 'overdraft fees', 'mobile banking security')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of FAQ sections to return (1-100)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 100,
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum relevance score threshold (0.0-1.0). Only return results with score >= this value",
                    "default": 0.0,
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "knowledge_base_id": {
                    "type": "string",
                    "description": "Optional Knowledge Base ID. If not provided, uses KNOWLEDGE_BASE_ID environment variable",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="answer_anybank_question",
        description="""Answer customer questions about AnyBank using the FAQ knowledge base with RAG (Retrieval Augmented Generation). 
        
        This tool retrieves relevant FAQ information and generates a comprehensive, natural answer with citations. Perfect for:
        - Customer service inquiries
        - Account questions
        - Policy clarifications
        - Fee explanations
        - Security concerns
        - Mortgage information
        - General banking questions
        
        The answer will be synthesized from multiple FAQ sections and include source citations.""",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Customer's question about AnyBank services, policies, or procedures",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of FAQ sections to use for generating the answer (1-100)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 100,
                },
                "knowledge_base_id": {
                    "type": "string",
                    "description": "Optional Knowledge Base ID. If not provided, uses KNOWLEDGE_BASE_ID environment variable",
                },
                "model_arn": {
                    "type": "string",
                    "description": "Optional model ARN for generation. If not provided, uses default Claude 3 Sonnet",
                },
            },
            "required": ["query"],
        },
    ),
]

TOOL_FUNCTIONS = {
    "search_anybank_faq": retrieve_from_knowledge_base,
    "answer_anybank_question": retrieve_and_generate,
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools"""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Call a tool with the given arguments"""
    if name not in TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool: {name}")

    try:
        func = TOOL_FUNCTIONS[name]
        result = func(**arguments)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        logger.error(f"Error calling tool {name}: {e}")
        raise


async def main():
    """Run the MCP server"""
    logger.info("=" * 70)
    logger.info("Starting AnyBank FAQ Knowledge Base MCP Server")
    logger.info("=" * 70)
    logger.info(f"AWS Region: {AWS_REGION}")

    if KNOWLEDGE_BASE_ID:
        logger.info(f"Knowledge Base ID: {KNOWLEDGE_BASE_ID}")
    else:
        logger.warning("⚠️  KNOWLEDGE_BASE_ID not set - must provide knowledge_base_id in tool calls")

    logger.info(f"Default Model ARN: {DEFAULT_MODEL_ARN}")
    logger.info("=" * 70)
    logger.info("FAQ Knowledge Base Content:")
    logger.info("  • General Banking (accounts, safety, closures)")
    logger.info("  • Online & Mobile Banking (security, deposits, bill pay)")
    logger.info("  • Fees & Charges (monthly fees, overdrafts, ATMs)")
    logger.info("  • Debit & Credit Cards (fraud, international use)")
    logger.info("  • Mortgages (pre-approval, documents, timeline)")
    logger.info("  • Security & Fraud (protection, liability)")
    logger.info("  • Customer Service (contact, hours, complaints)")
    logger.info("  • Account Access (authorization, international)")
    logger.info("=" * 70)
    logger.info("Available Tools:")
    logger.info("  1. search_anybank_faq - Search FAQ sections")
    logger.info("  2. answer_anybank_question - Generate answers with citations")
    logger.info("=" * 70)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
