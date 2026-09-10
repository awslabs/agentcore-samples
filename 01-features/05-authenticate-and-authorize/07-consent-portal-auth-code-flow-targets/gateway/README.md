# The GitHub MCP tool schema

[`github-tools.json`](github-tools.json) is the tool schema for
[GitHub's MCP server](https://github.com/github/github-mcp-server)
(`https://api.githubcopilot.com/mcp`), supplied to the gateway target as
`targetConfiguration.mcp.mcpServer.mcpToolSchema.inlinePayload` by
[`../deploy/04_create_github_target.py`](../deploy/04_create_github_target.py).

## Why the schema is supplied upfront

A gateway target for an MCP server can get its tool list two ways.

**Implicit sync** — the gateway connects to the MCP server at create time and
discovers the tools itself. For a target using the authorization code flow, that
means an admin has to complete a three-legged OAuth flow *during*
`CreateGatewayTarget`. That is exactly what the consent portal exists to avoid,
and it does not work from a pipeline.

**Schema upfront** (used here) — you provide the schema and the gateway parses
and caches it. The target is `READY` immediately, nobody authorizes anything at
deployment time, and `tools/list` works for every user without them having
connected GitHub. Only `tools/call` triggers the consent flow, which is what
makes the portal's out-of-band model work: users browse the catalogue freely and
authorize only the servers whose tools they actually invoke.

Two consequences worth knowing:

- `SynchronizeGatewayTargets` is not supported for a schema-upfront target. If
  GitHub adds tools, update the schema. You can switch a target between the two
  methods by updating its configuration.
- `listingMode: DYNAMIC` is incompatible with outbound 3LO, so it is not used.

## Trimming it

Everything in this file becomes a tool the model can call, and a permission
surface. For anything beyond a tutorial, cut it to the tools you actually want
exposed and narrow the `scopes` in
[`../deploy/03_create_github_provider.py`](../deploy/03_create_github_provider.py)
and [`../deploy/04_create_github_target.py`](../deploy/04_create_github_target.py)
to match. The gateway's semantic search (`searchType: SEMANTIC`) helps the model
choose among many tools, but it does not reduce what a compromised prompt could
reach.

This file is copied verbatim from the gateway-only walkthrough in
[`01-features/07-…/authorization-code-flow/github/github.json`](../../../07-centralize-and-govern-your-ai-infrastructure/01-gateway/01-attach-targets/mcp/mcp-servers/01-configure-auth/authorization-code-flow/github/github.json),
so the two samples describe the same server identically. Samples are
self-contained by convention — hence a copy rather than a shared import.
