# AWS Agent Registry samples

Registry lets publishers describe capabilities, administrators govern approval, and consumers search discoverable records. Publishing metadata is not permission to invoke a tool. Choose an example for the part of the lifecycle you need:

| Path | Lesson |
|---|---|
| [Registry end to end](01-registry-end-to-end/) | Publisher and administrator personas, records, approval and search |
| [End to end with OAuth](02-registry-end-to-end-oauth/) | Cognito JWT authentication |
| [Publish existing MCP and A2A capabilities](03-advanced/publish-agentcore-tools-in-registry/) | Create a draft from a reviewed descriptor, without deploying its source |
| [Synchronize an MCP endpoint](03-advanced/registry-synchronize-mcpserver/) | Public endpoint or existing IAM/OAuth-protected source |
| [Discover approved MCP and A2A records](03-advanced/discovery-and-invocation-at-runtime/) | Search from application code without sending credentials to discovered destinations |
| [Consumer semantic search](03-advanced/consumer-discovery-semantic-search/) | Compare queries over an e-commerce catalog |
| [Admin approval workflow](03-advanced/admin-approval-workflow/) | Event-driven approvals and notifications |
| [Custom metadata](03-advanced/custom-metadata-in-registry/) | Schema validation, automated approval and filtered search |
| [Push synchronization](03-advanced/registry-push-sync-lambda/) | Refresh records on Runtime updates |
| [Dynamic skill discovery](03-advanced/registry-skills-dynamic-discovery/) | Discover agent skill records |
| [Kiro publisher workflow](03-advanced/kiro/kiro-power-publisher-workflow/) | Manage records from Kiro |
| [Kiro and Auth0](03-advanced/kiro-registry-dcr-auth0/) | Dynamic client registration |
| [Strands MCP on ECS](03-advanced/strands-mcp-ecs-registry/) | Containerized MCP example |
| [Namespace migration](04-migrate-to-new-namespace/) | Migrate preview records to the new namespace |

Use the prerequisites and cleanup instructions in the selected sample. Control-plane and consumer operations use different clients: `agent-registry-control` manages records; `agent-registry` searches discoverable records. Don't install a repository-wide dependency bundle or assume approval makes an endpoint trusted for your credentials.
