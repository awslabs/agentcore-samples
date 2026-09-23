# OpenAPI Targets with Authorization Code Flow

Connect OpenAPI-defined APIs to AgentCore gateway using OAuth 2.0 authorization code grant for outbound authentication. The gateway handles user consent transparently via [URL-mode elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation#url-mode-flow) — users browse the tool catalog without authenticating, and the auth code flow is only triggered on tool invocation.

## How it works

### Creating the target (schema upfront)

Admin provides the OpenAPI schema directly during target creation. No OAuth flow is required at this stage — the gateway parses the spec and caches tool definitions immediately. The target becomes `READY` without any user interaction.

![create](./images/schema-upfront-diagram.png)

### Invoking a tool (triggers auth code flow)

When a gateway user calls a tool for the first time, the gateway initiates the OAuth authorization code flow via URL-mode elicitation. The user authorizes in their browser, session binding completes via a callback server, and the access token is cached. Subsequent calls reuse the cached token automatically.

![invoke](./images/invoke-tool-diagram.png)

## URL Session Binding

[URL Session Binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html) ensures that the user who initiated the OAuth authorization request is the same user who granted consent. When AgentCore identity generates an authorization URL, it also returns a session-URI. After the user completes consent, the browser redirects back to a callback URL with the session-URI. The application then is responsible for calling the [CompleteResourceTokenAuth](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CompleteResourceTokenAuth.html) API, presenting both the user's identity and the session-URI. AgentCore identity validates that the user who started the flow is the same user who completed it before exchanging the authorization code for an access token. This prevents a scenario where a user accidentally shares the authorization URL, and someone else completes the consent, which would grant access tokens to the wrong party. The authorization URL and session URI are only valid for 10 minutes, further limiting the window for misuse. Session binding applies during admin target creation (implicit sync) and during tool invocation.


### Security Considerations

- **Credential Handling**: OAuth client secrets are collected via `getpass` and stored only in memory during notebook execution. For production, store secrets in AWS Secrets Manager and retrieve them programmatically.
- **Least Privilege IAM**: The AWS Identity and Access Management (IAM) role created in this notebook follows least-privilege principles with scoped-down policies for specific AgentCore resources.
- **Token Expiry**: Access tokens and authorization URLs expire after a limited time (typically 1 hour for tokens, 10 minutes for authorization URLs). Expired tokens are automatically refreshed by AgentCore identity when refresh tokens are available.
- **Logging**: For production deployments, enable AWS CloudTrail to log all AgentCore API calls and configure Amazon CloudWatch for monitoring gateway invocations.
- **Shared Responsibility**: AWS manages the AgentCore gateway infrastructure and AgentCore identity service. Customers are responsible for securing their OAuth app credentials, configuring appropriate IAM policies, and implementing secure callback endpoints for session binding.


## Samples

| Sample | Provider | Description |
| :--- | :--- | :--- |
| [LinkedIn](linkedin/) | LinkedIn | User profile access via LinkedIn OpenID Connect |
