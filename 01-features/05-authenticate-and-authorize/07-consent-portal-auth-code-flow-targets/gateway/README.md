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
- `listingMode: DYNAMIC` is incompatible with the outbound authorization code
  flow, so it is not used.

## Which tools are here, and why only seven

The schema carries **7 read-only tools**: `get_me`, `search_repositories`,
`search_code`, `search_commits`, `search_users`, `get_teams` and
`get_team_members`.

GitHub's MCP server exposes 44, but the rest are deliberately left out because
**a standard MCP client cannot call them through the gateway.** Those tools take
`owner` and `repo` as *header-bound* parameters: the gateway advertises them as
ordinary body properties in `tools/list`, then rejects the call when forwarding
unless they arrive as `Mcp-Param-owner` / `Mcp-Param-repo` HTTP headers:

```
-32020  header mismatch: missing Mcp-Param-repo header for parameter "repo"
```

MCP sets headers per *connection*, not per call, while `owner` and `repo` change
on every call — so a client like the Strands `MCPClient` used by this sample has
nowhere to put them. An interactive client where you type headers by hand (MCP
Inspector) can call them, which is why the gateway-only walkthrough uses those
tools happily. Removing the `x-mcp-header` annotations from this file does *not*
help; the requirement comes from the gateway's own contract, not from the schema.

Keeping only the callable tools means everything advertised to the model works.
If you need the repo-scoped tools, use a client that can set per-call headers.

## Adding tools back

Everything in this file becomes a tool the model can call, and a permission
surface, so add deliberately and narrow the `scopes` in
[`../deploy/03_create_github_provider.py`](../deploy/03_create_github_provider.py)
and [`../deploy/04_create_github_target.py`](../deploy/04_create_github_target.py)
to match. Two things to know:

- **`tools/list` is paginated at 30 tools.** Past that the gateway returns a
  `nextCursor`, and a client that reads only the first page silently loses the
  rest — the model then reports that a tool does not exist, which looks like a
  model problem rather than a truncated list. The agent pages to the end
  (`list_all_tools` in [`../agent/agent.py`](../agent/agent.py)); keep that if
  you grow this file.
- Changing this file means recreating the target, not updating it: re-run
  [`../deploy/04_create_github_target.py`](../deploy/04_create_github_target.py)
  after deleting the existing target.

The tool definitions are taken from the gateway-only walkthrough in
[`01-features/07-…/authorization-code-flow/github/github.json`](../../../07-centralize-and-govern-your-ai-infrastructure/01-gateway/01-attach-targets/mcp/mcp-servers/01-configure-auth/authorization-code-flow/github/github.json),
trimmed to the subset above. Samples are self-contained by convention — hence a
copy rather than a shared import.
