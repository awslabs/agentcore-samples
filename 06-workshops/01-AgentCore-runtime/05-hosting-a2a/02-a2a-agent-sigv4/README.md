# AgentCore A2A with IAM Authentication Sample

This sample demonstrates how to deploy an A2A (Agent-to-Agent) agent on Amazon Bedrock AgentCore Runtime using AWS IAM for inbound authentication. It combines the A2A protocol with IAM-based authentication, providing a secure way to deploy agents that communicate using AWS credentials.

## Architecture

```
┌─────────────┐         IAM Auth          ┌──────────────────┐
│   Client    │ ────────────────────────> │  A2A Agent       │
│  (SigV4)    │                           │  (AgentCore)     │
└─────────────┘                           └──────────────────┘
```

## Key Features

* A2A protocol for agent-to-agent communication
* AWS IAM (SigV4) authentication
* Strands framework for agent implementation
* Deployment to AgentCore Runtime

## Prerequisites

* Python 3.10+
* AWS CLI configured with credentials
* Docker running
* pip installed

## Installation

```bash
pip install -r requirements.txt
```

## Quick Start

### Option 1: Using Jupyter Notebook (Recommended)

```bash
jupyter notebook hosting_a2a_iam_auth.ipynb
```

Follow the step-by-step instructions in the notebook.

### Option 2: Manual Deployment

#### Step 1: Test Locally (Optional)

```bash
# Terminal 1: Start the agent
python agent.py

# Terminal 2: Test the agent card
curl http://localhost:9000/.well-known/agent-card.json | jq .

# Terminal 2: Send a test message
curl -X POST http://localhost:9000 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-001",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "Hello! What can you do?"
        }],
        "messageId": "test-001"
      }
    }
  }' | jq .
```

#### Step 2: Deploy to AgentCore Runtime

```bash
python deploy.py
```

The script will:
1. Build and push a Docker image to ECR
2. Create an execution role with necessary permissions
3. Deploy the agent to AgentCore Runtime
4. Output the agent ARN

#### Step 3: Test Deployed Agent

```bash
# Set the agent ARN from the deploy output
export AGENT_ARN="arn:aws:bedrock-agentcore:us-east-1:..."

# Run the test client
python client.py
```

## Expected Output

```
INFO:__main__:Using AWS region: us-east-1
INFO:__main__:Testing agent: arn:aws:bedrock-agentcore:...
INFO:__main__:Session ID: ...
INFO:__main__:Fetching agent card...
INFO:__main__:Agent: A2A IAM Auth Agent
INFO:__main__:Description: A simple A2A agent demonstrating IAM authentication...

============================================================
INFO:__main__:Sending message: Hello! What can you do?

INFO:__main__:Agent response:
I am an A2A agent deployed on Amazon Bedrock AgentCore Runtime...
```

## Troubleshooting

### Docker Not Running

```
Error: Cannot connect to the Docker daemon
Solution: Start Docker Desktop or Docker daemon
```

### AWS Credentials Not Configured

```
Error: Unable to locate credentials
Solution: Run 'aws configure' or set AWS_PROFILE
```

### Permission Errors

The deployment requires these IAM permissions:
- `bedrock-agentcore:*` - AgentCore operations
- `ecr:*` - Container registry
- `iam:CreateRole`, `iam:PutRolePolicy` - Execution role creation
- `codebuild:*` - Building container images
- `logs:*` - CloudWatch logs access

The execution role needs:
- `ecr:GetAuthorizationToken`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` - ECR access
- `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` - Bedrock model access
- `logs:*` - CloudWatch logs
- `bedrock-agentcore:GetWorkloadAccessToken*` - Workload identity

See `execution-role-policy.json` for the complete execution role policy.

## Cleanup

```python
from bedrock_agentcore_starter_toolkit.operations.runtime import destroy_bedrock_agentcore
from pathlib import Path

destroy_bedrock_agentcore(
    config_path=Path(".bedrock-agentcore-config.yaml"),
    region="us-east-1"
)
```

## Files

* `agent.py` - A2A agent implementation with tools
* `client.py` - Client to test the deployed agent with IAM auth
* `deploy.py` - Deployment script
* `requirements.txt` - Python dependencies
* `execution-role-policy.json` - IAM policy for the execution role
* `hosting_a2a_iam_auth.ipynb` - Step-by-step tutorial notebook

## References

* [AgentCore Runtime Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)
* [A2A Protocol Specification](https://a2a-protocol.org/dev/specification/)
* [Strands Agents Framework](https://strandsagents.com/latest/)
