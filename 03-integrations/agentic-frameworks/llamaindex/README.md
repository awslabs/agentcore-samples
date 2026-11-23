# LlamaIndex on AgentCore: choose a walkthrough

- **Give an agent access to past context:** the [LlamaIndex long-term Memory examples](../../../01-features/04-manage-context-of-your-agent/memory/02-long-term-memory/examples/single-agent/with-llamaindex-agent/) show how Memory participates in the agent workflow.
- **Trace a LlamaIndex agent you host yourself:** the [observability example](./observability/) instruments a `FunctionAgent` and exports traces to CloudWatch. It does not deploy a managed Runtime.
- **Deploy and invoke a managed Runtime:** start with [Getting started](../../../00-getting-started/). This is a deployment walkthrough, not a LlamaIndex finance example.

## Historical finance assistant

The previous [source and walkthrough](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/agentic-frameworks/llamaindex) combined an OpenAI model, Yahoo Finance tools and arithmetic. Its deployment instructions passed a provider key on the command line. The maintained paths above teach Memory, tracing and Runtime deployment separately; none provides stock quotes or reproduces that financial assistant.

Existing deployments are unaffected by this source change. If you deployed the historical example, identify your specific Runtime and its deployment-created resources from your records before removing them. Do not delete a shared role or image repository based on its name. Rotate an OpenAI key if it may have been saved in shell history or logs.
