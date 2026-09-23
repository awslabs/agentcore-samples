# Tenant Credentials Integration

## Overview

The MCP server now uses tenant-specific AWS credentials passed from the Gateway interceptor Lambda function. This provides secure, tenant-isolated access to Athena queries.

## Architecture Flow

```
User → Gateway → Interceptor Lambda → DynamoDB Lookup → Assume Tenant Role → MCP Server → Athena
```

1. **User authenticates** with Cognito JWT token
2. **Gateway** validates JWT and forwards to Interceptor Lambda
3. **Interceptor Lambda**:
   - Extracts user principal from JWT
   - Queries DynamoDB for user-to-role mapping
   - Assumes the tenant role using STS
   - Passes temporary credentials in request context
4. **MCP Server**:
   - Receives tenant credentials from context
   - Uses credentials to create Athena client
   - Executes queries with tenant-specific permissions

## Changes Made

### athena_tools_secure.py

**Updated `_get_athena_client()` method:**
- Now accepts `tenant_credentials` parameter
- Priority 1: Use tenant credentials from interceptor (if provided)
- Priority 2: Assume role with session tags (fallback)
- Priority 3: Use default credentials (local development)

**Updated `_execute_query()` method:**
- Accepts `tenant_credentials` parameter
- Passes credentials to `_get_athena_client()`

**Updated all query methods:**
- `query_claims()` - accepts and passes `tenant_credentials`
- `get_claim_details()` - accepts and passes `tenant_credentials`
- `get_claims_summary()` - accepts and passes `tenant_credentials`

### server.py

**Updated `get_user_id_with_fallback()` function:**
- Now returns tuple: `(user_id, tenant_credentials)`
- Extracts `tenant_credentials` from context
- Logs tenant role information

**Updated all tool functions:**
- Extract both `user_id` and `tenant_credentials` from context
- Pass `tenant_credentials` to athena_tools methods
- Log tenant credential usage

## Context Structure

The interceptor passes credentials in this format:

```python
{
    "user_id": "policyholder001@example.com",
    "scopes": ["openid", "email"],
    "tenant_credentials": {
        "access_key_id": "ASIA...",
        "secret_access_key": "...",
        "session_token": "...",
        "role_arn": "arn:aws:iam::ACCOUNT:role/lakehouse-tenant-1",
        "role_name": "lakehouse-tenant-1",
        "expiration": "2024-01-01T13:00:00"
    }
}
```

## Security Benefits

1. **Tenant Isolation**: Each user's queries run with their tenant role's permissions
2. **Temporary Credentials**: Credentials expire after 1 hour
3. **No Shared Credentials**: Each request uses unique, scoped credentials
4. **Audit Trail**: All Athena queries logged with tenant role identity
5. **Least Privilege**: Tenant roles have minimal required permissions

## Testing

### With Tenant Credentials

When tenant credentials are provided:
```
👤 USER ID: policyholder001@example.com
🔑 TENANT CREDENTIALS: Role lakehouse-tenant-1
```

### Without Tenant Credentials (Fallback)

When no credentials provided (local development):
```
👤 USER ID: policyholder001@example.com
⚠️  Using test user for local development
```

## Backward Compatibility

The implementation maintains backward compatibility:
- If `tenant_credentials` not in context, falls back to session tags
- If no RLS role configured, uses default credentials
- Local development mode still works without tenant credentials

## Deployment

No additional deployment steps required. The MCP server automatically uses tenant credentials when available in the request context.

## Monitoring

Check CloudWatch logs for:
- Tenant credential usage: `🔑 TENANT CREDENTIALS: Role lakehouse-tenant-X`
- Athena query execution with tenant role
- Any credential-related errors

## Troubleshooting

### Error: "AccessDenied" when querying Athena

**Cause**: Tenant role lacks Athena/S3 permissions

**Solution**: Verify tenant role has required permissions (run `setup_iam_roles.py`)

### Credentials not being used

**Cause**: Context not properly passed from interceptor

**Solution**: Check interceptor Lambda logs to verify credentials are being added to context
