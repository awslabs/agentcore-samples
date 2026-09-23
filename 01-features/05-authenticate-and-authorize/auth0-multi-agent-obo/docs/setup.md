# Detailed Setup Guide

Complete step-by-step instructions for setting up the AgentCore Identity sample project.

## Prerequisites

### Required Tools

1. **AWS Account**
   - Access to AWS AgentCore Runtime
   - IAM permissions to create resources
   - AWS CLI configured

2. **Auth0 Account**
   - Free or paid tier
   - Ability to create applications and APIs
   - Access to Auth0 dashboard

3. **Development Environment**
   - Python 3.9 or higher
   - Node.js 18+ (for CDK)
   - Git
   - Code editor (VS Code recommended)

### Required AWS Permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "agentcore:*",
        "dynamodb:*",
        "iam:*",
        "lambda:*",
        "logs:*",
        "secretsmanager:*"
      ],
      "Resource": "*"
    }
  ]
}
```

## Step 1: Clone Repository

```bash
git clone <repository-url>
cd agentcore_identity_1
```

## Step 2: Install Python Dependencies

### Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Required Packages

```
streamlit>=1.30.0
boto3>=1.34.0
pyjwt>=2.8.0
cryptography>=41.0.0
requests>=2.31.0
python-dotenv>=1.0.0
pydantic>=2.5.0
pytest>=7.4.0
pytest-cov>=4.1.0
pytest-mock>=3.12.0
```

## Step 3: Configure Auth0

### 3.1 Create Auth0 Tenant

1. Log in to [Auth0 Dashboard](https://manage.auth0.com/)
2. Create a new tenant (or use existing)
3. Note your tenant domain: `your-tenant.auth0.com`

### 3.2 Create Auth0 Application

1. Go to **Applications** > **Create Application**
2. Name: `AgentCore Financial App`
3. Type: **Regular Web Application**
4. Technology: **Python**

#### Configure Application Settings

```
Name: AgentCore Financial App
Domain: your-tenant.auth0.com
Client ID: <generated>
Client Secret: <generated>

Application URIs:
  Allowed Callback URLs: http://localhost:9090/callback
  Allowed Logout URLs: http://localhost:8501
  Allowed Web Origins: http://localhost:8501

Advanced Settings:
  Grant Types:
    [x] Authorization Code
    [x] Refresh Token

  Token Endpoint Authentication Method: Post
```

### 3.3 Create Auth0 API

1. Go to **Applications** > **APIs** > **Create API**
2. Configuration:

```
Name: AgentCore Financial API
Identifier: https://agentcore-financial-api
Signing Algorithm: RS256

Scopes:
  profile:personal:read - Read personal profile data
  profile:personal:write - Update personal profile data
  profile:preferences:read - Read preferences
  profile:preferences:write - Update preferences
  accounts:savings:read - Read savings accounts
  accounts:savings:write - Perform savings account operations
  accounts:transaction:read - Read transaction history
  accounts:credit:read - Read credit account information
  accounts:credit:write - Perform credit account operations
  accounts:investment:read - Read investment account information
```

### 3.4 Configure Custom Claims (Action)

1. Go to **Actions** > **Library** > **Build Custom**
2. Create Action: `Add Custom Claims`
3. Trigger: **Login / Post Login**

```javascript
/**
* Handler that will be called during the execution of a PostLogin flow.
*
* @param {Event} event - Details about the user and the context in which they are logging in.
* @param {PostLoginAPI} api - Interface whose methods can be used to change the behavior of the login.
*/
exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://agentcore.example.com/';

  // Get user metadata (would come from your database in production)
  const customerId = event.user.app_metadata?.customer_id || `CUST-${event.user.user_id.substr(-8)}`;
  const accountTypes = event.user.app_metadata?.account_types || ['savings', 'checking'];
  const roles = event.user.app_metadata?.roles || ['customer'];
  const kycStatus = event.user.app_metadata?.kyc_status || 'verified';

  // Set custom claims
  api.idToken.setCustomClaim(`${namespace}customer_id`, customerId);
  api.idToken.setCustomClaim(`${namespace}account_types`, accountTypes);
  api.idToken.setCustomClaim(`${namespace}roles`, roles);
  api.idToken.setCustomClaim(`${namespace}kyc_status`, kycStatus);

  api.accessToken.setCustomClaim(`${namespace}customer_id`, customerId);
  api.accessToken.setCustomClaim(`${namespace}account_types`, accountTypes);
  api.accessToken.setCustomClaim(`${namespace}roles`, roles);
  api.accessToken.setCustomClaim(`${namespace}kyc_status`, kycStatus);
};
```

4. Deploy the Action
5. Go to **Actions** > **Flows** > **Login**
6. Add the action to the flow

### 3.5 Create Test User

1. Go to **User Management** > **Users** > **Create User**
2. Configuration:

```
Email: john.doe@example.com
Password: SecurePassword123!
Connection: Username-Password-Authentication
```

3. Set app_metadata:

```json
{
  "customer_id": "CUST-12345",
  "account_types": ["savings", "checking"],
  "roles": ["customer", "premium"],
  "kyc_status": "verified"
}
```

## Step 4: Configure Environment Variables

### Create .env File

```bash
cp .env.example .env
```

### Edit .env File

```bash
# Auth0 Configuration
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_CLIENT_ID=your_client_id
AUTH0_CLIENT_SECRET=your_client_secret
AUTH0_AUDIENCE=https://agentcore-financial-api
AUTH0_CALLBACK_URL=http://localhost:9090/callback

# AWS Configuration
AWS_REGION=us-east-1  # Change to your preferred AWS region
AWS_PROFILE=default

# AgentCore Configuration
COORDINATOR_AGENT_ID=
PROFILE_AGENT_ID=
ACCOUNTS_AGENT_ID=
AGENTCORE_IDENTITY_POOL_ID=
AGENTCORE_JWT_AUTHORIZER_ID=
AGENTCORE_MEMORY_ID=
AGENTCORE_GATEWAY_URL=

# Application Configuration
DEBUG=false
LOG_LEVEL=INFO
STREAMLIT_PORT=8501
OAUTH_CALLBACK_PORT=9090
```

## Step 5: Deploy Infrastructure with CDK

### 5.1 Install CDK Dependencies

```bash
cd infrastructure/cdk
npm install
```

### 5.2 Bootstrap CDK (First Time Only)

```bash
cdk bootstrap aws://ACCOUNT-ID/${AWS_REGION}  # e.g. ap-southeast-2
```

### 5.3 Configure CDK

Edit `infrastructure/cdk/bin/agentcore-identity.ts`:

```typescript
const app = new cdk.App();

new AgentcoreIdentityStack(app, 'AgentcoreIdentityStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-1',  // Change to your preferred AWS region
  },
  auth0Domain: process.env.AUTH0_DOMAIN,
  auth0Audience: process.env.AUTH0_AUDIENCE,
});
```

### 5.4 Deploy Stacks

```bash
# Deploy all stacks
cdk deploy --all

# Or deploy individually
cdk deploy AgentcoreIdentityStack
cdk deploy AgentcoreAgentsStack
```

### 5.5 Update .env with Outputs

After deployment, CDK will output resource IDs. Update your `.env`:

```bash
COORDINATOR_AGENT_ID=<coordinator-agent-id>
PROFILE_AGENT_ID=<profile-agent-id>
ACCOUNTS_AGENT_ID=<accounts-agent-id>
AGENTCORE_IDENTITY_POOL_ID=<identity-pool-id>
AGENTCORE_JWT_AUTHORIZER_ID=<jwt-authorizer-id>
```

## Step 6: Test AgentCore Agents

> **Note:** The coordinator agent uses RFC 8693 token exchange to issue attenuated tokens to sub-agents. When testing, the original JWT is validated by AgentCore's platform-level authorizer, then the coordinator exchanges it for per-agent tokens with reduced scopes.

### 6.1 Test JWT Authorizer

```bash
# Get a test token from Auth0
curl --request POST \
  --url https://your-tenant.auth0.com/oauth/token \
  --header 'content-type: application/json' \
  --data '{
    "client_id": "your_client_id",
    "client_secret": "your_client_secret",
    "audience": "https://agentcore-financial-api",
    "grant_type": "client_credentials"
  }'
```

### 6.2 Test Coordinator Agent

```bash
# Invoke coordinator agent
aws agentcore invoke-agent \
  --agent-id $COORDINATOR_AGENT_ID \
  --region ${AWS_REGION} \
  --session-id test-session \
  --input-text "Show me my profile" \
  --authentication-token $JWT_TOKEN
```

## Step 7: Run Streamlit Application

### 7.1 Navigate to Client Directory

```bash
cd client/streamlit_app
```

### 7.2 Run Streamlit

```bash
streamlit run app.py
```

### 7.3 Access Application

Open browser to: `http://localhost:8501`

### 7.4 Test Authentication Flow

1. Click "Login with Auth0"
2. Enter test user credentials
3. Grant permissions
4. Redirect back to app
5. View authenticated dashboard

## Step 8: Run Tests

### Run All Tests

```bash
pytest tests/ -v
```

### Run with Coverage

```bash
pytest tests/ -v --cov=. --cov-report=html
open htmlcov/index.html
```

### Run Specific Test Suites

```bash
# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# E2E tests only
pytest tests/e2e/ -v
```

## Local Development Setup

### Using Multiple Terminals

**Terminal 1: Streamlit App**
```bash
cd client/streamlit_app
streamlit run app.py
```

**Terminal 2: Watch Logs**
```bash
aws logs tail /aws/agentcore/coordinator --follow
```

**Terminal 3: Run Tests**
```bash
pytest tests/ -v --watch
```

## Configuration for Different Environments

### Development Environment

```bash
# .env.development
DEBUG=true
LOG_LEVEL=DEBUG
STREAMLIT_PORT=8501
```

### Production Environment

```bash
# .env.production
DEBUG=false
LOG_LEVEL=INFO
AUTH0_CALLBACK_URL=https://your-domain.com/callback
STREAMLIT_PORT=80
```

### Load Environment-Specific Config

```bash
# Load development config
export ENV=development
python -m dotenv -f .env.development

# Load production config
export ENV=production
python -m dotenv -f .env.production
```

## Troubleshooting

### Common Issues

#### 1. Auth0 Callback Error

**Error**: `redirect_uri_mismatch`

**Solution**: Verify callback URL in Auth0 matches `.env`:
```bash
# In Auth0 Application Settings
Allowed Callback URLs: http://localhost:9090/callback

# In .env
AUTH0_CALLBACK_URL=http://localhost:9090/callback
```

#### 2. JWT Validation Fails

**Error**: `Invalid token signature`

**Solution**: Verify JWKS configuration:
```bash
# Test JWKS endpoint
curl https://your-tenant.auth0.com/.well-known/jwks.json
```

#### 3. Agent Not Found

**Error**: `Agent not found`

**Solution**: Verify agent IDs in `.env`:
```bash
aws agentcore list-agents --region ${AWS_REGION}  # e.g. ap-southeast-2
```

#### 4. Permission Denied

**Error**: `Access denied`

**Solution**: Check IAM permissions and token scopes:
```bash
# Decode JWT to check scopes
echo $JWT_TOKEN | cut -d. -f2 | base64 -d | jq .
```

### Debug Mode

Enable debug logging:

```python
# In your code
import logging
logging.basicConfig(level=logging.DEBUG)
```

Or set in `.env`:
```bash
DEBUG=true
LOG_LEVEL=DEBUG
```

## Next Steps

1. Review [Architecture Documentation](architecture.md)
2. Explore [API Reference](api_reference.md)
3. Read [Auth0 Configuration Details](auth0_configuration.md)
4. Customize agents for your use case
5. Deploy to production environment

## Additional Resources

- [AWS AgentCore Documentation](https://docs.aws.amazon.com/agentcore/)
- [Auth0 Documentation](https://auth0.com/docs)
- [Streamlit Documentation](https://docs.streamlit.io/)
- [Python JWT Library](https://pyjwt.readthedocs.io/)
