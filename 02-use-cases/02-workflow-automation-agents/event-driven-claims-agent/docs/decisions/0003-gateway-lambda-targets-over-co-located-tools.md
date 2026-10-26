# ADR-0003: Gateway Lambda Targets Over Co-Located Tools

**Status:** Accepted  
**Date:** 2025-06-17

## Context

Tools can be implemented in two ways:
1. **Co-located (`@tool` decorators):** Python functions defined inside the Runtime container, directly callable by the Strands agent without any network hop.
2. **Gateway targets (Lambda functions):** Separate Lambda functions registered with the MCP Gateway, called via HTTP through the Gateway.

The choice affects latency, deployment complexity, and what AgentCore features are available.

## Decision

Tools are separate Lambda functions behind the AgentCore Gateway, not co-located `@tool` decorators in the Runtime container.

## Reasoning

Three reasons, in priority order:

1. **Cedar Policy Engine requires a Gateway.** Cedar policies evaluate before each tool call. Without a Gateway, there's no policy enforcement point — the agent could call any tool unconditionally. This sample's $100k blocking policy is the primary Cedar demonstration.

2. **Independent scaling and deployment.** Lambda tools scale independently from the Runtime container. A bug fix in `notification/handler.py` doesn't require rebuilding the Docker image and redeploying the Runtime. Each tool is a 30-line Python function that deploys in seconds.

3. **Learning objective.** This sample's purpose is to teach the full AgentCore Gateway feature set: MCP protocol, semantic tool discovery, WorkloadIdentity (agent-as-principal), and Cedar enforcement. Co-located tools would demonstrate Strands SDK usage but skip the Gateway entirely.

## Alternatives Considered

- **Co-located `@tool` decorators:** Simpler to develop (no separate Lambda, no schema file, no CDK wiring). Lower latency (no Gateway round-trip). Appropriate when Cedar enforcement is not needed and tools are tightly coupled to the agent. But does not demonstrate Gateway, MCP, or Cedar.

## Consequences

- **More moving parts:** 6 Lambda functions + 6 JSON schemas + CDK wiring instead of 6 Python functions in `main.py`
- **Added latency:** Each tool call adds a Gateway round-trip (~50-100ms)
- **Schema maintenance:** keep `lambdas/schemas/` in sync with Lambda handler parameters so the agent always sees the full tool signature — add a field to the handler, add it to the schema
- **Independent deploys:** Tool changes don't require container rebuilds
