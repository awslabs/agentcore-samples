# Intelligent Event Agent (retired)

The old notebook recommended sessions from the **2024 AWS re:Invent catalog**. It was a conversational retrieval demo, not an event-driven automation agent. A direct port of its Starter Toolkit deployment would still index a historical schedule and duplicate the repository's retrieval, personalization and hosting walkthroughs.

Choose an example by what you need to learn:

- **Build a Bedrock Knowledge Base backed by S3 Vectors:** the [IT incident response example](../it-incident-response-agent/README.md#add-a-knowledge-base) ingests runbooks and uses them to investigate incidents. It demonstrates the retrieval architecture, not a conference planner.
- **Remember preferences across conversations:** the [Strands customer-support Memory example](../../../01-features/04-manage-context-of-your-agent/memory/02-long-term-memory/examples/single-agent/with-strands-agent/01-built-in-hook/customer-support/) uses the built-in semantic and user-preference strategies.
- **Build a personalized planning application:** the [travel concierge blueprint](../../../05-blueprints/travel-concierge-agent/) covers itineraries, tool access and Cognito authentication. It is a larger application, not a replacement dataset for conference sessions.

**Already ran the notebook?** Its removal does not delete resources or stop charges. Consult the [historical source](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/02-use-cases/02-workflow-automation-agents/intelligent-event-agent) and your original account, Region and deployment records. Identify the Runtime(s), ECR repository, Memory, Knowledge Base/data source, S3/vector storage, Cognito pool and IAM roles you actually created. Do not detach policies, empty buckets, or force-delete repositories by name in a shared account. Reconcile ownership and retention with the resource owner before removal.
