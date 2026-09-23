"""
Deploy A2A Agent with IAM Authentication to AgentCore Runtime

This script deploys the agent to Amazon Bedrock AgentCore Runtime.
It will:
1. Build and push a Docker image to ECR
2. Create an execution role with necessary permissions
3. Deploy the agent to AgentCore Runtime

Note: The first deployment may fail if the auto-created execution role
is missing permissions. If this happens, manually add the permissions
from execution-role-policy.json to the role and run again.
"""

from bedrock_agentcore_starter_toolkit import Runtime
from boto3.session import Session

# Setup
boto_session = Session()
region = boto_session.region_name
account_id = boto_session.client("sts").get_caller_identity()["Account"]

print(f"Deploying to region: {region}")
print(f"Account ID: {account_id}")

agentcore_runtime = Runtime()

# Configure
agentcore_runtime.configure(
    entrypoint="agent.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="requirements.txt",
    region=region,
    protocol="A2A",
    agent_name="a2a_agent_iam",
)

# Launch (takes several minutes)
print("\nStarting deployment (this may take several minutes)...")
launch_result = agentcore_runtime.launch()

print("\n" + "=" * 60)
print("Deployment successful!")
print(f"Agent ARN: {launch_result.agent_arn}")
print("\nTo test the agent, run:")
print(f"  export AGENT_ARN='{launch_result.agent_arn}'")
print("  python client.py")
print("=" * 60)
