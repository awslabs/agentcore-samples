#!/usr/bin/env python

# # Amazon Bedrock AgentCore Runtime and AgentCore Memory Agent with Identity isolation
#
# ## Overview
#
# This tutorial demonstrates how to create your first memory-enabled agent with user isolation using AgentCore Runtime and AgentCore Memory. You'll build a simple "Hello World" conversational agent that remembers previous interactions within a session, enabling more natural and contextual conversations with users across interactions.
#
# Memory is a critical component for creating effective conversational agents, as it allows them to maintain context, recall user preferences, and provide consistent responses over time. Without memory, your agent would have to start from scratch with each interaction, leading to a disjointed user experience.
#
# The implementation leverages Amazon Bedrock AgentCore Memory with user identity propagation to automatically partition memory based on authenticated user credentials, creating secure, isolated memory spaces for each individual user.
#
# ### Tutorial Details
#
#
# | Information         | Details                                                          |
# |:--------------------|:-----------------------------------------------------------------|
# | Tutorial type       | Hello World / Introduction                                       |
# | Agent type          | Single Conversational Agent                                      |
# | Agentic Framework   | Strands Agents                                                   |
# | LLM model           | Anthropic Claude Haiku 3.5                                      |
# | Key features        | AgentCore Runtime, Memory Integration                            |
# | Example complexity  | Intermediate                                                         |
# | SDK used            | boto3, bedrock-agentcore                                         |
#
# ### What You'll Learn
#
# In this tutorial, you'll learn:
# 1. How to create a memory resource for your agent using AgentCore Memory
# 2. How to implement memory hooks to store and retrieve conversation history
# 3. How to deploy your agent to AgentCore Runtime for scalable production use
# 4. How to test your agent with session management and verify memory persistence
# 5. How to handle user identity and ensure memory isolation between different users
#
#
# ### Architecture
#
# This Hello World example demonstrates a simple conversational agent deployed to AgentCore runtime with memory integration:
#
# <div style="text-align:left">
#     <img src="runtime-memory-identity.png" width="90%"/>
# </div>
#

# ## 0. Prerequisites
#
# To execute this tutorial you will need:
# * Python 3.10 or newer
# * AWS credentials configured with appropriate permissions for Bedrock, ECR, IAM, and Cognito
# * Amazon Bedrock model access (Claude 3.5 Haiku)
# * Amazon Bedrock AgentCore SDK and dependencies
#
# First, let's install the required libraries.


# Run: pip install -qUr requirements.txt


# ### Setting Up Environment
#
# Let's import the required libraries and configure our environment. We'll be using:
# - `boto3` for AWS service interactions
# - `bedrock_agentcore.memory` for managing agent memory
# - Various utility functions for setting up authentication


# Imports
import os
import time
import boto3
import uuid
import logging
from bedrock_agentcore.memory import MemoryClient
from utils import setup_cognito_user_pool

# Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("runtime-memory-agent")
REGION = os.getenv("AWS_REGION", "us-west-2")
memory_client = MemoryClient(region_name=REGION)


# ## 1. Creating the Amazon Cognito User Pool
#
# In this section, we'll create an Amazon Cognito User Pool and users. Cognito provides user authentication and identity management for our agent, ensuring that each user's conversation history is accessible only to that user.
#
# The `setup_cognito_user_pool` function will:
# 1. Create a Cognito User Pool if it doesn't exist
# 2. Set up app clients for authentication
# 3. Create 2 test users with temporary passwords
# 4. Generate access tokens for testing


print("Setting up Amazon Cognito user pool and users...")
cognito_config = setup_cognito_user_pool(region=REGION)
print("Cognito setup completed ✓")


# ## 2. Creating Memory Resource
#
# In this section, we'll create a memory resource for our agent to store conversation history. Memory allows the agent to recall past interactions, maintain context, and provide more coherent responses over time.
#
# For this example, we'll create a simple short-term memory resource without any additional long-term strategies. The memory will store all conversation messages, helping our agent remember previous interactions when continuing a session after it has been terminated in AgentCore Runtime.


from botocore.exceptions import ClientError  # noqa: E402

# Create unique identifier for this resource
unique_id = str(uuid.uuid4())[:8]
memory_name = f"RuntimeIdentityMemoryAgent_{unique_id}"

try:
    # Create memory resource without strategies (short-term memory only)
    memory = memory_client.create_memory_and_wait(
        name=memory_name,
        strategies=[],  # No strategies for short-term memory
        description="Short-term memory for AgentCore Runtime agent authenticated with AgentCore Identity",
        event_expiry_days=7,  # Retention period for short-term memory
    )
    memory_id = memory["id"]
    logger.info(f"✅ Created memory: {memory_id}")
except ClientError as e:
    logger.info(f"❌ ERROR: {e}")
    if e.response["Error"]["Code"] == "ValidationException" and "already exists" in str(e):
        # If memory already exists, retrieve its ID
        memories = memory_client.list_memories()
        memory_id = next((m["id"] for m in memories if m["id"].startswith(memory_name)), None)
        logger.info(f"Memory already exists. Using existing memory ID: {memory_id}")
except Exception as e:
    # Show any errors during memory creation
    logger.error(f"❌ ERROR: {e}")
    import traceback

    traceback.print_exc()
    # Cleanup on error - delete the memory if it was partially created
    if "memory_id" in locals() and memory_id:
        try:
            memory_client.delete_memory_and_wait(memory_id=memory_id)
            logger.info(f"Cleaned up memory: {memory_id}")
        except Exception as cleanup_error:
            logger.error(f"Failed to clean up memory: {cleanup_error}")


# ## 3. Creating Your Memory-Enabled Agent
#
# In this section, we'll build our memory-enabled agent using Strands Agents framework with custom hooks for memory integration. This agent will maintain conversation context by storing and retrieving messages from AgentCore Memory.
#
# > **Why Memory Matters**: Sessions in AgentCore runtime expire after a certain time, which deletes the conversation context. By storing conversations in memory, we ensure previous information persists between sessions, creating a seamless experience for users even after long breaks.
#
# ### Agent Capabilities
#
# Our agent will:
# 1. Store each user and assistant message in memory automatically
# 2. Retrieve past conversation history when continuing an existing session
# 3. Maintain context across multiple interactions with the same user
# 4. Isolate conversations between different users through user identity verification
#
# ### Key Components of Our Implementation
#
# #### 1. Memory Hook Provider
# Our custom hook provider implements:
# - `on_agent_initialized`: Triggered when the agent starts, retrieves conversation history from AgentCore Memory
# - `on_message_added`: Triggered when a new message is added to the conversation, stores it in AgentCore Memory
#
# #### 2. Agent Initialization
# The `initialize_agent` function:
# - Configures the memory hook with the correct region
# - Sets up the agent with proper state variables (memory_id, actor_id, session_id)
# - Configures the system prompt for the LLM
#
# #### 3. User verification
# The `get_user_sub` function:
# - Verifies a Cognito access token against JWKS and returns the user's sub (unique ID).
#
# #### 4. Entry Point Handler
# The runtime_memory_agent function:
# - Parses input payload and extracts user message
# - Verifies user identity using JWT tokens from Cognito
# - Manages agent initialization and session tracking
# - Handles invocation of the agent with proper context
# - Returns formatted responses to the runtime environment
#
# Let's create our agent file:


# The following content was originally written to 'runtime_identity_memory_agent.py' via %%writefile magic.
# It has been extracted to a separate file. See 'runtime_identity_memory_agent.py' in the same directory.


# ## 4. Deploying to AgentCore Runtime
#
# In this section, we'll deploy our agent to Amazon Bedrock AgentCore Runtime, a managed agent runtime environment that provides scalability and simplified operations. AgentCore Runtime handles the infrastructure complexity, allowing you to focus on your agent's logic rather than deployment concerns.
#
# Unlike traditional deployment methods that require manual server setup and management, AgentCore Runtime automatically packages your code into containers, deploys them to AWS infrastructure, and provides secure HTTPS endpoints for invocation. This approach ensures your agent can scale with demand and operate reliably in production environments.
#
# ### Behind the Scenes
#
# When you deploy to AgentCore Runtime, several things happen automatically:
# 1. Your code is packaged into a Docker container image
# 2. The container image is pushed to Amazon ECR (Elastic Container Registry)
# 3. An AWS Lambda function or container service is provisioned to run your agent
# 4. API Gateway endpoints are created for secure access
# 5. IAM roles and permissions are configured for secure operation
#
# ### What You Need to Know
#
# - **AgentCore Runtime** packages your agent into a Docker container and deploys it to managed AWS infrastructure
# - **Environment Variables** will configure our agent:
#   - `MEMORY_ID`: The memory resource we created earlier
#   - `MODEL_ID`: Claude 3.5 Haiku model ID
#   - `AWS_REGION`: AWS region for deployment
#   - `COGNITO_USER_POOL`: The Cognito user pool for authentication
#
# > 💡 **Tip**: AgentCore Runtime uses S3-based code deployment — package your code and dependencies into a zip, upload to S3, and create the runtime via the `bedrock-agentcore-control` boto3 client.
#
# ### Configure the Deployment
#
# Let's set up our deployment configuration:


import json  # noqa: E402
import subprocess  # noqa: E402
import shutil  # noqa: E402
import requests  # noqa: E402
from botocore.auth import SigV4Auth  # noqa: E402
from botocore.awsrequest import AWSRequest  # noqa: E402

# ── Deploy to AgentCore Runtime (native boto3 S3 code deployment) ─────────────

agent_name = f"runtime_memory_agent_{unique_id}"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "runtime_identity_memory_agent.py"
AGENT_FILES = ["runtime_identity_memory_agent.py"]
ACCOUNT_ID = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{agent_name}/code.zip"

# Create IAM execution role
iam = boto3.client("iam", region_name=REGION)
role_name = f"agentcore-{agent_name[:40]}-role"

trust_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT_ID}},
        }
    ],
}
inline_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
            "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"],
        },
        {"Effect": "Allow", "Action": ["logs:DescribeLogGroups"], "Resource": ["*"]},
        {
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": [
                f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
            ],
        },
        {
            "Effect": "Allow",
            "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
            "Resource": ["*"],
        },
        {
            "Effect": "Allow",
            "Action": "cloudwatch:PutMetricData",
            "Resource": "*",
            "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
        },
        {
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": [
                "arn:aws:bedrock:*::foundation-model/*",
                f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:*",
            ],
        },
        {"Effect": "Allow", "Action": ["bedrock-agentcore:*"], "Resource": ["*"]},
    ],
}
try:
    resp = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description=f"Execution role for {agent_name}",
    )
    role_arn = resp["Role"]["Arn"]
    logger.info(f"✅ Created IAM role: {role_arn}")
except iam.exceptions.EntityAlreadyExistsException:
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    logger.info(f"✅ IAM role exists: {role_arn}")
iam.put_role_policy(
    RoleName=role_name,
    PolicyName=f"{agent_name}-policy",
    PolicyDocument=json.dumps(inline_policy),
)
logger.info("  Waiting 10s for IAM propagation...")
time.sleep(10)

# Build and upload deployment package
s3 = boto3.client("s3", region_name=REGION)
pkg_dir = "deployment_package"
zip_file = "deployment_package.zip"

try:
    if REGION == "us-east-1":
        s3.create_bucket(Bucket=S3_BUCKET)
    else:
        s3.create_bucket(Bucket=S3_BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    logger.info(f"✅ Created S3 bucket: {S3_BUCKET}")
except Exception:
    logger.info(f"✅ S3 bucket exists: {S3_BUCKET}")

if os.path.isdir(pkg_dir):
    shutil.rmtree(pkg_dir)
if os.path.exists(zip_file):
    os.remove(zip_file)

logger.info("  Installing arm64 dependencies with uv...")
subprocess.run(
    [
        "uv",
        "pip",
        "install",
        "--python-platform",
        "aarch64-manylinux2014",
        "--python-version",
        "3.13",
        "--target",
        pkg_dir,
        "--only-binary",
        ":all:",
        "-r",
        "requirements.txt",
    ],
    check=True,
)
logger.info("  Creating deployment zip...")
subprocess.run(["zip", "-r", f"../{zip_file}", "."], cwd=pkg_dir, check=True, capture_output=True)
for src_file in AGENT_FILES:
    subprocess.run(["zip", zip_file, src_file], check=True, capture_output=True)
logger.info(f"  Uploading to s3://{S3_BUCKET}/{S3_PREFIX}...")
s3.upload_file(zip_file, S3_BUCKET, S3_PREFIX)
shutil.rmtree(pkg_dir)
os.remove(zip_file)
logger.info("✅ Deployment package uploaded")

# Create AgentCore Runtime with JWT authorizer
control = boto3.client("bedrock-agentcore-control", region_name=REGION)
logger.info(f"  Creating AgentCore Runtime '{agent_name}'...")
runtime_response = control.create_agent_runtime(
    agentRuntimeName=agent_name,
    agentRuntimeArtifact={
        "codeConfiguration": {
            "code": {"s3": {"bucket": S3_BUCKET, "prefix": S3_PREFIX}},
            "runtime": PYTHON_RUNTIME,
            "entryPoint": [ENTRY_POINT],
        }
    },
    roleArn=role_arn,
    networkConfiguration={"networkMode": "PUBLIC"},
    protocolConfiguration={"serverProtocol": "HTTP"},
    description="Memory-enabled agent with identity isolation — tutorial example",
    environmentVariables={
        "MEMORY_ID": memory_id,
        "MODEL_ID": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "AWS_REGION": REGION,
        "COGNITO_USER_POOL": cognito_config["pool_id"],
    },
    authorizerConfiguration={
        "customJWTAuthorizer": {
            "discoveryUrl": cognito_config.get("discovery_url"),
            "allowedClients": [cognito_config.get("client_id")],
        }
    },
    requestHeaderConfiguration={"requestHeaderAllowlist": ["Authorization"]},
)
runtime_id = runtime_response["agentRuntimeId"]
logger.info(f"  ✅ Runtime created: {runtime_id}")

# Wait for READY
logger.info("  Waiting for runtime to be ready...")
end_status = ["READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"]
while True:
    status_resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
    status = status_resp["status"]
    logger.info(f"  Status: {status}")
    if status in end_status:
        break
    time.sleep(15)

if status == "READY":
    print("✅ Agent successfully deployed!")
else:
    print(f"❌ Deployment ended with status: {status}")

# Create endpoint
ep_resp = control.create_agent_runtime_endpoint(agentRuntimeId=runtime_id, name="default")
logger.info(f"  Endpoint created: {ep_resp['agentRuntimeEndpointArn']}")
logger.info("  Waiting for endpoint to be ready...")
endpoint_url = None
while True:
    eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
    for ep in eps.get("runtimeEndpoints", []):
        if ep["name"] == "default" and ep["status"] == "READY":
            endpoint_url = ep.get("liveEndpointUri")
            break
    if endpoint_url:
        break
    time.sleep(15)
logger.info(f"✅ Endpoint ready: {endpoint_url}")


def invoke_agent(payload, session_id, bearer_token=None):
    """Invoke the deployed AgentCore Runtime agent with SigV4 signing."""
    aws_req = AWSRequest(
        method="POST",
        url=f"{endpoint_url}?sessionId={session_id}",
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(boto3.Session().get_credentials(), "bedrock-agentcore", REGION).add_auth(aws_req)
    headers = dict(aws_req.headers)
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return requests.post(aws_req.url, data=aws_req.body, headers=headers, timeout=30).json()  # nosec B113


# ## 5. Testing Your Agent
#
# Now that our agent is deployed, let's test it by sending messages and verifying that it can remember previous interactions. We'll also test that different users have isolated memory contexts, ensuring that one user's conversation isn't visible to another user.
#
# **Important Notes on Session Management**
#
# - **Session Management**: While AgentCore Runtime will automatically generate a session ID if one isn't provided, it's recommended to explicitly manage session IDs in your application. This gives you better control over:
#   - Continuing conversations after session timeouts
#   - Creating new sessions when appropriate (e.g., user starts a new conversation)
#   - Handling multiple parallel conversations with the same user
#   - Implementing session expiration policies based on your application's needs
#
# - **Memory Persistence**: Even if a session expires in AgentCore Runtime, our agent can retrieve previous conversations from AgentCore Memory when a new session starts with the same user.
#
# Let's first define a helper function to validate JWT tokens:


def test_user_memory_isolation():
    """
    Test that each user has isolated memory in AgentCore.

    This test verifies that:
    1. Each user's conversation is stored separately
    2. The agent remembers previous interactions with each user
    3. User data is not shared between different users
    """
    print("\n" + "=" * 50)
    print("USER MEMORY ISOLATION TEST")
    print("=" * 50)

    # Create session IDs for testuser1 and testuser2
    testuser1_session_id = f"agent-session-testuser1-{int(time.time())}"
    testuser2_session_id = f"agent-session-testuser2-{int(time.time())}"

    testuser1_token = cognito_config["bearer_tokens"]["testuser1"]
    testuser2_token = cognito_config["bearer_tokens"]["testuser2"]

    # Step 1: testuser1 shares her favorite color
    print("\n" + "-" * 50)
    print("STEP 1: First user shares personal information")
    print("-" * 50)
    print('testuser1: "My favorite color is purple."')

    response1 = invoke_agent(
        {"prompt": "My favorite color is purple."},
        testuser1_session_id,
        testuser1_token,
    )
    print(f'Agent: "{response1["response"]}"')

    # Step 2: testuser2 shares his favorite food
    print("\n" + "-" * 50)
    print("STEP 2: Second user shares different information")
    print("-" * 50)
    print('testuser2: "My favorite food is pizza."')

    response2 = invoke_agent({"prompt": "My favorite food is pizza."}, testuser2_session_id, testuser2_token)
    print(f'Agent: "{response2["response"]}"')

    # Step 3: testuser1 asks about her color
    print("\n" + "-" * 50)
    print("STEP 3: First user tests agent's memory")
    print("-" * 50)
    print('testuser1: "What did I say my favorite color was?"')

    response3 = invoke_agent(
        {"prompt": "What did I say my favorite color was?"},
        testuser1_session_id,
        testuser1_token,
    )
    print(f'Agent: "{response3["response"]}"')

    # Step 4: testuser2 asks about his food
    print("\n" + "-" * 50)
    print("STEP 4: Second user tests agent's memory")
    print("-" * 50)
    print('testuser2: "What\'s my favorite food?"')

    response4 = invoke_agent({"prompt": "What's my favorite food?"}, testuser2_session_id, testuser2_token)
    print(f'Agent: "{response4["response"]}"')

    # Step 5: testuser1 asks about food (shouldn't know)
    print("\n" + "-" * 50)
    print("STEP 5: Testing memory isolation (first user)")
    print("-" * 50)
    print('testuser1: "What\'s my favorite food?"')

    response5 = invoke_agent({"prompt": "What's my favorite food?"}, testuser1_session_id, testuser1_token)
    print(f'Agent: "{response5["response"]}"')

    # Step 6: testuser2 asks about color (shouldn't know)
    print("\n" + "-" * 50)
    print("STEP 6: Testing memory isolation (second user)")
    print("-" * 50)
    print('testuser2: "What\'s my favorite color?"')

    response6 = invoke_agent({"prompt": "What's my favorite color?"}, testuser2_session_id, testuser2_token)
    print(f'Agent: "{response6["response"]}"')


test_user_memory_isolation()


# ## Key Concepts
#
# In this tutorial, you've learned several important concepts for building memory-enabled agents with AgentCore:
#
# 1. **Memory Integration**: How to use Amazon Bedrock Memory to store conversation history across sessions, enabling your agent to maintain context over time even when sessions expire.
#
# 2. **Session Management**: How to use session IDs to organize conversations and retrieve relevant history when a user returns, creating a seamless experience.
#
# 3. **AgentCore Deployment**: How to deploy your agent to a production runtime environment that handles scaling, security, and infrastructure management automatically.
#
# 4. **Memory Hooks**: How to implement custom hooks that integrate with memory services, allowing you to store and retrieve conversation history at specific points in the agent lifecycle.
#
# 5. **User Identity and Privacy**: How to use authentication to ensure that each user's conversation history is private and isolated from other users.
#
# These concepts provide a foundation for building more complex agents with persistent memory and sophisticated conversation management capabilities.

# ## Cleanup (Optional)
#
# If you no longer need the resources created in this tutorial, you can clean them up to avoid unnecessary AWS charges. This includes:
#
# 1. The AgentCore Runtime agent
# 2. The ECR repository containing the agent container image
# 3. The memory resource storing conversation history
#
# Let's first identify our resources:


# Get resource identifiers
if "runtime_id" in locals():
    print(f"Runtime ID: {runtime_id}")
else:
    print("Runtime not yet deployed")


# Only run this cell if you want to delete all resources

# 1. Delete the AgentCore Runtime
if "runtime_id" in locals():
    try:
        agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
        agentcore_control_client.delete_agent_runtime(agentRuntimeId=runtime_id)
        print(f"✅ Deleted AgentCore Runtime: {runtime_id}")
    except Exception as e:
        print(f"❌ Error deleting AgentCore Runtime: {e}")
else:
    print("No AgentCore Runtime to delete")

# 2. Delete the memory resource
if "memory_id" in locals() and memory_id:
    try:
        memory_client = MemoryClient(region_name=REGION)
        memory_client.delete_memory_and_wait(memory_id=memory_id)
        print(f"✅ Deleted memory resource: {memory_id}")
    except Exception as e:
        print(f"❌ Error deleting memory resource: {e}")
else:
    print("No memory resource to delete")

# 4. Delete the Cognito User Pool and associated resources
if "cognito_config" in locals() and cognito_config and "pool_id" in cognito_config:
    try:
        cognito_client = boto3.client("cognito-idp", region_name=REGION)

        # Get the user pool ID
        pool_id = cognito_config["pool_id"]

        # List and delete all user pool clients
        clients_response = cognito_client.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)

        for client in clients_response.get("UserPoolClients", []):
            client_id = client["ClientId"]
            cognito_client.delete_user_pool_client(UserPoolId=pool_id, ClientId=client_id)
            print(f"✅ Deleted User Pool Client: {client_id}")

        # Delete the user pool itself
        cognito_client.delete_user_pool(UserPoolId=pool_id)
        print(f"✅ Deleted Cognito User Pool: {pool_id}")

    except Exception as e:
        print(f"❌ Error deleting Cognito resources: {e}")
else:
    print("No Cognito resources to delete")

print("\n✅ Cleanup complete")


# ## Congratulations!
#
# You've successfully built and deployed your first memory-enabled agent with Amazon Bedrock AgentCore Runtime, AgentCore Identity and AgentCore Memory! This agent demonstrates several important capabilities:
#
# 1. **Memory Persistence**: Your agent can remember previous conversations.
# 2. **User Identity**: Your agent maintains separate conversation histories for different users
# 3. **Managed Infrastructure**: Your agent runs on AWS-managed infrastructure, scaling automatically as needed
#
# ### Next Steps
#
# Now that you understand the basics, you can enhance your agent in several ways:
#
# 1. **Add Tools**: Enhance your agent with tools like calculators, database connectors, or API calls to let it take actions beyond conversation
# 2. **Improve Memory**: Implement more sophisticated memory strategies with long-term memory
# 3. **Build a UI**: Create a web or mobile interface for your agent using frameworks like React, Flutter, or Swift
# 4. **Add Business Logic**: Integrate your agent with business systems like CRMs, knowledge bases, or internal tools
