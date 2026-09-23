# Auth0 Configuration for AgentCore Financial Services

This directory contains Auth0 configuration files and scripts for the AgentCore Financial Services identity and authentication system.

## Overview

The AgentCore platform uses Auth0 for:
- User authentication and authorization
- JWT token generation and validation (original user tokens)
- Custom claims for customer metadata
- Fine-grained scopes (13 scopes) for RFC 8693 token exchange / scope attenuation
- Secure API access with OAuth 2.0 scopes
- Multi-factor authentication (MFA) support

## Directory Structure

```
auth0/
├── config.json                    # Auth0 tenant and application configuration
├── rules/
│   └── add_custom_claims.js       # Legacy rule for adding custom claims
├── actions/
│   └── post_login_action.js       # Modern action for adding custom claims
└── README.md                      # This file
```

## Setup Instructions

### 1. Create Auth0 Account

1. Sign up for an Auth0 account at https://auth0.com
2. Create a new tenant for AgentCore Financial Services
3. Note your tenant domain (e.g., `your-tenant.us.auth0.com`)

### 2. Create Application

1. Navigate to **Applications > Applications** in the Auth0 Dashboard
2. Click **Create Application**
3. Name: `AgentCore Financial Services`
4. Type: **Single Page Web Applications**
5. Click **Create**

Configure the application:
- **Allowed Callback URLs**:
  ```
  http://localhost:8501/callback,
  http://localhost:3000/callback,
  https://app.agentcore.example.com/callback
  ```
- **Allowed Logout URLs**:
  ```
  http://localhost:8501,
  http://localhost:3000,
  https://app.agentcore.example.com
  ```
- **Allowed Web Origins**:
  ```
  http://localhost:8501,
  http://localhost:3000,
  https://app.agentcore.example.com
  ```
- **Allowed Origins (CORS)**:
  ```
  http://localhost:8501,
  http://localhost:3000,
  https://app.agentcore.example.com
  ```

Save the following for your application:
- **Domain** (e.g., `your-tenant.us.auth0.com`)
- **Client ID**
- **Client Secret** (if using confidential client)

### 3. Create M2M Application (Optional)

For service-to-service authentication:

1. Navigate to **Applications > Applications**
2. Click **Create Application**
3. Name: `AgentCore M2M Application`
4. Type: **Machine to Machine Applications**
5. Select the API to authorize: `AgentCore Financial API`
6. Select all required scopes

### 4. Create API

1. Navigate to **Applications > APIs**
2. Click **Create API**
3. Configure:
   - **Name**: `AgentCore Financial API`
   - **Identifier**: `https://api.agentcore.example.com`
   - **Signing Algorithm**: `RS256`

4. Add Permissions (Scopes):
   - `profile:personal:read` - Read personal profile data
   - `profile:personal:write` - Update personal profile data
   - `profile:preferences:read` - Read preferences
   - `profile:preferences:write` - Update preferences
   - `accounts:savings:read` - Read savings accounts
   - `accounts:savings:write` - Perform savings account operations
   - `accounts:transaction:read` - Read transaction history
   - `accounts:credit:read` - Read credit account information
   - `accounts:credit:write` - Perform credit account operations
   - `accounts:investment:read` - Read investment account information
   - `transactions:read` - Read transaction history
   - `cards:read` - Read card information and details
   - `agent:coordinate` - Coordinate multi-agent operations

5. Configure Token Settings:
   - **Token Expiration**: 86400 seconds (24 hours)
   - **Token Expiration For Browser Flows**: 7200 seconds (2 hours)
   - **Allow Offline Access**: Enabled

### 5. Configure Custom Actions (Recommended)

Actions are the modern replacement for Rules and offer better performance.

1. Navigate to **Actions > Library**
2. Click **Create Action**
3. Select **Build from Scratch**
4. Configure:
   - **Name**: `Add Custom Claims`
   - **Trigger**: `Login / Post Login`
   - **Runtime**: `Node 18 (Recommended)`

5. Copy the code from `actions/post_login_action.js`
6. Click **Deploy**

7. Navigate to **Actions > Flows > Login**
8. Click **Custom**
9. Drag the `Add Custom Claims` action to the flow
10. Click **Apply**

### 6. Configure Rules (Legacy Alternative)

If you prefer to use Rules (legacy approach):

1. Navigate to **Auth Pipeline > Rules**
2. Click **Create Rule**
3. Select **Empty Rule**
4. Name: `Add Custom Claims`
5. Copy the code from `rules/add_custom_claims.js`
6. Click **Save Changes**

**Note**: If you configure Actions, you don't need Rules. Actions are recommended.

### 7. Set Up User Metadata

When creating or updating users, add custom metadata:

#### User Metadata Structure

```json
{
  "user_metadata": {
    "customer_id": "CUST-12345",
    "account_types": ["checking", "savings", "credit"],
    "kyc_status": "verified",
    "security_level": "enhanced",
    "preferred_agent": "customer_profile"
  }
}
```

#### Via Auth0 Dashboard

1. Navigate to **User Management > Users**
2. Select a user
3. Click **User Metadata** section
4. Add the metadata fields as shown above
5. Click **Save**

#### Via Management API

```bash
curl -X PATCH 'https://YOUR_DOMAIN.auth0.com/api/v2/users/USER_ID' \
  -H 'Authorization: Bearer YOUR_MANAGEMENT_API_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "user_metadata": {
      "customer_id": "CUST-12345",
      "account_types": ["checking", "savings"],
      "kyc_status": "verified",
      "security_level": "standard",
      "preferred_agent": "coordinator"
    }
  }'
```

### 8. Configure Universal Login (Optional)

Customize the login experience:

1. Navigate to **Branding > Universal Login**
2. Select **New Universal Login Experience**
3. Customize:
   - Logo
   - Primary Color
   - Background Color
   - Company Name: `AgentCore Financial Services`

### 9. Enable MFA (Recommended)

1. Navigate to **Security > Multi-factor Auth**
2. Enable factors:
   - Push Notifications via Auth0 Guardian
   - SMS
   - Time-based One-Time Password (TOTP)
3. Configure MFA policies as needed

### 10. Test Configuration

1. Use the Auth0 Authentication API Debugger Extension
2. Or test with your application using the credentials
3. Verify that custom claims appear in the JWT token

## Environment Variables

Add these to your application's environment configuration:

```bash
# Auth0 Configuration
AUTH0_DOMAIN=your-tenant.us.auth0.com
AUTH0_CLIENT_ID=your_client_id
AUTH0_CLIENT_SECRET=your_client_secret  # For confidential clients only
AUTH0_AUDIENCE=https://api.agentcore.example.com
AUTH0_CALLBACK_URL=http://localhost:8501/callback

# Custom Claims Namespace
AUTH0_CLAIMS_NAMESPACE=https://agentcore.example.com/
```

## Custom Claims

The following custom claims are added to tokens:

| Claim | Description | Type | Default |
|-------|-------------|------|---------|
| `customer_id` | Unique customer identifier | string | user_id |
| `account_types` | Account types accessible by user | array | [] |
| `kyc_status` | KYC verification status | string | "pending" |
| `security_level` | Security clearance level | string | "basic" |
| `preferred_agent` | Preferred AI agent | string | "coordinator" |
| `roles` | User roles | array | [] |

All custom claims use the namespace: `https://agentcore.example.com/`

Example token payload:
```json
{
  "iss": "https://your-tenant.us.auth0.com/",
  "sub": "auth0|123456789",
  "aud": "https://api.agentcore.example.com",
  "exp": 1704672000,
  "iat": 1704585600,
  "scope": "profile:personal:read accounts:savings:read accounts:transaction:read",
  "https://agentcore.example.com/customer_id": "CUST-12345",
  "https://agentcore.example.com/account_types": ["checking", "savings"],
  "https://agentcore.example.com/kyc_status": "verified",
  "https://agentcore.example.com/security_level": "enhanced",
  "https://agentcore.example.com/preferred_agent": "customer_profile"
}
```

## Token Validation

To validate Auth0 JWT tokens in your application:

### Python Example

```python
from jose import jwt
import requests

# Get JWKS from Auth0
jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
jwks = requests.get(jwks_url).json()

# Decode and validate token
decoded = jwt.decode(
    token,
    jwks,
    algorithms=["RS256"],
    audience=AUTH0_AUDIENCE,
    issuer=f"https://{AUTH0_DOMAIN}/"
)

# Extract custom claims
customer_id = decoded.get("https://agentcore.example.com/customer_id")
kyc_status = decoded.get("https://agentcore.example.com/kyc_status")
```

## Security Best Practices

1. **Use HTTPS**: Always use HTTPS in production
2. **Rotate Secrets**: Regularly rotate client secrets
3. **Enable MFA**: Require MFA for sensitive operations
4. **Scope Minimization**: Request only necessary scopes
5. **Token Expiration**: Use short-lived tokens (15-60 minutes)
6. **Refresh Tokens**: Implement secure refresh token rotation
7. **Rate Limiting**: Enable rate limiting in Auth0
8. **Anomaly Detection**: Enable Auth0's anomaly detection features

## Troubleshooting

### Tokens don't contain custom claims

- Verify the Action/Rule is deployed and active
- Check the Action/Rule execution logs
- Ensure user metadata is properly set
- Verify the namespace is correct

### CORS errors

- Add your domain to Allowed Web Origins
- Ensure the domain includes the protocol (http/https)
- Check browser console for specific CORS errors

### Token validation fails

- Verify the audience matches your API identifier
- Check the issuer matches your Auth0 domain
- Ensure the token hasn't expired
- Validate the signing algorithm is RS256

## Additional Resources

- [Auth0 Documentation](https://auth0.com/docs)
- [Auth0 Actions Documentation](https://auth0.com/docs/customize/actions)
- [Auth0 Management API](https://auth0.com/docs/api/management/v2)
- [JWT.io - Token Debugger](https://jwt.io)

## Support

For issues or questions:
- Check Auth0 Dashboard logs
- Review Auth0 Community Forums
- Contact AgentCore support team
