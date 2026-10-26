# A2A Agent Targets

Attach Agent-to-Agent (A2A) protocol agents to the gateway as HTTP targets.

![arch](../images/agents.png)

A2A is a JSON-RPC 2.0 protocol for agent to agent communication. An A2A client first fetches the agent's **agent card** (a manifest at `/.well-known/agent-card.json` describing the agent's name, capabilities, and skills), then sends work with `message/send`. Fronting an A2A agent with the gateway lets you apply inbound authorization and outbound credential injection without changing the agent.

## Tutorials

| Section                                 | Description                                                  |
| :-------------------------------------- | :----------------------------------------------------------- |
| [agentcore-runtime](agentcore-runtime/) | A2A agent hosted on AgentCore Runtime, attached as an `http.agentcoreRuntime` target with Entra ID inbound auth and OBO token exchange outbound |
| [databricks-apps](databricks-apps/)     | A2A agent hosted on Databricks Apps, fronted via an `http.passthrough` target with Entra ID inbound auth and Databricks service-principal OAuth outbound |
