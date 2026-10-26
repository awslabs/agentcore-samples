# Gateway-Enforced Access

## Overview

An AgentCore Gateway adds value on the path to a target: it validates inbound tokens, runs interceptors, performs OBO token exchange, propagates allowlisted headers, and applies fine-grained access control. None of that runs when a caller skips the gateway and invokes the target directly.

That is a real gap whenever the target has its own inbound auth. A runtime with `CUSTOM_JWT` auth, for example, accepts any caller holding a valid token for its audience, regardless of whether the gateway was involved. A caller who knows the runtime invocation URL and holds a valid token can bypass every control the gateway enforces.

**Gateway-enforced access** closes that gap. The target is configured to accept invocations only when the request flowed through an approved gateway, so the gateway becomes the only usable entry point.

## How it works

Each invocation carries an identity chain that records the workloads it passed through. When the gateway invokes a target, that chain includes the gateway's workload identity. The target inspects the chain and accepts the request only when an approved workload is present.

For an AgentCore Runtime, this is expressed with `allowedWorkloadConfiguration` on the inbound `customJWTAuthorizer`:

```json
{
  "authorizerConfiguration": {
    "customJWTAuthorizer": {
      "discoveryUrl": "https://.../.well-known/openid-configuration",
      "allowedAudience": ["api://<runtime-client-id>"],
      "allowedWorkloadConfiguration": {
        "hostingEnvironments": [
          { "arn": "arn:aws:bedrock-agentcore:<region>:<account>:gateway/<gateway-id>" }
        ]
      }
    }
  }
}
```

- **hostingEnvironments** lists hosting environment ARNs whose workloads may invoke the target. The supported hosting environment is AgentCore Gateway, so this is the gateway ARN.
- **workloadIdentities** optionally lists specific workload identity names to allow.

After this is applied, a request through the approved gateway carries that gateway in its identity chain and is accepted. A direct call to the target carries no approved workload, so it is rejected even with an otherwise valid token. The token-based auth still applies; the workload restriction is an additional condition on top of it.

## Tutorials

| Section | Description |
| :--- | :--- |
| [agentcore-runtime](agentcore-runtime/) | Restrict an AgentCore Runtime to a specific gateway with `allowedWorkloadConfiguration`, then verify that direct invocation is blocked while the gateway path still works |

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Configure inbound JWT authorizer](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/inbound-jwt-authorizer.html)
- [HTTP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-http.html)
