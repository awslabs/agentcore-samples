# Architecture Documentation

## System Overview

The AgentCore Identity Streamlit client demonstrates a complete OAuth 2.0 authentication flow with Auth0, followed by secure communication with AWS AgentCore Runtime using JWT bearer tokens.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              End User                                        │
│                          (Web Browser)                                       │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │
                 │ (1) Access Application
                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Streamlit Application                                 │
│                          (localhost:8501)                                    │
│                                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ app.py       │  │ Components   │  │ Session      │  │ AgentCore    │   │
│  │ (Main App)   │  │ - login.py   │  │ Manager      │  │ Client       │   │
│  │              │  │ - chat.py    │  │              │  │              │   │
│  │              │  │ - sidebar.py │  │              │  │              │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
│         │                                     │                 │            │
└─────────┼─────────────────────────────────────┼─────────────────┼────────────┘
          │                                     │                 │
          │ (2) Initiate OAuth                 │                 │
          ▼                                     │                 │
┌─────────────────────────────────────────────────────────────────────────────┐
│                     OAuth2 Callback Server                                   │
│                       (localhost:9090)                                       │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  FastAPI Server                                                       │  │
│  │  - Starts when authentication begins                                 │  │
│  │  - Listens for Auth0 callback                                        │  │
│  │  - Displays success/error page                                       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │
                 │ (3) Authorization URL with PKCE
                 │     GET /authorize?
                 │       client_id=...
                 │       redirect_uri=http://localhost:9090/callback
                 │       code_challenge=...
                 │       code_challenge_method=S256
                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Auth0                                           │
│                       (your-tenant.auth0.com)                                │
│                                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Universal    │  │ User         │  │ Actions      │  │ JWT Signing  │   │
│  │ Login        │  │ Database     │  │ (Custom      │  │              │   │
│  │              │  │              │  │  Claims)     │  │              │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │
                 │ (4) User authenticates & authorizes
                 │
                 │ (5) Authorization code callback
                 │     GET /callback?code=...&state=...
                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     OAuth2 Callback Server                                   │
│                       (localhost:9090)                                       │
│                                                                              │
│  - Receives authorization code                                              │
│  - Signals Streamlit app                                                    │
│  - Displays success page                                                    │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │
                 │ (6) Exchange code for tokens
                 │     POST /oauth/token
                 │       code=...
                 │       code_verifier=...
                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Auth0                                           │
│                         Token Endpoint                                       │
│                                                                              │
│  - Validates authorization code                                             │
│  - Verifies PKCE code_verifier                                              │
│  - Issues tokens:                                                           │
│    - access_token (JWT)                                                     │
│    - id_token (JWT with user info)                                          │
│    - refresh_token (optional)                                               │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │
                 │ (7) Returns tokens
                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Streamlit Application                                 │
│                                                                              │
│  Session Manager stores:                                                    │
│  - access_token (for API calls)                                             │
│  - id_token (user profile)                                                  │
│  - refresh_token (token renewal)                                            │
│  - expiry time                                                              │
│                                                                              │
│  User is now authenticated!                                                 │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │
                 │ (8) User sends chat message
                 │
                 │ (9) Invoke agent with JWT
                 │     POST InvokeAgent
                 │       Authorization: Bearer <access_token>
                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       AWS Bedrock Agent Runtime                              │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  JWT Authorizer                                                       │  │
│  │  - Validates JWT signature                                           │  │
│  │  - Checks issuer & audience                                          │  │
│  │  - Verifies expiration                                               │  │
│  │  - Extracts custom claims                                            │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                               │
│                              │ (10) JWT validated, extract customer_id       │
│                              ▼                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Coordinator Agent                                                    │  │
│  │  - Receives user message                                             │  │
│  │  - Has access to identity context                                    │  │
│  │  - Scope-gates tools based on user permissions                       │  │
│  │  - Exchanges token (RFC 8693) with scope attenuation per agent       │  │
│  │  - Routes to specialized agents with attenuated tokens               │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                               │
│                   ┌──────────┼──────────┬──────────┐                        │
│                   ▼          ▼          ▼          ▼                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐                   │
│  │ Profile  │  │ Accounts │  │ Transac- │  │ Cards    │                   │
│  │ Agent    │  │ Agent    │  │ tions    │  │ Agent    │                   │
│  │          │  │          │  │ Agent    │  │          │                   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘                   │
│       │             │             │             │                            │
│       │ (11) Invoke tools with customer_id     │                            │
│       ▼             ▼             ▼             ▼                            │
│  ┌──────────────────────────────────────────────────────┐                  │
│  │            Agent Tools (Lambda Functions)             │                  │
│  │                                                        │                  │
│  │  - get_customer_profile(customer_id)                 │                  │
│  │  - update_customer_address(customer_id, ...)         │                  │
│  │  - get_accounts(customer_id)                         │                  │
│  │  - get_transactions(customer_id, account_id)         │                  │
│  │  - get_cards(customer_id)                            │                  │
│  │                                                        │                  │
│  │  Each tool validates customer_id from identity       │                  │
│  └──────────────────────────────────────────────────────┘                  │
│                              │                                               │
│                              │ (12) Returns results                          │
│                              ▼                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Coordinator Agent                                                    │  │
│  │  - Synthesizes response                                              │  │
│  │  - Streams back to client                                            │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │
                 │ (13) Streaming response chunks
                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Streamlit Application                                 │
│                                                                              │
│  AgentCore Client:                                                          │
│  - Receives streaming events                                                │
│  - Parses text chunks                                                       │
│  - Displays in real-time                                                    │
│                                                                              │
│  Session Manager:                                                           │
│  - Stores conversation history                                              │
│  - Maintains session ID                                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Details

### 1. Streamlit Application (app.py)

**Purpose**: Main application orchestrator

**Responsibilities**:
- Page configuration and layout
- Authentication state management
- Routing between login and chat views
- Integration of all components

**Key Files**:
- `app.py` - Entry point
- `components/login.py` - Login UI
- `components/chat.py` - Chat UI
- `components/sidebar.py` - User profile & controls

### 2. Auth0 Handler (auth0_handler.py)

**Purpose**: Auth0 OAuth 2.0 client with PKCE

**Key Methods**:
- `generate_auth_url()` - Creates authorization URL with PKCE challenge
- `exchange_code_for_tokens()` - Exchanges auth code for tokens
- `refresh_tokens()` - Refreshes expired access token
- `get_user_info()` - Fetches user profile from Auth0
- `decode_id_token()` - Decodes JWT claims

**Security Features**:
- PKCE (Proof Key for Code Exchange) for secure public client flow
- State parameter for CSRF protection
- Secure token storage in session state (memory only)

### 3. OAuth2 Callback Server (oauth2_callback.py)

**Purpose**: FastAPI server to handle OAuth callback

**Key Features**:
- Runs on separate port (default: 9090)
- Receives authorization code from Auth0
- Displays success/error page to user
- Signals completion to Streamlit app
- Automatic cleanup on logout

**Why Separate Server?**:
Streamlit apps can't directly handle OAuth callbacks because they don't expose HTTP endpoints. The callback server bridges this gap.

### 4. AgentCore Client (agentcore_client.py)

**Purpose**: Client for AWS Bedrock Agent Runtime

**Key Methods**:
- `invoke_coordinator_agent()` - Invokes agent with JWT token
- `parse_agent_response()` - Parses streaming response
- `invoke_with_streaming()` - Streams response to UI

**Authentication**:
- Passes JWT as Bearer token in session state
- Token validated by AgentCore JWT authorizer
- Custom claims extracted for authorization

### 5. Session Manager (session_manager.py)

**Purpose**: Manages Streamlit session state

**Manages**:
- OAuth tokens and expiry
- User profile information
- PKCE parameters during flow
- Conversation history
- AgentCore session ID

**Key Features**:
- Token expiry checking
- Automatic session cleanup
- Conversation history persistence
- Debug information

## Security Flow

### Authentication (Steps 1-7)

1. **User clicks login** - Streamlit app initiates OAuth flow
2. **PKCE generation** - Creates code_verifier and code_challenge
3. **Authorization request** - Redirects to Auth0 with challenge
4. **User authenticates** - Enters credentials on Auth0
5. **Authorization granted** - Auth0 redirects with code
6. **Callback received** - Callback server captures code
7. **Token exchange** - Exchanges code + verifier for tokens

### Authorization (Steps 8-13)

8. **User sends message** - Chat input submitted
9. **Agent invocation** - Calls Bedrock with JWT Bearer token
10. **JWT validation** - AgentCore validates token and extracts claims
11. **Tool invocation** - Agent tools receive customer_id from claims
12. **Authorization check** - Tools verify user can access resource
13. **Response streaming** - Results streamed back to client

## Data Flow

### Tokens

```
Auth0 -> Callback Server -> Session Manager -> AgentCore Client -> AgentCore Runtime
```

Tokens contain:
- `access_token`: JWT for API authentication
- `id_token`: JWT with user profile
- `refresh_token`: For token renewal
- Custom claims: `customer_id`, `permissions`, etc.

### Messages

```
User -> Chat Component -> AgentCore Client -> Coordinator Agent -> Specialized Agent -> Tool
                                                                                       |
User <- Chat Component <- AgentCore Client <- Coordinator Agent <- Specialized Agent <- Result
```

### Identity Context

```
JWT Claims -> AgentCore Authorizer -> Session Attributes -> Agent Tools
                                                             |
                                                      Authorization Decision
```

## Key Technologies

### Frontend
- **Streamlit**: Web framework for rapid Python app development
- **HTML/CSS**: Custom styling for login and chat components

### Authentication
- **Auth0**: Identity provider and OAuth 2.0 authorization server
- **PKCE**: Secure OAuth flow for public clients
- **JWT**: Token format for claims-based authorization

### Backend
- **FastAPI**: Async web framework for callback server
- **Uvicorn**: ASGI server for FastAPI
- **boto3**: AWS SDK for Python

### AWS Services
- **Bedrock Agent Runtime**: Agent invocation and orchestration
- **Lambda**: Agent tool execution
- **CloudWatch**: Logging and monitoring

## Configuration

### Environment Variables

**Required**:
- `AUTH0_DOMAIN` - Auth0 tenant domain
- `AUTH0_CLIENT_ID` - Application client ID
- `AUTH0_CLIENT_SECRET` - Application secret (for token exchange)
- `COORDINATOR_AGENT_ID` - AgentCore coordinator agent ID

**Optional**:
- `AUTH0_AUDIENCE` - API identifier
- `AGENTCORE_MEMORY_ID` - Conversation memory ID
- `STREAMLIT_PORT` - Application port (default: 8501)
- `OAUTH_CALLBACK_PORT` - Callback server port (default: 9090)

### Auth0 Configuration

**Application Settings**:
- Type: Single Page Application
- Grant Types: Authorization Code, Refresh Token
- Callback URLs: `http://localhost:9090/callback`
- CORS: Allowed Web Origins

**API Configuration**:
- Identifier: `https://agentcore-financial-api`
- Signing Algorithm: RS256
- RBAC: Enable
- Offline Access: Enable (for refresh tokens)

**Actions (Custom Claims)**:
```javascript
exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://agentcore.example.com/';
  api.idToken.setCustomClaim(`${namespace}customer_id`,
    event.user.user_metadata.customer_id);
  api.accessToken.setCustomClaim(`${namespace}customer_id`,
    event.user.user_metadata.customer_id);
};
```

### AgentCore Configuration

**JWT Authorizer**:
- Issuer: `https://your-tenant.auth0.com/`
- Audience: `https://agentcore-financial-api`
- JWKS URL: `https://your-tenant.auth0.com/.well-known/jwks.json`
- Claims mapping: Extract `customer_id` to session attributes

**Agent Tools**:
- Receive identity context via session attributes
- Validate customer_id on each invocation
- Enforce row-level security

## Error Handling

### Authentication Errors
- Invalid credentials -> Auth0 error page
- Timeout -> User prompted to retry
- State mismatch -> CSRF protection triggers

### Authorization Errors
- Token expired -> Refresh flow or re-login
- Invalid token -> Re-authentication required
- Permission denied -> Error message in chat

### Agent Errors
- Agent not found -> Configuration error displayed
- Tool failure -> Error message in conversation
- Network timeout -> Retry option provided

## Monitoring and Debugging

### Application Logs
- Streamlit console output
- Session information in sidebar
- Debug mode via `DEBUG=true`

### Auth0 Logs
- Authentication attempts
- Token issuance
- Action execution

### AWS CloudWatch
- Agent invocations
- Tool executions
- Error traces

## Extension Points

### Adding New Agents
1. Deploy new specialized agent
2. Update coordinator agent instructions
3. Add agent ID to configuration
4. Update sample prompts

### Custom Claims
1. Add Auth0 Action for new claims
2. Update JWT authorizer claim mapping
3. Access in agent tools via session attributes

### UI Customization
1. Modify component styles in CSS
2. Add new tabs or sections to main app
3. Customize login page branding
4. Add additional user profile fields

## Production Considerations

### Security
- Use HTTPS for all endpoints
- Rotate client secrets regularly
- Implement rate limiting
- Add request logging
- Use secure session storage

### Scalability
- Deploy behind load balancer
- Use Redis for session state
- Implement connection pooling
- Add caching layer
- Monitor token refresh patterns

### Reliability
- Add health checks
- Implement retry logic
- Monitor token expiry
- Log all authentication events
- Set up alerting for failures
