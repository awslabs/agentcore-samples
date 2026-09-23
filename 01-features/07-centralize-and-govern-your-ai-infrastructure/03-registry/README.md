# AWS Agent registry

AWS Agent registry lets you discover and manage agents, tools, and resources across your organization. It provides a centralized catalog where teams can publish, search, and consume AI capabilities — enabling reuse and standardization across agent-based applications.

## Key Capabilities

- **Centralized catalog** — register and discover agents, tools, and resources in one place
- **Discoverability** — search and browse available capabilities across teams and accounts
- **Metadata management** — attach descriptions, schemas, and usage documentation to registered resources
- **Governance integration** — works with AgentCore gateway and policy for end-to-end access control

## Top-level Layout

| Folder | What's Inside |
|:---|:---|
| `00-getting-started/step-by-step/` | Five focused scripts: personas, registry creation, publishing records, approval, search |
| `00-getting-started/end-to-end/01-registry-end-to-end/` | Full end-to-end walkthrough: registry → records → approval → search |
| `00-getting-started/end-to-end/02-registry-end-to-end-oauth/` | End-to-end with Cognito CUSTOM_JWT OAuth authentication |
| `01-advanced/admin-approval-workflow/` | EventBridge + Lambda + DynamoDB approval pipeline with Slack notifications |
| `01-advanced/consumer-discovery-semantic-search/` | 12 semantic search scenarios across 14 e-commerce capabilities |
| `01-advanced/discovery-and-invocation-at-runtime/` | Orchestrator agent that discovers and invokes tools from registry at runtime |
| `01-advanced/kiro-registry-dcr-auth0/` | registry as an MCP server in Kiro IDE via Auth0 DCR (RFC 7591) |
| `01-advanced/kiro/kiro-power-publisher-workflow/` | Manage registry records directly from Kiro IDE chat |
| `01-advanced/publish-agentcore-tools-in-registry/` | Deploy MCP + A2A agents to AgentCore runtime and register them |
| `01-advanced/registry-push-sync-lambda/` | Lambda + EventBridge pipeline to auto-sync registry on runtime updates |
| `01-advanced/registry-skills-dynamic-discovery/` | AGENT_SKILLS record type: dynamic skill discovery with Strands agent |
| `01-advanced/registry-synchronize-mcpserver/` | URL-based sync: public MCP, OAuth-protected, and IAM-protected servers |

## How This Tree Is Organized

The `00-getting-started` folder provides a linear path from registry basics to complete
end-to-end demos. The `01-advanced` folder covers production-grade patterns organized
by concern: authentication (OAuth, DCR), automation (Lambda sync), runtime integration
(AgentCore runtime + gateway), and IDE-native discovery (Kiro).

## Finding Things

- **By pattern** → Authentication: `02-registry-end-to-end-oauth`, `kiro-registry-dcr-auth0`; Automation: `registry-push-sync-lambda`, `registry-synchronize-mcpserver`; runtime deployment: `publish-agentcore-tools-in-registry`, `discovery-and-invocation-at-runtime`; Skills: `registry-skills-dynamic-discovery`
- **By role** → Consumer: `consumer-discovery-semantic-search`, `kiro/*`; Publisher: `00-getting-started/step-by-step`, `publish-agentcore-tools-in-registry`; Admin: `admin-approval-workflow`
- **By complexity** → Beginner: `00-getting-started/`; Intermediate: `consumer-discovery`, `admin-approval`, `registry-synchronize`; Advanced: `discovery-and-invocation-at-runtime`, `registry-push-sync-lambda`

## Prerequisites

- Python 3.10+
- AWS account with Bedrock AgentCore access
- AWS CLI configured with credentials
- `boto3` installed (`pip install boto3`)
- Some advanced examples also require `strands-agents`, `bedrock-agentcore-starter-toolkit`, or `python-dotenv` — see per-folder `requirements.txt`

## Running the Python Scripts

```bash
# Getting started — step by step
python 00-getting-started/step-by-step/01_create_user_personas_workflow.py
python 00-getting-started/step-by-step/02_creating_registry_workflow.py
python 00-getting-started/step-by-step/03_publishing_records_workflow.py
python 00-getting-started/step-by-step/04_admin_approval_workflow.py
python 00-getting-started/step-by-step/05_search_registry_workflow.py

# Getting started — end to end
python 00-getting-started/end-to-end/01-registry-end-to-end/getting_started_registry_end_to_end.py
python 00-getting-started/end-to-end/02-registry-end-to-end-oauth/registry_end_to_end_oauth.py

# Advanced
python 01-advanced/consumer-discovery-semantic-search/consumer_discovery_semantic_search.py
python 01-advanced/admin-approval-workflow/admin_approval_workflow.py
python 01-advanced/registry-skills-dynamic-discovery/registry_skills_dynamic_discovery.py
python 01-advanced/registry-synchronize-mcpserver/registry_synchronize_mcpserver.py
python 01-advanced/registry-push-sync-lambda/deploy_lambda_push_sync.py
python 01-advanced/publish-agentcore-tools-in-registry/publish_agentcore_a2a_mcp_in_registry.py
python 01-advanced/discovery-and-invocation-at-runtime/discovery_and_invocation_at_runtime.py
python 01-advanced/kiro-registry-dcr-auth0/dcr_registry_search_mcp_in_kiro.py
```

## Documentation

- [AWS Agent registry Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry.html)
