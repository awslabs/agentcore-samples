# End-to-End Weather Agent with Tools and Memory - CDK

This CDK stack deploys a complete Amazon Bedrock AgentCore Runtime with a sophisticated weather-based activity planning agent. This demonstrates the full power of AgentCore by integrating Browser tool, Code Interpreter, Memory, and S3 storage in a single deployment.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Deployment](#deployment)
- [Testing](#testing)
- [Sample Queries](#sample-queries)
- [How It Works](#how-it-works)
- [Cleanup](#cleanup)
- [Troubleshooting](#troubleshooting)

## Overview

This CDK stack creates a comprehensive AgentCore deployment that showcases:

### Core Components

- **AgentCore Runtime**: Hosts a Strands agent with multiple tools
- **Browser Tool**: Web automation for scraping weather data from weather.gov
- **Code Interpreter**: Python code execution for weather analysis
- **Memory**: Stores user activity preferences
- **S3 Bucket**: Stores generated activity recommendations
- **ECR Repository**: Container image storage
- **IAM Roles**: Comprehensive permissions for all components

### Agent Capabilities

The Weather Activity Planner agent can:

1. **Scrape Weather Data**: Uses browser automation to fetch 8-day forecasts from weather.gov
2. **Analyze Weather**: Generates and executes Python code to classify days as GOOD/OK/POOR
3. **Retrieve Preferences**: Accesses user activity preferences from memory
4. **Generate Recommendations**: Creates personalized activity suggestions based on weather and preferences
5. **Store Results**: Saves recommendations as Markdown files in S3

### Use Cases

- Weather-based activity planning
- Automated web scraping and data analysis
- Multi-tool agent orchestration
- Memory-driven personalization
- Asynchronous task processing

## Architecture

The architecture demonstrates a complete AgentCore deployment with multiple integrated tools:

**Core Components:**
- **User**: Sends weather-based activity planning queries
- **AWS CodeBuild**: Builds the ARM64 Docker container image with the agent code
- **Amazon ECR Repository**: Stores the container image
- **AgentCore Runtime**: Hosts the Weather Activity Planner Agent
  - **Weather Agent**: Strands agent that orchestrates multiple tools
  - Invokes Amazon Bedrock LLMs for reasoning and code generation
- **Browser Tool**: Web automation for scraping weather data from weather.gov
- **Code Interpreter Tool**: Executes Python code for weather analysis
- **Memory**: Stores user activity preferences (30-day retention)
- **S3 Bucket**: Stores generated activity recommendations
- **IAM Roles**: Comprehensive permissions for all components

**Workflow:**
1. User sends query: "What should I do this weekend in Richmond VA?"
2. Agent extracts city and uses Browser Tool to scrape 8-day forecast
3. Agent generates Python code and uses Code Interpreter to classify weather
4. Agent retrieves user preferences from Memory
5. Agent generates personalized recommendations
6. Agent calls `save_activity_plan` to store Markdown in the configured S3 results bucket using boto3

## Prerequisites

### AWS Account Setup

1. **AWS Account**: You need an active AWS account with appropriate permissions
   - [Create AWS Account](https://aws.amazon.com/account/)
   - [AWS Console Access](https://aws.amazon.com/console/)

2. **AWS CLI**: Install and configure AWS CLI with your credentials
   - [Install AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
   - [Configure AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html)
   
   ```bash
   aws configure
   ```

3. **Python 3.10+** and **AWS CDK v2** installed
   ```bash
   # Install CDK
   npm install -g aws-cdk
   
   # Verify installation
   cdk --version
   ```

4. **CDK version 2.218.0 or later** (for BedrockAgentCore support)

5. **Bedrock Model Access**: Enable the global Claude Sonnet 4.6 and Claude Haiku 4.5 inference profiles. The execution role permits these profiles and their destination models; your organization's Region policies must also allow them.
   - [Bedrock Model Access Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)

6. **Required Permissions**: Your AWS user/role needs permissions for:
   - CloudFormation stack operations
   - ECR repository management
   - IAM role creation
   - Lambda function creation
   - CodeBuild project creation
   - BedrockAgentCore resource creation (Runtime, Browser, CodeInterpreter, Memory)
   - S3 bucket operations (for CDK assets and results storage)

## Deployment

### CDK vs CloudFormation

This is the **CDK version** of the end-to-end weather agent. If you prefer CloudFormation, see the [CloudFormation version](../../cloudformation/end-to-end-weather-agent/).

### Option 1: Quick Deploy (Recommended)

```bash
# Install dependencies
pip install -r requirements.txt

# Bootstrap CDK (first time only)
cdk bootstrap

# Deploy
cdk deploy
```

### Option 2: Step by Step

```bash
# 1. Create and activate Python virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Bootstrap CDK in your account/region (first time only)
cdk bootstrap

# 4. Synthesize the CloudFormation template (optional)
cdk synth

# 5. Deploy the stack
cdk deploy --require-approval never

# 6. Get outputs
cdk list
```

### Deployment Time

- **Expected Duration**: 15-20 minutes
- **Main Steps**:
  - Stack creation: ~2 minutes
  - Docker image build (CodeBuild): ~10-12 minutes
  - Runtime and tools provisioning: ~3-5 minutes
  - Memory initialization: ~1 minute

## Testing

### Offline results-storage contract tests

From the repository root, with the sample's `agent-code/requirements.txt` installed in a Python 3.11 environment:

```bash
python -m pip install PyYAML  # shared tests parse the standalone CloudFormation template
python -m unittest discover -s 04-infrastructure-as-code/tests -v
```

The tests check tool registration, browser and interpreter session cleanup, and fixed-destination S3 writes with controlled service responses. They do not deploy resources or call a model.

The runtime image build checks the installed dependency graph and records package download URLs and hashes in `/tmp/runtime-dependencies.json` inside the image. Dependencies are still resolved at build time; this report is evidence of the selected graph, not a lockfile.

### Using AWS CLI

```bash
# Get the Runtime ARN from CDK outputs
RUNTIME_ARN=$(aws cloudformation describe-stacks \
  --stack-name WeatherAgentDemo \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`AgentRuntimeArn`].OutputValue' \
  --output text)

# Get the S3 bucket name
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name WeatherAgentDemo \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ResultsBucketName`].OutputValue' \
  --output text)

# Invoke the agent
aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn $RUNTIME_ARN \
  --qualifier DEFAULT \
  --payload $(echo '{"prompt": "What should I do this weekend in Richmond VA?"}' | base64) \
  response.json

# View the immediate response
cat response.json

# Wait a few minutes for processing, then check S3 for results
aws s3 ls s3://$BUCKET_NAME/

# Download the results
aws s3 cp s3://$BUCKET_NAME/results.md ./results.md
cat results.md
```

### Using AWS Console

1. Navigate to [Bedrock AgentCore Console](https://console.aws.amazon.com/bedrock-agentcore/)
2. Go to "Runtimes" in the left navigation
3. Find your runtime (name starts with `WeatherAgentDemo_`)
4. Click on the runtime name
5. Click "Test" button
6. Enter test payload:
   ```json
   {
     "prompt": "What should I do this weekend in Richmond VA?"
   }
   ```
7. Click "Invoke"
8. View the immediate response
9. Wait 2-3 minutes for background processing
10. Navigate to [S3 Console](https://console.aws.amazon.com/s3/) to download results.md from the results bucket

## Sample Queries

Try these queries to test the weather agent:

1. **Weekend Planning**:
   ```json
   {"prompt": "What should I do this weekend in Richmond VA?"}
   ```

2. **Specific City**:
   ```json
   {"prompt": "Plan activities for next week in San Francisco"}
   ```

3. **Different Location**:
   ```json
   {"prompt": "What outdoor activities can I do in Seattle this week?"}
   ```

4. **Vacation Planning**:
   ```json
   {"prompt": "I'm visiting Austin next week. What should I plan based on the weather?"}
   ```

## How It Works

### Step-by-Step Workflow

1. **User Query**: "What should I do this weekend in Richmond VA?"

2. **City Extraction**: Agent extracts "Richmond VA" from the query

3. **Weather Scraping** (Browser Tool):
   - Navigates to weather.gov
   - Searches for Richmond VA
   - Clicks "Printable Forecast"
   - Extracts 8-day forecast data (date, high, low, conditions, wind, precipitation)
   - Returns JSON array of weather data

4. **Code Generation** (LLM):
   - Agent generates Python code to classify weather days
   - Classification rules:
     - GOOD: 65-80°F, clear, no rain
     - OK: 55-85°F, partly cloudy, slight rain
     - POOR: <55°F or >85°F, cloudy/rainy

5. **Code Execution** (Code Interpreter):
   - Executes the generated Python code
   - Returns list of tuples: `[('2025-09-16', 'GOOD'), ('2025-09-17', 'OK'), ...]`

6. **Preference Retrieval** (Memory):
   - Fetches user activity preferences from memory
   - Preferences stored by weather type:
     ```json
     {
       "good_weather": ["hiking", "beach volleyball", "outdoor picnic"],
       "ok_weather": ["walking tours", "outdoor dining", "park visits"],
       "poor_weather": ["indoor museums", "shopping", "restaurants"]
     }
     ```

7. **Recommendation Generation** (LLM):
   - Combines weather analysis with user preferences
   - Creates day-by-day activity recommendations
   - Formats as Markdown document

8. **Storage** (S3 via the `save_activity_plan` tool):
   - Saves recommendations as `results.md` using boto3 `PutObject` in the stack-provided `RESULTS_BUCKET`
   - The tool does not accept arbitrary bucket names, object keys, or AWS operations. Empty input and upload failures return an error rather than claiming success.
   - User can download and review recommendations

### Asynchronous Processing

The agent runs asynchronously to handle long-running tasks:
- Immediate response: "Processing started..."
- Background processing: Completes all steps
- Results available in S3 after ~2-3 minutes

## Cleanup

### Using CDK (Recommended)

```bash
cdk destroy
```

### Using AWS CLI

```bash
# Step 1: Empty the S3 bucket (required before deletion)
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name WeatherAgentDemo \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ResultsBucketName`].OutputValue' \
  --output text)

aws s3 rm s3://$BUCKET_NAME --recursive

# Step 2: Terminate any active browser sessions
# Get the Browser ID
BROWSER_ID=$(aws cloudformation describe-stacks \
  --stack-name WeatherAgentDemo \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`BrowserId`].OutputValue' \
  --output text)

# List active sessions
aws bedrock-agentcore list-browser-sessions \
  --browser-id $BROWSER_ID \
  --region us-east-1

# Terminate each active session (replace SESSION_ID with actual session ID from list command)
# Repeat this command for each active session
aws bedrock-agentcore terminate-browser-session \
  --browser-id $BROWSER_ID \
  --session-id SESSION_ID \
  --region us-east-1

# Step 3: Delete the stack
aws cloudformation delete-stack \
  --stack-name WeatherAgentDemo \
  --region us-east-1

# Wait for deletion to complete
aws cloudformation wait stack-delete-complete \
  --stack-name WeatherAgentDemo \
  --region us-east-1
```

**Important**: Browser sessions are automatically created when the agent uses the browser tool. Always terminate active sessions before deleting the stack to avoid deletion failures.

### Using AWS Console

1. Navigate to [S3 Console](https://console.aws.amazon.com/s3/)
2. Find the bucket (name format: `<stack-name>-results-<account-id>`)
3. Empty the bucket
4. Navigate to [Bedrock AgentCore Console](https://console.aws.amazon.com/bedrock-agentcore/)
5. Go to "Browsers" in the left navigation
6. Find your browser (name starts with `WeatherAgentDemo_browser`)
7. Click on the browser name
8. In the "Sessions" tab, terminate any active sessions
9. Navigate to [CloudFormation Console](https://console.aws.amazon.com/cloudformation/)
10. Select the `WeatherAgentDemo` stack
11. Click "Delete"
12. Confirm deletion

## Troubleshooting

### CDK Bootstrap Required

If you see bootstrap errors:
```bash
cdk bootstrap aws://ACCOUNT-NUMBER/REGION
```

### Permission Issues

Ensure your IAM user/role has:
- `CDKToolkit` permissions or equivalent
- Permissions to create all resources in the stack
- `iam:PassRole` for service roles

### Python Dependencies

Install dependencies in the project directory:
```bash
pip install -r requirements.txt
```

### Build Failures

Check CodeBuild logs in the AWS Console:
1. Go to CodeBuild console
2. Find the build project (name contains "weather-agent-build")
3. Check build history and logs

### Browser Session Issues

If deployment fails due to browser sessions:
1. List active sessions using AWS CLI
2. Terminate all active sessions
3. Retry deployment or cleanup

### Memory Initialization Issues

If memory initialization fails:
1. Check Lambda function logs in CloudWatch
2. Verify IAM permissions for memory access
3. Retry deployment
