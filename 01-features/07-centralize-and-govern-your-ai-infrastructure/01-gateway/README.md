# Amazon Bedrock AgentCore gateway

Without a centralized gateway, every tool or agent your organization builds, whether it's an MCP server, a REST API, or an AI agent, must independently handle credentials, policy enforcement, private connectivity, and logging. This means your legal team's contract review agent, your finance team's data retrieval API, and your operations team's incident response tool each carry the same infrastructure burden. Security teams review each target individually; developers wait for approvals, and nobody has a unified view of how AI tool infrastructure is being used across the organization.

![architecture](./images/architecture.png)

AgentCore gateway eliminates this duplication by establishing a single-entry point that all traffic flows through. Each team builds only the business logic for their target. AgentCore gateway handles everything else. It supports two categories of targets: MCP targets, where AgentCore gateway acts as a unified MCP server that aggregates capabilities across all attached targets, and HTTP targets, where AgentCore gateway sends traffic directly without aggregation or protocol translation. You can attach different AgentCore identity Credential Providers to different targets, which lets you securely control access on a per-target basis.

## Popular Patterns

### Pattern 1: Unified MCP Access Across IDEs

Developers are adopting AgentCore gateway as a single endpoint for accessing multiple MCP servers across IDEs, including VS Code, Cursor, Claude Code, and Kiro. Rather than configuring each MCP server connection individually per IDE, teams point to one URL and get consistent access to their full MCP toolset regardless of which IDE they're working in.

This pattern is accelerating as customers move beyond building custom MCP servers and begin integrating production-grade third-party MCP servers, such as GitHub, Salesforce, Databricks, and AWS MCP servers into their workflows. It is important to note that some of these MCP servers are protected by their primary IDP via federation and some are protected by their own auth servers. As the number of MCP servers per organization grows, managing connections, authentication, and routing at the IDE level becomes unsustainable. AgentCore gateway centralizes this complexity, giving platform teams a single control plane for MCP access while giving developers a frictionless experience across their preferred tools.

### Pattern 2: Enterprise Platform Teams Building Internal Tool Catalogs

Enterprise platform and security teams are adopting AgentCore policy and AgentCore gateway as a centralized control plane for managing, governing, and exposing internal tools and APIs to AI agents across their organizations. These teams have sophisticated governance requirements and are building internal "Tools-as-a-Service" or "Agents-as-a-Service" platforms.

What makes AgentCore gateway compelling for these teams is the consolidation of several enterprise-critical capabilities into a single layer:

- **Central observability**: AgentCore gateway provides a unified view of all agent-to-tool interactions across the organization, giving platform teams full visibility into what agents are calling, how often, and with what outcomes. This is essential for audit, debugging, and usage analysis at scale.

- **Central Credential Management**: Rather than distributing secrets and API credentials across individual agents or teams, AgentCore gateway manages authentication centrally. This eliminates credential sprawl and gives security teams a single point of control for access management.

- **VPC Access to Private Data**: Enterprises need agents to reach internal APIs and data sources that live within private VPCs — not just public endpoints. AgentCore gateway enables secure egress into customer VPCs, ensuring agents can access proprietary data without exposing it to the public internet.

- **Multi-Tenancy**: Organizations building internal platforms need to serve multiple teams, business units, or even external customers from a shared AgentCore gateway infrastructure while maintaining strict isolation between tenants.

- **Deterministic policy Enforcement**: This is where AgentCore policy becomes essential. AgentCore policy allows central platform teams to define and enforce deterministic guardrails on tool calls. This gives governance teams a hard boundary around agent behavior — not probabilistic model-level guardrails, but deterministic, auditable controls that ensure agents only do what they're explicitly permitted to do.

## Tutorials

| Section                                                             | Description                                                                              |
| :------------------------------------------------------------------ | :--------------------------------------------------------------------------------------- |
| [00-optional-setup](00-optional-setup/)                             | Optional prerequisites and environment configuration                                     |
| [01-attach-targets](01-attach-targets/)                             | Attach HTTP and MCP tool targets to your AgentCore gateway                               |
| [02-set-up-inbound-authorization](02-set-up-inbound-authorization/) | Configure inbound auth for AgentCore gateway                                             |
| [03-private-connectivity](03-private-connectivity/)                 | Connect AgentCore gateway to private resources or connect to AgentCore gateway privately |
| [04-advanced-concepts](04-advanced-concepts/)                       | Check out other advanced concepts                                                        |
| [05-community](05-community/)                                       | Community-contributed samples and workshops                                              |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed (for building arm64 deployment packages)
- AWS account with Bedrock AgentCore access
- AWS CLI configured with credentials
- `boto3` installed (`pip install boto3`)


## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
