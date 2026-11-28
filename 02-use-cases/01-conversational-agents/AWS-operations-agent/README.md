# AWS Operations Agent (retired)

This sample is no longer distributed for new deployments. It used the deprecated Starter Toolkit and a model with a general AWS operations tool behind a broad read-only role. For a new agent, expose only the operations it needs. Start with the example that matches your goal:

| I want to… | Example |
|---|---|
| Triage an incident and look up the responsible service or runbook | [DevOps triage copilot](../multi-agent-devops-triage-typescript/): its runbook worker calls a service-catalog Lambda through AgentCore Gateway using IAM. |
| Authenticate incoming requests with Okta | [Inbound authentication with Okta](../../../01-features/05-authenticate-and-authorize/01-inbound-auth/03-inbound-auth-okta/). |
| Compare HTTP hosting approaches | See the [`BedrockAgentCoreApp` agent](../../../01-features/02-host-your-agent/01-runtime/01-hosting-agents/01-http-protocol/01-strands-bedrock/agent.py) and the [plain FastAPI `/ping` and `/invocations` implementation](../../../01-features/02-host-your-agent/01-runtime/05-measure-your-runtime/base-agent/agent.py). |

These examples teach individual capabilities; none is a drop-in replacement for querying inventory and costs across an AWS account.

**Already deployed the old sample?** Removing these files does not stop running resources or charges. Use your original deployment records and [the historical source](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/02-use-cases/01-conversational-agents/AWS-operations-agent) to identify your account, Region, stacks and resource IDs. Do not run the legacy bulk cleanup in a shared account: it includes options to delete unrelated gateways and identities. Remove only resources you can confirm belong exclusively to your deployment; retain shared roles, providers and Okta applications.
