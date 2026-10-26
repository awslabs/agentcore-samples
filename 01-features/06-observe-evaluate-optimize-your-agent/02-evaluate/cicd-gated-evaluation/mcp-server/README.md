# MCP Server

FastMCP server deployed as an AgentCore MCP runtime with role-based access control.

## 3-Layer Auth Pattern

1. **AgentCore (Layer 1):** Validates JWT signature, issuer, and expiry via `authorizer_configuration` before the request reaches the container.
2. **Header passthrough (Layer 2):** `request_header_allowlist=["Authorization"]` ensures the JWT is forwarded to the MCP server container.
3. **AuthMiddleware (Layer 3):** `fastmcp.server.dependencies.get_http_headers(include={"authorization"})` reads the JWT from HTTP headers. The middleware **re-verifies the signature** against the pool's JWKS as defence in depth — Layer 1 already validates it, but this layer must not trust claims it has not checked itself. Issuer, expiry and `token_use` are verified (the last because audience is not, and Cognito ID tokens share the same keys and issuer while also carrying `custom:roles`); verification fails closed. Authorization then matches each tool's declared `meta`: a gated tool needs a matching role **or** a matching scope, so user tokens are authorized by `custom:roles` and machine tokens by their granted scopes.

## Tools

| Tool | Access | Declared `meta` |
|---|---|---|
| `get_current_datetime` | Public | none |
| `get_stock_price` | Gated | `auth_meta(roles=["FinanceUser"], scopes=["mcp/finance"])` |
| `get_employee_count` | Gated | `auth_meta(roles=["HRUser"], scopes=["mcp/hr"])` |

Tools without `meta` are public (no role or scope check).

## Role Configuration

Gated tools declare both a **role** (for user tokens) and a **scope** (for M2M tokens)
via `auth_meta()`. A gated tool is authorized when the caller has a matching role **or**
a matching scope — user tokens satisfy the role requirement, machine tokens the scope
requirement. Declaring the scope is what lets CI's M2M token reach the tool; a gated tool
with no scope is unreachable by any machine token.

```python
from src.auth import auth_meta

@mcp.tool(tags={"Finance"}, meta=auth_meta(roles=["FinanceUser"], scopes=["mcp/finance"]))
def get_stock_price(symbol: str) -> str:
    """Requires FinanceUser role (user token) or mcp/finance scope (M2M token)."""
    ...

@mcp.tool(tags={"HR"}, meta=auth_meta(roles=["HRUser"], scopes=["mcp/hr"]))
def get_employee_count(department: str) -> str:
    """Requires HRUser role (user token) or mcp/hr scope (M2M token)."""
    ...
```

`auth_meta(roles=..., scopes=...)` accepts a string or a list for either argument; both
are optional, so a tool can be gated by role only, scope only, or both.

## Adding a new role-gated tool

1. Define the tool with `@mcp.tool()` and add `meta=auth_meta(roles=[...], scopes=[...])`.
2. The `AuthMiddleware` handles enforcement automatically — no manual checks needed.
3. Grant the new scope to the M2M client (in the CDK stack) if CI needs to reach the tool;
   until then it is denied to machine callers by design.

```python
@mcp.tool(tags={"Admin"}, meta=auth_meta(roles=["AdminUser"], scopes=["mcp/admin"]))
def my_sensitive_tool(param: str) -> str:
    """Requires AdminUser role (user token) or mcp/admin scope (M2M token)."""
    return "result"
```
