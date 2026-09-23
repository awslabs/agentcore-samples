# AgentCore Identity Sample Project

A comprehensive reference implementation demonstrating AWS AgentCore Runtime integration with Auth0 (Okta) identity provider using 3-legged OAuth 2.0 for financial services applications.

> **MVP Complete (2026-02-11):** 3 agents deployed to `us-east-1`, Auth0 PKCE + RFC 8693 token exchange with scope attenuation verified, distributed tracing working in CloudWatch GenAI Observability.

## Overview

This project showcases a multi-agent architecture for a financial services application with secure authentication and authorization. It demonstrates how to:

- Integrate Auth0 (Okta) with AWS AgentCore Runtime
- Implement 3-legged OAuth 2.0 authorization code flow
- Manage JWT tokens and custom claims
- Route requests between coordinator and action agents
- Enforce fine-grained authorization at the agent level
- Build an interactive Streamlit web application

## Architecture

```
┌─────────────┐
│   Browser   │
│  (Streamlit)│
└──────┬──────┘
       │ 1. User Login
       ▼
┌─────────────────┐
│     Auth0       │ ◄──── 2. OAuth Flow
│   (Okta IdP)    │
└────────┬────────┘
         │ 3. JWT Token
         ▼
┌──────────────────────────────┐
│     AgentCore Runtime        │
│  ┌────────────────────────┐  │
│  │  JWT Authorizer        │  │ ◄──── 4. Token Validation
│  │  (Identity Pool)       │  │
│  └────────────────────────┘  │
│                              │
│  ┌────────────────────────┐  │
│  │  Coordinator Agent     │  │ ◄──── 5. Request Routing
│  └────────┬───────────────┘  │
│           │                  │
│  ┌────────┴─────────┐        │
│  │  Action Agents   │        │ ◄──── 6. Execute Operations
│  ├──────────────────┤        │
│  │ - Profile        │        │
│  │ - Accounts       │        │
│  │ - Transactions   │        │
│  │ - Cards          │        │
│  └──────────────────┘        │
└──────────────────────────────┘
         │
         ▼
┌──────────────────┐
│    DynamoDB      │ ◄──── 7. Data Storage
└──────────────────┘
```

## Components

### 1. Identity Provider (Auth0/Okta)
- **Tenant**: Manages user authentication
- **Application**: OAuth 2.0 client configuration
- **API**: Resource server configuration
- **Custom Claims**: Customer ID, account IDs, roles, KYC status
- **Region**: Deployed in Auth0 cloud (global)

### 2. AgentCore Runtime
- **Identity Pool**: JWT token validation
- **JWT Authorizer**: Validates Auth0 tokens
- **Coordinator Agent**: Routes user requests to appropriate action agents
- **Action Agents**: Execute specific business operations
- **Memory Service**: Maintains conversation context
- **Region**: `us-east-1` (Sydney)

### 3. Action Agents

#### Profile Agent
- Get customer profile
- Update profile information
- Manage KYC status

#### Accounts Agent
- List customer accounts
- Get account details
- Check balances

### 4. Client Application
- **Streamlit App**: Interactive web interface
- **OAuth Client**: Handles Auth0 authentication flow
- **Session Management**: Stores JWT tokens securely
- **Port**: 8501 (default Streamlit port)

## Authentication Flow

```
1. User clicks "Login" in Streamlit app
   |
2. App redirects to Auth0 authorization endpoint
   |
3. User authenticates with Auth0
   |
4. Auth0 redirects back with authorization code
   |
5. App exchanges code for JWT tokens (access + ID)
   |
6. App extracts custom claims from JWT
   |
7. App stores tokens in session state
   |
8. User makes authenticated requests to coordinator agent
   |
9. AgentCore validates JWT with configured authorizer
   |
10. Coordinator routes to appropriate action agent
    |
11. Action agent validates authorization and executes
```

## Token Flow (RFC 8693 Token Exchange)

```
┌──────────┐                  ┌──────────┐
│  Client  │─── JWT Token ───->│Coordinator│
└──────────┘  (13 scopes)     └─────┬────┘
                                    │
                                    │ RFC 8693 Token Exchange
                                    │ (scope attenuation per agent)
                                    │
                    ┌───────────────┴───────────────┐
                    |                               |
              ┌─────────┐                    ┌─────────┐
              │ Profile │                    │Accounts │
              │  Agent  │                    │  Agent  │
              │(7 scopes│                    │(7 scopes│
              │ profile │                    │ accts   │
              │ only)   │                    │ only)   │
              └─────────┘                    └─────────┘
                    │                               │
                    └─ Dual-issuer validation ──────┘
```

Each agent validates the exchanged token (dual-issuer: Auth0 or exchange service) to ensure:
- Token signature is valid (RSA-signed by exchange service or Auth0)
- Token has not expired (5-minute lifetime for exchanged tokens)
- Scopes are appropriate for the agent's domain
- Customer ID matches the requested resource
- `act` claim present for delegation chain audit

## Custom Claims

JWT tokens include custom claims in the `https://agentcore.example.com/` namespace:

```json
{
  "sub": "auth0|123456789",
  "https://agentcore.example.com/customer_id": "CUST-12345",
  "https://agentcore.example.com/account_ids": ["ACC-001", "ACC-002"],
  "https://agentcore.example.com/roles": ["customer", "premium"],
  "https://agentcore.example.com/kyc_status": "verified",
  "scope": "openid profile email profile:personal:read profile:personal:write profile:preferences:read profile:preferences:write accounts:savings:read accounts:savings:write accounts:transaction:read accounts:credit:read accounts:credit:write accounts:investment:read"
}
```

## Authorization Model

### Scopes
- `openid`: Basic authentication
- `profile`: Access to user profile
- `email`: Access to email address
- `profile:personal:read`: Read personal profile data
- `profile:personal:write`: Update personal profile data
- `profile:preferences:read`: Read preferences
- `profile:preferences:write`: Update preferences
- `accounts:savings:read`: Read savings accounts
- `accounts:savings:write`: Perform savings account operations
- `accounts:transaction:read`: Read transaction history
- `accounts:credit:read`: Read credit account information
- `accounts:credit:write`: Perform credit account operations
- `accounts:investment:read`: Read investment account information

### Resource Access
- Customers can only access their own resources
- Customer ID in token must match requested resource
- Account IDs in token determine accessible accounts

### Role-Based Access
- `customer`: Basic customer access
- `premium`: Premium features and higher limits
- `admin`: Full administrative access (not in this sample)

## Prerequisites

- AWS Account with AgentCore access
- Auth0 account (or Okta account)
- Python 3.9+
- AWS CLI configured
- Node.js (for CDK deployment)

## Quick Start

### 1. Clone Repository

```bash
git clone <repository-url>
cd agentcore_identity_1
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Auth0

Follow the detailed setup in [auth0_configuration.md](auth0_configuration.md)

### 4. Set Environment Variables

```bash
cp .env.example .env
# Edit .env with your Auth0 and AgentCore configuration
```

### 5. Deploy Infrastructure

```bash
cd infrastructure/cdk
npm install
cdk deploy --all
```

### 6. Run Streamlit App

```bash
cd client/streamlit_app
streamlit run app.py
```

### 7. Test the Application

1. Open browser to `http://localhost:8501`
2. Click "Login with Auth0"
3. Authenticate with your credentials
4. Interact with the coordinator agent

## Project Structure

```
agentcore_identity_1/
├── agents/                      # Agent implementations
│   ├── coordinator/            # Coordinator agent
│   │   └── tools/             # Coordinator tools
│   ├── customer_profile/      # Profile agent
│   │   └── tools/             # Profile tools
│   └── accounts/              # Accounts agent
├── client/                     # Client applications
│   └── streamlit_app/         # Streamlit web app
├── shared/                     # Shared modules
│   ├── auth/                  # Authentication utilities
│   ├── models/                # Data models
│   └── config/                # Configuration
├── infrastructure/             # Infrastructure as Code
│   ├── cdk/                   # AWS CDK
│   └── auth0/                 # Auth0 configuration
│       ├── actions/           # Auth0 Actions
│       └── rules/             # Auth0 Rules (legacy)
├── tests/                      # Test suite
│   ├── unit/                  # Unit tests
│   ├── integration/           # Integration tests
│   └── e2e/                   # End-to-end tests
├── docs/                       # Documentation
│   ├── README.md              # This file
│   ├── setup.md               # Setup guide
│   ├── architecture.md        # Architecture details
│   ├── auth0_configuration.md # Auth0 setup
│   └── api_reference.md       # API documentation
├── .env.example               # Environment variables template
├── .gitignore                 # Git ignore rules
├── requirements.txt           # Python dependencies
├── pyproject.toml            # Python project config
└── README.md                  # Project README
```

## Testing

### Run Unit Tests

```bash
pytest tests/unit/ -v
```

### Run Integration Tests

```bash
pytest tests/integration/ -v
```

### Run End-to-End Tests

```bash
pytest tests/e2e/ -v
```

### Run All Tests

```bash
pytest tests/ -v --cov=. --cov-report=html
```

## Security Considerations

1. **Token Storage**: Tokens are stored in Streamlit session state (server memory only)
2. **Token Validation**: Every agent validates tokens independently
3. **HTTPS**: Always use HTTPS in production
4. **Secrets Management**: Use AWS Secrets Manager for sensitive configuration
5. **Token Expiry**: Implement token refresh logic
6. **PKCE**: Use PKCE for enhanced OAuth security
7. **State Parameter**: Validate state parameter to prevent CSRF

## Troubleshooting

### Token Validation Fails
- Verify JWT authorizer configuration
- Check Auth0 issuer and audience match
- Ensure JWKS URL is accessible

### Agent Not Responding
- Check AgentCore agent deployment status
- Verify IAM permissions
- Check CloudWatch logs

### Authentication Fails
- Verify Auth0 application configuration
- Check redirect URI matches
- Validate client credentials

## Further Reading

- [Detailed Setup Guide](setup.md)
- [Architecture Documentation](architecture.md)
- [Auth0 Configuration](auth0_configuration.md)
- [API Reference](api_reference.md)
- [AWS AgentCore Documentation](https://docs.aws.amazon.com/agentcore/)
- [Auth0 Documentation](https://auth0.com/docs)

## License

This project is provided as a sample implementation for demonstration purposes.

## Support

For issues and questions:
- Open an issue in the repository
- Consult AWS AgentCore documentation
- Review Auth0 documentation
- Check CloudWatch logs for debugging

## Contributing

This is a reference implementation. Feel free to fork and adapt for your needs.
