# API Reference

Complete API documentation for the AgentCore Identity sample project.

## Overview

This document describes the API endpoints, request/response formats, and authentication requirements for interacting with the AgentCore agents.

## Base URLs

```
Development: http://localhost:8501
Production: https://agentcore.us-east-1.amazonaws.com
```

## Authentication

All API requests require JWT authentication via Bearer token.

### Authorization Header

```http
Authorization: Bearer <jwt_token>
```

### Token Requirements

- **Algorithm**: RS256
- **Issuer**: `https://your-tenant.auth0.com/`
- **Audience**: `https://agentcore-financial-api`
- **Expiration**: Must not be expired
- **Custom Claims**: Must include customer_id and required scopes

## Coordinator Agent API

The coordinator agent routes requests to appropriate action agents.

### Invoke Coordinator

**Endpoint**: `POST /agents/coordinator/invoke`

**Description**: Send a natural language request to the coordinator agent.

**Request**:
```http
POST /agents/coordinator/invoke HTTP/1.1
Host: agentcore.us-east-1.amazonaws.com
Authorization: Bearer <jwt_token>
Content-Type: application/json

{
  "input": "Show me my profile",
  "sessionId": "session-123",
  "enableTrace": true
}
```

**Request Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `input` | string | Yes | Natural language request |
| `sessionId` | string | No | Session identifier for conversation continuity |
| `enableTrace` | boolean | No | Enable detailed tracing (default: false) |

**Response**:
```json
{
  "response": {
    "customer_id": "CUST-12345",
    "personal_info": {
      "first_name": "John",
      "last_name": "Doe",
      "email": "john.doe@example.com"
    }
  },
  "status": "success",
  "agent": "profile_agent",
  "sessionId": "session-123",
  "trace": {
    "traceId": "trace-abc-123",
    "latency": 245,
    "agentChain": ["coordinator", "profile_agent"]
  }
}
```

**Response Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `response` | object | Agent response data |
| `status` | string | Status: "success", "error", "awaiting_input" |
| `agent` | string | Agent that handled the request |
| `sessionId` | string | Session identifier |
| `trace` | object | Trace information (if enabled) |

**Error Response**:
```json
{
  "error": "unauthorized",
  "error_description": "Invalid or expired token",
  "status_code": 401
}
```

**Example Requests**:

```bash
# Get profile
curl -X POST https://agentcore.us-east-1.amazonaws.com/agents/coordinator/invoke \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Show me my profile"
  }'

# List accounts
curl -X POST https://agentcore.us-east-1.amazonaws.com/agents/coordinator/invoke \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "What are my account balances?",
    "sessionId": "session-456"
  }'

# Update profile
curl -X POST https://agentcore.us-east-1.amazonaws.com/agents/coordinator/invoke \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Update my email to newemail@example.com"
  }'
```

## Profile Agent API

Handles customer profile operations.

### Get Profile

**Endpoint**: `POST /agents/profile/get`

**Request**:
```http
POST /agents/profile/get HTTP/1.1
Authorization: Bearer <jwt_token>
Content-Type: application/json

{
  "customer_id": "CUST-12345"
}
```

**Response**:
```json
{
  "customer_id": "CUST-12345",
  "user_id": "auth0|123456789",
  "personal_info": {
    "first_name": "John",
    "last_name": "Doe",
    "date_of_birth": "1985-06-15",
    "email": "john.doe@example.com",
    "phone": "+61412345678"
  },
  "address": {
    "street": "123 Collins Street",
    "city": "Melbourne",
    "state": "VIC",
    "postcode": "3000",
    "country": "Australia"
  },
  "kyc_status": "verified",
  "kyc_verification_date": "2024-01-15T10:30:00Z",
  "account_types": ["savings", "checking"],
  "status": "active",
  "created_at": "2024-01-15T10:00:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**Required Scopes**: `profile:personal:read`

**Authorization**: Customer can only access their own profile

### Update Profile

**Endpoint**: `POST /agents/profile/update`

**Request**:
```http
POST /agents/profile/update HTTP/1.1
Authorization: Bearer <jwt_token>
Content-Type: application/json

{
  "customer_id": "CUST-12345",
  "updates": {
    "personal_info": {
      "email": "newemail@example.com",
      "phone": "+61412999888"
    }
  }
}
```

**Response**:
```json
{
  "customer_id": "CUST-12345",
  "personal_info": {
    "email": "newemail@example.com",
    "phone": "+61412999888"
  },
  "updated_at": "2024-01-20T10:30:00Z",
  "message": "Profile updated successfully"
}
```

**Required Scopes**: `profile:personal:write`

**Validation Rules**:
- Email: Valid email format
- Phone: Australian mobile format (+61...)
- Postcode: 4 digits
- Date of birth: Age between 18-120

## Accounts Agent API

Handles account information.

### List Accounts

**Endpoint**: `POST /agents/accounts/list`

**Request**:
```http
POST /agents/accounts/list HTTP/1.1
Authorization: Bearer <jwt_token>
Content-Type: application/json

{
  "customer_id": "CUST-12345"
}
```

**Response**:
```json
{
  "accounts": [
    {
      "account_id": "ACC-001",
      "customer_id": "CUST-12345",
      "account_type": "savings",
      "account_number": "123456789",
      "bsb": "083-001",
      "balance": 15000.50,
      "currency": "AUD",
      "status": "active",
      "created_at": "2024-01-15T10:00:00Z"
    },
    {
      "account_id": "ACC-002",
      "customer_id": "CUST-12345",
      "account_type": "checking",
      "account_number": "987654321",
      "bsb": "083-001",
      "balance": 5000.00,
      "currency": "AUD",
      "status": "active",
      "created_at": "2024-01-16T10:00:00Z"
    }
  ],
  "total_balance": 20000.50,
  "account_count": 2
}
```

**Required Scopes**: `accounts:savings:read`, `accounts:transaction:read`, `accounts:credit:read`, or `accounts:investment:read` (as appropriate for account type)

**Authorization**: Only returns accounts the user is authorized to access based on their scopes and account_types

### Get Account Details

**Endpoint**: `POST /agents/accounts/get`

**Request**:
```http
POST /agents/accounts/get HTTP/1.1
Authorization: Bearer <jwt_token>
Content-Type: application/json

{
  "account_id": "ACC-001"
}
```

**Response**:
```json
{
  "account_id": "ACC-001",
  "customer_id": "CUST-12345",
  "account_type": "savings",
  "account_number": "123456789",
  "bsb": "083-001",
  "balance": 15000.50,
  "available_balance": 14500.50,
  "currency": "AUD",
  "status": "active",
  "interest_rate": 2.5,
  "created_at": "2024-01-15T10:00:00Z",
  "last_transaction_date": "2024-01-20T14:30:00Z"
}
```

**Required Scopes**: `accounts:savings:read`, `accounts:transaction:read`, `accounts:credit:read`, or `accounts:investment:read` (as appropriate for account type)

**Authorization**: User must have appropriate account_type access in their JWT claims

## Error Codes

| Code | Status | Description |
|------|--------|-------------|
| `unauthorized` | 401 | Missing or invalid authentication token |
| `forbidden` | 403 | Authenticated but not authorized for this resource |
| `insufficient_scope` | 403 | Token missing required scope |
| `not_found` | 404 | Requested resource not found |
| `validation_error` | 400 | Request validation failed |
| `rate_limit_exceeded` | 429 | Too many requests |
| `service_unavailable` | 503 | Service temporarily unavailable |
| `internal_error` | 500 | Internal server error |

### Error Response Format

```json
{
  "error": "insufficient_scope",
  "error_description": "Required scope 'profile:personal:write' not present in token",
  "required_scopes": ["profile:personal:write"],
  "provided_scopes": ["openid", "profile", "profile:personal:read"],
  "status_code": 403
}
```

## Rate Limiting

API endpoints are rate limited per customer:

| Tier | Rate Limit | Burst |
|------|------------|-------|
| Standard | 100 requests/minute | 200 |
| Premium | 1000 requests/minute | 2000 |

Rate limit headers:
```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1640000000
```

## Pagination

List endpoints support pagination:

**Request**:
```json
{
  "limit": 10,
  "offset": 0
}
```

**Response**:
```json
{
  "items": [...],
  "total_count": 45,
  "has_more": true,
  "pagination": {
    "limit": 10,
    "offset": 0,
    "next_offset": 10
  }
}
```

## SDK Examples

### Python

```python
import requests

# Initialize with JWT token
token = "your_jwt_token"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# Get profile
response = requests.post(
    "https://agentcore.us-east-1.amazonaws.com/agents/coordinator/invoke",
    headers=headers,
    json={"input": "Show me my profile"}
)

profile = response.json()
print(f"Customer: {profile['response']['personal_info']['first_name']}")
```

### JavaScript/Node.js

```javascript
const axios = require('axios');

// Initialize with JWT token
const token = 'your_jwt_token';
const headers = {
  'Authorization': `Bearer ${token}`,
  'Content-Type': 'application/json'
};

// Get profile
async function getProfile() {
  const response = await axios.post(
    'https://agentcore.us-east-1.amazonaws.com/agents/coordinator/invoke',
    { input: 'Show me my profile' },
    { headers }
  );

  console.log('Customer:', response.data.response.personal_info.first_name);
}
```

### cURL

```bash
# Get profile
curl -X POST \
  https://agentcore.us-east-1.amazonaws.com/agents/coordinator/invoke \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input": "Show me my profile"}'

# List accounts
curl -X POST \
  https://agentcore.us-east-1.amazonaws.com/agents/accounts/list \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "CUST-12345"}'
```

## Webhooks (Future)

Webhook support for asynchronous notifications:

**Events**:
- `profile.updated`
- `account.created`
- `transaction.completed`
- `kyc.status_changed`

**Webhook Payload**:
```json
{
  "event_type": "profile.updated",
  "event_id": "evt_123456",
  "timestamp": "2024-01-20T10:30:00Z",
  "data": {
    "customer_id": "CUST-12345",
    "changes": {
      "email": "newemail@example.com"
    }
  }
}
```

## Changelog

### v1.1.0 (2026-02-11)
- RFC 8693 token exchange for agent-to-agent auth (scope attenuation)
- Fine-grained scopes (13 scopes replacing 3 legacy scopes)
- Scope-gated tools (accounts tool hidden if user lacks accounts scopes)
- Dual-issuer validation in sub-agents (Auth0 + token exchange service)

### v1.0.0 (2024-01-20)
- Initial API release
- Coordinator agent
- Profile agent
- Accounts agent
- JWT authentication
- Custom claims support

## Support

For API support:
- Documentation: [docs/README.md](README.md)
- GitHub Issues: Create an issue
- AWS Support: Contact AWS support for AgentCore issues

## License

See main project LICENSE file.
