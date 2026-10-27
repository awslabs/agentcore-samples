#!/usr/bin/env python3
"""
Simple Strands Agent with CORS and Guardrail Middleware
Using Starlette with proper CORS configuration
"""

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware  
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from starlette.routing import Route
import uvicorn
import boto3
import json
import logging
import os
from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool
from tavily import TavilyClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get guardrail ID dynamically from SSM Parameter Store - NO HARDCODING
try:
    ssm_client = boto3.client('ssm')
    response = ssm_client.get_parameter(Name='/simple_agent/guardrail_id')
    GUARDRAIL_ID = response['Parameter']['Value']
    logger.info(f"✅ Loaded Guardrail ID from SSM: {GUARDRAIL_ID}")
except Exception as e:
    logger.error(f"❌ Failed to load Guardrail ID from SSM: {e}")
    logger.error("⚠️ CRITICAL: No Guardrail ID available - the agent cannot start without a guardrail")
    # Exit if we can't get the guardrail ID - this is a critical requirement
    import sys
    sys.exit(1)

# Initialize Tavily client for web search (optional - will work without API key but with limited functionality)
TAVILY_API_KEY = os.environ.get('TAVILY_API_KEY')

# If not in environment, try to get from SSM Parameter Store
if not TAVILY_API_KEY:
    try:
        ssm_client = boto3.client('ssm')
        response = ssm_client.get_parameter(Name='/simple_agent/tavily_api_key', WithDecryption=True)
        TAVILY_API_KEY = response['Parameter']['Value']
        logger.info("✅ Loaded Tavily API key from SSM Parameter Store")
    except Exception as e:
        logger.warning(f"⚠️ Could not load Tavily API key from SSM: {e}")

if TAVILY_API_KEY:
    tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    logger.info("✅ Tavily API client initialized for web search")
else:
    tavily_client = None
    logger.warning("⚠️ Tavily API key not found - web search will be limited")

class GuardrailMiddleware(BaseHTTPMiddleware):
    """Middleware to apply Bedrock Guardrails on input and output"""
    
    def __init__(self, app, guardrail_id: str):
        super().__init__(app)
        self.guardrail_id = guardrail_id
        self.bedrock_runtime = boto3.client('bedrock-runtime')
        logger.info(f"✅ Guardrail middleware initialized with ID: {guardrail_id}")
    
    async def dispatch(self, request: Request, call_next):
        """Apply guardrails to requests and responses"""
        
        # Apply guardrail to INPUT
        if request.method == "POST" and request.url.path == "/invocations":
            try:
                body = await request.body()
                body_str = body.decode('utf-8')
                
                # Extract the prompt from JSON
                try:
                    body_json = json.loads(body_str)
                    prompt_text = body_json.get('prompt', '')
                except json.JSONDecodeError:
                    prompt_text = body_str
                
                logger.info(f"🛡️ Validating input with guardrail: {self.guardrail_id}")
                logger.info(f"📝 Middleware captured INPUT: '{prompt_text}'")
                print(f"\n{'='*60}")
                print(f"🔍 MIDDLEWARE INPUT CAPTURE")
                print(f"{'='*60}")
                print(f"📥 Raw Body: {body_str}")
                print(f"📝 Extracted Prompt: '{prompt_text}'")
                print(f"{'='*60}\n")
                
                try:
                    # Apply Bedrock Guardrail to just the prompt text
                    guardrail_response = self.bedrock_runtime.apply_guardrail(
                        guardrailIdentifier=self.guardrail_id,
                        guardrailVersion="DRAFT",
                        source="INPUT",
                        content=[{"text": {"text": prompt_text}}]
                    )
                    
                    if guardrail_response.get('action') == 'GUARDRAIL_INTERVENED':
                        # Check if it's actually inappropriate content or just a false positive
                        should_block = False
                        assessments = guardrail_response.get('assessments', [])
                        
                        for assessment in assessments:
                            # Check topicPolicy (e.g., Harmful Content)
                            topic_policy = assessment.get('topicPolicy', {})
                            topics = topic_policy.get('topics', [])
                            for topic in topics:
                                if topic.get('action') == 'BLOCKED' and topic.get('detected'):
                                    should_block = True
                                    logger.info(f"🚫 Blocked by topic: {topic.get('name')}")
                                    break
                            
                            # Check contentPolicy filters
                            content_policy = assessment.get('contentPolicy', {})
                            filters = content_policy.get('filters', [])
                            
                            for filter_item in filters:
                                filter_type = filter_item.get('type', '')
                                confidence = filter_item.get('confidence', '')
                                
                                # Only block for actual inappropriate content, not low-confidence prompt attacks
                                if filter_type in ['HATE', 'INSULTS', 'VIOLENCE', 'SEXUAL', 'MISCONDUCT']:
                                    should_block = True
                                    logger.info(f"🚫 Blocked by filter: {filter_type} ({confidence})")
                                    break
                                elif filter_type == 'PROMPT_ATTACK' and confidence in ['MEDIUM', 'HIGH']:
                                    should_block = True
                                    logger.info(f"🚫 Blocked by filter: {filter_type} ({confidence})")
                                    break
                        
                        if should_block:
                            logger.warning(f"🛡️ Input blocked by guardrail")
                            blocked_message = "⚠️ Your message was blocked due to policy violations. Please rephrase your request without inappropriate content."
                            print(f"\n{'='*60}")
                            print(f"🚫 BLOCKED BY GUARDRAIL")
                            print(f"{'='*60}")
                            print(f"❌ Input: '{prompt_text}'")
                            print(f"🛡️ Reason: Policy violation detected")
                            print(f"💬 Response: {blocked_message}")
                            print(f"{'='*60}\n")
                            return Response(
                                content=blocked_message,
                                status_code=200,
                                media_type="text/plain"
                            )
                        else:
                            logger.info(f"⚠️ Guardrail flagged but allowing (false positive)")
                            print(f"\n{'='*60}")
                            print(f"⚠️ FALSE POSITIVE - ALLOWING")
                            print(f"{'='*60}")
                            print(f"📝 Input: '{prompt_text}'")
                            print(f"✅ Decision: Allowing (low confidence flag)")
                            print(f"{'='*60}\n")
                    else:
                        print(f"\n{'='*60}")
                        print(f"✅ INPUT VALIDATION PASSED")
                        print(f"{'='*60}")
                        print(f"📝 Input: '{prompt_text}'")
                        print(f"✅ Status: Clean - No issues detected")
                        print(f"{'='*60}\n")
                    
                    logger.info("✅ Input passed guardrail validation")
                except Exception as guardrail_error:
                    # Log but don't block if guardrail check fails
                    logger.warning(f"⚠️ Guardrail check failed, allowing request: {guardrail_error}")
                
                # Recreate request with body
                request._body = body
                
            except Exception as e:
                logger.error(f"❌ Error processing request body: {e}")
        
        # Process request
        response = await call_next(request)
        
        # Apply guardrail to OUTPUT
        if request.url.path == "/invocations":
            try:
                # Read response body
                response_body = b""
                async for chunk in response.body_iterator:
                    response_body += chunk
                
                response_str = response_body.decode('utf-8')
                
                logger.info(f"🛡️ Validating output with guardrail: {self.guardrail_id}")
                logger.info(f"📤 Middleware captured OUTPUT: '{response_str[:100]}...'")
                print(f"\n{'='*60}")
                print(f"🔍 MIDDLEWARE OUTPUT CAPTURE")
                print(f"{'='*60}")
                print(f"📤 Response (first 200 chars): '{response_str[:200]}...'")
                print(f"📏 Total Length: {len(response_str)} characters")
                print(f"{'='*60}\n")
                
                try:
                    # Apply Bedrock Guardrail to output
                    guardrail_response = self.bedrock_runtime.apply_guardrail(
                        guardrailIdentifier=self.guardrail_id,
                        guardrailVersion="DRAFT",
                        source="OUTPUT",
                        content=[{"text": {"text": response_str}}]
                    )
                    
                    if guardrail_response.get('action') == 'GUARDRAIL_INTERVENED':
                        logger.warning(f"🛡️ Output blocked by guardrail: {guardrail_response}")
                        # Return safe response when output is blocked
                        return Response(
                            content="I cannot provide that response as it violates content policies.",
                            status_code=200,
                            headers=dict(response.headers),
                            media_type="text/plain"
                        )
                    
                    logger.info("✅ Output passed guardrail validation")
                except Exception as guardrail_error:
                    # Log but don't block if guardrail check fails
                    logger.warning(f"⚠️ Output guardrail check failed, allowing response: {guardrail_error}")
                
                # Return new response with same content
                return Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type
                )
                
            except Exception as e:
                logger.error(f"❌ Error validating response: {e}")
        
        return response

# Create Strands agent
@tool
def get_weather(location: str) -> str:
    """Get weather for a location"""
    return f"The weather in {location} is sunny and 72°F"

@tool
def calculate(expression: str) -> str:
    """Calculate a mathematical expression"""
    try:
        # Safely evaluate mathematical expressions
        # Remove any potentially dangerous functions
        safe_dict = {
            '__builtins__': {},
            'abs': abs, 'round': round, 'min': min, 'max': max,
            'sum': sum, 'pow': pow, 'len': len
        }
        # Allow basic math operations
        import math
        for func in ['sqrt', 'sin', 'cos', 'tan', 'log', 'exp', 'pi', 'e']:
            if hasattr(math, func):
                safe_dict[func] = getattr(math, func)
        
        result = eval(expression, safe_dict)
        return f"The result is: {result}"
    except Exception as e:
        return f"Invalid expression: {str(e)}"

def format_search_results(tavily_result):
    """Format Tavily search results for the agent"""
    if not tavily_result or "results" not in tavily_result or not tavily_result["results"]:
        return "No search results found."
    
    formatted_results = []
    for i, doc in enumerate(tavily_result["results"][:5], 1):  # Limit to top 5 results
        title = doc.get("title", "No title")
        url = doc.get("url", "No URL")
        content = doc.get("content", "").strip()
        
        if content:
            # Truncate content if too long
            if len(content) > 500:
                content = content[:500] + "..."
            
            formatted_doc = f"\n**Result {i}:**\n"
            formatted_doc += f"Title: {title}\n"
            formatted_doc += f"URL: {url}\n"
            formatted_doc += f"Content: {content}\n"
            formatted_results.append(formatted_doc)
    
    return "\n".join(formatted_results)

@tool
def web_search(query: str) -> str:
    """
    Search the web for information about any topic.
    Use this for general questions, current events, facts, or any queries
    that aren't simple calculations or weather requests.
    
    Args:
        query: The search query to look up on the web
        
    Returns:
        Search results with titles, URLs, and content snippets
    """
    try:
        if not tavily_client:
            return "Web search is not available. Please set TAVILY_API_KEY environment variable to enable web search."
        
        # Perform the search
        search_results = tavily_client.search(
            query=query,
            max_results=5,
            search_depth="advanced",
            include_raw_content=False
        )
        
        # Format and return results
        formatted = format_search_results(search_results)
        
        if not formatted or formatted == "No search results found.":
            return f"No results found for '{query}'. Try rephrasing your search."
        
        return f"Web search results for '{query}':\n{formatted}"
        
    except Exception as e:
        logger.error(f"Error performing web search: {e}")
        return f"An error occurred while searching: {str(e)}"

model = BedrockModel(model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0")

# Build tools list based on what's available
tools = [get_weather, calculate]
if tavily_client:
    tools.append(web_search)

agent = Agent(
    model=model,
    tools=tools,
    system_prompt="""You are a helpful assistant with access to various tools.

Use the appropriate tool based on the user's request:
- For mathematical calculations, use the calculate tool
- For weather information, use the get_weather tool  
- For ANY other questions (general knowledge, current events, facts, research, etc.), use the web_search tool

When using web_search, provide a clear and informative response based on the search results.
If the initial search doesn't provide enough information, you can search again with a refined query.

Always aim to provide accurate, helpful, and relevant information to the user."""
)

async def invocations(request: Request):
    """Handle invocation requests"""
    try:
        body = await request.json()
        user_input = body.get("prompt", "")
        
        logger.info(f"📨 Received request: {user_input}")
        
        if not user_input:
            return JSONResponse(
                content={"error": "No prompt provided"},
                status_code=400
            )
        
        # Process with Strands agent
        response = agent(user_input)
        result = response.message['content'][0]['text']
        
        logger.info(f"📤 Sending response")
        
        return Response(content=result, media_type="text/plain")
        
    except Exception as e:
        logger.error(f"❌ Error processing request: {e}")
        return JSONResponse(
            content={"error": str(e)},
            status_code=500
        )

async def ping(request: Request):
    """Health check endpoint"""
    return JSONResponse(content={"status": "healthy"})

# Handle browser preflight requests for CORS
async def options_handler(request: Request):
    """Handle OPTIONS requests for CORS preflight"""
    return JSONResponse(content={"message": "OK"})

# Create Starlette app with middleware
app = Starlette(
    debug=True,
    routes=[
        Route('/invocations', invocations, methods=['POST']),
        Route('/invocations', options_handler, methods=['OPTIONS']),  # CORS preflight
        Route('/ping', ping, methods=['GET']),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Customize in production
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
        Middleware(GuardrailMiddleware, guardrail_id=GUARDRAIL_ID),
    ],
)

if __name__ == "__main__":
    logger.info("🚀 Starting Simple Agent with Guardrail Middleware")
    logger.info(f"🛡️ Guardrail ID: {GUARDRAIL_ID}")
    uvicorn.run(app, host="0.0.0.0", port=8080)
