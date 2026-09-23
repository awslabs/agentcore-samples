# AgentCore Identity - Streamlit Client Application

A Streamlit-based client application demonstrating 3-legged OAuth authentication with Auth0 and integration with AWS AgentCore Runtime.

## Features

- **Auth0 OAuth 2.0 with PKCE**: Secure authentication flow with Proof Key for Code Exchange
- **JWT-based Authorization**: Bearer token authentication with AgentCore Runtime
- **Interactive Chat Interface**: Natural language interaction with financial services agents
- **Session Management**: Persistent conversation history and token management
- **Token Refresh**: Automatic token refresh for extended sessions
- **Multi-Agent Orchestration**: Coordinator agent routes requests to specialized agents

## Architecture

```
┌─────────────┐      OAuth 2.0       ┌──────────┐
│  Streamlit  │ ◄─────────────────► │  Auth0   │
│    Client   │      (PKCE)          │          │
└─────────────┘                      └──────────┘
       │
       │ JWT Bearer Token
       │
       ▼
┌─────────────────────────────────────────────┐
│         AWS AgentCore Runtime               │
│                                             │
│  ┌──────────────┐                          │
│  │ Coordinator  │                          │
│  │    Agent     │                          │
│  └──────────────┘                          │
│         │                                   │
│    ┌────┴────┬────────┬────────┐          │
│    ▼         ▼        ▼        ▼          │
│  ┌────┐  ┌────┐  ┌────┐  ┌────┐          │
│  │Prof│  │Acct│  │Tran│  │Card│          │
│  │ile │  │s   │  │s   │  │s   │          │
│  └────┘  └────┘  └────┘  └────┘          │
└─────────────────────────────────────────────┘
```

## Project Structure

```
client/streamlit_app/
├── __init__.py                 # Package initialization
├── app.py                      # Main Streamlit application
├── auth0_handler.py           # Auth0 OAuth handler
├── oauth2_callback.py         # OAuth callback server
├── agentcore_client.py        # AgentCore Runtime client
├── session_manager.py         # Session state management
├── requirements.txt           # Python dependencies
├── README.md                  # This file
└── components/
    ├── __init__.py
    ├── login.py               # Login page component
    ├── chat.py                # Chat interface component
    └── sidebar.py             # Sidebar component
```

## Prerequisites

1. **Auth0 Application**: Create an Auth0 application with:
   - Application Type: Single Page Application
   - Allowed Callback URLs: `http://localhost:9090/callback`
   - Allowed Logout URLs: `http://localhost:8501`
   - Grant Types: Authorization Code, Refresh Token
   - Token Endpoint Authentication: None (PKCE)

2. **Auth0 API**: Create an Auth0 API with:
   - Identifier: `https://agentcore-financial-api` (or your custom audience)
   - Enable RBAC and Add Permissions in Access Token

3. **AWS Credentials**: Configure AWS credentials with access to:
   - AWS Bedrock Agent Runtime
   - AgentCore coordinator agent

4. **AgentCore Setup**: Deploy AgentCore agents with JWT authorizer

## Installation

1. Install dependencies:
```bash
cd client/streamlit_app
pip install -r requirements.txt
```

2. Configure environment variables (create `.env` file):
```bash
# Auth0 Configuration
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_CLIENT_ID=your-client-id
AUTH0_CLIENT_SECRET=your-client-secret
AUTH0_AUDIENCE=https://agentcore-financial-api
AUTH0_CALLBACK_URL=http://localhost:9090/callback

# AgentCore Configuration
AWS_REGION=us-east-1
COORDINATOR_AGENT_ID=your-coordinator-agent-id
AGENTCORE_MEMORY_ID=your-memory-id
AGENTCORE_IDENTITY_POOL_ID=your-identity-pool-id
AGENTCORE_JWT_AUTHORIZER_ID=your-jwt-authorizer-id

# Optional Configuration
STREAMLIT_PORT=8501
OAUTH_CALLBACK_PORT=9090
DEBUG=false
LOG_LEVEL=INFO
```

## Running the Application

### Quick Start

```bash
cd client/streamlit_app
python3 -m streamlit run app.py --server.port 8501
```

Then open http://localhost:8501 and log in with Auth0.

### Standard Launch

```bash
streamlit run app.py
```

The application will:
1. Start Streamlit on `http://localhost:8501`
2. Start OAuth callback server on `http://localhost:9090` when authentication begins
3. Open your default browser to the Streamlit app

### Custom Port

```bash
streamlit run app.py --server.port 8502
```

### Development Mode

```bash
export DEBUG=true
export LOG_LEVEL=DEBUG
streamlit run app.py
```

## Usage

### Authentication Flow

1. Click "Sign in with Auth0" button
2. Browser opens Auth0 login page
3. Enter credentials and authorize application
4. Automatically redirected back to application
5. Chat interface appears with user profile

### Chat Interface

**Sample Prompts:**

Profile Management:
- "Show me my customer profile"
- "What's my current address?"
- "Update my email to john.doe@example.com"
- "Change my phone number to +61 400 123 456"

Account Information:
- "What accounts do I have?"
- "Show my account balances"
- "What's my savings account balance?"

Transaction History:
- "Show my recent transactions"
- "What did I spend on groceries last month?"
- "Show transactions over $100"

Card Services:
- "What cards do I have?"
- "Show my credit card details"
- "What's my card limit?"

### Session Management

- **New Session**: Start a fresh conversation with new session ID
- **Clear Chat**: Clear message history while keeping session ID
- **Logout**: End session and clear all authentication state

## Components

### auth0_handler.py

Manages Auth0 OAuth 2.0 authentication with PKCE:
- Generates authorization URLs with code challenge
- Exchanges authorization codes for tokens
- Refreshes expired access tokens
- Retrieves user information
- Decodes JWT tokens

### oauth2_callback.py

FastAPI server for OAuth callback handling:
- Runs on separate port (default: 9090)
- Receives authorization code from Auth0
- Displays success/error pages
- Signals completion to Streamlit app

### agentcore_client.py

Client for AWS AgentCore Runtime:
- Invokes coordinator agent with JWT bearer token
- Handles streaming responses
- Manages session IDs for conversation continuity
- Provides error handling for authorization failures

### session_manager.py

Manages Streamlit session state:
- Stores and validates OAuth tokens
- Manages conversation history
- Handles PKCE parameters
- Provides token expiry checking

### Components

- **login.py**: Login page with Auth0 integration
- **chat.py**: Interactive chat interface with streaming responses
- **sidebar.py**: User profile, session info, and logout

## Security Considerations

1. **PKCE**: Uses PKCE for secure OAuth without client secret exposure
2. **Token Storage**: Tokens stored in Streamlit session state (memory only)
3. **HTTPS**: Production should use HTTPS for all endpoints
4. **Token Validation**: AgentCore validates JWT signature and claims
5. **Scope Enforcement**: Custom claims used for fine-grained authorization

## Troubleshooting

### "Configuration errors detected"

Ensure all required environment variables are set:
- AUTH0_DOMAIN
- AUTH0_CLIENT_ID
- AUTH0_CLIENT_SECRET

### "Authentication timeout"

- Check Auth0 application configuration
- Verify callback URL matches: `http://localhost:9090/callback`
- Ensure callback server port (9090) is not blocked

### "Access denied to invoke agent"

- Verify AWS credentials are configured
- Check AgentCore agent ID is correct
- Ensure JWT authorizer is properly configured
- Verify token has required audience and scopes

### "Token expired"

- Click "Refresh Token" in sidebar (if refresh token available)
- Otherwise, sign in again

### Port already in use

Change ports via environment variables:
```bash
export STREAMLIT_PORT=8502
export OAUTH_CALLBACK_PORT=9091
streamlit run app.py --server.port 8502
```

## Development

### Adding Custom Claims

1. Configure Auth0 Action to add custom claims:
```javascript
exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://agentcore.example.com/';
  api.idToken.setCustomClaim(`${namespace}customer_id`, event.user.user_metadata.customer_id);
  api.accessToken.setCustomClaim(`${namespace}customer_id`, event.user.user_metadata.customer_id);
};
```

2. Access claims in application via `user_info` or decoded ID token

### Extending Agent Capabilities

1. Add new agent IDs to `shared/config/settings.py`
2. Update coordinator agent to route to new agents
3. Add sample prompts to `components/chat.py`

## Resources

- [Auth0 Documentation](https://auth0.com/docs)
- [Streamlit Documentation](https://docs.streamlit.io)
- [AWS Bedrock Agent Runtime](https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html)
- [OAuth 2.0 PKCE](https://oauth.net/2/pkce/)

## License

This is a sample application for demonstration purposes.
