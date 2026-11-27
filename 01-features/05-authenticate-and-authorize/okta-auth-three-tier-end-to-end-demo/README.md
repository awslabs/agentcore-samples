# Okta three-tier authentication: choosing the right flow

The earlier deployment notebook combined three separate questions: who may invoke an agent, which workload may reach a downstream tool, and what the original user may do with the result. Establish each boundary independently. A valid workload token at an MCP server does **not** prove that an arbitrary end-user group attached to the request is genuine.

| Need | Start here | Difference from the old demo |
|---|---|---|
| Authenticate an agent acting across Okta applications | [Okta Cross App Access](../06-okta-xaa/) | Uses Okta's XAA workload model and a resource app; not an interceptor-based three-tier clone. |
| Obtain a user's consent for GitHub behind an AgentCore Gateway | [Managed consent dashboard](../07-consent-portal-auth-code-flow-targets/) | Keeps provider grants out of agent tool arguments and uses managed consent rather than deploying a local callback stack. |
| Compare a transport token with a user-delegated token in an MCP Runtime | [Entra OBO + MCP](../04-entra-obo-mcp-runtime/) | Uses a different issuer and Microsoft Graph; not Okta deployment instructions. |

**Authorization rule:** derive groups and the subject at a trusted boundary from verified claims for the intended issuer and audience. Do not authorize from browser-supplied group headers, arbitrary tool arguments or JSON-RPC `_meta`. Separate tool discovery credentials from authorization for actual user calls. Keep MFA and other tenant security controls in place.

The [historical notebook and diagrams](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/05-authenticate-and-authorize/okta-auth-three-tier-end-to-end-demo) generated the agent, MCP server, interceptor, build configuration and deployment. It demonstrated per-tier credentials, group-specific tool results and propagated request metadata. This page does not replace that live three-tier deployment or its RBAC demonstration. The notebook's caller-provided group metadata must not be treated as an authorization source.

**Existing deployments:** removing sample code neither revokes grants nor deletes resources. Reconcile the original AWS account, Region, Runtime IDs, Gateway and targets, interceptor, Identity provider, roles, ECR assets, and corresponding Okta sandbox applications/users. Revoke dedicated grants and rotate exposed credentials with their owners. Do not delete or relax controls on shared Okta or AWS resources based only on a matching name.
