# Amazon Bedrock AgentCore Use Cases

End-to-end samples organized by agent type. Each folder maps to one of the three workload categories used in AgentCore documentation.

## Categories

### [01-conversational-agents](./01-conversational-agents/) 

Agents that interact with users in real time. Users authenticate through an identity provider, the agent maintains session and long-term memory per user, and responses stream back as the agent works. See the [category README](./01-conversational-agents/README.md) for the full list and a guide on which sample to start with.

| Sample | Description | Vertical | Key Features |
|--------|-------------|----------|--------------|
| [A2A-multi-agent-incident-response](./01-conversational-agents/A2A-multi-agent-incident-response/) | Multi-agent incident response implemented with three A2A frameworks | IT / DevOps | Runtime, Gateway, Memory, A2A (3 frameworks) |
| [AWS-operations-agent](./01-conversational-agents/AWS-operations-agent/) | Intelligent AWS operations assistant with Okta authentication and comprehensive monitoring capabilities | Cloud Operations | Runtime, Gateway, Memory, Policy, Observability |
| [customer-support-assistant-vpc](./01-conversational-agents/customer-support-assistant-vpc/) | Production-ready customer service agent with memory, knowledge base integration, and Google OAuth | Retail / E-commerce | Runtime, Gateway (VPC) |
| [data-analyst-conversational-assistant](./data-analyst-conversational-assistant/) | Data analysis assistant with Amplify frontend and CDK deployment | Data and Analytics | Runtime, Gateway, Memory, Policy, Identity, Evaluations, Observability |
| [deep-research-agent](./01-conversational-agents/deep-research-agent/) | Deep research assistant with web search and runtime deployment | Research / Q&A | Gateway (Web Search), Runtime |
| [device-management-agent](./01-conversational-agents/device-management-agent/) | IoT device management system with Cognito authentication and real-time monitoring | IoT / Smart Home | Runtime, Gateway, Policy, Identity (Cognito) |
| [finance-personal-assistant](./01-conversational-agents/finance-personal-assistant/) | Personal budget management with multi-agent workflows and guardrails | Personal Finance | Gateway, Policy |
| [healthcare-appointment-agent](./01-conversational-agents/healthcare-appointment-agent/) | FHIR-compliant healthcare appointment scheduling with patient data integration | Healthcare | Runtime, Gateway, Policy, Observability (FHIR R4) |
| [lakehouse-agent](./01-conversational-agents/lakehouse-agent/) | Secure data lakehouse assistant with memory and row-level access controls | Data and Analytics | Runtime, Gateway, Memory, Policy (row-level security) |
| [market-trends-agent](./01-conversational-agents/market-trends-agent/) | Financial market analysis with browser tools and memory integration | Financial Services | Runtime, Memory, Browser, Evaluations, Optimization |
| [SRE-agent](./01-conversational-agents/SRE-agent/) | Site reliability engineering assistant with multi-agent LangGraph workflows | Site Reliability | Runtime, Gateway, Memory, Observability |
| [video-games-sales-assistant](./01-conversational-agents/video-games-sales-assistant/) | Conversational video game sales analysis assistant | Retail / Gaming | Runtime, Gateway, Memory |

### [02-workflow-automation-agents](./02-workflow-automation-agents/) 

Agents that run without a user in the loop. They are triggered by events such as file uploads, webhook calls, or scheduled jobs. Identity is service-to-service rather than user-facing, and memory is minimal since state is carried in the event payload.

| Sample | Description | Vertical | Key Features |
|--------|-------------|----------|--------------|
| [event-driven-claims-agent](./02-workflow-automation-agents/event-driven-claims-agent/) | Event-driven insurance claims processing with policy enforcement and evaluation | Insurance | Runtime, Gateway, Memory, Policy, Evaluations, Observability |
| [visa-b2b-account-payable-agent](./02-workflow-automation-agents/visa-b2b-account-payable-agent/) | Automated B2B accounts payable workflows with Visa payments | B2B Payments | Runtime, Gateway, Policy, Payments |
| [enterprise-web-intelligence-agent](./02-workflow-automation-agents/enterprise-web-intelligence-agent/) | Web research and analysis agent using browser tools for competitive intelligence | Market Intelligence | Runtime, Browser |
| [intelligent-event-agent](./02-workflow-automation-agents/intelligent-event-agent/) | Event automation agent with runtime, memory, and gateway integration | General / Events | Runtime, Memory, Gateway *(in development)* |
| [multi-isv-orchestration](./02-workflow-automation-agents/multi-isv-orchestration/) | Multi-system workflow orchestration across enterprise CRM and ERP services | Enterprise CRM + ERP | Gateway (multi-target), Identity (Cognito + CustomOauth2) |
| [gpu-music-production-agent](./02-workflow-automation-agents/gpu-music-production-agent/) | Collaborative music production with local GPU inference, mastering, and compliance screening | Media & Entertainment | Runtime (EC2 capacity provider, GPU), Memory; local model inference, collocated agents on a shared volume |

### [03-coding-assistants](./03-coding-assistants/) 

Agents that help developers write, run, or fix code. Tasks tend to be longer-running and scoped to a project or repository. AgentCore Code Interpreter handles sandboxed execution, and Gateway can aggregate multiple developer tool APIs behind one MCP endpoint.

| Sample | Description | Use Case | Key Features |
|--------|-------------|----------|--------------|
| [text-to-python-ide](./03-coding-assistants/text-to-python-ide/) | Code generation and execution environment with AgentCore Code Interpreter | Text-to-Python IDE with sandboxed execution | Runtime, Code Interpreter, Memory, Policy |
| [claude-code-gateway-mcp-server](./03-coding-assistants/claude-code-gateway-mcp-server/) | Integrate Claude Code with MCP Server using AgentCore Gateway for dynamic tool loading and centralized access | Single MCP endpoint for Claude Code | Gateway, Identity |


## Resources
- [AgentCore docs](https://docs.aws.amazon.com/bedrock-agentcore/)
