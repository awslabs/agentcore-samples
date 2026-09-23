# Agent Runtime Module

## Architecture & Overview

### What is the Agent Runtime?

The Agent Runtime module is the core conversational AI component of the Device Management System. It handles natural language processing, conversation management, and tool execution using Amazon Bedrock AgentCore and the Strands Agents SDK.

### Key Responsibilities
- **Natural Language Processing**: Understands user queries and generates human-like responses
- **Conversation Management**: Maintains context across multi-turn conversations
- **Tool Orchestration**: Executes device management operations through MCP tools
- **Authentication**: Manages Amazon Cognito authentication for secure access
- **Observability**: Provides comprehensive logging, tracing, and metrics

### Architecture Components
- **Strands Agent**: Core conversational AI agent using Amazon Bedrock models
- **MCP Client**: Communicates with the Gateway to access device management tools
- **Authentication Provider**: Handles Amazon Cognito OAuth token management
- **Observability Stack**: Amazon CloudWatch Logs, AWS X-Ray tracing, and custom metrics

## Prerequisites

### Required Software
- **Python 3.10+**
- **Docker** (for containerized deployment)
- **AWS CLI** (configured with appropriate permissions)

### AWS Services Access
- **Amazon Bedrock AgentCore**
- **Amazon Cognito** (for authentication)
- **Amazon CloudWatch** (for observability)
- **AWS X-Ray** (for tracing)

### Environment Dependencies
- **Gateway Module**: Must be deployed first to provide MCP server endpoint
- **Device Management Module**: AWS Lambda function must be deployed and accessible through Gateway

## Deployment Steps

### Option 1: Automated Deployment (Recommended)

```bash
# From the agent-runtime directory
chmod +x setup.sh
./setup.sh
```

### Option 2: Manual Deployment

#### Step 1: Environment Configuration
```bash
# Create .env file
cp .env.example .env
# Edit .env with your specific values:
# - AWS_REGION
# - MCP_SERVER_URL (from Gateway module)
# - COGNITO_* variables
```

#### Step 2: Install Dependencies
```bash
pip install -r requirements-runtime.txt
```

#### Step 3: Deploy Agent Runtime
```bash
python strands_agent_runtime_deploy.py
```

#### Step 4: Docker Deployment (Optional)
```bash
# Build container
docker build -t device-management-agent-runtime .

# Run container
docker run -p 8080:8080 --env-file .env device-management-agent-runtime
```

### Deployment Verification

```bash
# Test local runtime
python strands-agent-runtime.py

# Check container health (if using Docker)
curl http://localhost:8080/health

# Verify Amazon CloudWatch logs
aws logs describe-log-groups --log-group-name-prefix "/aws/bedrock-agentcore"
```

## Sample Queries

Once the agent runtime is deployed, it can process these types of natural language queries:

### Device Management
```
"List all devices in the system"
"Show me devices that are offline"
"What's the status of device DG-10016?"
```

### Configuration Management
```
"Get the settings for device DG-10005"
"Show me the WiFi configuration for all devices"
"Update the SSID for device DG-10016 to 'NewNetwork'"
```

### User and Activity Queries
```
"List all users in the system"
"Show me login activities from yesterday"
"Who accessed device DG-10016 recently?"
```

### Expected Response Format
The agent runtime returns formatted, conversational responses:

```
I found 25 devices in your system. Here are the currently offline devices:

• Factory Sensor A3 (DG-10016) - Last seen: 2 hours ago
• Warehouse Camera (DG-10022) - Last seen: 1 day ago

Would you like me to show you more details about any of these devices?
```

## Cleanup Instructions

### Stop Running Services

```bash
# Stop local runtime
# Press Ctrl+C if running in foreground

# Stop Docker container
docker stop device-management-agent-runtime
docker rm device-management-agent-runtime
```

### Remove Docker Images

```bash
# Remove built image
docker rmi device-management-agent-runtime

# Remove base images (optional)
docker image prune
```

### Clean Up Amazon CloudWatch Resources

```bash
# Delete log groups
aws logs delete-log-group --log-group-name "/aws/bedrock-agentcore/device-management-agent"

# Clean up custom metrics (they expire automatically)
```

### Remove Configuration Files

```bash
# Remove environment file (contains sensitive data)
rm .env

# Remove deployment artifacts
rm -rf __pycache__/
rm -rf .pytest_cache/
```

## Configuration

### Environment Variables

```bash
# AWS Configuration
AWS_REGION=us-west-2
AWS_DEFAULT_REGION=us-west-2

# MCP Server Configuration
MCP_SERVER_URL=https://gateway-id.gateway.bedrock-agentcore.us-west-2.amazonaws.com
BEARER_TOKEN=your-cognito-access-token

# Amazon Cognito Configuration
COGNITO_DOMAIN=your-domain.auth.us-west-2.amazoncognito.com
COGNITO_CLIENT_ID=your-client-id
COGNITO_CLIENT_SECRET=your-client-secret

# Docker Configuration
DOCKER_CONTAINER=1  # Set when running in container
```

### Observability Configuration

The agent runtime includes comprehensive observability:

#### Amazon CloudWatch Logs
- **Log Group**: `/aws/bedrock-agentcore/device-management-agent`
- **Log Level**: INFO (configurable)
- **Includes**: Request/response data, errors, performance metrics

#### AWS X-Ray Tracing
- **Service Name**: device-management-agent-runtime
- **Trace Data**: Request flows, tool executions, response times
- **Console**: https://console.aws.amazon.com/xray/home

#### Custom Metrics
- **Namespace**: DeviceManagement/AgentRuntime
- **Metrics**: Request counts, error rates, response times
- **Console**: https://console.aws.amazon.com/cloudwatch/home

## Troubleshooting

### Common Issues

**Agent runtime fails to start**:
- Check MCP_SERVER_URL is accessible
- Verify Amazon Cognito credentials are valid
- Ensure Gateway module is deployed and running

**Authentication errors**:
- Regenerate Amazon Cognito access token
- Check COGNITO_* environment variables
- Verify Amazon Cognito User Pool configuration

**Tool execution failures**:
- Verify Gateway Target is properly configured
- Check AWS Lambda function is deployed and accessible
- Review Amazon CloudWatch logs for detailed errors

### Debug Commands

```bash
# Test MCP server connectivity
curl -H "Authorization: Bearer $BEARER_TOKEN" $MCP_SERVER_URL/mcp

# Check agent runtime logs
aws logs tail /aws/bedrock-agentcore/device-management-agent --follow

# Test local agent runtime
python -c "from strands_agent_runtime import test_connection; test_connection()"
```

## Integration with Other Modules

- **Gateway Module**: Provides MCP server endpoint and authentication
- **Device Management Module**: Executes actual device operations via AWS Lambda
- **Frontend Module**: Receives processed responses for user display
