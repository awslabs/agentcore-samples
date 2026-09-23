# Coordinator Agent - AgentCore Identity Sample

The Coordinator Agent is the supervisor/orchestrator agent in the AgentCore Identity financial services multi-agent system. It receives authenticated requests and intelligently routes them to specialized action agents.

## Architecture

The coordinator implements a supervisor pattern that:

1. **Receives authenticated requests** from AgentCore Gateway with JWT-based user context
2. **Extracts and validates user authorization** from Auth0 JWT claims
3. **Scope-gates tools**: users without `accounts:*` scopes don't see the accounts routing tool
4. **Performs RFC 8693 token exchange**: exchanges the user JWT for attenuated tokens with per-agent scope policies before routing
5. **Routes requests** to appropriate specialized agents:
   - **Customer Profile Agent**: Profile operations (receives only profile:* scopes)
   - **Accounts Agent**: Account queries and operations (receives only accounts:* scopes)
6. **Maintains session context** using AgentCore Memory
7. **Returns responses** to the client

## File Structure

```
coordinator/
├── __init__.py                 # Package initialization
├── main.py                     # Entry point with @app.entrypoint decorator
├── agent.py                    # CoordinatorAgent class implementation
├── auth_context.py             # Authentication context management
├── subagent_router.py          # SubAgent routing logic
├── tools/
│   ├── __init__.py            # Tools package initialization
│   ├── profile_tools.py       # MCP tools for profile agent
│   └── routing_tools.py       # Intent-based routing tools
├── Dockerfile                  # Container configuration
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

## Key Components

### main.py - Entry Point

The main entry point uses the AgentCore Runtime `@app.entrypoint` decorator pattern:

```python
@app.entrypoint
async def handler(request: InvocationRequest) -> InvocationResponse:
    # Extract user context from JWT claims
    user_context = extract_user_context(request)

    # Validate authorization
    if not validate_user_authorization(user_context):
        return InvocationResponse(error="AUTHORIZATION_FAILED")

    # Create agent and process request
    agent = create_agent(session_id, user_context, router)
    response = await agent.process(request.input_text, user_context)

    return InvocationResponse(output_text=response['output'])
```

### agent.py - CoordinatorAgent Class

The `CoordinatorAgent` class orchestrates the entire conversation:

- Uses **Claude 3.5 Sonnet** as the LLM
- Implements **tool calling** for routing to sub-agents
- Manages **conversation history** with AgentCore Memory
- Provides **context-aware system prompts**
- Handles **multi-turn conversations** with tool execution

### auth_context.py - Authentication Management

The `AuthContextManager` handles:

- **JWT claim extraction** from Auth0 tokens
- **Custom claims** with namespace support (`https://agentcore.example.com/`)
- **Permission validation** based on OAuth scopes
- **Context caching** for performance
- **Authorization checks** for sensitive operations

Key functions:
- `extract_user_context()` - Extract user claims from request
- `validate_user_authorization()` - Check user permissions
- `get_customer_id()` - Get financial services customer ID
- `has_permission()` - Check specific permission

### subagent_router.py - SubAgent Router

The `SubAgentRouter` manages communication with action agents:

- **Routes to profile agent** for profile operations with exchanged token (profile scopes only)
- **Routes to accounts agent** for account queries with exchanged token (accounts scopes only)
- **Passes exchanged token** in payload context for application-level scope enforcement
- **Handles streaming responses** from AgentCore Runtime

Methods:
- `route_to_profile()` - Route to Customer Profile Agent
- `route_to_accounts()` - Route to Accounts Agent
- `route_by_intent()` - Intent-based routing

### tools/profile_tools.py - Profile Agent Tools

MCP-compatible tool definitions for profile operations:

- `profile_get_customer_profile` - Retrieve customer profile
- `profile_update_customer_address` - Update mailing address
- `profile_update_customer_phone` - Update phone number
- `profile_update_customer_email` - Update email address
- `profile_update_customer_preferences` - Update preferences

Tools are **permission-aware** - only available if user has required scopes.

### tools/routing_tools.py - Routing Tools

Intent-based routing tools for the coordinator:

- `route_to_accounts_agent` - Route account queries
- `route_to_profile_agent` - Route profile queries
- `get_available_agents` - List available services
- `classify_intent()` - Intent classification helper

## Environment Variables

Required environment variables:

```bash
# AWS Configuration
AWS_REGION=us-east-1

# Auth0 Configuration (from shared settings)
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_AUDIENCE=https://agentcore-financial-api

# AgentCore Agent IDs
COORDINATOR_AGENT_ID=agent-xyz123
PROFILE_AGENT_ID=agent-abc456
ACCOUNTS_AGENT_ID=agent-def789

# AgentCore Memory
AGENTCORE_MEMORY_ID=memory-xyz123

# Note: This project uses AWS Bedrock for LLM access (no Anthropic API key needed)

# Logging
LOG_LEVEL=INFO
```

## Authentication & Authorization

### JWT Claims Structure

The coordinator expects the following JWT structure from Auth0:

```json
{
  "sub": "auth0|user123",
  "email": "customer@example.com",
  "email_verified": true,
  "scope": "openid profile email profile:personal:read profile:personal:write profile:preferences:read profile:preferences:write",
  "https://agentcore.example.com/customer_id": "CUST-001",
  "https://agentcore.example.com/account_tier": "premium"
}
```

### Permission Model

- **profile:personal:read** - Read personal profile data
- **profile:personal:write** - Update personal profile data
- **profile:preferences:read** - Read preferences
- **profile:preferences:write** - Update preferences
- **admin:read** - Admin read access (future use)
- **admin:write** - Admin write access (future use)

### Authorization Flow

1. **JWT validation** by AgentCore Gateway (JWT Authorizer)
2. **Claims extraction** by coordinator auth_context module
3. **Scope-gated tools** - accounts tool removed from LLM if user lacks accounts:* scopes
4. **Permission check before exchange** - coordinator returns PERMISSION_DENIED if scopes missing (safety net)
5. **RFC 8693 token exchange** - attenuated token minted with per-agent scope policy
6. **Customer ID enforcement** - users can only access their own data

## Usage Examples

### Basic Query

```python
# Customer asks: "What is my account balance?"
# Coordinator routes to Accounts Agent
request = InvocationRequest(
    input_text="What is my account balance?",
    session_id="session-123",
    request_attributes={
        "sub": "auth0|user123",
        "https://agentcore.example.com/customer_id": "CUST-001",
        "scope": "profile:personal:read accounts:savings:read"
    }
)
```

### Profile Update

```python
# Customer asks: "Update my address to 123 Main St, Seattle, WA 98101"
# Coordinator routes to Profile Agent with write permissions
request = InvocationRequest(
    input_text="Update my address to 123 Main St, Seattle, WA 98101",
    session_id="session-123",
    request_attributes={
        "sub": "auth0|user123",
        "https://agentcore.example.com/customer_id": "CUST-001",
        "scope": "profile:personal:read profile:personal:write"
    }
)
```

### Multi-Turn Conversation

```python
# Turn 1: "Show me my accounts"
# Coordinator routes to Accounts Agent

# Turn 2: "What about my savings account?"
# Coordinator maintains context and routes to Accounts Agent

# Turn 3: "Can you update my email to newemail@example.com?"
# Coordinator routes to Profile Agent (different agent, maintains context)
```

## Docker Build

Build the coordinator agent container:

```bash
cd agents/coordinator

# Build Docker image
docker build -t coordinator-agent:latest .

# Tag for ECR
docker tag coordinator-agent:latest \
  123456789012.dkr.ecr.us-east-1.amazonaws.com/coordinator-agent:latest

# Push to ECR
docker push 123456789012.dkr.ecr.us-east-1.amazonaws.com/coordinator-agent:latest
```

## Deployment

The coordinator agent is deployed as an AgentCore Runtime agent:

1. **Build and push container** to Amazon ECR
2. **Create AgentCore Agent** with container image
3. **Configure JWT Authorizer** for authentication
4. **Set environment variables** for sub-agent IDs
5. **Deploy to AgentCore Gateway** endpoint

## Testing

Run unit tests:

```bash
pytest tests/test_coordinator.py -v
```

Test locally with mock sub-agents:

```bash
# Set environment variables
export PROFILE_AGENT_ID=test-profile
export ACCOUNTS_AGENT_ID=test-accounts

# Run main.py test function
python main.py
```

## Logging & Monitoring

The coordinator provides structured logging:

- **INFO**: Request routing, agent invocations, responses
- **DEBUG**: Detailed request/response data, tool calls
- **WARNING**: Authorization failures, missing configuration
- **ERROR**: Invocation errors, sub-agent failures

CloudWatch Logs structure:

```json
{
  "timestamp": "2026-01-07T10:30:00Z",
  "level": "INFO",
  "message": "Processing request through coordinator agent",
  "session_id": "session-123",
  "user_id": "auth0|user123",
  "customer_id": "CUST-001",
  "intent": "accounts"
}
```

## Security Considerations

1. **JWT Validation**: Always performed by AgentCore Gateway before reaching coordinator
2. **Customer ID Enforcement**: Coordinator validates customer_id from JWT
3. **Permission Checks**: Required permissions checked before tool execution
4. **Context Isolation**: User context passed to sub-agents, enforcing data access
5. **Secrets Management**: API keys stored in AWS Secrets Manager
6. **Audit Logging**: All requests logged to CloudWatch with user context

## Performance

Expected performance characteristics:

- **Cold Start**: ~3-5 seconds (container initialization)
- **Warm Request**: ~500-1000ms (without sub-agent calls)
- **Sub-Agent Call**: +500-2000ms per agent invocation
- **Max Concurrency**: 100 concurrent requests per agent instance
- **Memory**: 512MB-1024MB recommended

## Future Enhancements

1. **Intent Caching**: Cache intent classification for better performance
2. **Response Aggregation**: Combine responses from multiple sub-agents
3. **Advanced Routing**: ML-based intent classification
4. **Session Analytics**: Track conversation flows and routing patterns
5. **A/B Testing**: Route requests to different agent versions
6. **Rate Limiting**: Per-customer rate limits for API protection

## Support

For issues or questions:
- AWS Documentation: https://docs.aws.amazon.com/agentcore/
- Auth0 Documentation: https://auth0.com/docs
- Anthropic Documentation: https://docs.anthropic.com/

## License

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
