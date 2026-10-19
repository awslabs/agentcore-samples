# AI Research Crew with Amazon Bedrock AgentCore

| Information         | Details                                                                      |
|---------------------|------------------------------------------------------------------------------|
| Agent type          | Synchronous                                                                 |
| Agentic Framework   | CrewAI                                                                      |
| LLM model           | Anthropic Claude 3 Sonnet                                                   |
| Components          | AgentCore Runtime, Multi-agent collaboration                                |
| Example complexity  | Medium                                                                      |
| SDK used            | Amazon BedrockAgentCore Python SDK                                          |

This example demonstrates how to integrate a CrewAI multi-agent system with AWS Bedrock AgentCore, enabling you to deploy collaborative AI agents that can perform comprehensive research tasks as a managed service.

## Project Overview

AI Research Crew is designed to help you set up a multi-agent AI system with ease. The agents collaborate effectively on complex research tasks, maximizing their collective intelligence and capabilities. With Amazon Bedrock AgentCore integration, you can deploy and manage these agents as serverless applications or containerized services.

## Prerequisites

* Python >=3.10 <3.14
* AWS CLI configured with appropriate permissions
* Amazon Bedrock access with Claude models enabled
* Docker (for containerized deployment)
* [UV](https://docs.astral.sh/uv/) for dependency management (optional)
* [jq](https://jqlang.github.io/jq/) for JSON processing in command-line scripts

## Quick Start Guide

### 1. Set up the environment

Clone the repository and navigate to the project directory:

```bash
git clone https://github.com/aws-samples/amazon-bedrock-agentcore-samples.git
cd amazon-bedrock-agentcore-samples/03-integrations/01-agentic-frameworks/04-crewai/ai_research_crew
```

### 2. Install dependencies

Using UV (recommended):

```bash
pip install uv
uv pip install -e .
```

Or using pip:

```bash
pip install -e .
```

### 3. Configure environment variables

Create a `.env` file in the root directory with the following variables:

```bash
AWS_REGION=us-west-2
MODEL=bedrock/us.anthropic.claude-3-7-sonnet-20250219-v1:0
```

## User Guide

### Running Locally

To run the AI Research Crew locally:

```bash
# Set your AWS profile (with access to used models)
export AWS_PROFILE=your-profile

# Run the application with a research prompt
python -m src.ai_research_crew.main "Research the latest advancements in LLMs"
```

The research report will be saved to the `output/report.md` file.

### Running in a Container

You have two options for running the AI Research Crew in a containerized environment:

#### Option 1: Using Docker directly

1. Build the Docker image:
   ```bash
   # Basic build
   docker build -t bedrock_agentcore-ai_research_crew .
   
   # Build with no cache (for clean rebuild)
   docker build --no-cache -t bedrock_agentcore-ai_research_crew .
   
   # Build with a specific tag
   docker build -t bedrock_agentcore-ai_research_crew:v1.0 .
   ```

2. Run the container:
   ```bash
   # Create output directory with proper permissions if it doesn't exist
   mkdir -p output
   chmod 777 output
   
   # Stop and remove existing container if it exists
   docker stop bedrock_agentcore 2>/dev/null || true
   docker rm bedrock_agentcore 2>/dev/null || true
   
   # Set your AWS profile
   export AWS_PROFILE=default  # Change to your AWS profile name
   
   # Basic container run
   docker run -d -p 8080:8080 \
     -e DISABLE_AUTH=true \
     -e AWS_REGION=us-west-2 \
     -e AWS_DEFAULT_REGION=us-west-2 \
     -e MODEL=bedrock/us.anthropic.claude-3-7-sonnet-20250219-v1:0 \
     -e AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile $AWS_PROFILE) \
     -e AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key --profile $AWS_PROFILE) \
     -e AWS_SESSION_TOKEN=$(aws configure get aws_session_token --profile $AWS_PROFILE 2>/dev/null) \
     -v $(pwd)/output:/app/output \
     --name bedrock_agentcore bedrock_agentcore-ai_research_crew:latest
   
   # Run with OpenTelemetry disabled
   docker run -d -p 8080:8080 \
     -e DISABLE_AUTH=true \
     -e OTEL_SDK_DISABLED=true \
     -e AWS_REGION=us-west-2 \
     -e AWS_DEFAULT_REGION=us-west-2 \
     -e MODEL=bedrock/us.anthropic.claude-3-7-sonnet-20250219-v1:0 \
     -e AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile $AWS_PROFILE) \
     -e AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key --profile $AWS_PROFILE) \
     -e AWS_SESSION_TOKEN=$(aws configure get aws_session_token --profile $AWS_PROFILE 2>/dev/null) \
     -v $(pwd)/output:/app/output \
     --name bedrock_agentcore bedrock_agentcore-ai_research_crew:latest
   
   # Run with custom port mapping (e.g., port 9090)
   docker run -d -p 9090:8080 \
     -e DISABLE_AUTH=true \
     -e AWS_REGION=us-west-2 \
     -e AWS_DEFAULT_REGION=us-west-2 \
     -e MODEL=bedrock/us.anthropic.claude-3-7-sonnet-20250219-v1:0 \
     -e AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile $AWS_PROFILE) \
     -e AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key --profile $AWS_PROFILE) \
     -e AWS_SESSION_TOKEN=$(aws configure get aws_session_token --profile $AWS_PROFILE 2>/dev/null) \
     -v $(pwd)/output:/app/output \
     --name bedrock_agentcore bedrock_agentcore-ai_research_crew:latest
   
   # Run with resource limits
   docker run -d -p 8080:8080 \
     --memory=2g --cpus=1 \
     -e DISABLE_AUTH=true \
     -e AWS_REGION=us-west-2 \
     -e AWS_DEFAULT_REGION=us-west-2 \
     -e MODEL=bedrock/us.anthropic.claude-3-7-sonnet-20250219-v1:0 \
     -e AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile $AWS_PROFILE) \
     -e AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key --profile $AWS_PROFILE) \
     -e AWS_SESSION_TOKEN=$(aws configure get aws_session_token --profile $AWS_PROFILE 2>/dev/null) \
     -v $(pwd)/output:/app/output \
     --name bedrock_agentcore bedrock_agentcore-ai_research_crew:latest
   ```

3. Test the API:
   ```bash
   # Basic API test
   curl -X POST http://localhost:8080/invocations \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Research quantum computing"}'
   
   # Save response to a file
   curl -X POST http://localhost:8080/invocations \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Research advancements in AI"}' \
     > response.json
   
   # Test with custom port (if you used a different port)
   curl -X POST http://localhost:9090/invocations \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Research quantum computing"}'
   
   # Test with timeout parameter
   curl -X POST http://localhost:8080/invocations \
     --max-time 300 \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Research quantum computing"}'
   
   # Test with verbose output
   curl -v -X POST http://localhost:8080/invocations \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Research quantum computing"}'
   ```

4. Monitor container logs:
   ```bash
   # View real-time logs
   docker logs -f bedrock_agentcore
   
   # View only the last 100 lines of logs
   docker logs --tail 100 bedrock_agentcore
   
   # View logs with timestamps
   docker logs -t bedrock_agentcore
   
   # Follow logs but limit to last 50 lines
   docker logs -f --tail 50 bedrock_agentcore
   ```

5. Access the generated report:
   ```bash
   # View the report
   cat output/report.md
   
   # Copy the report to another location
   cp output/report.md ~/Documents/research_report.md
   
   # View report with line numbers
   nl output/report.md
   ```

6. Stop the container when done:
   ```bash
   # Stop the container
   docker stop bedrock_agentcore
   
   # Stop and remove the container
   docker stop bedrock_agentcore && docker rm bedrock_agentcore
   
   # Stop all running containers (use with caution)
   docker stop $(docker ps -q)
   ```

#### Option 2: Using AgentCore local mode

1. Configure AgentCore (if not already done):
   ```bash
   # Basic configuration
   agentcore configure --entrypoint src/ai_research_crew/main.py --name ai_research_crew --execution-role BedrockAgentCoreRole
   
   # Configuration with region specified
   agentcore configure --entrypoint src/ai_research_crew/main.py --name ai_research_crew --execution-role BedrockAgentCoreRole --region us-west-2
   
   # Configuration with OpenTelemetry disabled
   agentcore configure --entrypoint src/ai_research_crew/main.py --name ai_research_crew --execution-role BedrockAgentCoreRole --disable-otel
   
   # Configuration with verbose output
   agentcore configure --entrypoint src/ai_research_crew/main.py --name ai_research_crew --execution-role BedrockAgentCoreRole --verbose
   ```

2. Launch locally with AgentCore:
   ```bash
   # Basic local launch
   agentcore launch --local
   
   # Launch with specific agent name
   agentcore launch --local --agent ai_research_crew
   
   # Launch with environment variables
   agentcore launch --local --env MODEL=bedrock/us.anthropic.claude-3-7-sonnet-20250219-v1:0 --env OTEL_SDK_DISABLED=true
   ```

3. Invoke the local agent:
   ```bash
   # Basic invocation
   agentcore invoke '{"prompt": "Research quantum computing"}'
   
   # Invocation with specific agent
   agentcore invoke --agent ai_research_crew '{"prompt": "Research quantum computing"}'
   
   # Invocation with session ID for context persistence
   agentcore invoke --session-id "research-session-1" '{"prompt": "Research quantum computing"}'
   
   # Invocation with local flag (for locally running agent)
   agentcore invoke --local '{"prompt": "Research quantum computing"}'
   ```

### Deploying to AWS

To deploy the AI Research Crew to AWS Bedrock AgentCore:

1. Create an IAM role with the necessary permissions:
   ```bash
   # Create trust policy document
   cat > trust-policy.json << EOF
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": {
           "Service": "bedrock-agentcore.amazonaws.com"
         },
         "Action": "sts:AssumeRole"
       }
     ]
   }
   EOF
   
   # Create role or update existing role
   aws iam create-role --role-name BedrockAgentCoreRole --assume-role-policy-document file://trust-policy.json
   
   # Create ECR access policy document
   cat > ecr-policy.json << EOF
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "ecr:GetAuthorizationToken",
           "ecr:BatchGetImage",
           "ecr:GetDownloadUrlForLayer"
         ],
         "Resource": "*"
       }
     ]
   }
   EOF
   
   # Create ECR access policy
   aws iam create-policy --policy-name BedrockAgentCoreECRAccess --policy-document file://ecr-policy.json
   
   # Get your AWS account ID
   ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
   
   # Attach required policies
   aws iam attach-role-policy --role-name BedrockAgentCoreRole --policy-arn arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess
   aws iam attach-role-policy --role-name BedrockAgentCoreRole --policy-arn arn:aws:iam::aws:policy/AmazonBedrockAgentCoreMemoryBedrockModelInferenceExecutionRolePolicy
   aws iam attach-role-policy --role-name BedrockAgentCoreRole --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/BedrockAgentCoreECRAccess
   ```

2. Configure AgentCore:
   ```bash
   # Configure AgentCore with your execution role and entrypoint
   agentcore configure --entrypoint src/ai_research_crew/main.py --name ai_research_crew --execution-role BedrockAgentCoreRole
   ```

3. Launch to AWS:
   ```bash
   # Deploy the agent to AWS Bedrock AgentCore
   agentcore launch
   ```

4. Invoke the deployed agent:
   ```bash
   # Invoke the agent with a research prompt
   agentcore invoke '{"prompt": "Research quantum computing"}'
   
   # Invoke with a specific AWS profile
   AWS_PROFILE=your-profile agentcore invoke '{"prompt": "Research advancements in AI"}'
   ```

5. View logs:
   ```bash
   # View the agent logs in CloudWatch
   aws logs tail /aws/bedrock-agentcore/runtimes/ai_research_crew-*-DEFAULT --since 10m
   
   # View logs with a specific AWS profile
   AWS_PROFILE=your-profile aws logs tail /aws/bedrock-agentcore/runtimes/ai_research_crew-*-DEFAULT --since 10m
   ```

6. Clean up resources when done:
   ```bash
   # Delete the deployed agent stack (if you created one)
   aws cloudformation delete-stack --stack-name ai-research-crew
   
   # Delete the IAM role (optional)
   ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
   aws iam detach-role-policy --role-name BedrockAgentCoreRole --policy-arn arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess
   aws iam detach-role-policy --role-name BedrockAgentCoreRole --policy-arn arn:aws:iam::aws:policy/AmazonBedrockAgentCoreMemoryBedrockModelInferenceExecutionRolePolicy
   aws iam detach-role-policy --role-name BedrockAgentCoreRole --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/BedrockAgentCoreECRAccess
   aws iam delete-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/BedrockAgentCoreECRAccess
   aws iam delete-role --role-name BedrockAgentCoreRole
   ```

## Agent Configuration

### LLM Configuration

The agents use Amazon Bedrock Claude models. You can configure the specific model in:

1.  The `.env` file with `MODEL=bedrock/model-id`
2.  The `agents.yaml` file for each agent:

```yaml
researcher:
  llm: bedrock/us.anthropic.claude-3-7-sonnet-20250219-v1:0

reporting_analyst:
  llm: bedrock/anthropic.claude-3-sonnet-20240229-v1:0
```

## Security Setup

### Option 1: Amazon Cognito Authentication

1. Create a Cognito User Pool:
   ```bash
   # Create a user pool with email as a required attribute
   aws cognito-idp create-user-pool \
     --pool-name AIResearchCrewUsers \
     --username-attributes email \
     --auto-verified-attributes email
   ```

2. Retrieve the User Pool ID:
   ```bash
   # Get the User Pool ID for the pool you just created
   # Using head -1 to ensure we only get one ID if multiple pools with the same name exist
   USER_POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 \
     | jq -r '.UserPools[] | select(.Name=="AIResearchCrewUsers") | .Id' | head -1)
   
   # Verify the User Pool ID was found
   echo "User Pool ID: $USER_POOL_ID"
   ```

3. Create a Cognito App Client:
   ```bash
   # Create an app client using the retrieved User Pool ID
   aws cognito-idp create-user-pool-client \
     --user-pool-id $USER_POOL_ID \
     --client-name AIResearchCrewClient \
     --no-generate-secret \
     --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH
   ```

4. Get the Client ID:
   ```bash
   # Get the Client ID from the previous command's output
   CLIENT_ID=$(aws cognito-idp list-user-pool-clients \
     --user-pool-id $USER_POOL_ID \
     | jq -r '.UserPoolClients[] | select(.ClientName=="AIResearchCrewClient") | .ClientId')
   
   # Verify the Client ID was found
   echo "Client ID: $CLIENT_ID"
   ```

5. Configure AgentCore with Cognito authentication:
   ```bash
   # Construct the discovery URL for your Cognito user pool
   DISCOVERY_URL="https://cognito-idp.$(aws configure get region --profile $(aws configure get profile)).amazonaws.com/$USER_POOL_ID/.well-known/openid-configuration"
   
   # Create JSON configuration for Cognito authorizer
   COGNITO_CONFIG='{
     "customJWTAuthorizer": {
       "discoveryUrl": "'$DISCOVERY_URL'",
       "allowedClients": ["'$CLIENT_ID'"]
     }
   }'
   
   # Configure AgentCore with Cognito authorizer
   agentcore configure --entrypoint src/ai_research_crew/main.py \
     --name ai_research_crew \
     --execution-role BedrockAgentCoreRole \
     --authorizer-config "$COGNITO_CONFIG"
   ```

### Option 2: OAuth2 Provider

1. Register your application with your OAuth provider (Auth0, Okta, etc.)

2. Configure AgentCore with OAuth authentication:
   ```bash
   # Create JSON configuration for OAuth authorizer
   OAUTH_CONFIG='{
     "customJWTAuthorizer": {
       "discoveryUrl": "YOUR_OAUTH_ENDPOINT/.well-known/openid-configuration",
       "allowedClients": ["YOUR_CLIENT_ID"]
     }
   }'
   
   # Configure AgentCore with OAuth authorizer
   agentcore configure --entrypoint src/ai_research_crew/main.py \
     --name ai_research_crew \
     --execution-role BedrockAgentCoreRole \
     --authorizer-config "$OAUTH_CONFIG"
   ```

## Advanced Configuration

### Customizing the Agents and Tasks

*   Modify `src/ai_research_crew/config/agents.yaml` to define your agents
*   Modify `src/ai_research_crew/config/tasks.yaml` to define your tasks
*   Modify `src/ai_research_crew/crew.py` to add your own logic, tools and specific args

### Output Handling

The research report is saved to a file in the `output` directory. Additionally, the API response now includes the report content in the JSON response:

```json
{
  "report": "# Latest Advancements in Large Language Models (LLMs) - 2025 Report\n\n## Multimodal Integration and Reasoning\n\n..."
}
```

When running in a container, you can:

1.  Mount a volume to persist the output: `-v $(pwd)/output:/app/output`
2.  Access the report content directly from the API response

## Monitoring and Management

- View CloudWatch logs:
  ```bash
  # View logs for a specific deployment
  aws logs tail /aws/bedrock-agentcore/runtimes/ai_research_crew-*-DEFAULT --since 10m
  ```

- Check agent status:
  ```bash
  # Get status of the agent
  agentcore status
  ```

- List configured agents:
  ```bash
  # List all configured agents
  agentcore configure list
  ```

- Clean up resources:
  ```bash
  # Delete CloudFormation stack (if created)
  aws cloudformation delete-stack --stack-name ai-research-crew
  ```
## Troubleshooting

### Installation Issues

**Problem**: Dependencies fail to install
```
ERROR: Could not find a version that satisfies the requirement...
```

**Solution**:
- Ensure you're using Python >=3.10 <3.14
- Try using UV for installation: `pip install uv && uv pip install -e .`
- Check your internet connection and proxy settings

### AgentCore Configuration Issues

**Problem**: AgentCore configuration fails
```
Error: Failed to configure AgentCore
```

**Solution**:
- Verify AWS CLI is properly configured with `aws configure`
- Ensure you have the necessary permissions
- Check that the entrypoint path is correct: `src/ai_research_crew/main.py`

### Local Execution Issues

**Problem**: Agent fails to launch locally
```
Error: Failed to launch agent locally
```

**Solution**:
- Ensure your `.env` file contains the required API keys
- Check that the main.py file has the correct BedrockAgentCoreApp setup
- Verify that the entrypoint function is properly decorated with `@app.entrypoint`

### Docker Container Issues

**Problem**: Docker container fails to start or run properly
```
Error: Failed to start container
```

**Solution**:
- Ensure Docker is installed and running
- Check if port 8080 is already in use: `lsof -i :8080`
- Verify AWS credentials are being passed correctly
- Check container logs: `docker logs bedrock_agentcore`
- Ensure the output directory has proper permissions: `chmod 777 output`

### AWS Deployment Issues

**Problem**: Deployment to AWS Bedrock AgentCore fails
```
Error: Role validation failed for 'arn:aws:iam::...'
```

**Solution**:
- Verify the IAM role has the necessary permissions
- Check that the trust policy allows the Bedrock AgentCore service to assume the role
- Ensure your AWS account has Amazon Bedrock access enabled
- Verify the ECR repository exists and you have permissions to push to it

**Problem**: ECR access denied during deployment
```
Access denied while validating ECR URI. The execution role requires permissions for ecr:GetAuthorizationToken, ecr:BatchGetImage, and ecr:GetDownloadUrlForLayer operations.
```

**Solution**:
- Ensure the BedrockAgentCoreECRAccess policy is attached to your role
- Verify the policy includes the necessary ECR permissions: GetAuthorizationToken, BatchGetImage, and GetDownloadUrlForLayer
- Check that your AWS account has access to the ECR repository