# Stateful MCP Examples

End-to-end tutorials demonstrating MCP (Model Context Protocol) server and client features on Amazon Bedrock AgentCore Runtime.

## MCP Feature Support in AgentCore Runtime

<table>
  <thead>
    <tr>
      <th>Category</th>
      <th>Feature</th>
      <th>Spec Methods</th>
      <th align="center">Runtime</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td rowspan="4"><strong>MCP Server Features</strong></td>
      <td>Tools</td>
      <td><code>tools/list</code>, <code>tools/call</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Tools (output schema)</td>
      <td><code>output schema</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Resources</td>
      <td><code>resources/list</code>, <code>resources/read</code>, <code>resources/subscribe</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Prompts</td>
      <td><code>prompts/list</code>, <code>prompts/get</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td rowspan="3"><strong>MCP Client Features</strong></td>
      <td>Sampling</td>
      <td><code>sampling/createMessage</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Roots</td>
      <td><code>roots/list</code></td>
      <td align="center">TBD</td>
    </tr>
    <tr>
      <td>Elicitation</td>
      <td><code>elicitation/create</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td rowspan="2"><strong>MCP Base Protocol</strong></td>
      <td>Lifecycle</td>
      <td><code>initialize</code>, <code>initialized</code>, <code>ping</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Transports</td>
      <td><code>response streaming</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td rowspan="4"><strong>MCP Utilities</strong></td>
      <td>Progress</td>
      <td><code>notifications/progress</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Cancellation</td>
      <td><code>notifications/cancelled</code></td>
      <td align="center">TBD</td>
    </tr>
    <tr>
      <td>Logging</td>
      <td><code>logging/setLevel</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Tasks</td>
      <td><code>tasks/list</code>, <code>tasks/cancel</code></td>
      <td align="center">✅</td>
    </tr>
  </tbody>
</table>

> **Legend:** ✅ Supported &nbsp;|&nbsp; TBD To Be Determined

## Project Structure

```
Stateful/
├── 1-server-e2e/          # MCP Server features (Tools, Resources, Prompts)
├── 2-client-e2e/          # MCP Client features (Elicitation, Sampling, Roots)
├── 3-utilities-e2e/       # MCP Utilities (Progress Notifications)
└── helpers/               # Shared utilities for AWS services and deployment
```

### 1. MCP Server Features (`1-server-e2e/`)

Complete tutorial demonstrating how to build and deploy an MCP server with all three core capabilities:

- **Tools**: Executable functions for expense tracking (add, list, get transactions)
- **Resources**: Dynamic expense reports exposed as readable resources
- **Prompts**: Pre-defined templates for expense analysis and categorization

**Tutorial:** [📓 mcp_server_features_e2e.ipynb](./01-server-e2e/mcp_server_features_e2e.ipynb)

**Includes:**
- Deployment to AgentCore Runtime
- DynamoDB integration for persistent storage
- Cognito authentication setup
- Real-world expense tracking example

### 2. MCP Client Features (`2-client-e2e/`)

Demonstrates client-side MCP capabilities for advanced stateful interactions:

- **Elicitation**: Multi-turn interactive user input collection (e.g., guided expense entry)
- **Sampling**: Server delegates LLM inference to client for AI-powered analysis
- **Roots**: Client exposes file system roots to server (limited Runtime support)

**Tutorial:** [📓 mcp_client_features_e2e.ipynb](./02-client-e2e/mcp_client_features_e2e.ipynb)


### 3. MCP Utilities (`3-utilities-e2e/`)

Tutorials for MCP utility features that enhance user experience:

- **Progress Notifications**: Real-time execution updates during long-running operations

**Tutorial:** [📓 01_progress.ipynb](./03-utilities-e2e/01_progress.ipynb)

**Demonstrates:**
- Fire-and-forget progress updates (vs request/response like elicitation/sampling)
- 5-step monthly financial report with live progress bar
- `ctx.report_progress()` for streaming execution status
- Custom `progress_handler` callback in client

### 4. Shared Utilities (`helpers/`)

Common utilities used across tutorials:

- `utils.py`: AWS service helpers (Cognito, IAM, DynamoDB)
- `dynamo_utils.py`: DynamoDB operations for finance tracking

**Usage in notebooks:**
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))

from helpers.utils import get_or_create_cognito_pool
from helpers.dynamo_utils import FinanceDB
```

## Prerequisites

- AWS CLI configured with appropriate permissions
- Python 3.12+ (3.13 recommended for Runtime deployments)
- Jupyter Notebook environment
- Access to Amazon Bedrock AgentCore Runtime
- AWS services: DynamoDB, Cognito, IAM


**AgentCore Runtime:**
- Full authentication with Cognito
- Managed infrastructure and scaling


## Resources

- [MCP Specification](https://modelcontextprotocol.io/specification/2025-11-25/server)
- [AWS Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
