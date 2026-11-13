# Data Analyst Assistant - Amazon Bedrock AgentCore and Data Source Deployment with CDK

Deploy the complete infrastructure for a Data Analyst Conversational Assistant using **[AWS Cloud Development Kit (CDK)](https://aws.amazon.com/cdk/)** and **[Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)**.

> [!NOTE]
> **Working Directory**: Make sure you are in the `cdk-data-analyst-assistant-agentcore-strands/` folder before starting this tutorial. All commands in this guide should be executed from this directory.

## Architecture

![Architecture](../images/architecture.png)

The data flow through the system operates as follows:

1. **User request** arrives at the AgentCore Runtime endpoint (via the front-end application or direct API invocation).
2. **AgentCore Runtime** hosts the Strands Agent container, which processes the request using the configured Bedrock model.
3. **Gateway (MCP)** provides tool access — the agent calls PostgreSQL query tools exposed as a Lambda target behind the AgentCore Gateway using the MCP protocol.
4. **AgentCore Policy** evaluates each tool call at the Gateway against [Dogwood](https://dogwood-policy.github.io/dogwood/) policies. It enforces tool-level access control, rules about the order and number of tool calls in a session (temporal policies), and blocks for sensitive SQL columns.
5. **Guardrails in policies** screen tool arguments and results at the Gateway, blocking prompt attacks and harmful content and suppressing PII in results.
6. **Memory** persists conversation context — short-term memory within a session and long-term semantic facts across sessions.
7. **Evaluators** run offline to measure SQL generation accuracy and response quality using LLM-as-a-Judge scoring.
8. **Observability** captures runtime logs, memory extraction logs, gateway invocation logs, and X-Ray traces for full-stack debugging.

## Overview

This CDK stack deploys a complete data analyst assistant powered by Amazon Bedrock AgentCore with the following components:

### Amazon Bedrock AgentCore Resources

This sample demonstrates **all** Amazon Bedrock AgentCore features in a single production-grade deployment:

| Feature | Description |
|---------|-------------|
| **Runtime** | Container-based runtime hosting the Strands Agent (ARM64, DEFAULT endpoint) |
| **Memory** | Long-term semantic memory with "Facts" strategy (`/facts/{actorId}` namespace) plus short-term event-based conversation history with 90-day retention |
| **Gateway (MCP)** | MCP-protocol gateway with a Lambda target exposing PostgreSQL query tools (`get_tables_information`, `execute_sql_query`) |
| **Policy Engine (Dogwood)** | Dogwood policies: temporal rules (schema lookup before querying, per-session rate limit, re-reading the schema after a denial), sensitive-column blocking, and guardrails (prompt attack detection, content filtering, PII suppression) |
| **Evaluators (LLM-as-a-Judge)** | Custom evaluators measuring SQL accuracy (TRACE level) and response quality (SESSION level) |
| **Observability** | CloudWatch Logs delivery for runtime, memory extraction, and gateway invocations, plus X-Ray traces |

### Data Source and VPC Infrastructure

- **Amazon Aurora Serverless v2 PostgreSQL**: Scalable database cluster (v17.4) with RDS Data API enabled and storage encryption
- **Amazon DynamoDB**: Table for tracking SQL query results with pay-per-request billing
- **AWS Secrets Manager**: Secure storage for database credentials
- **Amazon S3**: Import bucket for loading data into Aurora PostgreSQL with 7-day lifecycle policy
- **VPC with Public and Private Subnets**: Network isolation with NAT Gateway for outbound connectivity
- **Security Groups**: Database access control with self-referencing rule for PostgreSQL (port 5432)
- **VPC Gateway Endpoints**: Cost-effective access to S3 and DynamoDB services

> [!IMPORTANT]
> Remember to clean up resources after testing to avoid unnecessary costs by following the clean-up steps provided.

## Prerequisites

Before you begin, ensure you have:

* AWS Account and appropriate IAM permissions for services deployment
* **Development Environment**:
  * Python 3.10 or later installed
  * Node.js and [pnpm](https://pnpm.io/installation) installed
  * Docker installed and running (required for building the agent container image)
  * **[AWS CDK Installed](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html)**

* Run this command to create a service-linked role for RDS. This role is required for Aurora Serverless v2 to manage resources on your behalf. New AWS accounts that haven't used RDS before may not have this role, which can cause CDK deployment failures:

```bash
aws iam create-service-linked-role --aws-service-name rds.amazonaws.com
```

> [!NOTE]
> If the role already exists, you will see the message: `Service role name AWSServiceRoleForRDS has been taken in this account`. This is expected and you can proceed with the deployment.

## AWS Deployment

Install the required dependencies:

```bash
pnpm install
```

Bootstrap your AWS environment (if you haven't already):

```bash
cdk bootstrap
```

Synthesize the CloudFormation template to verify the stack:

```bash
cdk synth
```

Deploy the infrastructure:

```bash
cdk deploy
```

> [!NOTE]
> If you are using [Finch](https://runfinch.com/) instead of Docker Desktop, prefix the command with `CDK_DOCKER=finch cdk deploy` to use Finch for container image builds.

This deploys the complete stack, including the Policy Engine attached to the Gateway in ENFORCE mode with the Dogwood policies from [`policies/`](./policies/) (authorization, temporal, and guardrail rules).

Default Parameters:
- **DatabaseName**: "video_games_sales" - Name of the database
- **BedrockModelId**: "us.anthropic.claude-haiku-4-5-20251001-v1:0" - Bedrock model ID for the agent

### Deployed Resources

**AgentCore Resources:**
- AgentCore Memory with semantic "Facts" strategy, `/facts/{actorId}` namespace, and 90-day event retention
- AgentCore Runtime (container-based, ARM64) with DEFAULT endpoint
- AgentCore Gateway (MCP protocol) with Lambda target for database tools
- Policy Engine with 8 Dogwood policies (authorization, temporal, guardrails)
- Custom evaluators (SqlAccuracy, ResponseQuality)
- ECR repository with agent container image

**Observability:**
- Runtime application logs → CloudWatch Logs (`/aws/vendedlogs/bedrock-agentcore/<runtimeId>`)
- Runtime traces → AWS X-Ray
- Memory extraction logs → CloudWatch Logs (`/aws/vendedlogs/bedrock-agentcore/memory/<memoryId>`)
- Gateway invocation logs → CloudWatch Logs (`/aws/vendedlogs/bedrock-agentcore/gateway/<gatewayId>`)
- All log groups configured with 14-day retention

**Data Infrastructure:**
- VPC with public/private subnets, NAT Gateway, security groups, VPC endpoints
- Aurora PostgreSQL Serverless v2 (v17.4) with RDS Data API enabled
- DynamoDB table for SQL query results
- S3 bucket for data imports with lifecycle policies
- Secrets Manager for database credentials

**Configuration:**
- Environment variables passed directly to the AgentCore Runtime:
  - `MEMORY_ID`: AgentCore Memory ID
  - `BEDROCK_MODEL_ID`: Bedrock model ID for the agent
  - `READONLY_SECRET_ARN`: Read-only database user secret ARN
  - `AURORA_RESOURCE_ARN`: Aurora cluster ARN
  - `DATABASE_NAME`: Database name
  - `QUESTION_ANSWERS_TABLE`: DynamoDB table name
  - `MAX_RESPONSE_SIZE_BYTES`: Maximum response size (1MB)
  - `GATEWAY_URL`: AgentCore Gateway endpoint URL for MCP tool access (guardrails enforced at Gateway via Policy Engine)

### Stack Outputs

After deployment, the stack exports:
- `MemoryId`: AgentCore Memory ID
- `AuroraServerlessDBClusterARN`: Aurora cluster ARN
- `SecretARN`: Database credentials secret ARN
- `ReadOnlySecretARN`: Read-only database user secret ARN
- `DataSourceBucketName`: S3 import bucket name
- `QuestionAnswersTableName`: DynamoDB table name
- `QuestionAnswersTableArn`: DynamoDB table ARN
- `AgentRuntimeArn`: AgentCore runtime ARN
- `GatewayUrl`: AgentCore Gateway MCP endpoint URL
- `PolicyEngineId`: AgentCore Policy Engine ID (Dogwood policies)

> [!IMPORTANT] 
> This sample uses **guardrails in policies**: Bedrock guardrail checks written as `when guardrails { }` conditions in Dogwood policies and enforced at the AgentCore Gateway. No standalone `AWS::Bedrock::Guardrail` resource is needed. The policies call `BedrockGuardrails::PromptAttack`, `BedrockGuardrails::ContentFilter`, and `BedrockGuardrails::SensitiveInformation` directly.

### How Memory Works

The agent uses the [AgentCoreMemorySessionManager](https://strandsagents.com/docs/community/session-managers/agentcore-memory/) (Strands integration) to manage both short-term and long-term memory automatically:

- **Short-term memory (STM)**: Scoped by `actorId` + `sessionId`. Stores raw conversation events within a session. Each page load generates a new `sessionId`, so STM only contains the current conversation.
- **Long-term memory (LTM)**: Scoped by `/facts/{actorId}` namespace. After events are saved, AgentCore asynchronously extracts facts using the semantic strategy and stores them per user. When a new session starts, the agent searches this namespace using the user's query via vector similarity, retrieving relevant knowledge from all past sessions.
- **Per-user isolation**: The `actorId` is the Cognito user `sub`, so each user's facts are completely isolated from other users.
- **Async extraction**: LTM extraction takes 20-40 seconds after events are saved. Within the same session, STM handles continuity. LTM provides cross-session knowledge.

## Gateway and Policy Engine

### AgentCore Gateway (MCP Protocol)

The AgentCore Gateway exposes database tools to the agent using the **Model Context Protocol (MCP)**. Instead of embedding tool logic directly in the agent container, the Gateway provides a managed MCP layer that routes tool calls to a Lambda function.

**Gateway target tools:**

| Tool | Description |
|------|-------------|
| `get_tables_information` | Returns database schema metadata (table structures, columns, data types) |
| `execute_sql_query` | Executes a read-only SQL query against the PostgreSQL database |

The Lambda target handles tool routing via `context.client_context.custom['bedrockAgentCoreToolName']`. No standalone MCP server is required — the Gateway provides the full MCP protocol layer.

**Gateway URL format:**
```
https://gateway.bedrock-agentcore.<region>.amazonaws.com/gateways/<gatewayId>
```

### AgentCore Policy (Dogwood)

Every Gateway tool call is authorized by the **AgentCore Policy Engine**, attached to the Gateway in `ENFORCE` mode. Policies are written in **[Dogwood](https://dogwood-policy.github.io/dogwood/)**, the AgentCore Policy language. Dogwood is an open-source superset of [Cedar](https://www.cedarpolicy.com/), so every Cedar policy is also a valid Dogwood policy. It adds `when temporal { }` blocks that look at the **history of tool calls in the session**, not just the current request. A single engine evaluates three kinds of rules:

- **Authorization** (stateless): who may call which tool, and with what arguments (`when { }` / `unless { }`).
- **Temporal** (stateful): what must or must not have happened earlier in the session (`when temporal { }`).
- **Guardrails** (content): Amazon Bedrock Guardrails checks on tool arguments and results (`when guardrails { }`).

Evaluation is deny-by-default and a `forbid` always overrides a `permit`. Enforcement happens at the Gateway, outside the agent, so a confused or prompt-injected agent cannot reason its way around it.

The policies live in [`policies/`](./policies/), one `.dw` file per policy, and `cdk deploy` creates them in file order. See [`policies/README.md`](./policies/README.md) for how they're loaded.

| # | Policy | Effect | Kind | Rule |
|---|--------|--------|------|------|
| 1 | `permitGetTablesInformation` | `permit` | Authorization | The schema lookup tool is always reachable |
| 2 | `permitSqlAfterSchemaLookup` | `permit` | **Temporal** | `execute_sql_query` is allowed only if `get_tables_information` succeeded in the same session within the last 30 minutes (`formerly within 30m`) |
| 3 | `forbidSensitiveColumns` | `forbid` | Authorization | Blocks SQL whose text references PII columns (`email`, `phone`, `ssn`, `address`, `credit_card`) or internal cost columns (`cost_per_unit`, `profit_margin`, `wholesale_price`, `procurement`) |
| 4 | `forbidQueryRateLimit` | `forbid` | **Temporal** | Allows at most 20 queries per session in 5 minutes (`count` over `::request` events), which stops runaway agent loops |
| 5 | `forbidRequeryUntilSchemaRefresh` | `forbid` | **Temporal** | After any denied query, blocks further queries until the agent re-reads the schema (`!L since within 5m R`) |
| 6 | `guardrailPromptAttack` | `forbid` | Guardrail | Prompt injection or jailbreak text in `sql_query` / `description` (confidence > 0.4) |
| 7 | `guardrailHarmfulContent` | `forbid` | Guardrail | Violence or hate in `description` (confidence > 0.2) |
| 8 | `suppressPiiOutput` | `suppressOutput` | Guardrail | The query runs, but the result is withheld if it contains SSNs, card numbers, email addresses, or phone numbers |

`execute_sql_query` has **no unconditional permit**. Its only permit is the temporal policy #2, and policies #3–#7 carve exceptions out of it.

Example: the temporal permit (#2):

```
permit(
  principal,
  action == AgentCore::Action::"DatabaseToolsTarget___execute_sql_query",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when temporal {
  formerly within 30m
    AgentCore::Action::"DatabaseToolsTarget___get_tables_information"::response{
      eventResource: resource
    }
};
```

#### Policy sessions

Temporal policies evaluate the calls that share a **policy session ID**. The Gateway rejects tool calls without one (`sessionId is required when temporal policies are enabled`). The agent sends it in the `x-amzn-bedrock-agentcore-policy-session-id` header on every Gateway request (see `get_mcp_client()` in [`app.py`](./data-analyst-assistant-agentcore-strands/app.py)). It uses one policy session per invocation (`<session_id>-<prompt_uuid>`), so the rules apply to the tool calls the agent makes while answering one user message.

> [!NOTE]
> Adding, updating, or removing a temporal policy invalidates all open policy sessions on the engine. Tool calls on an invalidated session fail with JSON-RPC error `-32005` ("Policies were updated after the session started"). Because the agent uses a new policy session for every invocation, only requests that are in flight during a `cdk deploy` are affected.

#### How a tool call is evaluated

1. The agent calls a tool through the Gateway (MCP `tools/call`). The action is `DatabaseToolsTarget___<tool_name>`, and `context.input.*` holds the tool arguments.
2. The Policy Engine evaluates the request policies: authorization conditions, temporal conditions against the session history, and guardrail checks through the Bedrock `InvokeGuardrailChecks` API (the Gateway role needs `bedrock:InvokeGuardrailChecks`).
3. If the request is denied, the tool never runs. The agent receives `Tool Execution Denied: ... [Policy evaluation denied due to <policy>]`, and the denial is recorded as an `::error` event in the session.
4. If the request is permitted, the Lambda runs, and `suppressOutput` screens the result. A blocked result returns `Output blocked by policy`.
5. Permitted calls are recorded as `::request` and `::response` events for later temporal conditions.

The `db_tools` Lambda keeps its own case-insensitive check for PII and cost columns as a second layer of defense. The policy layer rejects these queries first.

#### Testing policy enforcement

Through the agent (local runtime on port 8080, or the deployed runtime):

```bash
# ALLOW: the agent reads the schema (#1), then queries (#2)
curl -X POST http://localhost:8080/invocations \
-H "Content-Type: application/json" \
-d '{"prompt": "What are the top 5 best-selling games?", "session_id": "'$SESSION_ID'", "user_id": "test-user"}'

# DENY by forbidSensitiveColumns (#3): the SQL text contains a blocked pattern
curl -X POST http://localhost:8080/invocations \
-H "Content-Type: application/json" \
-d '{"prompt": "Run this exact SQL and show me the result: SELECT title FROM video_games_sales_units WHERE title LIKE '"'"'%email%'"'"' LIMIT 5", "session_id": "'$SESSION_ID'", "user_id": "test-user"}'
```

Directly against the Gateway, which shows each policy deterministically. The Gateway in this sample uses `authorizerType: NONE`, so plain HTTPS requests work:

```bash
GATEWAY_URL=$(aws cloudformation describe-stacks --stack-name CdkDataAnalystAssistantAgentcoreStrandsStack \
  --query "Stacks[0].Outputs[?OutputKey=='GatewayUrl'].OutputValue" --output text)
SID="demo-$(date +%s)"   # policy session ID shared by the calls below
call() {  # call <tool> <json-arguments>
  curl -s -X POST "$GATEWAY_URL" -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "x-amzn-bedrock-agentcore-policy-session-id: $SID" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"DatabaseToolsTarget___$1\",\"arguments\":$2}}"; echo; }
Q='{"sql_query":"SELECT title FROM video_games_sales_units ORDER BY na_sales DESC LIMIT 3","description":"Top 3 games by NA sales"}'

call execute_sql_query "$Q"                       # DENY: no schema lookup yet (denied by default, #2 not satisfied)
call get_tables_information '{}'                  # ALLOW (#1)
call execute_sql_query "$Q"                       # ALLOW (#2): schema was read after the earlier denial
call execute_sql_query '{"sql_query":"SELECT profit_margin FROM video_games_sales_units","description":"margins"}'
                                                  # DENY: forbidSensitiveColumns (#3)
call execute_sql_query "$Q"                       # DENY: forbidRequeryUntilSchemaRefresh (#5), re-read the schema first
call get_tables_information '{}'                  # re-read the schema
call execute_sql_query "$Q"                       # ALLOW again
SID="demo-$(date +%s)-pii"; call get_tables_information '{}'
call execute_sql_query '{"sql_query":"SELECT '"'"'john.doe@example.com'"'"' AS contact","description":"contact"}'
                                                  # Output blocked: suppressPiiOutput (#8)
```

Policy decisions are logged by the Gateway. See [Observability](#deployed-resources) for the log group.

## Evaluations

The evaluation harness measures agent quality across two dimensions using custom **LLM-as-a-Judge** evaluators deployed to AgentCore.

### Custom Evaluators

| Evaluator | Level | What It Measures |
|-----------|-------|-----------------|
| **SqlAccuracy** | `TRACE` | Validates that generated SQL queries produce correct results compared to ground-truth expected outputs. Checks query structure, filtering logic, aggregation correctness, and result ordering. |
| **ResponseQuality** | `SESSION` | Evaluates the natural language response for relevance, completeness, clarity, and appropriate handling of out-of-scope requests. |

Additionally, the harness leverages **built-in evaluators**:
- `Builtin.Correctness` — General answer correctness
- `Builtin.GoalSuccessRate` — Whether the agent achieved the stated user goal

### Running the Evaluation Harness

The evaluation harness uses AgentCore's native **Dataset management** and **Batch evaluation** features:

1. Creates a **managed PREDEFINED dataset** (8 scenarios stored in AgentCore's dataset service)
2. Publishes an **immutable dataset version** for reproducible CI/CD evaluation runs
3. Runs **BatchEvaluationRunner** — invokes the agent, submits a server-side batch evaluation job, and returns aggregate scores
4. Optionally runs **EvaluationClient** for per-session evaluation against managed dataset ground truth

```bash
# Run batch evaluation (creates managed dataset, invokes agent, scores all scenarios)
python evaluations/evaluate.py --region us-east-1 --agent-runtime-arn $AGENT_RUNTIME_ARN

# Also run EvaluationClient for individual session scoring
python evaluations/evaluate.py --region us-east-1 --agent-runtime-arn $AGENT_RUNTIME_ARN --use-agentcore-evals

# Keep the managed dataset after evaluation (for inspection or re-use)
python evaluations/evaluate.py --region us-east-1 --agent-runtime-arn $AGENT_RUNTIME_ARN --keep-dataset
```

The harness runs the following test scenarios covering SQL accuracy and response quality:
- Aggregation queries (top-selling games, average scores)
- Filtering queries (year-specific data, platform-specific data)
- Comparison queries (regional sales comparisons)
- Trend analysis (releases over time)
- Out-of-scope handling (non-domain questions)

Results are saved to `evaluations/eval_results.json` with batch evaluation ID, aggregate scores, and per-evaluator summaries.

> [!NOTE]
> The `--use-agentcore-evals` flag requires that the agent has been invoked at least once so that traces are available in CloudWatch Transaction Search. Allow 10-15 seconds after invocation for traces to propagate.

## Set Up the PostgreSQL Database

1. Install required Python dependencies:

```bash
pip install boto3
```

2. Set up the required environment variables. These are needed for loading sample data and local testing:

```bash
# Set the stack name environment variable
export STACK_NAME=CdkDataAnalystAssistantAgentcoreStrandsStack

# Retrieve the output values and store them in environment variables

# AgentCore resources
export BEDROCK_MODEL_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Parameters[?ParameterKey=='BedrockModelId'].ParameterValue" --output text)
export MEMORY_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='MemoryId'].OutputValue" --output text)
export AGENT_RUNTIME_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text)

# Gateway resources (guardrails enforced via Policy Engine Dogwood policies)
export GATEWAY_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='GatewayUrl'].OutputValue" --output text)

# Database resources
export SECRET_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='SecretARN'].OutputValue" --output text)
export READONLY_SECRET_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='ReadOnlySecretARN'].OutputValue" --output text)
export AURORA_SERVERLESS_DB_CLUSTER_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='AuroraServerlessDBClusterARN'].OutputValue" --output text)
export DATABASE_NAME=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Parameters[?ParameterKey=='DatabaseName'].ParameterValue" --output text)
export TABLE_NAME="video_games_sales_units"

# DynamoDB resources
export QUESTION_ANSWERS_TABLE=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='QuestionAnswersTableName'].OutputValue" --output text)

# S3 resources
export DATA_SOURCE_BUCKET_NAME=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='DataSourceBucketName'].OutputValue" --output text)

# Agent runtime env vars (used by app.py for local testing)
export AURORA_RESOURCE_ARN="$AURORA_SERVERLESS_DB_CLUSTER_ARN"

cat << EOF
# Stack Configuration
STACK_NAME: ${STACK_NAME}
BEDROCK_MODEL_ID: ${BEDROCK_MODEL_ID}

# AgentCore Resources
MEMORY_ID: ${MEMORY_ID}
AGENT_RUNTIME_ARN: ${AGENT_RUNTIME_ARN}
GATEWAY_URL: ${GATEWAY_URL}

# Database Resources
SECRET_ARN: ${SECRET_ARN}
READONLY_SECRET_ARN: ${READONLY_SECRET_ARN}
AURORA_SERVERLESS_DB_CLUSTER_ARN: ${AURORA_SERVERLESS_DB_CLUSTER_ARN}
AURORA_RESOURCE_ARN: ${AURORA_RESOURCE_ARN}
DATABASE_NAME: ${DATABASE_NAME}
TABLE_NAME: ${TABLE_NAME}

# DynamoDB Resources
QUESTION_ANSWERS_TABLE: ${QUESTION_ANSWERS_TABLE}

# S3 Resources
DATA_SOURCE_BUCKET_NAME: ${DATA_SOURCE_BUCKET_NAME}
EOF
```

### Load Sample Data

Execute the following command to create the database table and load the sample data:

```bash
python3 resources/create-sales-database.py
```

The script uses the **[video_games_sales_no_headers.csv](./resources/database/video_games_sales_no_headers.csv)** as the data source.

> [!NOTE]
> The data source provided contains information from [Video Game Sales](https://www.kaggle.com/datasets/asaniczka/video-game-sales-2024) which is made available under the [ODC Attribution License](https://opendatacommons.org/licenses/odbl/1-0/).

### Create Read-Only Database User

Execute the following command to create the read-only database user:

```bash
python3 resources/create-readonly-user.py
```

This script creates a `readonly_user` with SELECT-only permissions on the sales data table, following the principle of least privilege. The agent automatically uses the read-only credentials when available.

## Local Testing

Before deploying to AWS, you can test the Data Analyst Agent locally to verify functionality:

1. Navigate to the agent folder and install the required dependencies:

```bash
cd data-analyst-assistant-agentcore-strands
pip install -r requirements.txt
```

2. Start the local agent server:

```bash
python3 app.py
```

This launches a local server on port 8080 that simulates the AgentCore runtime environment.

3. In a different terminal, create a session ID for conversation tracking:

```bash
export SESSION_ID=$(uuidgen)
```

4. Test the agent with example queries using curl:

```bash
curl -X POST http://localhost:8080/invocations \
-H "Content-Type: application/json" \
-d '{"prompt": "Hello world!", "session_id": "'$SESSION_ID'", "user_id": "local-test-user"}'
```

```bash
curl -X POST http://localhost:8080/invocations \
-H "Content-Type: application/json" \
-d '{"prompt": "what is the structure of your data available?!", "session_id": "'$SESSION_ID'", "user_id": "local-test-user"}'
```

```bash
curl -X POST http://localhost:8080/invocations \
-H "Content-Type: application/json" \
-d '{"prompt": "Which developers tend to get the best reviews?", "session_id": "'$SESSION_ID'", "user_id": "local-test-user"}'
```

```bash
curl -X POST http://localhost:8080/invocations \
-H "Content-Type: application/json" \
-d '{"prompt": "Give me a summary of our conversation", "session_id": "'$SESSION_ID'", "user_id": "local-test-user"}'
```

## Script-Based Deployment (Alternative)

For teams that prefer scripted or CI/CD-driven deployments over CDK, the repository includes a standalone deployment script at **[`../deploy.py`](../deploy.py)**.

This script uses the native `bedrock-agentcore` SDK and `boto3` to deploy all AgentCore resources programmatically:

1. **IAM execution role** with `bedrock-agentcore.amazonaws.com` trust policy
2. **AgentCore Memory** (STM + LTM with semantic facts strategy)
3. **AgentCore Runtime** (container-based, ARM64)
4. **AgentCore Gateway** + Lambda target (PostgreSQL query tools via MCP)

> **Note:** Guardrails are enforced via Dogwood policies in the Policy Engine (deployed by CDK), not as a standalone Bedrock Guardrail resource.

**Usage:**

```bash
# Full deployment
python deploy.py --region us-east-1 \
  --aurora-arn $AURORA_SERVERLESS_DB_CLUSTER_ARN \
  --secret-arn $READONLY_SECRET_ARN \
  --dynamodb-table $QUESTION_ANSWERS_TABLE \
  --db-tools-lambda-arn $DB_TOOLS_LAMBDA_ARN

# Use a pre-built container (skip CodeBuild)
python deploy.py --region us-east-1 --container-uri 123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:latest
```

> [!NOTE]
> The script-based deployment requires that the Aurora PostgreSQL cluster and database tools Lambda are already deployed (via the CDK stack). Run `cdk deploy` first, then use `deploy.py` for subsequent AgentCore-only updates.

Deployment outputs are saved to `deploy_outputs.json` with all resource ARNs and identifiers.

## Invoking the Agent

Once deployed and data is loaded, you can invoke the agent using the AgentCore Runtime Endpoint. The endpoint name is available in the stack outputs as `AgentEndpointName`.

## Next Step

You can now proceed to the **[Front-End Implementation](../amplify-data-analyst-conversational-assistant-agentcore-strands/)**.

## Cleaning-up Resources (Optional)

To avoid unnecessary charges, delete the CDK stack:

```bash
cdk destroy
```

## License

This project is licensed under the Apache-2.0 License.