# HTTP Agent Targets

Attach generic HTTP agents to the gateway, either hosted on AgentCore Runtime or fronted as a third-party HTTP endpoint.

![arch](../images/agents.png)


The gateway fronts an HTTP agent as an `http.agentcoreRuntime` or `http.passthrough` target, applying inbound authorization (Microsoft Entra ID) and outbound credential injection (OBO token exchange, or header propagation of the caller's own key) without changing the agent. Path-based routing forwards `{GATEWAY_URL}/{targetName}/{path}` to the agent's endpoint.

## Tutorials

| Section                                         | Description                                                |
| :---------------------------------------------- | :--------------------------------------------------------- |
| [claude-managed-agents](claude-managed-agents/) | Front Claude Managed Agents on `api.anthropic.com` through an `http.passthrough` CUSTOM target, with Entra ID inbound auth and header propagation of the caller's `x-api-key` outbound |
| [http-runtime-agents](http-runtime-agents/)     | Attach an HTTP agent hosted on AgentCore Runtime as an `http.agentcoreRuntime` target, with Entra ID inbound auth and OBO token exchange outbound |
