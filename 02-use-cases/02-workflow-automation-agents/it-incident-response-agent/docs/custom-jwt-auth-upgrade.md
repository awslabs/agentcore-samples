# Optional Gateway JWT M2M — fresh setup

This historical filename does not imply an existing-stack upgrade. Configure
JWT **before** the fresh deployment. Runtime inbound remains IAM; this mode
changes only Runtime → Gateway authentication.

## Prepare the IdP

Use an approved disposable OIDC provider with discovery metadata, a machine-to-
machine client, an API audience, and explicit scopes (for example
`resolve:tickets`). Configure the IdP to issue those scopes to that client.
No scope or audience is inferred from a provider default.

Set the unique project name in `agentcore/agentcore.json`, then run:

```bash
./scripts/enable-custom-jwt.sh
```

This is a **local configuration** helper. It:

- Requires CLI 1.2.0 and rejects existing deployment/ledger state;
- Reads a client secret without echoing it and passes it to the native CLI via
  stdin, never a command-line argument;
- Adds a project-qualified `<project>-gateway-m2m` OAuth credential;
- Writes Gateway discovery, `allowedAudience`, `allowedClients`, `allowedScopes`;
- Writes runtime provider, audience and `GATEWAY_OAUTH_SCOPES` settings;
- Preserves semantic search, all targets, Memory, LOG_ONLY policy and evaluators.

The helper uses a private `.sample-cli-home` for CLI preferences and local secret
key storage, with OS keychain access disabled. The CLI stores the secret in
gitignored `agentcore/.env.local`. Protect both with mode 0600/0700, never use
shell tracing, and do not copy either into the container
build context or reports. The helper does not obtain a token or deploy anything.

Use the [fresh deployment commands](../README.md#deploy-and-follow-an-incident)
after reviewing configuration. The wrapper rejects an existing provider name;
there is no unrelated-name adoption. The CDK rejects a missing or inconsistent
runtime scope list rather than removing the Gateway restriction. The actual
runtime decorator requests the configured scopes with `auth_flow="M2M"`.

## Verify

Authorized live checks must demonstrate successful scoped access and rejection
of expired/missing tokens, wrong client/audience, and insufficient scope without
tool execution. A locally valid JSON schema or captured decorator argument does
not prove IdP/Gateway behavior.

Gateway connection failure does not become a successful LLM-only incident
resolution. Diagnose the provider, claims and scopes; do not remove filters to
make a request succeed.

For an IAM-only fresh fixture, leave the default JSON configuration intact. This
sample does not provide a general deployed-stack auth reconfiguration framework.
Delete/recreate only a proven disposable fixture, keeping its cleanup ledger.
CLI-managed OAuth provider retention requires a separate owner-reviewed cleanup
step; stack deletion does not establish credential revocation.

[Jira USER_FEDERATION is separate](authentication-guide.md#jira-user_federation).
