# Conversational Agents

Agents that interact with users in real time through a chat or query interface. The user authenticates through an identity provider (Entra ID, Okta, Cognito), and the agent acts on their behalf. Memory keeps context across turns within a session and across sessions. Responses stream back as the agent works.

## Service configuration

| Service | Typical setup for conversational agents |
|---------|----------------------------------------|
| Identity | End-user OAuth token exchange via Entra ID, Okta, or Cognito |
| Memory | Session memory for multi-turn context plus long-term memory for cross-session recall |
| Runtime | Streaming enabled, session affinity, up to 8-hour sessions |
| Guardrails | Content filters, topic restrictions, PII redaction |
| Observability | Per-conversation traces with user context |
| Gateway | User-scoped tool access; the agent calls downstream APIs as the authenticated user |

## Common multi-agent patterns

| Pattern | When it fits |
|---------|-------------|
| Single agent with tools | One domain, up to around 10 tools, linear flow |
| Agents-as-tools | Multiple domains with routing between specialized agents |
| Graph / intent router | Branching flows based on intent classification |
| Human-in-the-loop | Regulated domains (healthcare, finance) where a human must approve certain steps |

## Samples

| Sample | Vertical | Complexity | AgentCore features |
|--------|----------|------------|-------------------|
| [A2A-multi-agent-incident-response](./A2A-multi-agent-incident-response/) | IT / DevOps | Advanced | Runtime, Gateway, Memory, A2A using Strands + OpenAI Agents + Google ADK |
| [AWS-operations-agent](./AWS-operations-agent/) | Cloud Operations | Advanced | Runtime, Gateway, Memory, Policy, Observability; built with Strands, ADK, and OpenAI Agents SDK |
| [customer-support-assistant-vpc](./customer-support-assistant-vpc/) | Retail / E-commerce | Intermediate | Runtime, Gateway deployed inside a VPC with private endpoints |
| [deep-research-agent](./deep-research-agent/) | Research / Q&A | Intermediate | Runtime, Gateway (Web Search); iterative Plan → Search → Reflect → Synthesize loop with auto-provisioning |
| [device-management-agent](./device-management-agent/) | IoT / Smart Home | Intermediate | Runtime, Gateway, Policy, Identity (Cognito); React frontend |
| [finance-personal-assistant](./finance-personal-assistant/) | Personal Finance | Beginner | Gateway, Policy; notebook-based |
| [healthcare-appointment-agent](./healthcare-appointment-agent/) | Healthcare | Intermediate | Runtime, Gateway, Policy, Observability; FHIR R4 via HealthLake |
| [lakehouse-agent](./lakehouse-agent/) | Data and Analytics | Advanced | Runtime, Gateway, Memory, Policy; OAuth row-level security over S3 Tables and Athena |
| [market-trends-agent](./market-trends-agent/) | Financial Services | Advanced | Runtime, Memory, Browser, Evaluations, Optimization; personalized broker investment assistant |
| [SRE-agent](./SRE-agent/) | Site Reliability | Advanced | Runtime, Gateway, Memory, Observability; multi-agent system with MCP-based tools and runbooks |
| [video-games-sales-assistant](./video-games-sales-assistant/) | Retail / Gaming | Intermediate | Runtime, Gateway, Memory; Next.js frontend with Amplify Gen 2 |


## See also

- [02-workflow-automation-agents](../02-workflow-automation-agents/) - event-driven and background agents
- [03-coding-assistants](../03-coding-assistants/) - developer tools and code generation
