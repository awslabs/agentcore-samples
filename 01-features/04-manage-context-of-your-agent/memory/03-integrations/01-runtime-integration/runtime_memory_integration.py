#!/usr/bin/env python

# # Amazon Bedrock AgentCore Runtime and AgentCore Memory Agent
#
# ## Overview
#
# This tutorial demonstrates how to create your first memory-enabled agent using AgentCore Runtime and AgentCore Memory. You'll build a simple "Hello World" conversational agent that remembers previous interactions within a session.
#
# ### Tutorial Details
#
#
# | Information         | Details                                                          |
# |:--------------------|:-----------------------------------------------------------------|
# | Tutorial type       | Hello World / Introduction                                       |
# | Agent type          | Single Conversational Agent                                      |
# | Agentic Framework   | Strands Agents                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                      |
# | Key features        | AgentCore Runtime, Memory Integration                            |
# | Example complexity  | Beginner                                                         |
# | SDK used            | boto3, bedrock-agentcore                                         |
#
# ### What You'll Learn
#
# In this tutorial, you'll learn:
# 1. How to create a memory resource for your agent
# 2. How to use AgentCoreMemorySessionManager for automatic conversation persistence
# 3. How to deploy your agent to AgentCore Runtime
# 4. How to test your agent with session management
#
#
# ### Architecture
#
# This Hello World example demonstrates a simple conversational agent deployed to AgentCore runtime with memory integration:
#
# <div style="text-align:left">
#     <img src="RuntimeMemoryIntegration.png" width="90%"/>
# </div>
#

# ## 0. Prerequisites
#
# To execute this tutorial you will need:
# * Python 3.10+
# * AWS credentials configured
# * Amazon Bedrock model access (Claude Haiku 4.5)
# * Amazon Bedrock AgentCore SDK
#
# First, let's install the required libraries:


# ### Setting Up Environment
#
# Let's import the required libraries and configure our environment:


# Imports
import os
import boto3
import uuid
import logging
from bedrock_agentcore.memory import MemoryClient, MemorySessionManager

# Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("runtime-memory-agent")
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for the agent
memory_client = MemoryClient(region_name=REGION)


# ## 1. Creating Memory Resource
#
# In this section, we'll create a memory resource for our agent to store conversation history. Memory allows the agent to recall past interactions, maintain context, and provide more coherent responses over time.
#
# For this example, we'll create a simple short-term memory resource without any additional long-term strategies. The memory will store all conversation messages, helping our agent remember previous interactions when continuing a session after it has been terminated in AgentCore Runtime.


from botocore.exceptions import ClientError  # noqa: E402

# Create unique identifier for this resource
unique_id = str(uuid.uuid4())[:8]
memory_name = f"RuntimeMemoryAgent_{unique_id}"

try:
    # Create memory resource without strategies (short-term memory only)
    memory = memory_client.create_memory_and_wait(
        name=memory_name,
        strategies=[],  # No strategies for short-term memory
        description="Short-term memory for AgentCore Runtime agent",
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


# ## 2. Creating Your Memory-Enabled Agent
#
# In this section, we'll build our memory-enabled agent using Strands Agents framework with the built-in AgentCoreMemorySessionManager for memory integration.
#
# > **Why Memory Matters**: Sessions in AgentCore runtime expire after a certain time, which deletes the conversation context. By storing conversations in memory, we ensure previous information persists between sessions, creating a seamless experience for users even after long breaks.
#
# ### Agent Capabilities
#
# Our agent will:
# 1. Store each user and assistant message in memory automatically
# 2. Retrieve past conversation history when continuing an existing session
# 3. Maintain context across multiple interactions with the same user
#
# ### Key Components of Our Implementation
#
# #### 1. AgentCoreMemorySessionManager (Recommended)
# The built-in Strands integration that automatically handles:
# - Storing each user and assistant message in memory
# - Retrieving past conversation history when continuing an existing session
# - Managing session and actor context
#
# #### 2. Agent Initialization
# The `initialize_agent` function:
# - Configures memory using `AgentCoreMemoryConfig` with memory_id, session_id, and actor_id
# - Creates an `AgentCoreMemorySessionManager` instance
# - Sets up the agent with the session_manager
#
# #### 3. Entry Point Handler
# The runtime_memory_agent function:
# - Parses input payload and extracts user message
# - Manages agent initialization and session tracking
# - Handles invocation of the agent with proper context
# - Returns formatted responses to the runtime environment
#
# Let's create our agent file:


# The following content was originally written to 'runtime_memory_agent.py' via %%writefile magic.
# It has been extracted to a separate file. See 'runtime_memory_agent.py' in the same directory.


# ## 3. Deploying to AgentCore Runtime
#
# In this section, we'll deploy our agent to Amazon Bedrock AgentCore Runtime, a managed agent runtime environment that provides scalability and simplified operations. AgentCore Runtime handles the infrastructure complexity, allowing you to focus on your agent's logic rather than deployment concerns.
#
# Unlike traditional deployment methods that require manual server setup and management, AgentCore Runtime automatically packages your code into containers, deploys them to AWS infrastructure, and provides secure HTTPS endpoints for invocation. This approach ensures your agent can scale with demand and operate reliably in production environments.
#
# ### What You Need to Know
#
# - **AgentCore Runtime** packages your agent into a Docker container and deploys it to managed AWS infrastructure
# - **Environment Variables** will configure our agent:
#   - `MEMORY_ID`: The memory resource we created earlier
#   - `MODEL_ID`: Claude Haiku 4.5 model ID
#   - `AWS_REGION`: AWS region for deployment
#
# > 💡 **Tip**: The AgentCore starter toolkit handles all the complex deployment steps for us, including IAM roles, ECR repositories, and container builds.
#
# ### Configure the Deployment
#
# Let's set up our deployment configuration:


import time  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import shutil  # noqa: E402

# ── Deploy to AgentCore Runtime (native boto3 S3 code deployment) ─────────────

agent_name = f"runtime_memory_agent_{unique_id}"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "runtime_memory_agent.py"
AGENT_FILES = ["runtime_memory_agent.py"]
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

# Create AgentCore Runtime
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
    description="Memory-enabled agent — tutorial example",
    environmentVariables={
        "MEMORY_ID": memory_id,
        "MODEL_ID": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    },
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
    logger.info("✅ Agent successfully deployed!")
else:
    logger.error(f"❌ Deployment ended with status: {status}")

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


# ## Testing Your Agent
#
# Now that our agent is deployed, let's test it by sending a message.
#
# **Important Notes on Actor ID and Session Management**
#
# - Actor ID: In production applications, the actor_id would typically come from your authentication system when a user logs in. This identifier helps the agent maintain separate conversation histories for different users. In our example, we're using a hardcoded value (test_user_123), but in real-world scenarios, you would pass the authenticated user's unique identifier.
#
# - Session Management: While AgentCore Runtime will automatically generate a session ID if one isn't provided, it's recommended to explicitly manage session IDs in your application. This gives you better control over:
#   - Continuing conversations after session timeouts
#   - Creating new sessions when appropriate (e.g., user starts a new conversation)
#   - Handling multiple parallel conversations with the same user
#   - Implementing session expiration policies based on your application's needs
#


# Generate a test session ID
test_session_id = "agent-runtime-memory-session-123456789"  # Min length is 33

# Send our first message.
# Use sigv4 signing via requests + botocore
from botocore.auth import SigV4Auth  # noqa: E402
from botocore.awsrequest import AWSRequest  # noqa: E402
import requests  # noqa: E402

aws_request = AWSRequest(
    method="POST",
    url=f"{endpoint_url}?sessionId={test_session_id}",
    data=json.dumps(
        {
            "prompt": "Hello! My name is John. What can you do?",
            "actor_id": "test_user_123",
        }
    ),
    headers={"Content-Type": "application/json"},
)
SigV4Auth(boto3.Session().get_credentials(), "bedrock-agentcore", REGION).add_auth(aws_request)
invoke_response = requests.post(  # nosec B113
    aws_request.url, data=aws_request.body, headers=dict(aws_request.headers)
)
print(invoke_response.json())


# ### Display the agent's response
#
# Let's display the response in a more readable format:


import json  # noqa: E402

# The entrypoint returns a bare string, which the runtime serializes as JSON, so
# `.json()` yields that string directly. (invoke_response is a requests.Response —
# it is NOT subscriptable, so `invoke_response["response"]` would raise TypeError.)
response_text = invoke_response.json()
# display(Markdown(response_text))  # notebook display removed


# ### Test persistence
#
# Now let's test if our agent remembers the previous interaction by sending a follow-up message in the same session:


# Send a follow-up message using the same session ID
follow_up_request = AWSRequest(
    method="POST",
    url=f"{endpoint_url}?sessionId={test_session_id}",
    data=json.dumps({"prompt": "What is my name?", "actor_id": "test_user_123"}),
    headers={"Content-Type": "application/json"},
)
SigV4Auth(boto3.Session().get_credentials(), "bedrock-agentcore", REGION).add_auth(follow_up_request)
follow_up_response = requests.post(  # nosec B113
    follow_up_request.url,
    data=follow_up_request.body,
    headers=dict(follow_up_request.headers),
)
follow_up_text = follow_up_response.json()
print(follow_up_text)


# ### Verify memory content
#
# Let's check what's stored in our memory to confirm our messages were properly saved:


# Use MemorySessionManager for session operations
manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
session = manager.create_memory_session(actor_id="test_user_123", session_id=test_session_id)

# Get conversation history using session-scoped method
stored_turns = session.get_last_k_turns(k=10)

print(f"Found {len(stored_turns)} conversation turns in memory (shown in chronological order):")
for idx, turn in enumerate(stored_turns):
    print(f"\nTurn {idx + 1}:")
    for message in turn:
        role = message["role"]
        text = message["content"]["text"]
        print(f"- {role}: {text[:100]}...")


# ## Key Concepts
#
# 1. **Memory Integration**: How to use Amazon Bedrock Memory to store conversation history
# 2. **Session Management**: How to use session IDs to maintain conversation context
# 3. **AgentCore Deployment**: How to deploy your agent to a production runtime environment
# 4. **AgentCoreMemorySessionManager**: How to use the built-in Strands integration for automatic memory handling
#
# These concepts provide a foundation for building more complex agents with persistent memory.

# ## Cleanup (Optional)
#
# If you no longer need the resources created in this tutorial, you can clean them up:


# Get resource identifiers
if "runtime_id" in locals():
    print(f"Runtime ID: {runtime_id}")
else:
    print("Runtime not yet deployed")


# Only run this cell if you want to delete the resources

# Delete the AgentCore Runtime
if "runtime_id" in locals():
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    control.delete_agent_runtime(agentRuntimeId=runtime_id)
    print(f"Deleted AgentCore Runtime: {runtime_id}")

# Delete the memory resource
memory_client.delete_memory_and_wait(memory_id=memory_id)
print(f"Deleted memory resource: {memory_id}")


# ## Congratulations!
#
# You've successfully built and deployed your first memory-enabled agent with Amazon Bedrock AgentCore Runtime and AgentCore Memory!
#
# ### Next Steps
#
# Now that you understand the basics, you can:
#
# 1. **Add Tools**: Enhance your agent with tools like calculators, database connectors, or API calls
# 2. **Improve Memory**: Implement more sophisticated memory strategies with long-term memory
# 3. **Build a UI**: Create a web or mobile interface for your agent
