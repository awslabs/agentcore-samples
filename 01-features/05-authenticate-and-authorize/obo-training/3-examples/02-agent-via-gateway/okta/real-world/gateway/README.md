# Gateway target — OpenAPI spec for the mock downstream API

This folder holds the OpenAPI 3 document that AgentCore Gateway converts into an MCP tool surface.

## What's here

- `downstream_openapi.json` — minimal spec with one operation: `callDownstreamApi` (`GET /anything`) pointed at `https://httpbin.org`.

## Why httpbin.org

The learning goal of Use Case 2 is to show **two OBO hops chained** — the agent does OBO #1, the Gateway does OBO #2, both against the same IdP. What sits behind the Gateway is deliberately kept trivial so nothing distracts from the OBO mechanics.

httpbin.org/anything is a public HTTP echo service. When the Gateway performs OBO #2 and calls it with the exchanged token, httpbin echoes the entire request back as JSON — including the `Authorization: Bearer <T_downstream>` header. That gives learners two things at once:

1. **Proof the Gateway did OBO #2.** The echoed Bearer token is a fresh JWT you can decode at jwt.io and compare against the T_gateway that the agent minted.
2. **A visible trail of what the downstream saw.** Real production APIs validate the token's `iss`, `aud`, and `scp` before responding; httpbin just accepts anything and reflects it. `ARCHITECTURE.md` calls this out — swap in your own API when you're building the real thing.

The equivalent UC2 Entra example points at Microsoft Graph `/me`, which is Microsoft's own resource server. The UC2 Okta equivalent would be Okta's `/v1/userinfo` — except Okta refuses OIDC scopes (`openid`, `profile`, `email`) on Token Exchange (reason: `openid_not_allowed_token_exchange`), and userinfo requires `openid`. Custom-scope OBO tokens against Okta's own endpoints don't work; you can only OBO to your own API. httpbin.org sidesteps that gracefully and keeps the example self-contained.

## How it gets attached to a Gateway target

`deploy/02_create_gateway.py` reads this file and inlines it into the `targetConfiguration` of the `CreateGatewayTarget` call:

```python
target_response = ac_control.create_gateway_target(
    gatewayIdentifier=gateway_id,
    name="downstream-echo-obo",
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
                "scopes": [DOWNSTREAM_SCOPE],           # "downstream.access"
                "grantType": "TOKEN_EXCHANGE",
                "customParameters": {
                    # RFC 8693 requires the caller to declare what kind of
                    # token is being exchanged; Okta rejects the request
                    # without this.
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token"
                }
            }
        }
    }]
)
```

Three notes on the Okta variant of the OBO config:

- **`grantType: "TOKEN_EXCHANGE"`** on the target is a Gateway-level enum meaning "outbound = OBO." It's distinct from the credential provider's own `onBehalfOfTokenExchangeConfig.grantType`, which in Okta's case is also `TOKEN_EXCHANGE` (Okta's OBO grant is RFC 8693 Token Exchange — different wire protocol from Entra's RFC 7523 JWT-bearer). Same enum name, two different layers of config, both required.
- **`subject_token_type`** on `customParameters` tells Okta what kind of token is being exchanged. Without this, Okta returns HTTP 400 with `invalid_request`. AgentCore Identity does NOT auto-add it for CustomOauth2 providers, so it must be set here (and again in the agent code's `GetResourceOauth2Token` call for OBO #1).
- **`scopes: [DOWNSTREAM_SCOPE]`** requests the custom scope on the Gateway's target. The Access Policy on Okta's default authorization server permits `GatewayApp` to receive `downstream.access` via the Token Exchange grant — see `IDP_SETUP.md` Step 3d.

## Not shown here (but part of the exchange)

The Gateway target also caches the allowed audience for inbound tokens (see `deploy/02_create_gateway.py`'s Gateway-level `authorizerConfiguration`). Both the Runtime and the Gateway validate `aud = api://default` on inbound tokens — that's Okta's default authorization server audience. Actor identity across the two hops is tracked via the `cid` claim (frontend -> agent -> gateway), not via `aud` which stays constant. See `ARCHITECTURE.md` for the full claim walk.
