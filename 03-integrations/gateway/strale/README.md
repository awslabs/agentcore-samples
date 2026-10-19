# Connecting External MCP Servers to AgentCore: Strale Data Capabilities

> [!IMPORTANT]
> This is an educational example. Review the code and understand the resources created before running in your AWS account.

## Overview

This example connects [Strale](https://strale.dev) — a catalog of data capabilities for AI agents — to Amazon Bedrock AgentCore Gateway as a remote MCP server. Once connected, any agent using the gateway can validate IBANs, look up companies across 27 country registries, screen against sanctions lists, extract structured data from URLs, and more.

Strale's MCP server uses Streamable HTTP transport at `https://api.strale.io/mcp`. AgentCore's semantic tool search makes capabilities discoverable by description, not just by name.

| Information          | Details                                    |
|:---------------------|:-------------------------------------------|
| Use case type        | Integration — External MCP Server          |
| Agent type           | Strands Agent with remote MCP tools        |
| Use case components  | Gateway, Identity, Tools                   |
| Use case vertical    | Financial Services / Compliance            |
| Example complexity   | Easy                                       |
| SDK used             | boto3 + strands-agents + mcp               |

## Architecture

```
┌──────────────┐     ┌─────────────────────┐     ┌──────────────────┐
│              │     │  AgentCore Gateway   │     │  Strale MCP      │
│  Your Agent  │────▶│  (Semantic Search)   │────▶│  api.strale.io   │
│  (Strands)   │     │                     │     │  catalog tools   │
└──────────────┘     └─────────────────────┘     └──────────────────┘
       │                      │                          │
       │ Claude Sonnet        │ JWT Auth                 │ Bearer Token
       │                      │ Tool Discovery           │ Quality Scores
       ▼                      ▼                          ▼
  Natural language      Auto-discovers 8          Validated data with
  compliance queries    Strale meta-tools         provenance + audit trail
```

The agent asks questions in natural language. AgentCore's semantic search finds the right Strale tool. Strale executes the capability and returns structured JSON with a quality score, data provenance, and an audit trail.

## Prerequisites

- AWS account with Bedrock AgentCore access (us-east-1)
- Python 3.10+
- Strale API key — sign up at [strale.dev/signup](https://strale.dev/signup) (free EUR 2.00 trial credits, no card required)

## Quick Start

### 1. Install dependencies

```bash
pip install boto3 strands-agents mcp
```

### 2. Set environment variables

```bash
export AWS_REGION=us-east-1
export STRALE_API_KEY=sk_live_your_key_here
```

### 3. Run the example

```bash
python agent-example.py
```

This creates an AgentCore Gateway, registers Strale's MCP server as a target, and provides a gateway URL for connecting agents.

## Step-by-Step

### Create the Gateway

```python
import boto3

client = boto3.client('bedrock-agentcore-control', region_name='us-east-1')

response = client.create_gateway(
    name='strale-compliance-gateway',
    roleArn=role_arn,
    protocolType='MCP',
    protocolConfiguration={
        'mcp': {
            'supportedVersions': ['2025-03-26'],
            'searchType': 'SEMANTIC'
        }
    },
    description='Gateway with Strale data capabilities'
)

gateway_id = response['gatewayId']
gateway_url = response['gatewayUrl']
```

### Register Strale as a Remote Target

```python
# Store your Strale API key as a credential provider
provider = client.create_api_key_credential_provider(
    name='strale-api-key',
    apiKey=os.environ['STRALE_API_KEY'],
)

# Register the MCP server
client.create_gateway_target(
    gatewayIdentifier=gateway_id,
    name='strale-mcp-target',
    targetConfiguration={
        'mcp': {
            'mcpServer': {
                'endpoint': 'https://api.strale.io/mcp'
            }
        }
    },
    credentialProviderConfigurations=[{
        'credentialProviderType': 'API_KEY',
        'credentialProvider': {
            'apiKeyCredentialProvider': {
                'providerArn': provider['credentialProviderArn'],
                'credentialParameterName': 'Authorization',
                'credentialPrefix': 'Bearer ',
                'credentialLocation': 'HEADER',
            }
        }
    }]
)
```

After registration, AgentCore automatically discovers Strale's 8 meta-tools:

| Tool | Description |
|------|-------------|
| `strale_search` | Browse the capability catalog by keyword |
| `strale_execute` | Run any capability by slug |
| `strale_trust_profile` | Check Strale Quality Score (SQS) before calling |
| `strale_balance` | Check wallet balance |
| `strale_ping` | Health check |
| `strale_getting_started` | Free capabilities with examples |
| `strale_methodology` | Quality scoring methodology |
| `strale_transaction` | Retrieve past execution records |

### Connect an Agent

```python
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient

def create_transport():
    return streamablehttp_client(
        gateway_url,
        headers={"Authorization": f"Bearer {access_token}"}
    )

mcp_client = MCPClient(create_transport)
model = BedrockModel(model_id="us.anthropic.claude-sonnet-4-20250514-v1:0")

with mcp_client:
    tools = mcp_client.list_tools_sync()
    agent = Agent(model=model, tools=tools)

    # The agent can now use any Strale capability
    agent("Validate IBAN DE89370400440532013000")
    agent("Look up the Swedish company with org number 5591674668")
    agent("Is the pep-check capability reliable enough for production?")
```

### Use Semantic Search

AgentCore's gateway supports semantic tool search. Agents can discover the right Strale capability without knowing the exact slug:

```bash
curl -X POST $GATEWAY_URL \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "x_amz_bedrock_agentcore_search",
      "arguments": {"query": "validate a European bank account number"}
    },
    "id": 1
  }'
```

This returns `strale_execute` as a match, with `iban-validate` as the suggested slug.

## Free Tier

Five Strale capabilities work without an API key (10 calls/day):

- `email-validate` — verify email deliverability
- `iban-validate` — validate international bank account numbers
- `dns-lookup` — DNS records for any domain
- `url-to-markdown` — convert any URL to markdown
- `json-repair` — fix malformed JSON

## Cleanup

```bash
python agent-example.py
# Follow the prompts to delete the gateway and target
```

Or manually:

```python
client.delete_gateway_target(
    gatewayIdentifier=gateway_id,
    targetIdentifier=target_id
)
client.delete_gateway(gatewayIdentifier=gateway_id)
```

## Links

- [Strale Documentation](https://strale.dev/docs)
- [Capability Catalog](https://api.strale.io/v1/capabilities)
- [AgentCore Gateway Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-gateway.html)
- [Strale MCP Server on npm](https://www.npmjs.com/package/strale-mcp)

## License

Apache-2.0
