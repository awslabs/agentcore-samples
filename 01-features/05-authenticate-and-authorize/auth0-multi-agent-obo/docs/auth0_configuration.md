# Auth0 Configuration Guide

Complete guide for configuring Auth0 (Okta) as the identity provider for AgentCore Identity sample.

## Overview

This guide covers:
- Creating and configuring Auth0 tenant
- Setting up OAuth 2.0 application
- Configuring API resource server
- Adding custom claims with Actions
- Creating test users
- Testing authentication flow

## Prerequisites

- Auth0 account (free tier sufficient for development)
- Admin access to Auth0 Dashboard
- Understanding of OAuth 2.0 concepts

## Step 1: Create Auth0 Tenant

### 1.1 Sign Up for Auth0

1. Go to [auth0.com](https://auth0.com/)
2. Click "Sign Up"
3. Choose sign-up method:
   - Email/Password
   - Google
   - GitHub
   - Microsoft

### 1.2 Create Tenant

1. After sign-up, create a new tenant
2. Configuration:
   ```
   Tenant Name: agentcore-demo (or your preferred name)
   Region: Choose closest to us-east-1
         - Asia Pacific (Sydney) if available
         - Otherwise US/EU
   Environment Tag: Development
   ```

3. Note your tenant domain:
   ```
   your-tenant.auth0.com
   ```

## Step 2: Create OAuth 2.0 Application

### 2.1 Create Application

1. Navigate to **Applications** > **Applications**
2. Click **Create Application**
3. Configure:
   ```
   Name: AgentCore Financial App
   Application Type: Regular Web Applications
   Technology: Python
   ```
4. Click **Create**

### 2.2 Configure Application Settings

#### Basic Information

```
Name: AgentCore Financial App
Description: Sample financial services app with AgentCore
Application Type: Regular Web Application
Token Endpoint Authentication Method: Post
```

#### Application URIs

```
Application Login URI: http://localhost:8501
Allowed Callback URLs:
  http://localhost:9090/callback
  http://localhost:8501/callback

Allowed Logout URLs:
  http://localhost:8501
  http://localhost:8501/logout

Allowed Web Origins:
  http://localhost:8501

Allowed Origins (CORS):
  http://localhost:8501
```

#### Application Properties

```
Application Logo: (optional)
Application Type: Regular Web Application
Token Endpoint Authentication Method: Post
```

### 2.3 Configure Grant Types

Go to **Advanced Settings** > **Grant Types**

Enable:
- [x] Authorization Code
- [x] Refresh Token
- [x] Client Credentials (for M2M testing)

Disable:
- [ ] Implicit
- [ ] Password
- [ ] Device Code

### 2.4 Get Credentials

Copy these values for your `.env` file:

```
Domain: your-tenant.auth0.com
Client ID: abc123xyz (example)
Client Secret: def456uvw (example - keep secure!)
```

## Step 3: Create API Resource Server

### 3.1 Create API

1. Navigate to **Applications** > **APIs**
2. Click **Create API**
3. Configure:
   ```
   Name: AgentCore Financial API
   Identifier: https://agentcore-financial-api
   Signing Algorithm: RS256
   ```

### 3.2 Configure API Settings

```
Name: AgentCore Financial API
Identifier (Audience): https://agentcore-financial-api
Signing Algorithm: RS256
Allow Skipping User Consent: No
Allow Offline Access: Yes
Token Expiration: 86400 seconds (24 hours)
Token Expiration For Browser Flows: 7200 seconds (2 hours)
```

### 3.3 Define Scopes

Go to **Permissions** tab and add the following 13 fine-grained scopes. The coordinator uses RFC 8693 token exchange to issue attenuated tokens per sub-agent -- the profile agent receives only the 7 profile-related scopes, the accounts agent receives only the 7 account-related scopes. Legacy coarse scopes (customer:read, customer:write, accounts:read) have been fully deprecated.

**OIDC Standard Scopes:**

| Scope | Description |
|-------|-------------|
| `openid` | OpenID Connect authentication |
| `profile` | Access to user profile information |
| `email` | Access to user email address |

**Profile Agent Scopes:**

| Scope | Description |
|-------|-------------|
| `profile:personal:read` | Read personal profile data |
| `profile:personal:write` | Update personal profile data |
| `profile:preferences:read` | Read user preferences |
| `profile:preferences:write` | Update user preferences |

**Accounts Agent Scopes:**

| Scope | Description |
|-------|-------------|
| `accounts:savings:read` | View savings account information |
| `accounts:savings:write` | Perform savings account operations |
| `accounts:transaction:read` | View transaction history |
| `accounts:credit:read` | View credit account information |
| `accounts:credit:write` | Perform credit account operations |
| `accounts:investment:read` | View investment account information |

### 3.4 Enable RBAC

Go to **Settings** tab:

```
Enable RBAC: Yes
Add Permissions in the Access Token: Yes
```

## Step 4: Configure Custom Claims with Actions

### 4.1 Create Login Action

1. Navigate to **Actions** > **Library**
2. Click **Build Custom**
3. Configure:
   ```
   Name: Add Custom Claims
   Trigger: Login / Post Login
   Runtime: Node 18
   ```

### 4.2 Action Code

```javascript
/**
* Handler that will be called during the execution of a PostLogin flow.
*
* @param {Event} event - Details about the user and the context.
* @param {PostLoginAPI} api - Interface to change login behavior.
*/
exports.onExecutePostLogin = async (event, api) => {
  // Custom claims namespace (must use HTTPS URL format)
  const namespace = 'https://agentcore.example.com/';

  // Extract user metadata
  // In production, this would come from your database or external service
  const userId = event.user.user_id;
  const appMetadata = event.user.app_metadata || {};

  // Generate or retrieve customer ID
  const customerId = appMetadata.customer_id ||
                     generateCustomerId(userId);

  // Get account types (from database in production)
  const accountTypes = appMetadata.account_types ||
                     ['savings', 'checking'];

  // Get user roles
  const roles = event.authorization?.roles ||
                appMetadata.roles ||
                ['customer'];

  // Get KYC status
  const kycStatus = appMetadata.kyc_status || 'pending';

  // Set custom claims in ID token
  api.idToken.setCustomClaim(`${namespace}customer_id`, customerId);
  api.idToken.setCustomClaim(`${namespace}account_types`, accountTypes);
  api.idToken.setCustomClaim(`${namespace}roles`, roles);
  api.idToken.setCustomClaim(`${namespace}kyc_status`, kycStatus);

  // Set custom claims in access token
  api.accessToken.setCustomClaim(`${namespace}customer_id`, customerId);
  api.accessToken.setCustomClaim(`${namespace}account_types`, accountTypes);
  api.accessToken.setCustomClaim(`${namespace}roles`, roles);
  api.accessToken.setCustomClaim(`${namespace}kyc_status`, kycStatus);

  // Optional: Set user metadata if not already set
  if (!appMetadata.customer_id) {
    api.user.setAppMetadata('customer_id', customerId);
  }
};

/**
 * Helper function to generate customer ID from user ID
 */
function generateCustomerId(userId) {
  // Extract last 8 characters of user_id for demo
  // In production, use proper ID generation
  const suffix = userId.split('|')[1]?.substring(0, 8) ||
                 Math.random().toString(36).substring(2, 10);
  return `CUST-${suffix.toUpperCase()}`;
}
```

### 4.3 Deploy Action

1. Click **Deploy** (bottom right)
2. Action is now deployed but not active yet

### 4.4 Add Action to Flow

1. Navigate to **Actions** > **Flows** > **Login**
2. Find your flow diagram
3. Drag **Add Custom Claims** from the right panel
4. Place it between **Start** and **Complete**
5. Click **Apply**

Flow should look like:
```
Start -> Add Custom Claims -> Complete
```

## Step 5: Create Test Users

### 5.1 Create User via Dashboard

1. Navigate to **User Management** > **Users**
2. Click **Create User**
3. Configure:
   ```
   Email: john.doe@example.com
   Password: SecurePassword123!
   Connection: Username-Password-Authentication
   Email Verified: Yes
   ```

### 5.2 Set User Metadata

1. Click on the created user
2. Go to **Metadata** section
3. Add `app_metadata`:

```json
{
  "customer_id": "CUST-12345",
  "account_types": ["savings", "checking"],
  "roles": ["customer", "premium"],
  "kyc_status": "verified"
}
```

4. Click **Save**

### 5.3 Create Additional Test Users

**Unverified KYC User**
```json
{
  "email": "jane.smith@example.com",
  "password": "SecurePassword123!",
  "app_metadata": {
    "customer_id": "CUST-67890",
    "account_types": ["savings"],
    "roles": ["customer"],
    "kyc_status": "pending"
  }
}
```

**Premium User**
```json
{
  "email": "premium.user@example.com",
  "password": "SecurePassword123!",
  "app_metadata": {
    "customer_id": "CUST-99999",
    "account_types": ["savings", "checking", "credit"],
    "roles": ["customer", "premium"],
    "kyc_status": "verified"
  }
}
```

## Step 6: Configure Social Connections (Optional)

### 6.1 Enable Google Login

1. Navigate to **Authentication** > **Social**
2. Click **Google**
3. Configure:
   ```
   Client ID: (from Google Cloud Console)
   Client Secret: (from Google Cloud Console)
   Attributes: Email, Profile
   Permissions: email, profile
   ```

### 6.2 Enable Other Providers

Similar process for:
- GitHub
- Microsoft
- Apple
- LinkedIn

## Step 7: Configure Branding

### 7.1 Universal Login

1. Navigate to **Branding** > **Universal Login**
2. Configure:
   ```
   Logo: Upload company logo
   Primary Color: #0066CC
   Background Color: #F5F5F5
   ```

### 7.2 Customize Login Page

1. Click **Advanced Options**
2. Enable **Customize Login Page**
3. Modify HTML/CSS as needed

## Step 8: Test Authentication

### 8.1 Test via Auth0 Dashboard

1. Go to your Application
2. Click **Test**
3. Review connection settings
4. Click **Try Connection**

### 8.2 Test with cURL

**Get Access Token (Client Credentials)**
```bash
curl --request POST \
  --url https://your-tenant.auth0.com/oauth/token \
  --header 'content-type: application/json' \
  --data '{
    "client_id": "YOUR_CLIENT_ID",
    "client_secret": "YOUR_CLIENT_SECRET",
    "audience": "https://agentcore-financial-api",
    "grant_type": "client_credentials"
  }'
```

**Decode JWT**
```bash
# Extract payload
echo "JWT_TOKEN" | cut -d. -f2 | base64 -d | jq .
```

### 8.3 Test Authorization Flow

1. Start Streamlit app
2. Click "Login with Auth0"
3. Enter test user credentials
4. Grant permissions
5. Verify redirect back to app
6. Check that custom claims are present in token

### 8.4 Verify Custom Claims

Decode the JWT and verify:
```json
{
  "iss": "https://your-tenant.auth0.com/",
  "sub": "auth0|123456789",
  "aud": [
    "https://agentcore-financial-api",
    "https://your-tenant.auth0.com/userinfo"
  ],
  "https://agentcore.example.com/customer_id": "CUST-12345",
  "https://agentcore.example.com/account_types": ["savings", "checking"],
  "https://agentcore.example.com/roles": ["customer", "premium"],
  "https://agentcore.example.com/kyc_status": "verified",
  "scope": "openid profile email profile:personal:read profile:personal:write profile:preferences:read profile:preferences:write accounts:savings:read accounts:transaction:read"
}
```

## Step 9: Configure for Production

### 9.1 Update Callback URLs

```
Production URLs:
  https://your-domain.com/callback
  https://your-domain.com/logout
```

### 9.2 Enable MFA (Recommended)

1. Navigate to **Security** > **Multi-factor Auth**
2. Enable:
   - SMS
   - Push notifications
   - Time-based One-Time Password (TOTP)

### 9.3 Configure Attack Protection

1. Navigate to **Security** > **Attack Protection**
2. Enable:
   - Brute Force Protection
   - Suspicious IP Throttling
   - Breached Password Detection

### 9.4 Set Up Monitoring

1. Navigate to **Monitoring** > **Logs**
2. Enable:
   - Log Streaming to CloudWatch
   - Failed Login Alerts
   - Anomaly Detection

## Scope vs Permissions Claims

### Why Auth0 JWTs Contain Both Claims

Auth0 access tokens include two authorization-related claims that serve different purposes:

- **`scope`** (OAuth 2.0 standard) -- Represents what the **client application** was granted access to. Auth0 populates this with all API-defined scopes by default, regardless of the user's RBAC roles. Every user who authenticates through the same client application receives the same `scope` values.

- **`permissions`** (Auth0 RBAC) -- Represents what the **individual user** is allowed to do, based on their assigned roles. This claim is only present when "Add Permissions in the Access Token" is enabled in the API's RBAC settings.

### Why `permissions` Is the Correct Source of Truth

The `scope` claim is **not suitable for per-user authorization decisions** because it reflects client-level grants, not user-level restrictions. For example, a profile-only user and a full-access user will both have the same `scope` claim containing all API scopes.

The `permissions` claim is the correct source for authorization because:
1. It is restricted by the user's RBAC role assignments
2. Different users can have different permission sets
3. It reflects the principle of least privilege

The coordinator reads `permissions` as the primary authorization source and falls back to `scope` for non-Auth0 identity providers (e.g., Okta, Cognito) that may not include a `permissions` claim.

### How to Configure

1. Navigate to **Auth0 Dashboard** > **Applications** > **APIs** > **AgentCore Financial API**
2. Go to the **Settings** tab
3. Under **RBAC Settings**, enable:
   - **Enable RBAC**: Yes
   - **Add Permissions in the Access Token**: Yes
4. Click **Save**

Then assign permissions to roles:

1. Navigate to **User Management** > **Roles**
2. Create roles (e.g., "Full Access", "Profile Only")
3. Assign API permissions to each role
4. Assign roles to users

### Example JWT Claims

**Full-access user** (both scope and permissions contain all scopes):
```json
{
  "scope": "openid profile email profile:personal:read accounts:savings:read ...",
  "permissions": [
    "openid", "profile", "email",
    "profile:personal:read", "profile:personal:write",
    "accounts:savings:read", "accounts:savings:write",
    "accounts:transaction:read", "accounts:credit:read",
    "accounts:credit:write", "accounts:investment:read"
  ]
}
```

**Profile-only user** (scope has everything, permissions is restricted):
```json
{
  "scope": "openid profile email profile:personal:read accounts:savings:read ...",
  "permissions": [
    "openid", "profile", "email",
    "profile:personal:read", "profile:personal:write",
    "profile:preferences:read", "profile:preferences:write"
  ]
}
```

The coordinator uses the `permissions` array to determine which tools to expose, so the profile-only user will not see accounts tools despite `scope` containing accounts scopes.

### Fallback Behavior for Non-Auth0 IdPs

For identity providers that do not include a `permissions` claim (e.g., Okta, AWS Cognito), the coordinator falls back to reading the `scope` claim. This ensures compatibility across IdP vendors while preferring RBAC enforcement when available.

## Troubleshooting

### Issue: Redirect URI Mismatch

**Error**: `redirect_uri_mismatch`

**Solution**:
1. Verify callback URL in Auth0 Application settings
2. Ensure exact match with `.env` configuration
3. Check for trailing slashes

### Issue: Custom Claims Not Present

**Error**: Claims missing in JWT

**Solution**:
1. Verify Action is deployed
2. Check Action is added to Login flow
3. Ensure Action code is saving correctly
4. Check user's `app_metadata`

### Issue: Token Validation Fails

**Error**: `Invalid token signature`

**Solution**:
1. Verify JWKS URL is accessible
2. Check algorithm is RS256
3. Ensure issuer and audience match

### Issue: CORS Errors

**Error**: CORS policy blocks request

**Solution**:
1. Add origin to **Allowed Web Origins** in Auth0
2. Verify **Allowed Origins (CORS)** includes your domain
3. Ensure HTTPS in production

## Best Practices

1. **Use HTTPS**: Always use HTTPS in production
2. **Rotate Secrets**: Regularly rotate client secrets
3. **Monitor Logs**: Review Auth0 logs regularly
4. **Enable MFA**: Require MFA for sensitive operations
5. **Rate Limiting**: Enable attack protection
6. **Token Lifetime**: Use short-lived tokens (24h max)
7. **Refresh Tokens**: Implement refresh token rotation
8. **Namespace Claims**: Always use proper HTTPS namespace
9. **Test Thoroughly**: Test all authentication flows
10. **Document**: Keep configuration documented

## Security Checklist

- [ ] Client secrets stored securely (Secrets Manager)
- [ ] Callback URLs validated
- [ ] HTTPS enforced in production
- [ ] MFA enabled for production
- [ ] Attack protection enabled
- [ ] Token expiration configured appropriately
- [ ] RBAC enabled on API
- [ ] Custom claims using proper namespace
- [ ] Brute force protection enabled
- [ ] Monitoring and alerting configured

## Additional Resources

- [Auth0 Documentation](https://auth0.com/docs)
- [OAuth 2.0 Specification](https://oauth.net/2/)
- [JWT.io](https://jwt.io/) - JWT decoder
- [Auth0 Actions Documentation](https://auth0.com/docs/customize/actions)
- [Auth0 Community](https://community.auth0.com/)

## Next Steps

1. Review [Setup Guide](setup.md) for complete deployment
2. See [Architecture Documentation](architecture.md) for system design
3. Check [API Reference](api_reference.md) for endpoints
