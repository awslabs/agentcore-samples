# SRE Agent Memory System

## Overview

The SRE Agent includes a sophisticated long-term memory system built on [Amazon Bedrock AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html) that enables persistent user preferences, cross-session learning, and personalized investigation experiences. This system remembers user preferences, learns from past investigations, and tailors reports based on individual user roles and workflows.

The system provides three distinct memory strategies for different types of information and comes pre-configured with user personas to demonstrate personalized investigations.

## Pre-configured User Personas

The system comes with two example user personas in [`scripts/user_config.yaml`](../scripts/user_config.yaml) that demonstrate how personalized investigations work:

### Alice - Technical SRE Engineer
- **Investigation Style**: Detailed, systematic, multi-dimensional investigations with comprehensive analysis
- **Communication**: Technical team channels (`#alice-alerts`, `#sre-team`) with detailed metrics and troubleshooting steps
- **Escalation**: Technical management (`alice.manager@company.com`) with 15-minute delay threshold
- **Reports**: Technical exposition with step-by-step methodologies and complete tool references
- **Preferences**: Detailed analysis, UTC timezone, includes troubleshooting steps

### Carol - Executive/Director  
- **Investigation Style**: Executive-focused with business impact analysis and streamlined presentation
- **Communication**: Strategic channels (`#carol-executive`, `#strategic-alerts`) with filtered notifications (critical only)
- **Escalation**: Executive team (`carol.director@company.com`) with faster 20-minute timeline
- **Reports**: Business-focused summaries without detailed technical steps, emphasizing impact and business consequences
- **Preferences**: Executive summary format, EST timezone, business impact focus

## Personalized Investigation Examples

When running investigations with different user IDs, the agent produces similar technical findings but presents them according to each user's preferences:

```bash
# Alice's detailed technical investigation
USER_ID=Alice sre-agent --prompt "API response times have degraded 3x in the last hour" --provider bedrock

# Carol's executive-focused investigation  
USER_ID=Carol sre-agent --prompt "API response times have degraded 3x in the last hour" --provider bedrock
```

Both commands identify identical technical issues but present them differently:
- **Alice** receives detailed technical analysis with step-by-step troubleshooting and comprehensive tool references
- **Carol** receives executive summaries focused on business impact with rapid escalation timelines

For a detailed comparison showing how the memory system personalizes identical incidents, see: [**Memory System Report Comparison**](examples/Memory_System_Analysis_User_Personalization_20250802_162648.md)

## Amazon Bedrock AgentCore Memory Architecture

The memory system uses Amazon Bedrock AgentCore Memory's sophisticated event-based model with automatic namespace routing:

### Memory Strategies and Namespaces
When the SRE Agent initializes, it creates three memory strategies with specific namespace patterns:

1. **User Preferences Strategy**: Namespace pattern `/sre/users/{user_id}/preferences`
2. **Infrastructure Knowledge Strategy**: Namespace pattern `/sre/infrastructure/{user_id}/{session_id}`  
3. **Investigation Memory Strategy**: Namespace pattern `/sre/investigations/{user_id}/{session_id}`

### How Namespace Routing Works
The key insight is that **the SRE Agent only needs to provide the actor_id** when calling `create_event()`. Amazon Bedrock AgentCore Memory automatically:

1. **Strategy Matching**: Examines all strategies associated with the memory resource
2. **Namespace Resolution**: Determines which namespace(s) the event belongs to based on the actor_id
3. **Automatic Routing**: Places the event in the correct strategy's namespace without requiring explicit namespace specification
4. **Multi-Strategy Storage**: A single event can be stored in multiple strategies if the namespaces match

### Actor ID Design for Memory Namespace Isolation
The memory system uses a consistent actor_id strategy to ensure proper namespace isolation:

- **User preferences**: Use user_id as actor_id (e.g., "Alice") for personal namespaces (`/sre/users/Alice/preferences`)
- **Infrastructure knowledge**: Use agent-specific actor_ids (e.g., "kubernetes-agent") for domain expertise namespaces
- **Investigation summaries**: Use user_id as actor_id for personal investigation history (`/sre/investigations/Alice`)
- **Conversation memory**: Use user_id to maintain personal conversation context

This design ensures that:
- User-specific data remains isolated to individual users
- Infrastructure knowledge is organized by the agent that discovered it
- Memory operations route to the correct namespaces automatically
- Cross-session memory retrieval works reliably

### Event-based Model Benefits
- **Immutable Events**: All memory entries are stored as immutable events that cannot be modified
- **Accumulative Learning**: New events accumulate over time without deleting old ones
- **Strategy Aggregation**: Memory strategies aggregate events from their namespace to provide relevant context
- **Automatic Organization**: Events are automatically organized by user, session, and memory type

### Example Event Flow
```python
# SRE Agent calls create_event with just actor_id and content
memory_client.create_event(
    memory_id="sre_agent_memory-xyz",
    actor_id="Alice",  # Amazon Bedrock AgentCore Memory uses this to route to correct namespace
    session_id="investigation_2025_01_15",
    messages=[("preference_data", "ASSISTANT")]
)

# Amazon Bedrock AgentCore Memory automatically:
# 1. Checks all strategy namespaces for this memory
# 2. Matches actor_id "Alice" to namespace "/sre/users/Alice/preferences" 
# 3. Stores event in User Preferences Strategy
# 4. Makes event available for future retrievals
```

## Memory Strategies

These are the three long-term memory strategies supported by Amazon Bedrock AgentCore (see [Memory Getting Started Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-getting-started.html)):

### 1. User Preferences Memory
**Strategy:** Semantic Memory with 90-day retention  
**Purpose:** Remember user-specific operational preferences

**Captures:**
- Escalation contacts and procedures
- Notification channels (Slack, email, etc.)
- Investigation workflow preferences  
- Communication style preferences

**Example Usage:**
```python
# When user mentions "escalate to ops-team@company.com for database issues"
# The system automatically captures:
{
  "user_id": "user123",
  "preference_type": "escalation",
  "preference_value": {
    "contact": "ops-team@company.com",
    "service_category": "database"
  },
  "context": "Investigation of Redis connection failures"
}
```

### 2. Infrastructure Knowledge Memory
**Strategy:** Semantic Memory with 30-day retention  
**Purpose:** Build understanding of infrastructure patterns and relationships

**Captures:**
- Service dependencies and relationships
- Failure patterns and common issues
- Configuration insights and best practices
- Performance baselines and thresholds

**Example Usage:**
```python
# When investigating a service outage, the system learns:
{
  "service_name": "web-api",
  "knowledge_type": "dependency",
  "knowledge_data": {
    "depends_on": "postgres-db",
    "failure_mode": "connection_timeout",
    "typical_recovery_time": "2-5 minutes"
  },
  "confidence": 0.8
}
```

### 3. Investigation Summaries Memory
**Strategy:** Summary Memory with 60-day retention  
**Purpose:** Maintain history of investigations for learning and reference

**Captures:**
- Investigation timeline and actions taken
- Key findings and root causes
- Resolution strategies and outcomes
- Cross-team collaboration context

**Example Usage:**
```python
{
  "incident_id": "incident_20250128_1045",
  "query": "Why is the checkout service responding slowly?",
  "timeline": [
    {"time": "10:45", "action": "Started investigation with metrics agent"},
    {"time": "10:47", "action": "Identified high CPU usage"},
    {"time": "10:50", "action": "Checked application logs for errors"}
  ],
  "actions_taken": [
    "Analyzed CPU and memory metrics",
    "Reviewed application error logs",  
    "Identified memory leak in payment processing"
  ],
  "resolution_status": "completed",
  "key_findings": [
    "Memory leak in payment service consuming 2GB/hour",
    "Database connection pool exhausted during peak traffic",
    "Missing circuit breaker causing cascade failures"
  ]
}
```

## Memory Flow During Investigation

```
┌─────────────┐    ┌─────────────────────┐              ┌──────────────────────┐
│    User     │    │     Supervisor      │              │  Amazon Bedrock      │
│             │    │      Agent          │              │  AgentCore Memory    │
└──────┬──────┘    └──────────┬──────────┘              └──────────┬───────────┘
       │                      │                                    │
       │ Investigation Query  │                                    │
       ├─────────────────────►│                                    │
       │                      │                                    │
       │              ┌───────▼───────┐                            │
       │              │ on_investigation_start()                   │
       │              │ (memory_hooks) │                           │
       │              └───────┬───────┘                            │
       │                      │                                    │
       │                      │ retrieve_memory(preferences)       │
       │                      ├───────────────────────────────────►│
       │                      │◄───────────────────────────────────┤
       │                      │ User preferences (10)              │
       │                      │                                    │
       │                      │ retrieve_memory(infrastructure)    │
       │                      ├───────────────────────────────────►│
       │                      │◄───────────────────────────────────┤
       │                      │ Infrastructure data (50)           │
       │                      │                                    │
       │                      │ retrieve_memory(investigations)    │
       │                      ├───────────────────────────────────►│
       │                      │◄───────────────────────────────────┤
       │                      │ Past investigations (5)            │
       │                      │                                    │
       │              ┌───────▼───────┐                            │
       │              │ Planning Agent with Memory Tools           │
       │              │ (supervisor.py)                            │
       │              └───────┬───────┘                            │
       │                      │                                    │
       │              ┌───────▼───────┐                            │
       │              │ Execute Investigation                      │
       │              └───────┬───────┘                            │
       │                      │                                    │
       │                      ├─► Metrics Agent                    │
       │                      ├─► Logs Agent                       │
       │                      ├─► K8s Agent                        │
       │                      ├─► Runbooks Agent                   │
       │                      │                                    │
       │              ┌───────▼───────┐                            │
       │              │ Agent Response Processing                  │
       │              │ (pattern extraction & storage)             │
       │              └───────┬───────┘                            │
       │                      │                                    │
       │              ┌───────▼───────┐                            │
       │              │ on_investigation_complete()                │
       │              │ (save investigation summary)               │
       │              └───────┬───────┘                            │
       │                      │                                    │
       │ Final Response       │                                    │
       │◄─────────────────────┤                                    │
       │                      │                                    │
```

### Key Memory Interactions

The memory system integrates at three key points during an investigation. The [`supervisor.py`](../sre_agent/supervisor.py) orchestrates memory retrieval at startup and saves investigation summaries at completion. Individual agent responses are processed by [`agent_nodes.py`](../sre_agent/agent_nodes.py) which triggers pattern extraction through [`memory/hooks.py`](../sre_agent/memory/hooks.py).

- **Investigation Start**: Retrieves user preferences, infrastructure knowledge, and past investigations to provide context
- **Agent Responses**: Automatically extracts patterns like escalation contacts, notification channels, and service dependencies  
- **Investigation Complete**: Saves comprehensive summary with timeline, actions taken, and key findings

## Memory Tool Architecture and Planning Integration

The memory system uses a centralized architecture where **only the supervisor agent has direct access to memory tools**:

### Tool Distribution Architecture
- **Supervisor Agent**: Has access to all 4 memory tools (`retrieve_memory`, `save_preference`, `save_infrastructure`, `save_investigation`)
- **Individual Agents**: Have NO direct access to memory tools, only domain-specific tools:
  - **Kubernetes Agent**: 5 k8s-api tools (get_pod_status, get_deployment_status, etc.)
  - **Application Logs Agent**: 5 logs-api tools (search_logs, get_error_logs, etc.)  
  - **Performance Metrics Agent**: 5 metrics-api tools (get_performance_metrics, analyze_trends, etc.)
  - **Operational Runbooks Agent**: 5 runbooks-api tools (search_runbooks, get_incident_playbook, etc.)

### Centralized Memory Management
This design ensures:
- **Memory operations are coordinated** through the supervisor
- **Individual agents focus on their domain expertise** without memory complexity
- **Memory context is retrieved once** and distributed to agents as needed
- **Consistent memory patterns** across all investigations

### Available Memory Tools (Supervisor Only)
- **save_preference**: Saves user preferences to long-term memory
- **save_infrastructure**: Saves infrastructure knowledge to long-term memory
- **save_investigation**: Saves investigation summaries to long-term memory  
- **retrieve_memory**: Retrieves relevant information from long-term memory

### Memory Context in Planning

When creating investigation plans, the supervisor agent incorporates memory context from three sources. The planning agent uses the `retrieve_memory` tool to gather relevant context before creating plans.

#### Planning Agent Memory Usage Example

Here's a real example from `agent.log` showing how the planning agent retrieves and uses memory context:

```log
# Memory context retrieval during planning (from agent.log)
2025-08-03 17:48:56,072,p1290668,{supervisor.py:339},INFO,Retrieved memory context for planning: 10 preferences, 50 knowledge items from 1 agents, 5 past investigations

# Planning agent tool calls to gather context
2025-08-03 17:49:01,067,p1290668,{tools.py:317},INFO,retrieve_memory called: type=preference, query='user settings communication escalation notification', actor_id=Alice -> Alice, max_results=5
2025-08-03 17:49:01,067,p1290668,{client.py:236},INFO,Retrieving preferences memories: actor_id=Alice, namespace=/sre/users/Alice/preferences, query='user settings communication escalation notification'
```

This shows the planning agent:
1. **Retrieved 10 user preferences** from Alice's preference namespace
2. **Retrieved 50 infrastructure knowledge items** from accumulated agent investigations  
3. **Retrieved 5 past investigations** for similar query patterns
4. **Used retrieve_memory tool** with structured queries to gather context before planning

#### Enhanced Planning Prompt with Memory Context

The planning prompt now uses XML structure for better Claude interaction:

```xml
<memory_retrieval>
CRITICAL: Before creating the investigation plan, you MUST use the retrieve_memory tool to gather relevant context:
1. Use retrieve_memory("preference", "user settings communication escalation notification", "{user_id}", 5)
2. Use retrieve_memory("infrastructure", "[relevant service terms from query]", "sre-agent", 10, null)  
3. Use retrieve_memory("investigation", "[key terms from user query]", "{user_id}", 5, null)
</memory_retrieval>

<planning_guidelines>
After gathering memory context, create a simple, focused investigation plan with 2-3 steps maximum.
Consider user preferences and past investigation patterns from memory.
</planning_guidelines>

<response_format>
MANDATORY: Your response MUST be ONLY valid JSON that matches this exact structure:
{
  "steps": ["Step 1 description", "Step 2 description"],
  "agents_sequence": ["kubernetes_agent", "logs_agent"],
  "complexity": "simple",
  "auto_execute": true,
  "reasoning": "Brief explanation based on retrieved memory context"
}
</response_format>
```

#### Memory-Informed Planning Example

```python
# Enhanced planning prompt includes:
"""
User's query: list kubernetes pods

Retrieved Memory Context:
- User Preferences (10 items): Auto-approval for simple Kubernetes plans, technical detail preference
- Infrastructure Knowledge (50 items): Production namespace layout, pod dependency patterns  
- Past Investigations (5 items): Previous successful pod listing investigations

Create an investigation plan considering this context...
"""
```

The planning agent then creates plans like:
```json
{
  "steps": ["Use Kubernetes agent to retrieve current pod status across all namespaces", "Analyze pod health and resource utilization", "Provide structured technical report with pod details"],
  "agents_sequence": ["kubernetes_agent"],
  "complexity": "simple", 
  "auto_execute": true,
  "reasoning": "Based on user preferences for auto-approval of simple Kubernetes plans and past successful investigations, this is a straightforward pod listing task requiring only the Kubernetes agent"
}
```

## Memory Capture and Pattern Recognition

The SRE Agent automatically captures information during investigations through a sophisticated pattern recognition and structured data conversion process:

### How Memory Capture Works

The SRE agent code (specifically `sre_agent/memory/hooks.py`) uses regex patterns to parse agent responses and extract structured information:

1. **Response Analysis**: After each agent response, the system scans the text for specific patterns
2. **Pattern Matching**: Uses regex to identify key information types
3. **Data Structuring**: Converts matched patterns into structured Pydantic models
4. **Memory Storage**: Calls Amazon Bedrock AgentCore Memory's `create_event()` API to store the structured data

### SRE Agent Pattern Recognition

Every individual agent response triggers automatic memory pattern extraction through the `on_agent_response()` hook. This ensures that valuable information discovered during domain-specific investigations is captured and made available for future use.

### Infrastructure Knowledge Extraction via Agent JSON Responses

The system uses a sophisticated agent-based approach for infrastructure knowledge extraction. Each agent is instructed to include infrastructure knowledge in their responses using structured JSON format:

#### Agent Response Format
```json
{
  "infrastructure_knowledge": [
    {
      "service_name": "web-app-deployment",
      "knowledge_type": "baseline",
      "knowledge_data": {
        "cpu_usage_normal": "75%",
        "memory_usage_normal": "85%",
        "typical_pods": 1,
        "node_distribution": "node-1"
      },
      "confidence": 0.9,
      "context": "Pod status analysis revealed normal resource usage patterns"
    }
  ]
}
```

#### Knowledge Types Captured
- **dependency**: Service relationships and dependencies
- **pattern**: Recurring infrastructure patterns and behaviors  
- **config**: Configuration insights and settings
- **baseline**: Performance baselines and normal operating ranges

#### Automatic Extraction Process
1. **Agent Response Processing**: Each agent response is scanned for JSON blocks containing `infrastructure_knowledge`
2. **JSON Parsing**: The system extracts and validates the JSON structure
3. **Knowledge Storage**: Valid knowledge items are stored in the infrastructure memory namespace
4. **Cross-Session Availability**: Knowledge becomes available for future investigations across all sessions

### Enhanced Agent Response Processing and Logging

#### Comprehensive Response Logging
The system provides detailed logging of agent responses and memory operations:

```log
# From agent.log - Message breakdown logging
2025-08-03 17:45:30,397,p1289365,{agent_nodes.py:347},INFO,Kubernetes Infrastructure Agent - Message breakdown: 1 USER, 1 ASSISTANT, 1 TOOL messages
2025-08-03 17:45:30,397,p1289365,{agent_nodes.py:349},INFO,Kubernetes Infrastructure Agent - Tools called: k8s-api___get_pod_status

# Memory pattern extraction logging  
2025-08-03 17:45:30,398,p1289365,{hooks.py:193},INFO,on_agent_response called for agent: Kubernetes Infrastructure Agent, user_id: Alice
2025-08-03 17:45:30,399,p1289365,{hooks.py:383},INFO,Extracted 5 infrastructure knowledge items from agent response
```

#### Infrastructure Knowledge Validation
The system includes validation and error handling for infrastructure knowledge extraction:

```log
# Successful extraction logging
2025-08-03 17:45:30,401,p1289365,{hooks.py:387},INFO,Saved infrastructure knowledge: web-app-deployment (baseline) with confidence 0.9
2025-08-03 17:45:30,402,p1289365,{hooks.py:387},INFO,Saved infrastructure knowledge: database-pod (pattern) with confidence 0.8
```

#### Automatic Conversation Memory Storage
All agent interactions are automatically stored in conversation memory with message type breakdown:

```log
# Conversation storage with tool tracking
2025-08-03 17:45:30,397,p1289365,{agent_nodes.py:347},INFO,Kubernetes Infrastructure Agent - Message breakdown: 1 USER, 1 ASSISTANT, 1 TOOL messages
2025-08-03 17:45:30,397,p1289365,{agent_nodes.py:349},INFO,Kubernetes Infrastructure Agent - Tools called: k8s-api___get_pod_status
2025-08-03 17:45:30,530,p1289365,{agent_nodes.py:375},INFO,Kubernetes Infrastructure Agent: Successfully stored conversation in memory
```

#### Cross-Session Memory Access
The system provides cross-session memory retrieval for better investigation context:

```log
# Cross-session infrastructure knowledge retrieval
2025-08-03 17:45:30,140,p1289365,{hooks.py:71},INFO,Retrieved infrastructure knowledge for user 'Alice' from 1 different sources: Alice: 50 memories
2025-08-03 17:45:30,140,p1289365,{client.py:245},INFO,Retrieved 50 infrastructure memories for Alice
```

### Memory Capture Methods
1. **Supervisor tool calls**: `retrieve_memory` called during planning; `save_investigation` called via planning agent
2. **Automatic pattern extraction**: Agent responses are processed by `on_agent_response()` hook to extract:
   - User preferences (escalation emails, Slack channels)
   - Infrastructure knowledge (service dependencies, baselines)
   - Calls `_save_*` functions directly (not tool calls)
3. **Manual configuration**: User preferences loaded via `manage_memories.py update`
4. **Conversation storage**: All agent responses and tool calls stored as conversation memory

### Memory Storage Process
1. **Pattern Detection**: SRE agent code identifies relevant information in responses
2. **Data Conversion**: Creates structured objects (UserPreference, InfrastructureKnowledge, etc.)
3. **Event Creation**: Calls `create_event()` with actor_id and structured data
4. **Namespace Routing**: Amazon Bedrock AgentCore Memory automatically routes to correct namespace based on strategy configuration

## Agent Memory Integration

The memory system integrates seamlessly with existing SRE agents:

### Kubernetes Agent
- **Captures:** Service dependencies, deployment patterns, resource baselines
- **Uses:** Past deployment issues, known resource requirements
- **Example Knowledge Captured:**
  ```json
  {
    "service_name": "web-app-deployment",
    "knowledge_type": "baseline",
    "knowledge_data": {
      "cpu_usage_normal": "75%",
      "memory_usage_normal": "85%",
      "typical_pods": 1
    }
  }
  ```

### Logs Agent  
- **Captures:** Common error patterns, log query preferences, resolution strategies
- **Uses:** Similar error patterns, effective log queries from past investigations
- **Example Knowledge Captured:**
  ```json
  {
    "service_name": "payment-service",
    "knowledge_type": "pattern",
    "knowledge_data": {
      "common_errors": ["connection timeout", "memory leak"],
      "effective_queries": ["error AND payment AND timeout"]
    }
  }
  ```

### Metrics Agent
- **Captures:** Performance baselines, alert thresholds, metric correlations  
- **Uses:** Historical baselines, known performance patterns
- **Example Knowledge Captured:**
  ```json
  {
    "service_name": "api-gateway",
    "knowledge_type": "baseline",
    "knowledge_data": {
      "normal_response_time": "200ms",
      "peak_traffic_hours": "14:00-17:00 UTC"
    }
  }
  ```

### Runbooks Agent
- **Captures:** Successful resolution procedures, team escalation paths
- **Uses:** Proven resolution strategies, appropriate runbook recommendations
- **Example Knowledge Captured:**
  ```json
  {
    "service_name": "database",
    "knowledge_type": "dependency",
    "knowledge_data": {
      "escalation_team": "database-team@company.com",
      "recovery_runbook": "DB-001"
    }
  }
  ```

## Manual Memory Management

Memory management is handled through the `manage_memories.py` script:

### Viewing Memories
```bash
# List all memory types
uv run python scripts/manage_memories.py list

# List specific memory type
uv run python scripts/manage_memories.py list --memory-type preferences

# List memories for specific user
uv run python scripts/manage_memories.py list --memory-type preferences --actor-id Alice
```

### Managing User Preferences
```bash
# Load user preferences from YAML configuration
uv run python scripts/manage_memories.py update

# Load from custom configuration file
uv run python scripts/manage_memories.py update --config-file custom_users.yaml
```

## Benefits

- **Personalized Investigations:** Tailors reports and communication to individual user preferences and roles
- **Faster Resolution:** Leverages historical context and past investigation knowledge
- **Knowledge Preservation:** Automatically captures and shares tribal knowledge across team changes
- **Pattern Recognition:** Identifies recurring issues and optimizes escalation routing
- **Reduced MTTR:** Accelerates problem resolution through accumulated institutional knowledge

## Privacy and Data Management

### Data Retention
- User preferences: 90 days (configurable)
- Infrastructure knowledge: 30 days (configurable)  
- Investigation summaries: 60 days (configurable)

## Setting Up Memory System

### Initial Setup

The memory system is automatically initialized during the setup process:

```bash
# Initialize memory system and load user preferences (included in setup instructions)
uv run python scripts/manage_memories.py update
```

This command:
1. Creates a new memory resource if none exists
2. Configures the three memory strategies
3. Loads user preferences from `scripts/user_config.yaml`
4. Stores the memory ID in `.memory_id` for future use

### Adding User Preferences

To add new users or modify existing preferences:

1. Edit `scripts/user_config.yaml` to add new user configurations
2. Run the update command to load new preferences:
```bash
uv run python scripts/manage_memories.py update
```

### Managing Memories

```bash
# List all memory types
uv run python scripts/manage_memories.py list

# List specific memory type
uv run python scripts/manage_memories.py list --memory-type preferences

# List preferences for specific user
uv run python scripts/manage_memories.py list --memory-type preferences --actor-id Alice
```