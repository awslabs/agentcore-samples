# Built-in connectors

Connectors are pre-built, fully-managed MCP integrations you attach to a gateway with a `connectorId`. The gateway handles schema management, parameter governance, endpoint resolution, and service authentication. Agents discover the connector's tools with a standard `tools/list` call and invoke them like any MCP tool. No third-party APIs to provision and no outbound credentials to manage.

![arch](./images/architecture.png)

## Knowledge layers for agents

An agent is only as good as the information it can reach. Connectors give your agents access to the knowledge they need across distinct layers, each exposed through the same gateway and the same MCP interface.

### Organizational knowledge: Amazon Bedrock Managed Knowledge Base

Your most valuable information is scattered across SharePoint, Google Drive, Confluence, Amazon S3, and internal wikis. Making it available to agents has traditionally required building custom ingestion pipelines, tuning retrieval, and maintaining data freshness over time, which is months of engineering before an agent can answer a basic question about your own business.

Bedrock Managed Knowledge Base on AgentCore replaces that work. You connect your unstructured data sources and AgentCore manages the vector store, the embedding and re-ranking models used during retrieval, and scalability concerns like rate limits, so your team can focus on building agents rather than operating pipelines. At its core is an agentic retriever that goes beyond traditional RAG: instead of matching a query to the closest chunks, it plans queries across your knowledge bases, connects related concepts across documents, evaluates intermediate results, and re-ranks before answering. For complex, multi-part queries that span several topics at once, agentic retrieval surfaces broader and more complete coverage than basic retrieval. See the [Managed Knowledge Base tutorial](managed-kb/) to attach it to your gateway.

### World knowledge: Web Search

Internal knowledge has gaps. Regulations change, markets shift, and competitors launch new products constantly. For research, fact-checking, customer service, and market intelligence, agents need to understand what is happening outside your organization.

Web Search gives agents information from the web while keeping data within your secured AWS environment. Built on the same search infrastructure from Amazon that powers Alexa+, Amazon Quick Suite, and Kiro, it is optimized for agentic retrieval, returning high-value excerpts that deliver high intelligence per token. It takes a multi-source grounding approach, combining public web information with Amazon's proprietary knowledge graph for structured entity data, verified facts, and real-time information like stock prices and sports scores. Queries stay within your AWS security and compliance boundary, with no extra vendor to onboard and none of the orchestration, authentication, and billing workflows that come with one. See the [Web Search tutorial](websearch/) to attach it to your gateway.

> "At Sony, we're building an enterprise AI agent platform on AgentCore where teams across business units can develop, share, and reuse AI agents, from knowledge assistants to workflow automation agents, each tailored to their needs. Our enterprise knowledge is distributed across repositories such as SharePoint, Confluence, and Amazon S3, and includes complex documents such as PDFs, presentations, and spreadsheets with charts and tables. Now that Bedrock Managed Knowledge Base and Web Search are available in AgentCore, we can equip agents with advanced retrieval and live web grounding with a consistent governance model, without building these capabilities from scratch. This accelerates our vision of transforming how people work, with AI as a catalyst, at scale."
>
> Masahiro Oba, Senior General Manager, Sony Group Corporation

### Paid knowledge: AgentCore payments and AWS WAF AI traffic monetization

The best information is not always free. Financial market feeds, licensed research, proprietary datasets, and premium APIs all sit behind a paywall. If an agent cannot access paid resources, it returns a suboptimal answer and the user never knows what was missed.

Accessing paid content takes two parts: agents need a way to pay, and providers need a way to get paid. AgentCore payments handles the agent side, letting agents discover paid services and content, access them, and pay within their execution loop. AWS WAF AI traffic monetization handles the provider side, giving content owners the ability to control agent access: block it, allow it, or get paid for it. Because both capabilities run on the same platform, providers using WAF automatically recognize agents verified on AgentCore. The result is a trusted channel: lower friction for verified agents and compensation for providers, building the infrastructure for both sides of the agent economy so agents can reach everything, not just what happens to be free. See the [paid knowledge tutorial](paid-knowledge/) for the walkthrough.

## Connector target configuration

Every connector is attached the same way: a `create_gateway_target` call with an `mcp.connector` target configuration. Connector targets support only the `GATEWAY_IAM_ROLE` credential provider type; the gateway signs the backend call with its execution role, so there are no outbound secrets to manage.

```python
import boto3

gateway_client = boto3.client("bedrock-agentcore-control", region_name="<REGION>")

gateway_client.create_gateway_target(
    name="<target-name>",
    gatewayIdentifier="<GATEWAY_ID>",
    targetConfiguration={
        "mcp": {
            "connector": {
                "source": {"connectorId": "<connector-id>"},
                "configurations": [
                    {
                        "name": "<ToolName>",
                        "parameterValues": { },
                        "parameterOverrides": [ ],
                    }
                ],
            }
        }
    },
    credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
)
```

### Parameters

| Parameter | Where | What it does |
| :-- | :-- | :-- |
| `connectorId` | `connector.source` | Selects the built-in connector to attach. For example, `web-search` for Web Search and `bedrock-knowledge-bases` for Managed Knowledge Base. |
| `configurations` | `connector` | A list with one entry per tool the connector exposes. Each entry is matched to a tool by `name` and shapes how that tool behaves for your agents. |
| `name` | each `configurations` entry | The connector tool this entry configures, for example `WebSearch`, or `AgenticRetrieveStream` and `Retrieve` for Managed Knowledge Base. |
| `parameterValues` | each `configurations` entry | Administrator-set values the gateway applies on every call. The agent does not supply these. Use them to bind required inputs (such as a `knowledgeBaseId`) and pin governance defaults (such as a result count or a domain denylist). |
| `parameterOverrides` | each `configurations` entry | Controls how individual request fields are exposed to the agent at runtime. |

Each `parameterOverrides` entry targets one field and accepts:

| Field | What it does |
| :-- | :-- |
| `path` | A JSON Pointer to the request field, for example `/maxResults` or `$.retrievalConfiguration.managedSearchConfiguration.numberOfResults`. |
| `description` | Optional text shown to the agent describing the field. |
| `visible` | `true` exposes the field so the agent can see and set it; `false` hides it while still sending any administrator-configured default. |

In short, `parameterValues` is what the gateway fixes for the agent, and `parameterOverrides` is what the agent can see and set. Together they let you take a connector tool and govern it for your use case. Each tutorial below shows the `configurations` block for that connector.

## Tutorials

| Section                       | Description                                                                  |
| :---------------------------- | :--------------------------------------------------------------------------- |
| [websearch](websearch/)       | Web Search Tool: managed, MCP-compliant live web search for agents           |
| [managed-kb](managed-kb/)     | Amazon Bedrock Managed Knowledge Base: agentic retrieval over your data (coming soon) |
| [paid-knowledge](paid-knowledge/) | AgentCore payments and AWS WAF AI traffic monetization (coming soon)     |

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
