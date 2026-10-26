# Header and Query Parameter Propagation with AgentCore gateway

## Overview

AgentCore gateway supports forwarding custom HTTP headers and query parameters from client requests to targets, and response headers back to clients. This enables enterprise patterns like distributed tracing, multi-tenant isolation, API versioning, and rate limiting without requiring interceptor code.

![Architecture](images/allowlist.png)

### How it works

Header and query parameter propagation is configured per-target via `metadataConfiguration`:

```json
{
  "metadataConfiguration": {
    "allowedRequestHeaders": ["x-correlation-id", "x-tenant-id"],
    "allowedResponseHeaders": ["x-rate-limit-remaining"],
    "allowedQueryParameters": ["version", "environment"]
  }
}
```

The gateway:

1. Extracts only the allowlisted headers and query parameters from the client request
2. Forwards them to the target (Lambda targets receive them in `context.client_context.custom['bedrockAgentCorePropagatedHeaders']` and `context.client_context.custom['bedrockAgentCorePropagatedQueryParameters']`; MCP server targets receive them as HTTP headers)
3. Returns only the allowlisted response headers back to the client

![End-to-end flow](images/flow.png)

### Limits and restrictions

The following headers **cannot** be configured for propagation: `Authorization`, `Content-Type`, `Host`, `User-Agent`, `Cookie`, `Set-Cookie`, and all standard HTTP/security/CORS headers. See [full restricted list](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html).

> [!NOTE]
> The `Authorization` header cannot be allowlisted during target creation. However, it **is** forwarded to the target when provided by an interceptor Lambda. See the interceptor section below.

### Header propagation from interceptor Lambda

When using a REQUEST interceptor, headers returned in the interceptor's `transformedGatewayRequest.headers` are merged with the client's allowlisted headers:

- **Authorization override**: The interceptor can inject a different `Authorization` header (e.g., a refreshed token from a vault). This overrides the credential provider's token.
- **Header precedence**: Interceptor-provided headers take precedence over client-provided headers for the same name.
- **Security validation**: Except for `Authorization`, all interceptor headers must still be in the target's allowlist to be forwarded.

![Interceptor token passthrough](images/token-passthrough.png)

![Interceptor header override](images/header-override.png)

### Use cases

| Pattern | Headers/Params | Description |
| :--- | :--- | :--- |
| Distributed tracing | `x-correlation-id`, `x-request-id` | Track requests across microservices |
| Multi-tenancy | `x-tenant-id`, `x-org-id` | Route to correct tenant data |
| API versioning | `?version=v2` | Target specific API implementations |
| Environment routing | `?environment=staging` | Route to staging vs production |
| Rate limiting | Response: `x-rate-limit-remaining` | Communicate quota to clients |
| Feature flags | `x-feature-flags` | Enable/disable features per request |
## Tutorials

| Section | Description |
| :--- | :--- |
| [custom-header-query](custom-header-query/) | Allowlisted header/query propagation, interceptor precedence, non-allowlisted headers dropped |
| [token-passthrough](token-passthrough/) | Pass client Authorization token through to MCP and Lambda targets via interceptor, DEFAULT vs DYNAMIC listing |

## Documentation

- [Header Propagation with gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html)
- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [gateway Interceptors](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html)
