# Lab Structure Template

Use this template when creating new AgentCore Gateway tutorials. Each tutorial follows the same structure so users can navigate consistently.

## Conventions

### Deployment priority

1. **AgentCore CLI** (`agentcore add`, `agentcore deploy`): primary method for MCP servers, gateways, targets, credentials
2. **CDK (TypeScript)**: for infrastructure not supported by the CLI
3. **boto3 / AWS CLI**: for resources not supported by CDK (e.g., CloudFormation stacks, one-off API calls)

Before using `agentcore add gateway` or `agentcore add gateway-target`, check whether the feature you need is supported by the CLI. If not, use boto3 (`create_gateway` / `create_gateway_target`) instead.

### Features not yet supported by AgentCore CLI

The following gateway and target features require **boto3** (`bedrock-agentcore-control` client):

| Feature | Resource | boto3 API |
| :-- | :-- | :-- |
| Streaming configuration (`enableResponseStreaming`) | Gateway | `create_gateway` / `update_gateway` |
| Session configuration (`sessionTimeoutInSeconds`) | Gateway | `create_gateway` / `update_gateway` |
| Lambda interceptors | Gateway | `create_gateway` |
| `mcp.supportedVersions`, `mcp.instructions` | Gateway | `create_gateway` |
| `kmsKeyArn` | Gateway | `create_gateway` |
| Dynamic listing (`listingMode='DYNAMIC'`) | Target | `create_gateway_target` |
| Resource priority (`resourcePriority`) | Target | `create_gateway_target` |
| Header/query propagation (`metadataConfiguration`) | Target | `create_gateway_target` |
| `toolOverrides` for API Gateway targets | Target | `create_gateway_target` |
| `GATEWAY_IAM_ROLE` credential type | Target | `create_gateway_target` |
| `API_KEY` credential type | Target | `create_gateway_target` |
| 3LO auth code flow (outbound) | Target | `create_gateway_target` |
| VPC egress support | Target | `create_gateway_target` |
| Token exchange | Target | `create_gateway_target` |
| HTTP targets | Target | `create_gateway_target` |
| Gateway rules | Gateway | `create_gateway_rule` |
| Resource-based policy | Gateway | `put_resource_policy` |

For these features, use `GatewayBoto3Client` from `gatewaylabproject/gateway_admin.py` or boto3 scripts in `gatewaylabproject/scripts/<tutorial>/`. The MCP server deployment (`agentcore add agent` + `agentcore deploy`) still uses the CLI.

> [!IMPORTANT]
> Do not mention CLI limitations in user-facing READMEs. Just use the appropriate tool (CLI or boto3) without explaining why.

Reference:

- [create_gateway](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/create_gateway.html)
- [create_gateway_target](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/create_gateway_target.html)

### API Gateway targets: operationId caveat

API Gateway does not preserve `operationId` in exported specs. When AgentCore Gateway reads the spec from a deployed API Gateway, the `operationId` field is missing. This means:

- The CLI's `--type api-gateway` will fail unless every operation in the filtered paths has an `operationId` in the original spec
- Since API Gateway drops `operationId`, you must use boto3 with `toolOverrides` to name the tools
- Use `deploy_targets.py` scripts with `GatewayBoto3Client` for API Gateway targets

### IAM permissions for API Gateway targets

When API Gateway targets use `GATEWAY_IAM_ROLE` credential type, the gateway's IAM role needs `execute-api:Invoke` permission. If targets are created via boto3 (outside CDK), CDK doesn't add this permission automatically. The `deploy_targets.py` script should add the permission to the gateway role, or document that the user needs to add it manually.

### Single working directory

All user-facing commands run from `gatewaylabproject/`. Users navigate there once and never leave. READMEs stay in the tutorial directories (they are documentation, not code), but every code block assumes `gatewaylabproject/` as the working directory.

### Shared resources

- All tutorials share a single AgentCore CLI project at [`gatewaylabproject/`](gatewaylabproject/).
- App code (MCP servers) lives in `gatewaylabproject/app/<name>/`.
- Demo scripts live in `gatewaylabproject/scripts/<tutorial>/`. One `uv sync` at the project root installs everything.
- CloudFormation templates live in `gatewaylabproject/cloudformation/<tutorial>/`. This keeps all executable code in one directory so users never switch between the tutorial README dir and the project dir.
- Each tutorial's scripts read/write their own `.env` at `gatewaylabproject/scripts/<tutorial>/.env` (not a shared project-level `.env`). This prevents tutorials from overwriting each other's state.
- Cleanup scripts must only delete resources created by the corresponding deploy script. Do not delete resources created via AgentCore CLI or CloudFormation. Do not print "next step" or "also delete" instructions: those belong in the README.
- `gateway_mcp_client.py` lives in `gatewaylabproject/`. Scripts import it via `sys.path.insert(0, project_root)`.
- `gateway_admin.py` (`GatewayBoto3Client`) lives in `gatewaylabproject/`. Used by scripts that need boto3 for gateway/target creation.
- A single `pyproject.toml` at `gatewaylabproject/` manages all Python dependencies. `uv.lock` is gitignored.
- Amazon Cognito is deployed once via [00-optional-setup/](00-optional-setup/) and shared across tutorials.

### GatewayBoto3Client

`gatewaylabproject/gateway_admin.py` provides `GatewayBoto3Client` for boto3-based gateway operations:

- `create_gateway_role()`: least-privilege IAM role (specify `oauth_targets`, `api_key_targets`, `lambda_targets`, `s3_schemas`, `policy_engine_arn`)
- `create_gateway()`: creates gateway with any protocol config. Always sets `exceptionLevel: 'DEBUG'` for tutorials.
- `create_target()`: MCP server targets with optional `resource_priority`, `listing_mode`, `metadata_config`
- `create_credential_provider()`: OAuth2 credential providers
- `delete_gateway()`: deletes all targets then the gateway
- `delete_gateway_role()`: deletes IAM role and policies
- `synchronize_targets()`: explicit sync
- `update_target()`: implicit sync

The SDK client resolves region from `~/.aws/config` (same as `aws configure`). No hardcoded regions.

### Naming conventions

| Resource | Pattern | Allowed chars | Max | Example |
| :-- | :-- | :-- | :-- | :-- |
| Agent | `^[a-zA-Z][a-zA-Z0-9_]{0,47}$` | Letters, numbers, underscores. Starts with letter. | 48 | `client_credentials_mcp_server` |
| Gateway | `^[0-9a-zA-Z](?:[0-9a-zA-Z-]*[0-9a-zA-Z])?$` | Letters, numbers, hyphens. | 100 | `agentcore-gateway` |
| Credential | `^[a-zA-Z0-9\-_]+$` | Letters, numbers, hyphens, underscores. | 128 | `client_credentials_mcp_server-oauth` |
| Gateway Target | Alphanumeric + hyphens | Letters, numbers, hyphens. | 100 | `client-credentials-mcp-server-target` |

Use the auth flow or feature as the prefix for tutorial-specific resources to keep names unique across the shared project.

### Amazon Cognito (optional, for tutorials only)

Amazon Cognito is **not required** for AgentCore Gateway. Tutorials use it to keep focus on gateway patterns. For production, use any OAuth 2.0 compliant identity provider (Entra ID, Auth0, Okta).

The Cognito stack (`gatewaylabproject/cloudformation/cognito/cognito-signup-stack.yaml`) creates three clients:

| Client | Secret | OAuth Flow | Scopes | Use Case |
| :-- | :-- | :-- | :-- | :-- |
| **WebClient** | No (public PKCE) | `code` | `openid`, `email`, `profile` | Auth code + PKCE flow (browser). Also supports `USER_PASSWORD_AUTH` for programmatic testing. |
| **MCPClient** | Yes (confidential) | `client_credentials` | `api/mcp` | M2M. Gateway uses this for outbound auth to MCP servers on AgentCore Runtime. |
| **GatewayClient** | Yes (confidential) | `client_credentials` | `api/gateway` | M2M. Callers use this for inbound auth to the gateway. |

Capture stack outputs into environment variables for use in subsequent steps:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export MCP_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`MCPClientId`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export MCP_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $MCP_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

> [!NOTE]
> Client secrets are not surfaced by CloudFormation outputs. Fetch them via `aws cognito-idp describe-user-pool-client`.

### Auto-created credentials

When using `--client-id` and `--client-secret` on `agentcore add agent` or `agentcore add gateway`, the CLI auto-creates an OAuth credential provider with `"managed": true`. These credentials:

- Are named `<agent-name>-oauth` or `<gateway-name>-oauth`
- Can be referenced by `--credential-name` when creating gateway targets
- Cannot be removed via `agentcore remove credential` (`-y` does not override the managed check — this is a CLI bug where `BasePrimitive.registerRemoveSubcommand` doesn't forward `cliOptions.yes` as `force` to `CredentialPrimitive.remove()`)
- Must be removed manually from `agentcore/agentcore.json` or via `agentcore remove all --yes`

### Capturing MCP server URL after deploy

The MCP server URL is only available after `agentcore deploy`. Capture it with:

```bash
export MCP_SERVER_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == '<agent-name>'))
")
```

> [!NOTE]
> `agentcore status --json` may include trailing ANSI escape characters from the Ink TUI. Use `JSONDecoder().raw_decode()` instead of `json.load()`.

### Removing gateway targets

The `agentcore remove gateway-target` command takes only `--name` — there is no `--gateway` flag:

```bash
agentcore remove gateway-target --name <target-name> -y
```

### Environment variables and `export`

All variable assignments in code blocks must use `export` so that `uv run` subprocesses can access them. Shell variables set without `export` are not inherited by child processes:

```bash
# Wrong — uv run python won't see this
API_ID=$(aws ...)

# Correct
export API_ID=$(aws ...)
```

### Python dependencies

Use [uv](https://docs.astral.sh/uv/getting-started/installation/) for Python package management. A single `pyproject.toml` at `gatewaylabproject/` manages all demo script dependencies. `uv.lock` is gitignored.

Run `uv sync` once from `gatewaylabproject/` before running any demo script. Only mention it in the Demo section, not in deployment steps.

### Gateway exception level

Always create gateways with `--exception-level DEBUG` (CLI) or `exceptionLevel: 'DEBUG'` (boto3). This is set automatically in `GatewayBoto3Client.create_gateway()`.

### AWS region handling

- The MCP Inspector server resolves the AWS region from `~/.aws/config` via the SDK's default credential chain — same as `aws configure`. No hardcoded regions.
- `GatewayBoto3Client` uses `new Client({})` which also resolves from the config.
- The Inspector UI shows the resolved region next to the gateway list header.

### No `cd` in code blocks

Do not put `cd` commands inside code blocks. Instead, tell the user to navigate to the directory in prose, then show only the commands.

### Markdown formatting

- Use Prettier (`.prettierrc` at gateway root) for consistent formatting: `npx prettier --write "**/*.md"`
- Use markdownlint (`.markdownlint.json` at gateway root) for linting. MD013 (line length) is disabled since Prettier handles wrapping.
- Heading levels must increment by one (MD001). Fix with the heading-increment script if needed.
- All images must have alt text (MD045).
- Fenced code blocks must have a language specified (MD040). Use `text` for generic output.
- No reversed link syntax (MD011): use `[text](url)` not `(text)[url]`.
- Use factual language ("supports", "provides") not announcement language ("now supports", "just launched").

### Tutorial quality checklist

Every tutorial must pass these checks before merge. Run each command exactly as written in the README.

#### Working directory

Every README must include this banner before the first code block in the Deployment Steps section:

```markdown
> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](<relative-path>/gatewaylabproject/) directory. Navigate there before proceeding.
```

#### Independence

Each tutorial must work independently of every other tutorial:
- A tutorial must deploy and run successfully even if no other tutorial has ever been run.
- A tutorial must deploy and run successfully even if another tutorial was run and not cleaned up.
- Tutorial-specific resource names must not collide with other tutorials (use the auth flow or feature as a prefix).
- Script-local `.env` files (not shared state) prevent cross-tutorial interference.
- Cleanup scripts must tolerate already-deleted resources gracefully (catch exceptions, don't fail).

#### Prerequisites and infrastructure

1. Use CloudFormation to deploy prerequisites (Lambda functions, API Gateway APIs, etc.). Templates live in `gatewaylabproject/cloudformation/<tutorial>/` with Launch Stack banners (us-east-1, us-west-2) in each README. If CDK or Terraform already exists for a resource, use that instead.
2. Amazon Cognito is deployed once from [00-optional-setup/](00-optional-setup/) and shared across all tutorials. `00-optional-setup/README.md` must include a cleanup section.

#### Code blocks

3. Every code block must be a self-contained command the user can copy-paste and run. No inline Python snippets that require a notebook or REPL — wrap them in a `uv run python -c "..."` or a script file.
4. All variable assignments must use `export` (see [Environment variables and `export`](#environment-variables-and-export)).

#### Demo sections

5. Every tutorial must have at least one Demo section. Include a `> [!TIP]` linking to the [AgentCore Gateway MCP Inspector](05-community/gateway-mcp-inspector/) as an alternative.
6. If the tutorial has intermediate demos between deployment steps to showcase functionality, each demo section must also include the Inspector tip.

#### Cleanup

7. Cleanup scripts (`scripts/<tutorial>/cleanup.py`) delete only the resources they created (targets, IAM policies, credential providers). They do not delete the gateway, MCP server, or CloudFormation stacks.
8. AgentCore CLI cleanup (`agentcore remove gateway-target`, `agentcore remove gateway`, `agentcore remove agent`, then `agentcore deploy --yes`) handles CLI-created resources.
9. CloudFormation/CDK/Terraform cleanup (`aws cloudformation delete-stack`) handles infrastructure stacks.
10. Cognito cleanup is listed last with a disclaimer: "Delete the Cognito stack (if no longer needed by other tutorials)".
11. Cleanup must delete everything the tutorial created (except Cognito). Verify by listing resources after cleanup.

#### Validation

12. Run every command in the README exactly as written, in order, using `aws configure`-configured credentials. If a command fails, the README is wrong — fix the README, not the workaround.

---

## Tutorial Template

Copy the structure below for each new tutorial.

---

```markdown
## <Title>

Brief description of what this tutorial demonstrates.

## Architecture

<!-- Add architecture diagram -->
<!-- ![Architecture](images/architecture.png) -->

| Component | Role |
| :-- | :-- |
| AgentCore Gateway | ... |
| AgentCore Runtime | ... |
| AgentCore Identity | ... |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Bedrock model access for `global.anthropic.claude-haiku-4-5-20251001-v1:0` (if using Strands demo)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore Gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../00-optional-setup/).

Once deployed, capture the outputs into environment variables:

` ` `bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export MCP_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`MCPClientId`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export MCP_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $MCP_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
` ` `

### Step 2: Register MCP Server (AgentCore CLI)

From the [`gatewaylabproject/`](../gatewaylabproject/) directory. The `--client-id` and `--client-secret` flags auto-create an OAuth credential provider for outbound auth.

` ` `bash
agentcore add agent \
  --name <agent_name> \
  --type byo \
  --language Python \
  --protocol MCP \
  --code-location app/<app-dir> \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $MCP_CLIENT_ID \
  --allowed-scopes api/mcp \
  --client-id $MCP_CLIENT_ID \
  --client-secret $MCP_CLIENT_SECRET
` ` `

### Step 3: Create AgentCore Gateway (AgentCore CLI)

The `--client-id` and `--client-secret` flags allow the CLI to fetch gateway bearer tokens for testing.

` ` `bash
agentcore add gateway \
  --name agentcore-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $GATEWAY_CLIENT_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --client-secret $GATEWAY_CLIENT_SECRET \
  --exception-level DEBUG
` ` `

### Step 4: Deploy MCP Server and Gateway (AgentCore CLI)

` ` `bash
agentcore deploy --yes
` ` `

Capture the MCP server URL and gateway URL:

` ` `bash
export MCP_SERVER_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == '<agent_name>'))
")

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier agentcore-gateway \
  --query 'gatewayUrl' --output text)/mcp

echo "MCP Server URL: $MCP_SERVER_URL"
echo "Gateway URL:    $GATEWAY_URL"
` ` `

### Step 5: Create Gateway Target (AgentCore CLI)

The `--credential-name` references the OAuth credential auto-created in Step 2.

` ` `bash
agentcore add gateway-target \
  --name <target-name> \
  --type mcp-server \
  --endpoint $MCP_SERVER_URL \
  --gateway agentcore-gateway \
  --outbound-auth oauth \
  --credential-name <agent_name>-oauth
` ` `

### Step 6: Deploy Gateway Target (AgentCore CLI)

` ` `bash
agentcore deploy --yes
` ` `

Verify:

` ` `bash
agentcore status
` ` `

## Demo

### Option 1: AgentCore Gateway MCP Inspector

Connect the [AgentCore Gateway MCP Inspector](../05-community/gateway-mcp-inspector/) to your gateway:

1. Start the inspector by following [instructions](../05-community/gateway-mcp-inspector/)
2. Select your gateway from the gateway list, or paste the gateway URL
3. Under Authentication, select **Manual Token** and enter the Cognito JWT
4. Click **Connect** and explore tools, prompts, and resources

<!-- Add screenshots -->

### Option 2: AgentCore Gateway MCP Client

Install Python dependencies:

` ` `bash
uv sync
` ` `

Run the demo script:

` ` `bash
uv run python scripts/<tutorial>/invoke.py
` ` `

### Option 3: MCP SDK (optional)

` ` `python
from mcp.client.streamable_http import streamablehttp_client
from mcp.client import ClientSession

async with streamablehttp_client(
    gateway_url, headers={"Authorization": f"Bearer {token}"}
) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print(tools)
` ` `

### Option 4: Strands Agents (optional)

` ` `python
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client

client = MCPClient(
    lambda: streamablehttp_client(
        gateway_url, headers={"Authorization": f"Bearer {token}"}
    )
)

model = BedrockModel(model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0")

with client:
    tools = client.list_tools_sync()
    agent = Agent(model=model, tools=tools)
    agent("What tools do you have access to?")
` ` `

## Cleanup

From the [`gatewaylabproject/`](../gatewaylabproject/) directory, remove resources in reverse order:

` ` `bash
agentcore remove gateway-target --name <target-name> -y
agentcore remove gateway --name agentcore-gateway -y
agentcore remove agent --name <agent_name> -y
` ` `

> [!NOTE]
> The auto-created credentials are CLI-managed. Remove them by running `agentcore remove all --yes` or manually from `agentcore/agentcore.json`, then run `agentcore deploy --yes`.

Deploy to apply all removals:

` ` `bash
agentcore deploy --yes
` ` `

Delete the Cognito stack (if no longer needed):

` ` `bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
` ` `

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
- [Identity Provider Setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-idps.html)
```
