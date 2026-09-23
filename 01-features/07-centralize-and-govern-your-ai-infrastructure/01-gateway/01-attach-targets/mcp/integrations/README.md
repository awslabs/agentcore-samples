# gateway Target Integrations

Amazon Bedrock AgentCore provides built-in templates from integration providers that you can add as targets in your gateway. These templates expose third-party service APIs as MCP tools without requiring you to build or host custom servers.

## Supported Providers

> [!IMPORTANT]
> For the most up-to-date list of supported providers, check the [official documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-integrations.html).

| Provider | Outbound Auth | Description |
| :--- | :--- | :--- |
| Amazon Bedrock runtime | IAM | Converse, InvokeModel, ApplyGuardrail |
| Agents for Amazon Bedrock runtime | IAM | InvokeAgent, Retrieve, RetrieveAndGenerate |
| Amazon CloudWatch | IAM | DescribeAlarms, GetMetricData, ListMetrics |
| Amazon DynamoDB | IAM | GetItem, PutItem, Query, Scan |
| Asana | OAuth | Tasks, projects, workspaces |
| BambooHR | API Key | Employee data, time off, directory |
| Brave Search | API Key | Web search, local search |
| Coinbase x402 Bazaar | API Key | Payment-gated API access |
| Confluence | OAuth | Pages, spaces, search |
| Jira | OAuth | Issues, projects, boards |
| Microsoft Exchange | OAuth | Email, calendar, contacts |
| Microsoft OneDrive | OAuth | Files, folders, sharing |
| Microsoft SharePoint | OAuth | Sites, lists, documents |
| Microsoft Teams | OAuth | Messages, channels, meetings |
| PagerDuty | API Key | Incidents, services, escalations |
| Salesforce | OAuth | Objects, queries, records |
| ServiceNow | OAuth | Incidents, catalog, knowledge |
| Slack | OAuth | Messages, channels, users |
| Smartsheet | OAuth | Sheets, rows, columns |
| Tavily Search | API Key | AI-optimized web search |
| Zendesk | OAuth | Tickets, users, organizations |
| Zoom | OAuth | Meetings, users, recordings |

> [!NOTE]
> Integration provider templates can only be added as targets through the AWS Management Console (not the API). AgentCore does not host any servers natively — you must set up server hosting yourself.

## Documentation

- [Built-in templates from integration providers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-integrations.html)
- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [gateway Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-outbound-auth.html)
