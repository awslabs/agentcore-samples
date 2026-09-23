# System Components

The SRE Agent system is built on three core Amazon Bedrock AgentCore components that work together to provide a scalable, secure, and intelligent infrastructure management solution.

## Architecture Overview

```
┌──────────────────────────────────┐  ┌─────────────────────────────────────┐
│        AgentCore Memory          │  │         AgentCore Runtime           │
│  • User Preferences              │  │  ┌──────────────────────────────────┐
│  • Infrastructure Knowledge      │  │  │   Multi-Agent System (LangGraph) │
│  • Investigation Summaries       │  │  │  ┌─────────────────┐             │
└─────────────┬────────────────────┘  │  │  │   Supervisor    │             │
              │                       │  │  │     Agent       │             │
              └──────────────────────►│  │  └────────┬────────┘             │
                                      │  │           │                      │
                                      │  │  ┌────────┴────┬─────┬─────┬───┐ │
                                      │  │  ▼             ▼     ▼     ▼   ▼ │
                                      │  │┌──────┐  ┌──────┐ ┌─────┐ ┌───┐│ │
                                      │  ││ K8s  │  │ Logs │ │Metr-│ │Run││ │
                                      │  ││Agent │  │Agent │ │ics  │ │bks││ │
                                      │  │└───┬──┘  └──┬───┘ └──┬──┘ └─┬─┘│ │
                                      │  └────┼────────┼────────┼──────┼──┘ │
                                      └───────┼────────┼────────┼──────┼────┘
                                              │        │        │      │
                                              ▼        ▼        ▼      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        AgentCore Gateway                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────┐     │
│  │  MCP Tools  │  │    Auth     │  │   API Translation           │     │
│  └──────┬──────┘  └──────┬──────┘  └─────────┬───────────────────┘     │
└─────────┼────────────────┼───────────────────┼─────────────────────────┘
          │                │                   │
          ▼                ▼                   ▼
    Backend APIs    Identity Provider    OpenAPI Specs
```

## Multi-Agent System

Built on **LangGraph**, the multi-agent system orchestrates specialized agents for complex investigations.

### Supervisor Agent
The central coordinator that:
- Analyzes incoming queries and determines investigation strategy
- Routes tasks to appropriate specialist agents based on expertise
- Coordinates multi-step investigations across multiple domains
- **Manages all memory interactions** - only the supervisor has direct access to AgentCore Memory
- Personalizes investigations based on retrieved user preferences and past investigations

### Specialist Agents
Each agent focuses on a specific domain with dedicated tools:

#### Kubernetes Infrastructure Agent
Handles container orchestration and cluster operations. This agent investigates issues across pods, deployments, services, and nodes by examining cluster state, analyzing pod health, resource utilization, and recent events.

**Capabilities:**
- Check pod status across namespaces
- Examine deployment configurations and rollout history
- Investigate cluster events for anomalies
- Analyze resource usage patterns
- Monitor node health and capacity

#### Application Logs Agent
Processes log data to find relevant information. This agent understands log patterns, identifies anomalies, and correlates events across multiple services.

**Capabilities:**
- Full-text search with regex support
- Error log aggregation and categorization
- Pattern detection for recurring issues
- Time-based correlation of events
- Statistical analysis of log volumes

#### Performance Metrics Agent
Monitors system metrics and identifies performance issues. This agent understands relationships between different metrics and provides both real-time analysis and historical trending.

**Capabilities:**
- Application performance metrics (response times, throughput)
- Error rate analysis with thresholding
- Resource utilization metrics (CPU, memory, disk)
- Availability and uptime monitoring
- Trend analysis for capacity planning

#### Operational Runbooks Agent
Provides access to documented procedures, troubleshooting guides, and best practices. This agent helps standardize incident response by retrieving relevant procedures based on the current situation.

**Capabilities:**
- Incident-specific playbooks for common scenarios
- Detailed troubleshooting guides with step-by-step instructions
- Escalation procedures with contact information
- Common resolution patterns for known issues
- Best practices for system operations

#### Search Agent
Provides cross-domain information retrieval capabilities:

**Capabilities:**
- Unified search across all infrastructure domains
- Context-aware result ranking and filtering
- Cross-reference information between different agent domains

### Agent Collaboration
The supervisor coordinates complex investigations by:
1. Breaking down queries into specialized tasks
2. Routing tasks to appropriate agents in parallel or sequence
3. Aggregating results from multiple agents
4. Applying memory-based personalization to findings
5. Generating unified, context-aware reports

## Amazon Bedrock AgentCore

The system leverages three fundamental AgentCore primitives that provide enterprise-grade AI infrastructure:

### 1. AgentCore Runtime
A **serverless execution environment** designed specifically for AI agents:

- **Managed Infrastructure**: Fully managed compute with automatic scaling from zero to thousands of concurrent sessions
- **Container-based Deployment**: Supports ARM64 Docker containers with built-in security and isolation
- **Enterprise Integration**: Native AWS IAM support with session-level security boundaries
- **Multi-model Support**: Compatible with Amazon Bedrock models and external LLM providers
- **Production Features**: Built-in monitoring, logging, debugging, and observability

### 2. AgentCore Gateway
A **secure API bridge** that enables agents to interact with backend systems:

- **Protocol Translation**: Converts REST/GraphQL/gRPC APIs into standardized MCP (Model Context Protocol) tools
- **Universal Tool Interface**: Provides a consistent interface for any agent framework to discover and use tools
- **Enterprise Security**: JWT-based authentication with support for multiple identity providers (Cognito, Auth0, Okta)
- **API Management**: Health monitoring, automatic retries, rate limiting, and error handling
- **Schema-driven**: Uses OpenAPI specifications to automatically generate tool definitions

### 3. AgentCore Memory
A **persistent knowledge system** that enables agents to learn and personalize over time:

- **Event-based Storage**: Immutable event log that accumulates knowledge without data loss
- **Namespace Isolation**: Automatic routing based on actor IDs for user and context separation
- **Flexible Retention**: Configurable retention policies for different memory types (30-90 days)
- **Pattern Extraction**: Automatic extraction of structured data from unstructured agent responses
- **Cross-session Learning**: Enables agents to build on past investigations and user interactions

## Integration Architecture

### Data Flow
1. **User Query** → Supervisor Agent analyzes and retrieves relevant memories
2. **Investigation Planning** → Supervisor routes to specialist agents
3. **Tool Execution** → Agents access backend APIs through Gateway
4. **Response Processing** → Memory system extracts and stores patterns
5. **Report Generation** → Personalized report based on user preferences

### Security Model
- **IAM Integration**: Full AWS IAM support with `BedrockAgentCoreFullAccess` policy
- **JWT Authentication**: Bearer tokens for gateway communication
- **SSL/TLS Required**: All endpoints must use HTTPS
- **Namespace Isolation**: Memory events isolated by actor ID

## Tool Domains

The gateway provides 20 specialized tools across 4 domains:

### Kubernetes Operations (5 tools)
- `get_pod_status`: Monitor pod health and state
- `get_deployment_status`: Check deployment rollout status
- `get_cluster_events`: Retrieve recent cluster events
- `get_resource_usage`: Analyze CPU/memory utilization
- `get_node_status`: Monitor node health and capacity

### Log Analysis (5 tools)
- `search_logs`: Full-text search across log streams
- `get_error_logs`: Extract error and exception logs
- `analyze_log_patterns`: Detect recurring patterns
- `get_recent_logs`: Retrieve latest log entries
- `count_log_events`: Aggregate log event statistics

### Metrics Collection (5 tools)
- `get_performance_metrics`: Application performance data
- `get_error_rates`: Error rate trends and spikes
- `get_resource_metrics`: Infrastructure resource usage
- `get_availability_metrics`: Service uptime and SLAs
- `analyze_trends`: Historical trend analysis

### Runbook Management (5 tools)
- `search_runbooks`: Find relevant procedures
- `get_incident_playbook`: Incident response guides
- `get_troubleshooting_guide`: Step-by-step debugging
- `get_escalation_procedures`: Contact and escalation paths
- `get_common_resolutions`: Known issue solutions

## Demo Environment

For evaluation and testing, the system includes a demo environment with:

- **Mock API Servers**: Simulated Kubernetes, logs, metrics, and runbooks APIs
- **Realistic Data**: Representative infrastructure scenarios and failure patterns
- **Safe Testing**: Isolated environment prevents production impact
- **Full Feature Support**: All agent capabilities available in demo mode

## Development to Production

The architecture supports seamless progression from development to production:

```
Local Development → Container Testing → Production Deployment
     (CLI)              (Docker)         (AgentCore Runtime)
       ↓                   ↓                    ↓
   Gateway Only      Gateway + Runtime    Full Stack with Memory
```

This unified approach ensures consistent behavior across all deployment stages while providing the scalability and security required for enterprise production use.