# Data Analyst Assistant - Amazon Bedrock AgentCore and Data Source Deployment with CDK

Deploy the complete infrastructure for a Data Analyst Assistant for Video Game Sales using **[AWS Cloud Development Kit (CDK)](https://aws.amazon.com/cdk/)** and **[Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)**.

> [!NOTE]
> **Working Directory**: Make sure you are in the `cdk-data-analyst-assistant-agentcore-strands/` folder before starting this tutorial. All commands in this guide should be executed from this directory.

## Overview

This CDK stack deploys a complete data analyst assistant powered by Amazon Bedrock AgentCore with the following components:

### Amazon Bedrock AgentCore Resources

- **AgentCore Memory**: Long-term semantic memory with a "Facts" strategy (`/facts/{actorId}` namespace) for extracting and persisting knowledge across sessions per user, plus short-term event-based conversation history with 90-day retention
- **AgentCore Runtime**: Container-based runtime hosting the Strands Agent with ARM64 architecture and DEFAULT endpoint
- **Observability**: CloudWatch Logs delivery for runtime application logs and memory extraction, plus X-Ray traces for runtime invocations

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

Default Parameters:
- **DatabaseName**: "video_games_sales" - Name of the database
- **BedrockModelId**: "us.anthropic.claude-haiku-4-5-20251001-v1:0" - Bedrock model ID for the agent

### Deployed Resources

**AgentCore Resources:**
- AgentCore Memory with semantic "Facts" strategy, `/facts/{actorId}` namespace, and 90-day event retention
- AgentCore Runtime (container-based, ARM64) with DEFAULT endpoint
- ECR repository with agent container image

**Observability:**
- Runtime application logs → CloudWatch Logs (`/aws/vendedlogs/bedrock-agentcore/<runtimeId>`)
- Runtime traces → AWS X-Ray
- Memory extraction logs → CloudWatch Logs (`/aws/vendedlogs/bedrock-agentcore/memory/<memoryId>`)
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

> [!IMPORTANT] 
> Enhance AI safety and compliance by implementing **[Amazon Bedrock Guardrails](https://aws.amazon.com/bedrock/guardrails/)** for your AI applications with the seamless integration offered by **[Strands Agents SDK](https://strandsagents.com/latest/user-guide/safety-security/guardrails/)**.

### How Memory Works

The agent uses the [AgentCoreMemorySessionManager](https://strandsagents.com/docs/community/session-managers/agentcore-memory/) (Strands integration) to manage both short-term and long-term memory automatically:

- **Short-term memory (STM)**: Scoped by `actorId` + `sessionId`. Stores raw conversation events within a session. Each page load generates a new `sessionId`, so STM only contains the current conversation.
- **Long-term memory (LTM)**: Scoped by `/facts/{actorId}` namespace. After events are saved, AgentCore asynchronously extracts facts using the semantic strategy and stores them per user. When a new session starts, the agent searches this namespace using the user's query via vector similarity, retrieving relevant knowledge from all past sessions.
- **Per-user isolation**: The `actorId` is the Cognito user `sub`, so each user's facts are completely isolated from other users.
- **Async extraction**: LTM extraction takes 20-40 seconds after events are saved. Within the same session, STM handles continuity. LTM provides cross-session knowledge.

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

## Invoking the Agent

Once deployed and data is loaded, you can invoke the agent using the AgentCore Runtime Endpoint. The endpoint name is available in the stack outputs as `AgentEndpointName`.

## Next Step

You can now proceed to the **[Front-End Implementation - Integrating AgentCore with a Ready-to-Use Data Analyst Assistant Application](../amplify-video-games-sales-assistant-agentcore-strands/))**.

## Cleaning-up Resources (Optional)

To avoid unnecessary charges, delete the CDK stack:

```bash
cdk destroy
```

## License

This project is licensed under the Apache-2.0 License.

