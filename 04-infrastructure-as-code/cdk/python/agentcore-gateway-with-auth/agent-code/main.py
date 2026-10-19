import os
import boto3
import logging
from botocore.config import Config as BotocoreConfig
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from streamable_http_sigv4 import streamablehttp_client_with_sigv4
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize AgentCore Runtime App
app = BedrockAgentCoreApp()

def get_full_tools_list(client):
    """Retrieve all tools from MCP client, handling pagination."""
    more_tools = True
    tools = []
    pagination_token = None
    
    while more_tools:
        tmp_tools = client.list_tools_sync(pagination_token=pagination_token)
        tools.extend(tmp_tools)
        
        if tmp_tools.pagination_token is None:
            more_tools = False
        else:
            pagination_token = tmp_tools.pagination_token
    
    return tools

def create_mcp_transport():
    """Create SigV4 authenticated MCP transport for AgentCore Gateway connection."""
    gateway_url = os.environ.get('GATEWAY_URL')
    region = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
    
    if not gateway_url:
        raise RuntimeError("GATEWAY_URL environment variable is required")
    
    # Get AWS credentials from AgentCore Runtime execution role
    session = boto3.Session()
    credentials = session.get_credentials()
    
    return streamablehttp_client_with_sigv4(
        url=gateway_url,
        credentials=credentials,
        service="bedrock-agentcore",
        region=region
    )

@app.entrypoint
async def invoke(payload=None):
    """AgentCore Runtime entrypoint for gateway-based ticket agent."""
    try:
        # Initialize MCP client
        mcp_client = MCPClient(
            lambda: create_mcp_transport()
        )
        
        with mcp_client:
            tools = get_full_tools_list(mcp_client)
            logger.info(f"Discovered {len(tools)} tools from gateway")

            # Create Bedrock model
            model = BedrockModel(
                model_id=os.getenv("AGENT_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"),
                temperature=0.1,
                boto_session=boto3.Session(region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
            )

            # Extract user context from payload
            user_input = payload.get("input", "Hello") if payload else "Hello"
            user_id = payload.get("user_id", "anonymous_user") if payload else "anonymous_user"
            session_id = payload.get("session_id", "session-default-fallback-demo-testing-0001") if payload else "session-default-fallback-demo-testing-0001"
            
            logger.info(f"[SESSION {session_id}] User: {user_id}, Input: {user_input[:50]}...")
            
            # Create system prompt with user context
            system_prompt = f"""You are a Ticket Management Assistant for user: {user_id} (Session: {session_id})

You have access to ticket management capabilities through secure gateway connections.

## Core Capabilities:

### Ticket Management
- Create new support tickets with descriptions and optional comments
- Retrieve detailed information about specific tickets by ID
- List all tickets for the current user with optional status filtering
- Update ticket comments or status information

## Tool Call Guidelines:
- Execute operations carefully and wait for responses before deciding next steps
- After receiving successful responses, explain the result clearly to the user

## Important Guidelines:
- IMPORTANT: You are authenticated as {user_id}. Always use this user_id ({user_id}) for all operations that require user_id parameter
- Ticket IDs follow format REQ-XXXXXXXX
- When creating tickets, return the ticket ID to the user
- When listing tickets, show ticket IDs, descriptions, and status
- When updating tickets, confirm the update was successful

## Professional Communication:
- NEVER mention tool names, function names, or technical implementation details
- NEVER expose raw error messages or system responses
- Focus on what the user can do, not on system limitations or technical processes
- Be helpful and professional in your responses
"""
            
            # Setup memory integration if memory_id is available
            session_manager = None
            memory_id = os.getenv("MEMORY_ID")
            logger.info(f"MEMORY_ID environment variable: '{memory_id}'")
            
            if memory_id and memory_id.strip():
                try:
                    logger.info(f"Attempting to create memory config with memory_id: {memory_id}")
                    config = AgentCoreMemoryConfig(
                        memory_id=memory_id,
                        session_id=session_id,
                        actor_id=user_id,
                        retrieval_config={
                            "tickets/{actorId}/preferences/": RetrievalConfig(
                                top_k=3,
                                relevance_score=0.7
                            ),
                            "tickets/{actorId}/context/": RetrievalConfig(
                                top_k=5,
                                relevance_score=0.5
                            )
                        }
                    )
                    logger.info(f"Memory config created successfully")
                    
                    session_manager = AgentCoreMemorySessionManager(
                        config, 
                        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")
                    )
                    logger.info(f"Memory integration enabled with memory_id: {memory_id}")
                except Exception as e:
                    logger.error(f"Memory integration failed, continuing without memory: {e}", exc_info=True)
                    session_manager = None
            else:
                logger.info(f"No valid memory_id provided (value: '{memory_id}'), running without memory")
            
            # Create agent WITH session manager
            agent = Agent(
                model=model,
                tools=tools,
                system_prompt=system_prompt,
                name="TicketAgent",
                session_manager=session_manager
            )
            
            # Process request
            logger.info(f"Calling agent with {len(tools)} tools available")
            result = agent(user_input)
            
            # Extract response
            response_text = result.message['content'][0]['text']
            logger.info(f"[SESSION {session_id}] Response: {response_text[:100]}...")
            
            return {
                "status": "success",
                "response": response_text,
                "user_id": user_id,
                "session_id": session_id
            }
        
    except Exception as e:
        logger.error(f"Error during invocation: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "error": str(e)
        }

if __name__ == "__main__":
    app.run()
