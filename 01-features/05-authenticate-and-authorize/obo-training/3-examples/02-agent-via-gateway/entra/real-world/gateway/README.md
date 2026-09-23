# Gateway target — OpenAPI spec for Microsoft Graph

This folder holds the OpenAPI 3 document that AgentCore Gateway converts into an MCP tool surface.

## What's here

- `graph_openapi.json` — minimal spec with one operation: `getMyProfile` (`GET /v1.0/me`).

## Why so minimal

The point of UC2 is to show the OBO mechanics, not to give the agent a comprehensive Graph integration. One operation is enough to demonstrate that:

1. The agent can list tools via MCP.
2. It can call the tool with no token-handling logic.
3. The Gateway runs OBO #2 transparently and forwards the request to Graph.
4. Graph returns the profile of the *user*, proving identity propagation.

Adding more operations (`/me/messages`, `/me/calendar/events`, etc.) is a copy-paste exercise from Microsoft's spec — we keep it scoped here.

## How it gets attached to a Gateway target

`deploy/02_create_gateway.py` reads this file and inlines it into the `targetConfiguration` of the `CreateGatewayTarget` call:

```python
target_response = ac_control.create_gateway_target(
    gatewayIdentifier=gateway_id,
    name="microsoft-graph-obo",
    targetConfiguration={
        "mcp": {
            "openApiSchema": {
                "inlinePayload": json.dumps(spec)
            }
        }
    },
    credentialProviderConfigurations=[{
        "credentialProviderType": "OAUTH",
        "credentialProvider": {
            "oauthCredentialProvider": {
                "providerArn": gateway_provider_arn,
                "scopes": ["https://graph.microsoft.com/.default"],
                "grantType": "TOKEN_EXCHANGE",
                "customParameters": {
                    "requested_token_use": "on_behalf_of"
                }
            }
        }
    }]
)
```

Two notes:

- The Gateway target's `grantType: "TOKEN_EXCHANGE"` is **distinct** from the credential provider's grant type. The credential provider is configured with `JWT_AUTHORIZATION_GRANT` (Entra's RFC 7523 flavor); the Gateway target says "use this provider for an OBO exchange." The Gateway's enum here is unfortunately ambiguous — it really means "outbound = OBO," not "RFC 8693 token exchange."
- `requested_token_use: "on_behalf_of"` is required by Entra. Without it the exchange silently fails with no useful error message.
