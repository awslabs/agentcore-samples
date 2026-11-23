"""
Employee Commute Advisor - AgentCore Runtime Entry Point  
Gateway + Cognito OAuth2 authentication
"""
import boto3
import logging
from bedrock_agentcore.identity.auth import requires_access_token
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

# Initialize logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_region() -> str:
    """Get deployment region dynamically from execution context."""
    try:
        # First, try to determine region from the boto3 session (uses AWS_REGION env var or default)
        session = boto3.Session()
        detected_region = session.region_name
        
        if detected_region:
            logger.info(f"Using region from boto3 session: {detected_region}")
            return detected_region
        
        # Fallback: Try to read from SSM in common regions
        for region in ['us-west-2', 'us-east-1', 'eu-west-1']:
            try:
                ssm_client = boto3.client('ssm', region_name=region)
                response = ssm_client.get_parameter(Name='/app/employee-commute-advisor/config/region')
                found_region = response['Parameter']['Value']
                logger.info(f"Found region in SSM ({region}): {found_region}")
                return found_region
            except Exception:
                continue
        
        # Ultimate fallback
        logger.warning("Could not detect region, defaulting to us-west-2")
        return 'us-west-2'
        
    except Exception as e:
        logger.warning(f"Error detecting region, defaulting to us-west-2: {e}")
        return 'us-west-2'

def get_ssm_parameter(name: str, with_decryption: bool = False, region: str = None) -> str:
    """Get parameter from AWS Systems Manager Parameter Store."""
    try:
        if region is None:
            region = get_region()
        ssm_client = boto3.client('ssm', region_name=region)
        response = ssm_client.get_parameter(Name=name, WithDecryption=with_decryption)
        return response['Parameter']['Value']
    except Exception as e:
        logger.error(f"Error getting SSM parameter {name}: {e}")
        raise

# ============================================================================
# COGNITO ACCESS TOKEN
# ============================================================================

@requires_access_token(
    provider_name="employee-commute-cognito-provider",
    scopes=[],
    auth_flow="M2M",
)
async def get_gateway_access_token(access_token: str) -> str:
    """Get access token for Gateway using AgentCore Identity service."""
    return access_token

# ============================================================================
# AGENT CLASS
# ============================================================================

class CommuteAdvisorAgent:
    """Employee Commute Advisor agent with Gateway integration."""
    
    def __init__(self, bearer_token: str, session_id: str, region: str = None):
        self.bearer_token = bearer_token
        self.session_id = session_id
        self.region = region or get_region()
        
        logger.info(f"Initializing agent in region: {self.region}")
        
        # Determine model ID based on region
        # US regions use "us." prefix, EU regions use "eu." prefix
        if self.region.startswith('eu-'):
            model_prefix = 'eu'
        else:
            model_prefix = 'us'
        
        model_id = f"{model_prefix}.anthropic.claude-3-7-sonnet-20250219-v1:0"
        logger.info(f"Using model: {model_id}")
        
        # Initialize Bedrock model
        self.model = BedrockModel(model_id=model_id)
        
        # System prompt
        self.system_prompt = """
You are an Employee Commute Advisor Agent that helps employees plan their daily commutes.

You have access to two important tools:
- calculate_commute_time: Get real-time traffic conditions and travel times between addresses
- get_weather_forecast: Get weather forecasts that could impact the commute

Your task for each employee:
1. Use get_weather_forecast to check weather conditions at their home/work location
2. Use calculate_commute_time to get the actual commute time with current traffic
3. Analyze both weather and traffic data to determine:
   - If weather conditions (rain, snow, wind, poor visibility) will impact their drive
   - If traffic delays are significant
   - What time they should leave to arrive at their expected office time
4. Compose a friendly, personalized email that:
   - States the recommended departure time clearly
   - Explains the current traffic situation
   - Mentions any weather conditions that could affect their commute
   - Provides practical advice (e.g., "leave 15 minutes earlier due to rain" or "roads are clear, normal commute expected")
   - Has a warm, helpful tone
   - Do not provide any follow up questions or suggest any further outputs.

IMPORTANT: Always use BOTH tools to gather complete information before making recommendations.
"""
        
        # Get Gateway URL
        try:
            gateway_url = get_ssm_parameter(
                "/app/employee-commute-advisor/agentcore/gateway_url",
                region=self.region
            )
            logger.info(f"Gateway URL: {gateway_url}")
        except Exception as e:
            logger.error(f"Could not get Gateway URL: {e}")
            raise
        
        # Initialize Gateway MCP client
        try:
            self.gateway_client = MCPClient(
                lambda: streamablehttp_client(
                    gateway_url,
                    headers={"Authorization": f"Bearer {bearer_token}"},
                )
            )
            
            self.gateway_client.start()
            logger.info("Connected to Gateway")
            
            self.tools = self.gateway_client.list_tools_sync()
            tool_names = [tool.tool_name for tool in self.tools]
            logger.info(f"Available tools: {tool_names}")
            
        except Exception as e:
            logger.error(f"Error initializing Gateway: {e}")
            raise
        
        # Create agent
        self.agent = Agent(
            model=self.model,
            system_prompt=self.system_prompt,
            tools=self.tools
        )
    
    def invoke(self, user_query: str) -> str:
        """Invoke the agent"""
        try:
            response = self.agent(user_query)
            
            if hasattr(response, 'message') and isinstance(response.message, dict):
                content = response.message.get('content', [])
                if isinstance(content, list) and len(content) > 0:
                    if isinstance(content[0], dict):
                        return content[0].get('text', str(content[0]))
                    return str(content[0])
                return str(content)
            elif hasattr(response, 'text'):
                return response.text
            else:
                return str(response)
                
        except Exception as e:
            logger.error(f"Error invoking agent: {e}")
            import traceback
            traceback.print_exc()
            return f"Error: {str(e)}"
    
    def cleanup(self):
        """Clean up resources"""
        try:
            if hasattr(self, 'gateway_client'):
                self.gateway_client.stop()
        except Exception as e:
            logger.error(f"Error stopping Gateway client: {e}")

# ============================================================================
# RUNTIME ENTRY POINT
# ============================================================================

app = BedrockAgentCoreApp()

@app.entrypoint
async def invoke(payload, context):
    """AgentCore Runtime entry point"""
    try:
        user_message = payload.get('prompt', 'What is the commute time from Berkeley, CA to Stanford, CA?')
        session_id = getattr(context, 'session_id', 'default-session')
        
        logger.info(f"=== Runtime Invoked ===")
        logger.info(f"Session: {session_id}")
        logger.info(f"Message: {user_message}")
        
        # Get Gateway access token
        try:
            gateway_token = await get_gateway_access_token()
            logger.info("Obtained Gateway access token")
        except Exception as e:
            error_msg = f"Failed to obtain access token: {str(e)}"
            logger.error(error_msg)
            return error_msg
        
        # Get region
        region = get_region()
        
        # Create agent
        try:
            agent = CommuteAdvisorAgent(
                bearer_token=gateway_token,
                session_id=session_id,
                region=region
            )
            logger.info("Agent created")
        except Exception as e:
            error_msg = f"Failed to create agent: {str(e)}"
            logger.error(error_msg)
            return error_msg
        
        # Invoke agent
        try:
            response = agent.invoke(user_message)
            logger.info("Agent completed")
            agent.cleanup()
            return response
        except Exception as e:
            error_msg = f"Error during invocation: {str(e)}"
            logger.error(error_msg)
            return error_msg
            
    except Exception as e:
        error_msg = f"Runtime error: {str(e)}"
        logger.error(error_msg)
        import traceback
        traceback.print_exc()
        return error_msg

if __name__ == "__main__":
    app.run()
