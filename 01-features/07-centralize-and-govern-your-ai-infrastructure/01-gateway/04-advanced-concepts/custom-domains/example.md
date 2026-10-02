# Request examples

Assumed config: one gateway at the **root** of `mcp.example.com`, with endpoints
`mcp`, `http_mcp` target `catalog`, `http_a2a` agent `agent`, `inference`, and
`http_agent` target `tool-agent`.

```json
{
  "domain_name": "mcp.example.com",
  "routes": [
    { "path": "/", "gateway_url": "https://<gw>.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
      "endpoints": [
        { "type": "mcp" },
        { "type": "http_mcp", "target_name": "catalog" },
        { "type": "http_a2a", "target_name": "agent" },
        { "type": "inference" },
        { "type": "http_agent", "target_name": "tool-agent" }
      ] }
  ]
}
```

---

## 1. `GET https://mcp.example.com/mcp` (no token)

```
< HTTP/1.1 401 Unauthorized
< www-authenticate: Bearer error="invalid_token",
    resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
```

Follow the challenge:

```
GET https://mcp.example.com/.well-known/oauth-protected-resource/mcp
< HTTP/1.1 200 OK
< content-type: application/json
{
  "resource": "https://mcp.example.com/mcp",
  "authorization_servers": ["https://login.microsoftonline.com/<tenant>/v2.0"],
  "scopes_supported": ["..."]
}
```

Retry with the token → proxied to the gateway `/mcp`:

```
GET https://mcp.example.com/mcp   Authorization: Bearer <token>
< HTTP/1.1 200 OK   (MCP session)
```

---

## 2. `GET https://mcp.example.com/catalog/mcp` (no token)

```
< HTTP/1.1 401 Unauthorized
< www-authenticate: Bearer error="invalid_token",
    resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/catalog/mcp"
```

```
GET https://mcp.example.com/.well-known/oauth-protected-resource/catalog/mcp
< HTTP/1.1 200 OK
{
  "resource": "https://mcp.example.com/catalog/mcp",
  "authorization_servers": ["https://login.microsoftonline.com/<tenant>/v2.0"]
}
```

```
GET https://mcp.example.com/catalog/mcp   Authorization: Bearer <token>
< HTTP/1.1 200 OK   (proxied to gateway /catalog/mcp)
```

> Note: `authorization_servers` is the **gateway's** (Entra). the upstream target's own OAuth is the gateway's *outbound* target auth — invisible to this client.

---

## 3. `GET https://mcp.example.com/agent` (A2A)

### 3a. Fetch the card, no token

```
GET https://mcp.example.com/agent/.well-known/agent-card.json
< HTTP/1.1 401 Unauthorized
< www-authenticate: Bearer error="invalid_token",
    resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/agent"
```

```
GET https://mcp.example.com/.well-known/oauth-protected-resource/agent
< HTTP/1.1 200 OK
{
  "resource": "https://mcp.example.com/agent",
  "authorization_servers": ["https://login.microsoftonline.com/<tenant>/v2.0"]
}
```

### 3b. Fetch the card, with token

```
GET https://mcp.example.com/agent/.well-known/agent-card.json   Authorization: Bearer <token>
< HTTP/1.1 200 OK
{
  "name": "agent",
  "url": "https://mcp.example.com/agent",
  "additionalInterfaces": [{ "transport": "JSONRPC", "url": "https://mcp.example.com/agent" }],
  "skills": [ ... ]
}
```

### 3c. Talk to the agent

```
POST https://mcp.example.com/agent   Authorization: Bearer <token>
< HTTP/1.1 200 OK   (proxied to gateway /agent)
```

---

## 4. `GET https://mcp.example.com/inference` (no token)

Gateway aggregated inference endpoint — behaves exactly like `/mcp` (PRM,
`resource` override, no agent card).

```
< HTTP/1.1 401 Unauthorized
< www-authenticate: Bearer error="invalid_token",
    resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/inference"
```

```
GET https://mcp.example.com/.well-known/oauth-protected-resource/inference
< HTTP/1.1 200 OK
{
  "resource": "https://mcp.example.com/inference",
  "authorization_servers": ["https://login.microsoftonline.com/<tenant>/v2.0"]
}
```

```
POST https://mcp.example.com/inference   Authorization: Bearer <token>
< HTTP/1.1 200 OK   (proxied to gateway /inference)
```

---

## 5. `GET https://mcp.example.com/tool-agent` (`http_agent`, no card)

Agent-as-tool with **no** `.well-known/agent-card.json` — same PRM flow as
above; the card path is not served (default-deny 403).

```
< HTTP/1.1 401 Unauthorized
< www-authenticate: Bearer error="invalid_token",
    resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/tool-agent"
```

```
GET https://mcp.example.com/.well-known/oauth-protected-resource/tool-agent
< HTTP/1.1 200 OK
{
  "resource": "https://mcp.example.com/tool-agent",
  "authorization_servers": ["https://login.microsoftonline.com/<tenant>/v2.0"]
}
```

```
POST https://mcp.example.com/tool-agent   Authorization: Bearer <token>
< HTTP/1.1 200 OK   (proxied to gateway /tool-agent)
```

```
GET https://mcp.example.com/tool-agent/.well-known/agent-card.json
< HTTP/1.1 403 Forbidden      (no card served for http_agent — use type http_a2a for that)
```

---

## Anything unmatched

```
GET https://mcp.example.com/nope
< HTTP/1.1 403 Forbidden      (CloudFront viewer-request function default-deny)
```

---

# Path-based: two gateways under one domain

A **different** config (no root gateway — root and path gateways are mutually
exclusive): the IT gateway under `/it`, the HR gateway under `/ht`. Each path
routes to its own gateway; the same URL prefix is stripped before the origin.

```json
{
  "domain_name": "mcp.example.com",
  "routes": [
    { "path": "/it", "gateway_url": "https://<gw-it>.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
      "endpoints": [ { "type": "mcp" } ] },
    { "path": "/ht", "gateway_url": "https://<gw-hr>.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
      "endpoints": [ { "type": "mcp" }, { "type": "http_a2a", "target_name": "recruiter" } ] }
  ]
}
```

## 6. `GET https://mcp.example.com/it/mcp` → IT gateway

```
< HTTP/1.1 401 Unauthorized
< www-authenticate: Bearer error="invalid_token",
    resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/it/mcp"
```

```
GET https://mcp.example.com/.well-known/oauth-protected-resource/it/mcp
< HTTP/1.1 200 OK
{
  "resource": "https://mcp.example.com/it/mcp",
  "authorization_servers": ["https://login.microsoftonline.com/<it-tenant>/v2.0"]
}
```

```
POST https://mcp.example.com/it/mcp   Authorization: Bearer <token>
< HTTP/1.1 200 OK   (proxied to the IT gateway /mcp)
```

## 7. `GET https://mcp.example.com/ht/mcp` → HR gateway

```
GET https://mcp.example.com/.well-known/oauth-protected-resource/ht/mcp
< HTTP/1.1 200 OK
{
  "resource": "https://mcp.example.com/ht/mcp",
  "authorization_servers": ["https://login.microsoftonline.com/<hr-tenant>/v2.0"]
}
```

```
POST https://mcp.example.com/ht/mcp   Authorization: Bearer <token>
< HTTP/1.1 200 OK   (proxied to the HR gateway /mcp — a different gateway than /it)
```

The A2A recruiter agent on the HR gateway is reachable at
`https://mcp.example.com/ht/recruiter` (card at
`https://mcp.example.com/ht/recruiter/.well-known/agent-card.json`), exactly
like scenario 3 but prefixed with `/ht`.

> Each path's `authorization_servers` come from **that** gateway's own PRM, so
> the two gateways can use different auth servers/tenants.
