"""
AWS Agent Registry with Auth0 Dynamic Client Registration (DCR).

Demonstrates how to create a CUSTOM_JWT-secured registry and use it as an
MCP server inside the Kiro IDE with zero-config OAuth via DCR (RFC 7591):
  1. Create an AWS Agent Registry with Auth0 CUSTOM_JWT authorizer
  2. Seed the registry with 4 sample agent records
  3. List registries and records to verify
  4. Instructions for connecting the registry as an MCP server in Kiro
  5. Clean up all created resources

Usage:
    python dcr_registry_search_mcp_in_kiro.py

Prerequisites:
    - Auth0 account with a tenant configured (see Step 1 in README)
    - .env file configured from .env.example with:
        AWS_REGION, AWS_ACCOUNT_ID, AUTH0_DOMAIN, AUTH0_AUDIENCE
    - pip install python-dotenv requests boto3
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from seed_records import _cp_client, create_registry, delete_registry, seed


# ── ANSI colors ────────────────────────────────────────────────────────────────
class C:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


REGION = os.getenv("AWS_REGION", "us-west-2")

# ── Step 2: Create Registry with Auth0 CUSTOM_JWT Authorizer ──────────────────
print(f"\n{C.BOLD}=== Step 2: Create Registry with Auth0 CUSTOM_JWT Authorizer ==={C.RESET}")
print(
    "  Creates a new AWS Agent Registry configured with Auth0 as the OAuth identity\n"
    "  provider, polls until READY, then automatically adds the MCP endpoint URL to\n"
    "  the registry's allowedAudience."
)

registry = create_registry(
    name="auth0-demo-registry-dcr",
    description="Demo registry with Auth0 OAuth for notebook walkthrough",
)
registry_id = registry["registryId"]
print(f"  {C.GREEN}✅ Registry created{C.RESET}")
print(f"  {C.BOLD}Registry ID:{C.RESET} {C.CYAN}{registry_id}{C.RESET}")
print(f"  {C.BOLD}Status:{C.RESET}      {registry['status']}")
print(
    f"  {C.BOLD}MCP URL:{C.RESET}     {C.CYAN}https://agent-registry.{REGION}.api.aws/registry/{registry_id}/mcp{C.RESET}"
)

# ── Step 3: Seed Registry with Sample Capability Records ──────────────────────
print(f"\n{C.BOLD}=== Step 3: Seed Registry with Sample Capability Records ==={C.RESET}")
print(
    "  Populates the registry with four sample agent records:\n"
    "  weather_agent, order_status_agent, customer_support_agent, inventory_lookup_agent.\n"
    "  Each record is created as DRAFT, submitted for approval, and auto-approved."
)

records = seed(registry_id=registry_id)
print(f"\n  {C.GREEN}✅ Seeded {len(records)} record(s):{C.RESET}")
for r in records:
    print(f"    • {r['name']} ({C.DIM}{r['recordId']}{C.RESET})")

# ── Verify: List registries and records ───────────────────────────────────────
print(f"\n{C.BOLD}=== Verify: List Registries and Records ==={C.RESET}")

cp = _cp_client()
for reg in cp.list_registries()["registries"]:
    if reg["name"] == "auth0-demo-registry-dcr":
        print(f"  Registry: {C.CYAN}{reg['registryId']}{C.RESET}")
        recs = cp.list_registry_records(registryId=reg["registryId"]).get("registryRecords", [])
        for rec in recs:
            sc = C.GREEN if rec["status"] == "APPROVED" else C.YELLOW
            print(f"    {sc}[{rec['status']:15s}]{C.RESET} {rec['recordId']:15s} {rec['name']}")

# ── Step 4: Connect Registry as MCP Server in Kiro ────────────────────────────
print(f"\n{C.BOLD}=== Step 4: Connect Registry as MCP Server in Kiro ==={C.RESET}")
mcp_url = f"https://agent-registry.{REGION}.api.aws/registry/{registry_id}/mcp/"
print(f"""
  Add the following to your Kiro MCP configuration (.kiro/settings/mcp.json):

  {{
    "mcpServers": {{
      "dcr-registry-server": {{
        "type": "http",
        "url": "{mcp_url}",
        "disabled": false
      }}
    }}
  }}

  When Kiro connects, it will:
  1. Discover the Auth0 authorization server via the registry's well-known endpoint
  2. Use DCR (RFC 7591) to auto-register as an OAuth client (POST /oidc/register)
  3. Obtain an access token via PKCE authorization code flow
  4. Use the token to call registry search via MCP

  Once authenticated, open Kiro chat and ask:
    "Use the AWS registry to search for weather records"

  Reference commands:
  - View Auth0 metadata:
    curl https://<domain>/.well-known/oauth-authorization-server

  - Manually register a DCR client:
    curl -X POST https://<domain>/oidc/register \\
      -H "Content-Type: application/json" \\
      -d '{{"client_name": "test-mcp-client",
            "redirect_uris": ["http://localhost:65358/callback"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none"}}'
""")

# ── Step 5: Clean Up ──────────────────────────────────────────────────────────
print(f"\n{C.BOLD}=== Step 5: Clean Up ==={C.RESET}")
print(f"  Deleting registry {C.DIM}{registry_id}{C.RESET} and all records...")
delete_registry(registry_id)
print(f"  {C.GREEN}✅ Registry and records deleted.{C.RESET}")

print(f"\n{C.GREEN}✅ DCR Registry demo complete!{C.RESET}")
