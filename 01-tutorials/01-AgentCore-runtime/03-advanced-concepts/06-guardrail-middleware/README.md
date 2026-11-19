# AWS Bedrock AgentCore Guardrail Middleware Tutorial

This tutorial demonstrates how to implement AWS Bedrock Guardrails as middleware in a Strands agent deployed on AWS Bedrock AgentCore Runtime. The solution provides content moderation for both inputs and outputs using Starlette middleware pattern.

## 🎯 Overview

This implementation showcases:
- **Guardrail Middleware**: Custom Starlette middleware that intercepts all requests and responses
- **Content Filtering**: Blocks inappropriate content (hate speech, insults, violence, etc.)
- **Smart Detection**: Handles false positives intelligently (e.g., allows math questions)
- **CORS Support**: Configured for browser-based applications
- **Enhanced Logging**: Detailed capture of inputs/outputs for debugging

## 📁 Files

```
06-guardrail-middleware/
├── simple_agent.py              # Main agent with guardrail & CORS middleware
├── app.py                       # Streamlit UI for testing
├── deploy_simple_agent.py       # Deployment script with automatic IAM setup
├── cleanup_all.py              # Cleanup script for all resources
├── test_web_search.py          # Test script for web search functionality
├── simple_requirements.txt      # Agent dependencies
├── streamlit_requirements.txt   # UI dependencies
└── README.md                    # This file
```

## 🏗️ Architecture

```
User Request
    ↓
Streamlit UI (app.py)
    ↓
AWS Bedrock AgentCore Runtime
    ↓
CORS Middleware (browser support)
    ↓
Guardrail Middleware ← AWS Bedrock Guardrails API
    ├─ Input Validation
    ├─ Process Request → Strands Agent
    └─ Output Validation
    ↓
Response to User
```

## ✨ Features

### 1. **Guardrail Protection**
- Validates all inputs before processing
- Validates all outputs before returning
- Uses AWS Bedrock Guardrails 
- Configurable policies and filters

### 2. **Smart Filtering**
- **Blocks**: Hate speech, insults, violence, sexual content, misconduct
- **Allows**: Normal queries, math questions, weather requests, web searches
- **Handles False Positives**: Ignores low-confidence PROMPT_ATTACK flags

### 3. **Web Search Integration (NEW!)**
- **Tavily API Integration**: Real-time web search capabilities
- **Intelligent Routing**: Automatically uses web search for general knowledge questions
- **Formatted Results**: Clean presentation of search results with titles, URLs, and content
- **Optional Feature**: Works without Tavily API key (falls back gracefully)

### 4. **CORS Configuration**
- Full CORS middleware support
- OPTIONS handler for preflight requests
- Ready for browser-based applications

### 5. **Enhanced Logging**
- Captures and displays all inputs/outputs
- Shows validation decisions and reasons
- Visual formatting for clarity

## 📋 Prerequisites

1. **AWS Account** with:
   - AWS Bedrock access
   - AWS Bedrock AgentCore access
   - Configured AWS credentials

2. **Local Tools**:
   ```bash
   # Required
   - Python 3.8+
   - AWS CLI configured
   - pip or conda
   
   # Install SDK
   pip install bedrock-agentcore-starter-toolkit
   ```

3. **AWS Guardrail**:
   - The deployment script automatically creates a guardrail with appropriate filters
   - Guardrail configuration includes: HATE, INSULTS, VIOLENCE, SEXUAL, MISCONDUCT, and Harmful Content topic
   - The guardrail ID is dynamically managed and stored in Parameter Store

4. **Tavily API (Optional - for web search)**:
   - Sign up at [app.tavily.com](https://app.tavily.com/home/) for a free API key
   - Set the environment variable: `export TAVILY_API_KEY=your-api-key`
   - The agent will work without this, but web search will be unavailable

## 🚀 Deployment

### 1. Configure Tavily API (Optional)

If you want web search capabilities:
```bash
# Sign up at https://app.tavily.com/home/ for a free API key
export TAVILY_API_KEY=your-tavily-api-key-here
```

### 2. Deploy the Agent

```bash
cd 06-guardrail-middleware
python deploy_simple_agent.py
```

This will:
- Create a new guardrail (or reuse existing one from SSM Parameter Store)
- Save the guardrail ID to SSM Parameter Store (`/simple_agent/guardrail_id`)
- Save Tavily API key to SSM Parameter Store if provided (as SecureString)
- Build the Docker container in AWS CodeBuild (ARM64)
- Deploy to AWS Bedrock AgentCore Runtime
- Automatically find and update the execution role with required permissions:
  - `bedrock:ApplyGuardrail` and `bedrock:GetGuardrail` for guardrails
  - `ssm:GetParameter` for reading configuration from Parameter Store
  - `kms:Decrypt` for SecureString parameters
- Store all configuration in SSM Parameter Store for reuse

### 3. Launch Streamlit UI

```bash
streamlit run app.py
```

Opens at http://localhost:8501

## 🧪 Testing

### Test via Streamlit UI

1. **Normal Queries** (Should Work):
   - "What's 2+2?" → Returns: "The result is: 4"
   - "Calculate 25 * 4" → Returns: "The result is: 100"
   - "What's the weather in Seattle?" → Returns weather info

2. **Web Search Queries** (Should Work with Tavily API):
   - "What are the latest AI developments?" → Returns web search results
   - "Who won the Nobel Prize in Physics 2024?" → Returns current information
   - "What is quantum computing?" → Returns detailed explanations from the web
   - "Latest news about climate change" → Returns recent articles and information

3. **Inappropriate Content** (Should Block):
   - "I hate you!!" → Blocked with warning message
   - Explicit profanity → Blocked
   - Insults → Blocked

### Test Web Search Functionality

```bash
python test_web_search.py
```

This will:
- Test Tavily API directly
- Test the agent's web search integration
- Show formatted search results

## 🔧 How It Works

### Dynamic Configuration Management

The deployment script automatically:
1. Creates a guardrail with comprehensive filters (or reuses existing)
2. Stores the guardrail ID in SSM Parameter Store (`/simple_agent/guardrail_id`)
3. Stores Tavily API key in SSM Parameter Store as SecureString (if provided)
4. Agent reads configuration from SSM at runtime - no hardcoding!

```python
# Configuration is dynamically loaded from SSM Parameter Store
try:
    ssm_client = boto3.client('ssm')
    response = ssm_client.get_parameter(Name='/simple_agent/guardrail_id')
    GUARDRAIL_ID = response['Parameter']['Value']
    
    # Also try to load Tavily API key from SSM
    try:
        response = ssm_client.get_parameter(
            Name='/simple_agent/tavily_api_key', 
            WithDecryption=True
        )
        TAVILY_API_KEY = response['Parameter']['Value']
    except:
        TAVILY_API_KEY = os.environ.get('TAVILY_API_KEY')
except:
    # Fail safely if SSM is not available
    sys.exit(1)
```

### IAM Permission Management

The deployment script automatically:
1. Identifies the most recently created execution role
2. Adds inline policy with all required permissions
3. Waits for permissions to propagate before completing
4. No manual IAM configuration needed!


### Middleware Implementation

```python
class GuardrailMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Extract prompt from request
        prompt_text = extract_prompt(request)
        
        # 2. Validate input with guardrail
        if guardrail_blocks_input(prompt_text):
            return blocked_response()
        
        # 3. Process request
        response = await call_next(request)
        
        # 4. Validate output with guardrail
        if guardrail_blocks_output(response):
            return safe_response()
        
        # 5. Return validated response
        return response
```

### Guardrail Logic

The middleware checks both:
1. **topicPolicy**: Topics like "Harmful Content"
2. **contentPolicy**: Filters like HATE, INSULTS, VIOLENCE

```python
# Smart filtering logic
if filter_type in ['HATE', 'INSULTS', 'VIOLENCE', 'SEXUAL', 'MISCONDUCT']:
    should_block = True
elif filter_type == 'PROMPT_ATTACK' and confidence in ['MEDIUM', 'HIGH']:
    should_block = True
else:
    # Allow low-confidence prompt attacks (usually false positives)
    should_block = False
```


## 🗑️ Cleanup

To remove all resources:

```bash
python cleanup_all.py
```

This will:
- Delete the AgentCore Runtime and endpoints
- Delete the created guardrail
- Remove all Parameter Store entries including:
  - `/simple_agent/guardrail_id`
  - `/simple_agent/runtime/*`
  - `/simple_agent/tavily_api_key` (SecureString)
- Delete ECR repository
- Remove IAM roles and inline policies
- Clean up all related resources

### Tool Integration

The agent now includes three tools that are automatically selected based on the user's query:

```python
# Available tools
@tool
def calculate(expression: str) -> str:
    """Mathematical calculations with safe evaluation"""
    
@tool
def get_weather(location: str) -> str:
    """Weather information for any location"""
    
@tool  
def web_search(query: str) -> str:
    """Web search via Tavily API for general knowledge questions"""
```

The agent intelligently routes queries to the appropriate tool:
- Math questions → `calculate` tool
- Weather queries → `get_weather` tool  
- Everything else → `web_search` tool (if Tavily API is configured)

## 🎓 Key Learnings

1. **Middleware Pattern**: How to implement custom middleware with Starlette
2. **Guardrail Integration**: Using AWS Bedrock Guardrails API
3. **Tool Integration**: Adding external API tools (Tavily) to Strands agents
4. **Dynamic Tool Selection**: Agent intelligently routes queries to appropriate tools
5. **CORS Configuration**: Enabling browser support
6. **Error Handling**: Fail-open design for service resilience
7. **Logging**: Comprehensive logging for debugging

## 📚 Additional Resources

- [AWS Bedrock Guardrails Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html)
- [AWS Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
- [Starlette Middleware Documentation](https://www.starlette.io/middleware/)
- [Strands Agents Documentation](https://github.com/BenevolenceMessiah/strands-agents)
- [Tavily API Documentation](https://docs.tavily.com/)

## 📝 License

This sample code is provided under the MIT-0 License. See the LICENSE file.
