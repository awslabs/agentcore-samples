# Building Multi-Agent Site Reliability Engineering Assistants with Amazon Bedrock AgentCore

by Amit Arora, Dheeraj Oruganty | 25 JUL 2025 in Amazon Bedrock, Amazon Bedrock Agents, Amazon Bedrock Knowledge Bases, Amazon Machine Learning, Artificial Intelligence, Generative AI

Site Reliability Engineers (SREs) face an increasingly complex challenge in modern distributed systems. During production incidents, they must rapidly correlate data from multiple sources—logs, metrics, Kubernetes events, and operational runbooks—to identify root causes and implement solutions. Traditional monitoring tools provide raw data but lack the intelligence to synthesize information across these diverse systems, often leaving SREs to manually piece together the story behind system failures.

Imagine being able to ask your infrastructure questions in natural language: ***"Why are the payment-service pods crash looping?"*** or ***"What's causing the API latency spike?"*** and receiving comprehensive, actionable insights that combine infrastructure status, log analysis, performance metrics, and step-by-step remediation procedures. This capability transforms incident response from a manual, time-intensive process into an intelligent, collaborative investigation.

In this post, we demonstrate how to build a multi-agent SRE assistant using [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html), [LangGraph](https://langchain-ai.github.io/langgraph/), and the [Model Context Protocol (MCP)](https://modelcontextprotocol.io). This system deploys specialized AI agents that collaborate to provide the deep, contextual intelligence that modern SRE teams need for effective incident response and infrastructure management. We'll walk you through the complete implementation, from setting up the demo environment to deploying on Amazon Bedrock AgentCore Runtime for production use.

## Solution overview

This solution presents a comprehensive multi-agent architecture that addresses the challenges of modern SRE operations through intelligent automation. The system consists of four specialized AI agents working together under a supervisor agent to provide comprehensive infrastructure analysis and incident response assistance.

> **Note on Demo Environment**: The examples in this post use synthetically generated data from our demo environment. The backend servers simulate realistic Kubernetes clusters, application logs, performance metrics, and operational runbooks. In production deployments, these stub servers would be replaced with connections to your actual infrastructure systems, monitoring platforms, and documentation repositories.

The architecture demonstrates several key capabilities:

- **Natural language infrastructure queries** – Ask complex questions about your infrastructure in plain English and receive detailed analysis combining data from multiple sources
- **Multi-agent collaboration** – Specialized agents for Kubernetes, logs, metrics, and operational procedures work together to provide comprehensive insights
- **Real-time data synthesis** – Agents access live infrastructure data through standardized APIs and present correlated findings
- **Automated runbook execution** – Retrieve and display step-by-step operational procedures for common incident scenarios
- **Source attribution** – Every finding includes explicit source attribution for verification and audit purposes

The following diagram illustrates the solution architecture:

![SRE Agent Architecture with AgentCore Components](./images/sre-agent-architecture.png)

The architecture demonstrates how the SRE Support Agent integrates seamlessly with Amazon Bedrock AgentCore components:

- **Customer Interface**: Receives alerts about degraded API response times and returns comprehensive agent responses
- **AgentCore Runtime**: Manages the execution environment for the multi-agent SRE system
- **SRE Support Agent**: Multi-agent collaboration system that processes incidents and orchestrates responses
- **AgentCore Gateway**: Routes requests to specialized tools through OpenAPI interfaces:
  - Tool 1: K8s API for getting cluster events
  - Tool 2: Logs API for analyzing log patterns
  - Tool 3: Metrics API for analyzing performance trends
  - Tool 4: Runbooks API for searching operational procedures
- **AgentCore Memory**: Stores and retrieves session context and previous interactions for continuity
- **AgentCore Identity**: Handles authentication for tool access via Amazon Cognito integration
- **AgentCore Observability**: Collects and visualizes agent traces for monitoring and debugging
- **Amazon Bedrock LLMs**: Powers the agent intelligence through Claude models

### Architecture components

The multi-agent system uses a supervisor-agent pattern where a central orchestrator coordinates four specialized agents:

**Supervisor Agent**: Analyzes incoming queries and creates investigation plans, routing work to appropriate specialists and aggregating results into comprehensive reports.

**Kubernetes Infrastructure Agent**: Handles container orchestration and cluster operations, investigating pod failures, deployment issues, resource constraints, and cluster events.

**Application Logs Agent**: Processes log data to find relevant information, identifies patterns and anomalies, and correlates events across multiple services.

**Performance Metrics Agent**: Monitors system metrics and identifies performance issues, providing both real-time analysis and historical trending.

**Operational Runbooks Agent**: Provides access to documented procedures, troubleshooting guides, and escalation procedures based on the current situation.

### Leveraging Amazon Bedrock AgentCore Primitives

The system showcases the power of Amazon Bedrock AgentCore by utilizing multiple core primitives:

**AgentCore Gateway**: The centerpiece that converts any backend API into MCP (Model Context Protocol) tools. This enables agents built with any open-source framework supporting MCP (like LangGraph in our case) to seamlessly access infrastructure APIs.

**AgentCore Identity**: Provides comprehensive security for the entire system:
- **Ingress Authentication**: Secure access control for agents connecting to the Gateway
- **Egress Authentication**: Manages authentication with backend servers, ensuring secure API access without hardcoding credentials

**AgentCore Memory**: Transforms the SRE Agent from a stateless system into an intelligent, learning assistant that personalizes investigations based on user preferences and historical context. The memory system provides three distinct strategies:

- **User Preferences Strategy** (`/sre/users/{user_id}/preferences`): Stores individual user preferences for investigation style, communication channels, escalation procedures, and report formatting. For example, Alice (Technical SRE) receives detailed systematic analysis with troubleshooting steps, while Carol (Executive/Director) receives business-focused summaries with impact analysis.

- **Infrastructure Knowledge Strategy** (`/sre/infrastructure/{user_id}/{session_id}`): Accumulates domain expertise across investigations, allowing agents to learn from past discoveries. When the Kubernetes agent identifies a memory leak pattern, this knowledge becomes available for future investigations, enabling faster root cause identification.

- **Investigation Memory Strategy** (`/sre/investigations/{user_id}/{session_id}`): Maintains historical context of past incidents and their resolutions. This enables the system to suggest proven remediation approaches and avoid anti-patterns that previously failed.

The memory system demonstrates its value through personalized investigations. When both Alice and Carol investigate "API response times have degraded 3x in the last hour," they receive identical technical findings but completely different presentations:

```python
# Alice receives technical analysis
memory_client.retrieve_user_preferences(user_id="Alice")
# Returns: {"investigation_style": "detailed_systematic_analysis", 
#          "reports": "technical_exposition_with_troubleshooting_steps"}

# Carol receives executive summary  
memory_client.retrieve_user_preferences(user_id="Carol")
# Returns: {"investigation_style": "business_impact_focused",
#          "reports": "executive_summary_without_technical_details"}
```

**AgentCore Runtime**: Provides the serverless execution environment for deploying agents at scale. The Runtime offers automatic scaling from zero to thousands of concurrent sessions while maintaining complete session isolation. Authentication and authorization to agents deployed on AgentCore Runtime is handled by AWS IAM - applications invoking the agent must have appropriate IAM permissions and trust policies. Learn more about [AgentCore security and IAM configuration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/security-iam.html).

**AgentCore Observability**: Adding observability to an Agent deployed on the AgentCore Runtime is straightforward using the observability primitive. This enables comprehensive monitoring through Amazon CloudWatch with metrics, traces, and logs. Setting up observability requires three simple steps:

First, add the OpenTelemetry packages to your `pyproject.toml`:
```toml
dependencies = [
    # ... other dependencies ...
    "opentelemetry-instrumentation-langchain",
    "aws-opentelemetry-distro~=0.10.1",
]
```

Second, configure observability for your agents following the [Amazon Bedrock AgentCore observability configuration guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html#observability-configure-builtin) to enable metrics in Amazon CloudWatch.

Finally, start your container using the `opentelemetry-instrument` utility to automatically instrument your application:
```dockerfile
# Run application with OpenTelemetry instrumentation
CMD ["uv", "run", "opentelemetry-instrument", "uvicorn", "sre_agent.agent_runtime:app", "--host", "0.0.0.0", "--port", "8080"]
```

Once deployed with observability enabled, you gain visibility into:
- **LLM invocation metrics**: Token usage, latency, and model performance across all agents
- **Tool execution traces**: Duration and success rates for each MCP tool call
- **Memory operations**: Retrieval patterns and storage efficiency
- **End-to-end request tracing**: Complete request flow from user query to final response

![Agent Metrics Dashboard](./images/agent-metrics.gif)

The observability primitive automatically captures these metrics without additional code changes, providing production-grade monitoring capabilities out of the box.

**Foundation Models**: The system supports two providers for the Claude language models:
- **Amazon Bedrock**: Claude Haiku 4.5 (global.anthropic.claude-haiku-4-5-20251001-v1:0) for AWS-integrated deployments
- **Anthropic Direct**: Claude Haiku 4.5 (claude-haiku-4-5-20251001) for direct API access

### Development to Production Flow

The SRE Agent follows a structured deployment process from local development to production:

```
STEP 1: LOCAL DEVELOPMENT
┌─────────────────────────────────────────────────────────────────────┐
│  Develop Python Package (sre_agent/)                                │
│  └─> Test locally with CLI: uv run sre-agent --prompt "..."         │
│      └─> Agent connects to AgentCore Gateway via MCP protocol       │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
STEP 2: CONTAINERIZATION  
┌─────────────────────────────────────────────────────────────────────┐
│  Add agent_runtime.py (FastAPI server wrapper)                      │
│  └─> Create Dockerfile (ARM64 for AgentCore)                        │
│      └─> Uses deployment/build_and_deploy.sh script                 │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
STEP 3: LOCAL CONTAINER TESTING
┌─────────────────────────────────────────────────────────────────────┐
│  Build: LOCAL_BUILD=true ./deployment/build_and_deploy.sh           │
│  └─> Run: docker run -p 8080:8080 sre_agent:latest                  │
│      └─> Test: curl -X POST http://localhost:8080/invocations       │
│          └─> Container connects to same AgentCore Gateway           │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
STEP 4: PRODUCTION DEPLOYMENT
┌─────────────────────────────────────────────────────────────────────┐
│  Build & Push: ./deployment/build_and_deploy.sh                     │
│  └─> Pushes container to Amazon ECR                                 │
│      └─> deployment/deploy_agent_runtime.py deploys to AgentCore    │
│          └─> Test: uv run python deployment/invoke_agent_runtime.py │
│              └─> Production agent uses production Gateway           │
└─────────────────────────────────────────────────────────────────────┘

Key Points:
• Core agent code (sre_agent/) remains unchanged
• Deployment/ folder contains all deployment-specific utilities
• Same agent works locally and in production via environment config
• AgentCore Gateway provides MCP tools access at all stages
```

## Prerequisites

To implement this solution, you need the following:

* **Python 3.12 or later** with the `uv` package manager for Python dependency management
* **AWS account** with appropriate permissions to create resources
* **EC2 instance** (recommended: `t3.xlarge` or larger) for hosting the demo backend servers and as the development machine for building this solution
* **SSL certificates** for HTTPS endpoints (required by Amazon Bedrock AgentCore Gateway)
* **API credentials** for either:
  * Anthropic API key for direct Claude model access, OR
  * AWS credentials configured for Amazon Bedrock access
* **Model access** enabled in Amazon Bedrock for Claude models (Claude 3.7 Sonnet)

## Implementation walkthrough

In this section, we focus on how AgentCore Gateway, Memory, and Runtime work together to build this multi-agent collaboration system and deploy it end-to-end with MCP support and persistent intelligence. The step-by-step guidance to run this solution is found in the README and other documentation - here we provide a bullet point overview of what those steps entail:

- **Clone and setup**: Repository cloning, virtual environment creation, and dependency installation
- **Environment configuration**: Setting up API keys, LLM providers, and deployment configurations  
- **Backend APIs deployment**: Starting demo infrastructure APIs with SSL certificates
- **AgentCore Gateway setup**: Creating the gateway, identity providers, and MCP tool access
- **AgentCore Memory initialization**: Creating memory strategies and loading user personas for personalized investigations
- **Agent configuration**: Defining agent-to-tool mappings, memory integration, and system behavior
- **Multi-agent system initialization**: Setting up LangGraph workflow with memory-enabled agent coordination
- **Testing and validation**: Running CLI tests with user personas and validating personalized functionality
- **Containerization**: Building ARM64 Docker images for AgentCore Runtime compatibility
- **Production deployment**: Deploying containers to AgentCore Runtime with proper IAM configuration and memory persistence

Detailed instructions for each step are provided in the repository:
- [Use Case Setup Guide](https://github.com/awslabs/amazon-bedrock-agentcore-samples/tree/main/02-use-cases/SRE-agent#use-case-setup) - Backend deployment and development setup
- [Deployment Guide](https://github.com/awslabs/amazon-bedrock-agentcore-samples/tree/main/02-use-cases/SRE-agent/docs/deployment-guide.md) - Production containerization and AgentCore Runtime deployment

### Converting APIs to MCP Tools with AgentCore Gateway

Amazon Bedrock AgentCore Gateway demonstrates the power of protocol standardization by converting existing backend APIs into MCP (Model Context Protocol) tools that any agent framework can consume. This transformation happens seamlessly, requiring only OpenAPI specifications.

#### Step 1: Upload OpenAPI specifications

The gateway process begins by uploading your existing API specifications to Amazon S3. The [create_gateway.sh](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/feature/issue-143-deploy-sre-agent-agentcore-runtime/02-use-cases/SRE-agent/gateway/create_gateway.sh) script automatically handles uploading the four API specifications (Kubernetes, Logs, Metrics, and Runbooks) to your configured S3 bucket with proper metadata and content types. These specifications will be used to create API Endpoint Targets in the gateway.

#### Step 2: Create identity provider and gateway

Authentication is handled seamlessly through AgentCore Identity. The [main.py](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/feature/issue-143-deploy-sre-agent-agentcore-runtime/02-use-cases/SRE-agent/gateway/main.py) script creates both the credential provider and gateway:

```python
# Create AgentCore Gateway with JWT authorization
def create_gateway(
    client: Any,
    gateway_name: str,
    role_arn: str,
    discovery_url: str,
    allowed_clients: list = None,
    description: str = "AgentCore Gateway created via SDK",
    search_type: str = "SEMANTIC",
    protocol_version: str = "2025-03-26",
) -> Dict[str, Any]:
    
    # Build auth config for Cognito
    auth_config = {"customJWTAuthorizer": {"discoveryUrl": discovery_url}}
    if allowed_clients:
        auth_config["customJWTAuthorizer"]["allowedClients"] = allowed_clients
    
    protocol_configuration = {
        "mcp": {"searchType": search_type, "supportedVersions": [protocol_version]}
    }

    response = client.create_gateway(
        name=gateway_name,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration=auth_config,
        protocolConfiguration=protocol_configuration,
        description=description,
        exceptionLevel='DEBUG'
    )
    return response
```

#### Step 3: Deploy API Endpoint Targets with credential providers

Each API becomes an MCP target through the gateway. The system automatically handles credential management:

```python
def create_api_endpoint_target(
    client: Any,
    gateway_id: str,
    s3_uri: str,
    provider_arn: str,
    target_name_prefix: str = "open",
    description: str = "API Endpoint Target for OpenAPI schema",
) -> Dict[str, Any]:
    
    api_target_config = {"mcp": {"openApiSchema": {"s3": {"uri": s3_uri}}}}

    # API key credential provider configuration
    credential_config = {
        "credentialProviderType": "API_KEY",
        "credentialProvider": {
            "apiKeyCredentialProvider": {
                "providerArn": provider_arn,
                "credentialLocation": "HEADER",
                "credentialParameterName": "X-API-KEY",
            }
        },
    }
    
    response = client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name_prefix,
        description=description,
        targetConfiguration=api_target_config,
        credentialProviderConfigurations=[credential_config],
    )
    return response
```

#### Result: MCP Tools Ready for Any Agent Framework

Once deployed, the AgentCore Gateway provides a standardized `/mcp` endpoint secured with JWT tokens. Testing the deployment with [mcp_cmds.sh](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/feature/issue-143-deploy-sre-agent-agentcore-runtime/02-use-cases/SRE-agent/gateway/mcp_cmds.sh) reveals the power of this transformation:

```bash
🔧 Tool Summary:
================
📊 Total Tools Found: 21

📝 Tool Names:
   • x_amz_bedrock_agentcore_search
   • k8s-api___get_cluster_events
   • k8s-api___get_deployment_status
   • k8s-api___get_node_status
   • k8s-api___get_pod_status
   • k8s-api___get_resource_usage
   • logs-api___analyze_log_patterns
   • logs-api___count_log_events
   • logs-api___get_error_logs
   • logs-api___get_recent_logs
   • logs-api___search_logs
   • metrics-api___analyze_trends
   • metrics-api___get_availability_metrics
   • metrics-api___get_error_rates
   • metrics-api___get_performance_metrics
   • metrics-api___get_resource_metrics
   • runbooks-api___get_common_resolutions
   • runbooks-api___get_escalation_procedures
   • runbooks-api___get_incident_playbook
   • runbooks-api___get_troubleshooting_guide
   • runbooks-api___search_runbooks
```

#### Universal Agent Framework Compatibility

This MCP-standardized gateway can now be configured as a streamable HTTP server for any MCP client, including:

- **[AWS Strands](https://docs.aws.amazon.com/strands/)**: Amazon's agent development framework
- **[LangGraph](https://langchain-ai.github.io/langgraph/)**: The framework used in our SRE Agent implementation  
- **[CrewAI](https://github.com/joaomdmoura/crewAI)**: Multi-agent collaboration framework

The advantage of this approach is that existing APIs require no modification—only OpenAPI specifications. AgentCore Gateway handles:
- **Protocol Translation**: REST APIs ↔ MCP Protocol
- **Authentication**: JWT token validation and credential injection
- **Security**: TLS termination and access control
- **Standardization**: Consistent tool naming and parameter handling

This means you can take any existing infrastructure API (Kubernetes, monitoring, logging, documentation) and instantly make it available to any AI agent framework that supports MCP—all through a single, secure, standardized interface.

### Implementing Persistent Intelligence with AgentCore Memory

While AgentCore Gateway provides seamless API access, AgentCore Memory transforms the SRE Agent from a stateless system into an intelligent, learning assistant. The memory implementation demonstrates how a few lines of code can enable sophisticated personalization and cross-session knowledge retention.

#### Step 1: Initialize Memory Strategies

The SRE Agent memory system is built on Amazon Bedrock AgentCore Memory's event-based model with automatic namespace routing. During initialization, the system creates three memory strategies with specific namespace patterns:

```python
from sre_agent.memory.client import SREMemoryClient
from sre_agent.memory.strategies import create_memory_strategies

# Initialize memory client
memory_client = SREMemoryClient(
    memory_name="sre_agent_memory", 
    region="us-east-1"
)

# Create three specialized memory strategies
strategies = create_memory_strategies()
for strategy in strategies:
    memory_client.create_strategy(strategy)
```

The three strategies each serve distinct purposes:
- **User Preferences**: `/sre/users/{user_id}/preferences` - Individual investigation styles and communication preferences
- **Infrastructure Knowledge**: `/sre/infrastructure/{user_id}/{session_id}` - Domain expertise accumulated across investigations
- **Investigation Summaries**: `/sre/investigations/{user_id}/{session_id}` - Historical incident patterns and resolutions

#### Step 2: Load User Personas and Preferences

The system comes pre-configured with user personas that demonstrate personalized investigations. The [manage_memories.py](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/feature/issue-143-deploy-sre-agent-agentcore-runtime/02-use-cases/SRE-agent/scripts/manage_memories.py) script loads these personas:

```python
# Load Alice - Technical SRE Engineer
alice_preferences = {
    "investigation_style": "detailed_systematic_analysis",
    "communication": ["#alice-alerts", "#sre-team"],
    "escalation": {"contact": "alice.manager@company.com", "threshold": "15min"},
    "reports": "technical_exposition_with_troubleshooting_steps",
    "timezone": "UTC"
}

# Load Carol - Executive/Director
carol_preferences = {
    "investigation_style": "business_impact_focused", 
    "communication": ["#carol-executive", "#strategic-alerts"],
    "escalation": {"contact": "carol.director@company.com", "threshold": "5min"},
    "reports": "executive_summary_without_technical_details",
    "timezone": "EST"
}

# Store preferences using memory client
memory_client.store_user_preference("Alice", alice_preferences)
memory_client.store_user_preference("Carol", carol_preferences)
```

#### Step 3: Automatic Namespace Routing in Action

The power of AgentCore Memory lies in its automatic namespace routing. When the SRE Agent creates events, it only needs to provide the `actor_id`—Amazon Bedrock AgentCore Memory automatically determines which namespace(s) the event belongs to:

```python
# During investigation, the supervisor agent stores context
memory_client.create_event(
    memory_id="sre_agent_memory-abc123",
    actor_id="Alice",  # AgentCore Memory routes this automatically
    session_id="investigation_2025_01_15", 
    messages=[("investigation_started", "USER")]
)

# Memory system automatically:
# 1. Checks all strategy namespaces
# 2. Matches actor_id "Alice" to /sre/users/Alice/preferences
# 3. Stores event in User Preferences Strategy
# 4. Makes event available for future retrievals
```

#### Result: Personalized Investigation Experience

The memory system's impact becomes clear when both Alice and Carol investigate the same issue. Using identical technical findings, the system produces completely different presentations:

**Alice's Technical Report** (detailed systematic analysis):
```markdown
## Technical Investigation Summary
**Root Cause**: Payment processor memory leak causing OOM kills
**Analysis**:
- Pod restart frequency increased 300% at 14:23 UTC
- Memory utilization peaked at 8.2GB (80% of container limit)  
- JVM garbage collection latency spiked to 2.3s
**Next Steps**:
1. Implement heap dump analysis (kubectl exec payment-pod -- jmap)
2. Review recent code deployments for memory management changes
3. Consider increasing memory limits and implementing graceful shutdown
```

**Carol's Executive Summary** (business impact focused):
```markdown
## Business Impact Assessment  
**Status**: CRITICAL - Customer payment processing degraded
**Impact**: 23% transaction failure rate, $47K revenue at risk
**Timeline**: Issue detected 14:23 UTC, resolution ETA 45 minutes
**Business Actions**:
- Customer communication initiated via status page
- Finance team alerted for revenue impact tracking  
- Escalating to VP Engineering if not resolved by 15:15 UTC
```

The memory system enables this personalization while continuously learning from each investigation, building organizational knowledge that improves incident response over time.

### Deploying to production with Amazon Bedrock AgentCore Runtime

Amazon Bedrock AgentCore makes it remarkably simple to deploy existing agents to production. The process involves three key steps: containerizing your agent, deploying to AgentCore Runtime, and invoking the deployed agent.

#### Step 1: Containerize your agent

AgentCore Runtime requires ARM64 containers. Here's the complete [Dockerfile](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/feature/issue-143-deploy-sre-agent-agentcore-runtime/02-use-cases/SRE-agent/Dockerfile):

```dockerfile
# Use uv's ARM64 Python base image
FROM --platform=linux/arm64 ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Copy uv files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy SRE agent module
COPY sre_agent/ ./sre_agent/

# Set environment variables
# Note: Set DEBUG=true to enable debug logging and traces
ENV PYTHONPATH="/app" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8080

# Run application with OpenTelemetry instrumentation
CMD ["uv", "run", "opentelemetry-instrument", "uvicorn", "sre_agent.agent_runtime:app", "--host", "0.0.0.0", "--port", "8080"]
```

The key insight: any existing agent just needs a FastAPI wrapper (`agent_runtime:app`) to become AgentCore-compatible, and we add `opentelemetry-instrument` for enabling observability through AgentCore.

#### Step 2: Deploy to AgentCore Runtime

Deploying to AgentCore Runtime is surprisingly straightforward with the [deploy_agent_runtime.py](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/feature/issue-143-deploy-sre-agent-agentcore-runtime/02-use-cases/SRE-agent/deployment/deploy_agent_runtime.py) script:

```python
import boto3

# Create AgentCore client
client = boto3.client('bedrock-agentcore', region_name=region)

# Environment variables for your agent
env_vars = {
    'GATEWAY_ACCESS_TOKEN': gateway_access_token,
    'LLM_PROVIDER': llm_provider,
    'ANTHROPIC_API_KEY': anthropic_api_key  # if using Anthropic
}

# Deploy container to AgentCore Runtime
response = client.create_agent_runtime(
    agentRuntimeName=runtime_name,
    agentRuntimeArtifact={
        'containerConfiguration': {
            'containerUri': container_uri  # Your ECR container URI
        }
    },
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn=role_arn,
    environmentVariables=env_vars
)

print(f"Agent Runtime ARN: {response['agentRuntimeArn']}")
```

That's it! AgentCore handles all the infrastructure, scaling, and session management automatically.

#### Step 3: Invoke your deployed agent

Calling your deployed agent is just as simple with [invoke_agent_runtime.py](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/feature/issue-143-deploy-sre-agent-agentcore-runtime/02-use-cases/SRE-agent/deployment/invoke_agent_runtime.py):

```python
# Prepare your query with user_id and session_id for memory personalization
payload = json.dumps({
    "input": {
        "prompt": "API response times have degraded 3x in the last hour",
        "user_id": "Alice",  # User for personalized investigation
        "session_id": "investigation-20250127-123456"  # Session for context
    }
})

# Invoke the deployed agent
response = agent_core_client.invoke_agent_runtime(
    agentRuntimeArn=runtime_arn,
    runtimeSessionId=session_id,
    payload=payload,
    qualifier="DEFAULT"
)

# Get the response
response_data = json.loads(response['response'].read())
print(response_data)  # Full response includes output with agent's investigation
```

#### Key benefits of AgentCore Runtime

- **Zero infrastructure management**: No servers, load balancers, or scaling to configure
- **Built-in session isolation**: Each conversation is completely isolated
- **AWS IAM integration**: Secure access control without custom authentication
- **Automatic scaling**: Scales from zero to thousands of concurrent sessions

The complete deployment process, including building containers and handling AWS permissions, is documented in the [Deployment Guide](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/feature/issue-143-deploy-sre-agent-agentcore-runtime/02-use-cases/SRE-agent/docs/deployment-guide.md).


## Real-world use cases

Let's explore how the SRE Agent handles common incident response scenarios with a real investigation.

### Investigating API performance degradation

When facing a production issue, you can query the system in natural language. The system uses AgentCore Memory to personalize the investigation based on your role and preferences:

```bash
export USER_ID=Alice
sre-agent --prompt "API response times have degraded 3x in the last hour"
```

The supervisor retrieves Alice's preferences from memory (detailed systematic analysis style) and creates an investigation plan tailored to her role as a Technical SRE:

```
📋 ## 🔍 Investigation Plan
**1.** Use metrics_agent to analyze API performance metrics including response times, error rates, and resource utilization to identify the extent and pattern of slowdown
**2.** Use logs_agent to examine application logs for errors related to slow API responses, focusing on database connection issues and memory errors
**3.** Use kubernetes_agent to check pod status and resource constraints, especially for web-service and database pods, looking for CrashLoopBackOff states and missing ConfigMaps
**📊 Complexity:** Simple
**🤖 Auto-execute:** Yes
**👥 Agents involved:** Metrics Agent, Logs Agent, Kubernetes Agent
```

The agents investigate sequentially according to the plan, each contributing their specialized analysis. The system then aggregates these findings into a comprehensive executive summary:

```
## 📋 Executive Summary
### 🎯 Key Insights
- **Root Cause**: Database service failure due to missing ConfigMap 'database-config' in production namespace, causing database pod to crash repeatedly
- **Impact**: Severe performance degradation with API response times increased from 150ms to 5000ms (33x slower)
- **Severity**: High - Database unavailability, memory exhaustion (100%), and CPU saturation (95%) causing 75% error rate

### ⚡ Next Steps
1. **Immediate** (< 1 hour): Create/update ConfigMap 'database-config' in production namespace and restart database pod
2. **Short-term** (< 24 hours): 
   - Fix permissions on '/var/lib/postgresql/data' directory
   - Increase Java heap space for web-service to address OutOfMemoryErrors
   - Optimize UserService.loadAllUsers method causing memory issues
3. **Long-term** (< 1 week): 
   - Implement resource monitoring with alerts for CPU (>80%), memory (>90%)
   - Optimize slow database queries, particularly "SELECT * FROM users WHERE status='active'"
   - Scale up resources or implement autoscaling for web-service

### 🚨 Critical Alerts
- Database pod (database-pod-7b9c4d8f2a-x5m1q) in CrashLoopBackOff state
- Web-service experiencing OutOfMemoryErrors in UserService.loadAllUsers(UserService.java:45)
- Node-3 experiencing memory pressure (>85% usage)
- Web-app-deployment showing readiness probe failures with 503 errors

### 🔍 Troubleshooting Steps
1. Verify ConfigMap status: `kubectl get configmap database-config -n production`
2. Check database pod logs: `kubectl logs database-pod-7b9c4d8f2a-x5m1q -n production`
3. Create/update ConfigMap: `kubectl create configmap database-config --from-file=database.conf -n production`
4. Fix data directory permissions: `kubectl exec database-pod-7b9c4d8f2a-x5m1q -n production -- chmod -R 700 /var/lib/postgresql/data`
5. Restart database pod: `kubectl delete pod database-pod-7b9c4d8f2a-x5m1q -n production`
```

This investigation demonstrates all AgentCore primitives working together: **AgentCore Gateway** provides secure access to infrastructure APIs through MCP tools, **AgentCore Identity** handles ingress and egress authentication, **AgentCore Runtime** hosts the multi-agent system with automatic scaling, **AgentCore Memory** personalizes Alice's experience and stores investigation knowledge for future incidents, and **AgentCore Observability** captures detailed metrics and traces in Amazon CloudWatch for monitoring and debugging.

The SRE Agent demonstrates intelligent agent orchestration, with the supervisor routing work to specialists based on the investigation plan. The system's memory capabilities ensure that each investigation builds organizational knowledge and provides personalized experiences based on user roles and preferences.

This investigation showcases several key capabilities:
- **Multi-source correlation**: Connecting database configuration issues to API performance degradation
- **Sequential investigation**: Agents work systematically through the investigation plan while providing live updates
- **Source attribution**: Every finding includes the specific tool and data source
- **Actionable insights**: Clear timeline of events and prioritized recovery steps
- **Cascading failure detection**: Understanding how one failure propagates through the system

## Business impact

Organizations implementing AI-powered SRE assistance report significant improvements in key operational metrics:

**Faster Incident Resolution**: Initial investigations that previously took 30-45 minutes can be completed in 5-10 minutes, providing SREs with comprehensive context before diving into detailed analysis.

**Reduced Context Switching**: Instead of navigating multiple dashboards and tools, SREs can ask questions in natural language and receive aggregated insights from all relevant data sources.

**Knowledge Democratization**: Junior team members can access the same comprehensive investigation techniques as senior engineers, reducing dependency on tribal knowledge and on-call burden.

**Consistent Methodology**: The system ensures consistent investigation approaches across team members and incident types, improving overall reliability and reducing the chance of missed evidence.

**Documentation and Learning**: Automatically generated investigation reports provide valuable documentation for post-incident reviews and help teams learn from each incident.

**Seamless AWS Integration**: The system naturally extends your existing AWS infrastructure investments, working alongside services like CloudWatch, Systems Manager, and other AWS operational tools to provide a unified operational intelligence platform.

## Extending the solution

The modular architecture makes it easy to extend the system for your specific needs:

### Custom agents

Add specialized agents for your domain:

- **Security Agent**: For compliance checks and security incident response
- **Database Agent**: For database-specific troubleshooting and optimization
- **Network Agent**: For connectivity and infrastructure debugging

### Real infrastructure integration

Replace the demo APIs with connections to your actual systems:

- **Kubernetes Integration**: Connect to your cluster APIs for pod status, deployments, and events
- **Log Aggregation**: Integrate with your log management platform (Elasticsearch, Splunk, CloudWatch Logs)  
- **Metrics Platform**: Connect to your monitoring system (Prometheus, DataDog, CloudWatch Metrics)
- **Runbook Repository**: Link to your operational documentation and playbooks stored in wikis, git repositories, or knowledge bases

## Clean up

To avoid incurring future charges, use the comprehensive cleanup script to remove all AWS resources:

```bash
# Complete cleanup - deletes AWS resources and local files
./scripts/cleanup.sh
```

This script will automatically:
- Stop all backend servers
- Delete the AgentCore Gateway and all its targets
- Delete AgentCore Memory resources
- Delete the AgentCore Runtime
- Remove generated files (gateway URIs, tokens, agent ARNs, memory IDs)

The cleanup script ensures complete removal of all billable AWS resources created during the demo.

## Conclusion

The SRE Agent demonstrates how multi-agent systems can transform incident response from a manual, time-intensive process into an intelligent, collaborative investigation that provides SREs with the insights they need to resolve issues quickly and confidently.

By combining Amazon Bedrock AgentCore's enterprise-grade infrastructure with the Model Context Protocol's standardized tool access, we've created a foundation that can adapt as your infrastructure evolves and new capabilities emerge. 

The complete implementation is available in our GitHub repository, including demo environments, configuration guides, and extension examples. We encourage you to explore the system, customize it for your infrastructure, and share your experiences with the community.

To get started building your own SRE assistant, refer to the following resources:

* [Amazon Bedrock AgentCore documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html)
* [SRE Agent GitHub repository](https://github.com/awslabs/amazon-bedrock-agentcore-samples)
* [Model Context Protocol specification](https://modelcontextprotocol.io)
* [LangGraph framework documentation](https://langchain-ai.github.io/langgraph/)

What operational challenges will you solve with AI-powered SRE assistance? Start your journey today and experience the future of intelligent infrastructure operations.

---

### About the authors

**[Author Name]** is a [Title] at Amazon Web Services, where [brief description of role and expertise]. [Additional background and interests].

**[Author Name]** is a [Title] at Amazon Web Services, specializing in [expertise areas]. [Background and contributions].
