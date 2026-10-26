# Automation Agents

Agents that run without a user in the loop. They are triggered by system events such as file uploads, queue messages, scheduled jobs, or webhooks, and they process work end-to-end until they either complete or hit a confidence threshold that requires human review.

## Service configuration

| Service | Typical setup for automation agents |
|---------|-------------------------------------|
| Identity | Service credentials; the agent authenticates as itself |
| Memory | Minimal or none; persistent state lives in the event payload or an external database |
| Runtime | Longer timeouts, no streaming required |
| Guardrails | Confidence thresholds to decide when to auto-complete vs. escalate to a human |
| Observability | Throughput and accuracy metrics rather than per-user traces |
| Gateway | System-to-system API access: ticketing systems, ERPs, payment processors |

## Common patterns

| Pattern | When it fits |
|---------|-------------|
| Workflow (DAG) | A sequence of dependent steps, e.g. validate invoice, match PO, generate payment file |
| Agents-as-tools | An orchestrator delegates to specialized sub-agents for distinct tasks |
| A2A | Agents on separate runtimes that communicate using the A2A protocol |
| Human-in-the-loop | The agent completes what it can and flags items below a confidence threshold for human review |

## Samples

| Sample | Vertical | Complexity | AgentCore features |
|--------|----------|------------|-------------------|
| [event-driven-claims-agent](./event-driven-claims-agent/) | Insurance | Advanced | Runtime, Gateway, Memory, Policy, Evaluations, Observability; S3 to EventBridge to Lambda to Runtime |
| [visa-b2b-account-payable-agent](./visa-b2b-account-payable-agent/) | B2B Payments | Advanced | Runtime, Gateway, Policy, Payments; automated invoice matching and ISO 20022 payment file generation via Visa B2B Connect |
| [enterprise-web-intelligence-agent](./enterprise-web-intelligence-agent/) | Market Intelligence | Intermediate | Runtime, Browser; automated web scraping pipeline implemented twice (LangGraph and Strands) for comparison |
| [intelligent-event-agent](./intelligent-event-agent/) | General | Beginner | Runtime, Memory, Gateway *(in development, no README yet)* |
| [multi-isv-orchestration](./multi-isv-orchestration/) | Enterprise CRM + ERP | Intermediate | Gateway (multi-target), Identity (Cognito inbound + CustomOauth2 outbound); Salesforce + SAP MCP Server through one Gateway for cross-system queries |
| [gpu-music-production-agent](./gpu-music-production-agent/) | Media & Entertainment | Advanced | Runtime (EC2 capacity provider, GPU), Memory; a generative audio model runs on the instance GPU and three collocated agents hand files to each other over a shared EBS volume, with a computed verdict that escalates to human review |


## See also

- [01-conversational-agents](../01-conversational-agents/) - agents that interact with users in real time
- [03-coding-assistants](../03-coding-assistants/) - developer tools and code generation
