# Advanced AgentCore runtime Capabilities

Beyond basic agent and tool hosting, AgentCore runtime provides advanced capabilities for production workloads.

## Tutorials

| # | Tutorial | What It Demonstrates | Format |
|:--|:---------|:--------------------|:-------|
| 01 | [Streaming Responses](01-streaming-responses/) | Stream partial results in real time via SSE | deploy/invoke/cleanup |
| 02 | [Session Management](02-session-management/) | Maintain context across invocations, session isolation | deploy/invoke/cleanup |
| 03 | [Bidirectional Streaming](03-bidirectional-streaming/) | WebSocket-based real-time voice agents | Reference (Docker-based) |
| 04 | [Async Agents](04-async-agents/) | Long-running background tasks with async task tracking | Notebooks |
| 05 | [Execute Commands](05-execute-commands/) | Run shell commands inside runtime sessions | deploy/invoke/cleanup |
| 06 | [Multi-Agent](06-multi-agent/) | Orchestrate multiple agents across runtimes | deploy/invoke/cleanup |
| 07 | [Persistent Filesystems](07-persistent-filesystems/) | Persist files across session stop/resume cycles | deploy/invoke/cleanup |
| 08 | [Connect to VPC Resources](08-connect-to-vpc-resources/) | Deploy agents in your VPC for private resource access | CDK (TypeScript) |
| 09 | [MCP End-to-End](09-mcp-e2e/) | Full MCP feature set: tools, resources, prompts, sampling, elicitation, progress | Notebooks |
| 10 | [MCP Dynamic Client Registration](10-mcp-dynamic-client-registration/) | Auth0 OAuth + DCR for MCP server authentication | deploy/invoke/cleanup |
| 11 | [Middleware Support](11-middleware-support/) | Starlette middleware for observability and error handling | deploy/invoke/cleanup |
| 12 | [Egress-Controlled Code Execution](12-egress-coding-execution/) | Sandbox untrusted code in the microVM with broker-mediated, allowlisted egress | Reference (Docker-based) |

## Tutorial Formats

### deploy/invoke/cleanup (self-contained Python scripts)

These tutorials follow the standard pattern:

```bash
python deploy.py     # Deploy to AgentCore runtime
python invoke.py     # Run the demo
python cleanup.py    # Tear down all resources
```

### Notebooks

Complex tutorials with multi-step workflows, multiple AWS services, or interactive elements use Jupyter notebooks with step-by-step instructions.

### Reference / CDK

Some tutorials require Docker containers (WebSocket servers) or CDK infrastructure (VPC). These have their own deployment mechanisms documented in their READMEs.

## Key Concepts at a Glance

| Capability | Key API / Parameter | When to Use |
|:-----------|:-------------------|:------------|
| **Streaming** | `accept="text/event-stream"` on `invoke_agent_runtime` | Real-time token display |
| **Sessions** | `runtimeSessionId` on `invoke_agent_runtime` | Multi-turn conversations |
| **Session timeouts** | `lifecycleConfiguration` on `create_agent_runtime` | Control session lifetime |
| **Command execution** | `invoke_agent_runtime_command` | Debugging, package installation |
| **Persistent storage** | `filesystemConfigurations` on `create_agent_runtime` | Files that survive session restarts |
| **Multi-agent** | `environmentVariables` + `invoke_agent_runtime` as tool | Specialist agent orchestration |
| **VPC mode** | `networkConfiguration.networkMode: "VPC"` | Private resource access |
| **Middleware** | `BedrockAgentCoreApp(middleware=[...])` | Cross-cutting concerns (logging, auth) |
| **Async tasks** | `app.add_async_task()` / `app.complete_async_task()` | Long-running background work |
| **MCP auth** | `authorizerConfiguration` + Auth0 DCR | OAuth-protected MCP servers |
