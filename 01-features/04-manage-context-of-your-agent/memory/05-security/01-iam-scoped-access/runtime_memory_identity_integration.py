#!/usr/bin/env python

# # AgentCore Runtime + Memory with IAM-scoped actor isolation
#
# ## Overview
#
# This tutorial shows how to isolate a memory-enabled agent's conversation history per user by enforcing an IAM condition on `bedrock-agentcore:actorId`. The agent deploys to AgentCore Runtime behind a Cognito JWT authorizer; the user's Cognito `sub` is used as the `actorId`, and the runtime execution role's policy restricts memory operations to that actor.
#
# See [AgentCore Memory docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html) for the underlying API and the IAM condition keys reference.
#
# ### Tutorial Details
#
# | Information         | Details                                                          |
# |---------------------|------------------------------------------------------------------|
# | Tutorial type       | Security & Identity Management                                   |
# | Agent type          | Single Conversational Agent                                      |
# | Agentic Framework   | Strands Agents                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                       |
# | Key features        | Memory Isolation, IAM `actorId` condition, Cognito JWT           |
# | SDK used            | boto3, bedrock-agentcore                                         |
#
# ### What You'll Learn
#
# 1. Configure an IAM policy that scopes memory access by `bedrock-agentcore:actorId`
# 2. Propagate user identity from a Cognito JWT into the `actorId` used by the agent
# 3. Deploy to AgentCore Runtime with a JWT authorizer
# 4. Verify isolation — swapping the allowed `actorId` blocks access to another user's events
#
# ### Architecture
#
# <div style="text-align:left">
#     <img src="architecture.png" width="90%"/>
# </div>

# ## 0. Prerequisites
#
# To execute this tutorial you will need:
# * Python 3.10 or newer
# * AWS credentials configured with appropriate permissions for Bedrock, ECR, IAM, and Cognito
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
import uuid
import logging
import boto3
from botocore.exceptions import ClientError
from bedrock_agentcore.memory import MemoryClient
from utils import (
    setup_cognito_user_pool,
    reauthenticate_users,
    get_user_sub,
    create_agentcore_role,
)

# Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("runtime-memory-agent")
REGION = os.getenv("AWS_REGION", "us-west-2")


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


# Create unique identifier for this resource
unique_id = str(uuid.uuid4())[:8]
memory_name = f"RuntimeIdentityMemoryAgent_{unique_id}"

# Initialize Memory Client
memory_client = MemoryClient(region_name=REGION)

# Create memory
print("\n🧠 Creating memory...")
print("   This takes 2-3 minutes...\n")

try:
    memory = memory_client.create_memory_and_wait(
        name=memory_name,
        strategies=[],
        description="Memory isolation with IAM example.",
        event_expiry_days=30,
    )
    MEMORY_ID = memory["id"]
except ClientError as e:
    if e.response["Error"]["Code"] == "ValidationException" and "already exists" in str(e):
        memories = memory_client.list_memories()
        MEMORY_ID = next((m["id"] for m in memories if m["id"].startswith(memory_name)), None)
    else:
        raise

print("\n✅ Memory created successfully!")
print(f"   Memory ID: {MEMORY_ID}")


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
# Unlike traditional deployment methods that require manual server setup and management, AgentCore Runtime deploys your agent containers to AWS infrastructure, and provides secure HTTPS endpoints for invocation. This approach ensures your agent can scale with demand and operate reliably in production environments.
#
# > 💡 **Tip**: AgentCore Runtime uses S3-based code deployment — package your code and dependencies into a zip, upload to S3, and create the runtime via the `bedrock-agentcore-control` boto3 client.
#
# ### Configure the Deployment
#
# Let's set up our deployment configuration:


import time  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import shutil  # noqa: E402
import requests  # noqa: E402
from botocore.auth import SigV4Auth  # noqa: E402
from botocore.awsrequest import AWSRequest  # noqa: E402

iam_role = create_agentcore_role(agent_name=f"runtime_memory_agent_{unique_id}", region=REGION)

# ── Deploy to AgentCore Runtime (native boto3 S3 code deployment) ─────────────

agent_name = f"runtime_memory_agent_{unique_id}"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "runtime_identity_memory_agent.py"
AGENT_FILES = ["runtime_identity_memory_agent.py"]
ACCOUNT_ID = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{agent_name}/code.zip"
role_arn = iam_role["Role"]["Arn"]
role_name = iam_role["Role"]["RoleName"]
account_id = ACCOUNT_ID

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
    description="Memory-enabled agent with IAM actor isolation — tutorial example",
    environmentVariables={
        "MEMORY_ID": MEMORY_ID,
        "MODEL_ID": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
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
_ep_wait_start = time.time()
while time.time() - _ep_wait_start < 120:
    eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
    for ep in eps.get("runtimeEndpoints", []):
        if ep["name"] == "default" and ep["status"] == "READY":
            endpoint_url = ep.get("liveEndpointUri")
            break
        elif ep["name"] == "default" and "FAILED" in ep.get("status", ""):
            raise RuntimeError(f"Endpoint creation failed: {ep['status']}")
    if endpoint_url:
        break
    time.sleep(10)
if not endpoint_url:
    raise RuntimeError("Endpoint did not become READY within 120 seconds")
logger.info(f"✅ Endpoint ready: {endpoint_url}")

print(f"Account ID is {account_id}")
print(f"Agent role is {role_arn}")
print(f"Role name is {role_name}")


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


# Re-authenticate if needed
tokens = reauthenticate_users(cognito_config["client_id"], REGION)
cognito_config["bearer_tokens"]["testuser1"] = tokens["testuser1"]
cognito_config["bearer_tokens"]["testuser2"] = tokens["testuser2"]


import time  # noqa: E402
import boto3  # noqa: E402


def test_user_memory_isolation_with_iam():
    """
    Test user memory isolation with dynamic IAM policy controls using Cognito subject IDs.
    """
    print("\n" + "=" * 50)
    print("USER MEMORY ISOLATION WITH IAM POLICY TEST")
    print("=" * 50)

    # Setup IAM client
    iam_client = boto3.client("iam")

    # Extract bearer tokens
    testuser1_token = cognito_config["bearer_tokens"]["testuser1"]
    testuser2_token = cognito_config["bearer_tokens"]["testuser2"]

    user_pool_id = cognito_config["pool_id"]

    # Get Cognito subs (these will be our actor IDs)
    testuser1_actor_id = get_user_sub(testuser1_token, REGION, user_pool_id)
    testuser2_actor_id = get_user_sub(testuser2_token, REGION, user_pool_id)

    print(f"TestUser1 Cognito Sub (actor ID): {testuser1_actor_id}")
    print(f"TestUser2 Cognito Sub (actor ID): {testuser2_actor_id}")

    # Define the dynamic policy template
    def create_actor_policy(actor_id, policy_name):
        policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "DynamicActorIdRestriction",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:GetEvent",
                        "bedrock-agentcore:GetMemory",
                        "bedrock-agentcore:GetMemoryRecord",
                        "bedrock-agentcore:ListActors",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:ListMemoryRecords",
                        "bedrock-agentcore:ListSessions",
                        "bedrock-agentcore:DeleteEvent",
                        "bedrock-agentcore:DeleteMemoryRecord",
                        "bedrock-agentcore:RetrieveMemoryRecords",
                    ],
                    "Resource": [f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:memory/*"],
                    "Condition": {"StringEquals": {"bedrock-agentcore:actorId": actor_id}},
                }
            ],
        }

        try:
            # Create or update the policy
            try:
                response = iam_client.create_policy(
                    PolicyName=policy_name,
                    PolicyDocument=json.dumps(policy_document),
                    Description=f"Dynamic actor restriction policy for {actor_id}",
                )
                policy_arn = response["Policy"]["Arn"]
            except iam_client.exceptions.EntityAlreadyExistsException:
                # Update existing policy
                policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
                iam_client.create_policy_version(
                    PolicyArn=policy_arn,
                    PolicyDocument=json.dumps(policy_document),
                    SetAsDefault=True,
                )

            # Attach policy to role
            iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

            print(f"Policy updated for Cognito sub (actor ID): {actor_id}")
            return policy_arn

        except Exception as e:
            print(f"Error updating policy: {e}")
            raise

    # Define function to reset policy
    def detach_policy(policy_arn):
        try:
            iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            print(f"Policy {policy_arn} detached")
        except Exception as e:
            print(f"Error detaching policy: {e}")

    # Create unique session IDs for each test phase
    user1_session_id = f"memory-agent-session-user1-{int(time.time())}"
    user2_session_id = f"memory-agent-session-user2-{int(time.time())}"

    # Create policy for this test
    policy_name = f"actor-restriction-{int(time.time())}"

    try:
        # PHASE 1: Test with user1's memory persistence with correct policy
        print("\n" + "=" * 50)
        print("PHASE 1: USER 1 MEMORY PERSISTENCE TEST")
        print("=" * 50)

        # Create policy for user 1
        policy_arn = create_actor_policy(testuser1_actor_id, policy_name)

        # Wait for policy to propagate
        print("Waiting for IAM policy to propagate...")
        time.sleep(10)

        # Step 1: User 1 shares initial information
        print("\n" + "-" * 50)
        print("STEP 1: User 1 shares information")
        print("-" * 50)

        user1_prompt1 = "My name is John and my favorite color is purple."
        response1 = invoke_agent({"prompt": user1_prompt1}, user1_session_id, testuser1_token)
        print(f'User 1 prompt: "{user1_prompt1}"')
        print(f'User 1 response: "{response1}"')

        # Wait for session to terminate (75 seconds)
        print("\nWaiting 75 seconds for session to terminate...")
        time.sleep(75)

        # Step 2: User 1 asks to recall information (should work with policy)
        print("\n" + "-" * 50)
        print("STEP 2: User 1 recalls information (should succeed)")
        print("-" * 50)

        user1_prompt2 = "What is my name and favorite color?"
        response2 = invoke_agent({"prompt": user1_prompt2}, user1_session_id, testuser1_token)
        print(f'User 1 prompt: "{user1_prompt2}"')
        print(f'User 1 response: "{response2}"')

        # PHASE 2: Test user2 memory isolation with policy control
        print("\n" + "=" * 50)
        print("PHASE 2: USER 2 MEMORY ISOLATION TEST")
        print("=" * 50)

        # Step 3: User 2 shares information (will create memory but should work as API call)
        print("\n" + "-" * 50)
        print("STEP 3: User 2 shares information")
        print("-" * 50)

        # Temporarily change policy to user 2 to allow initial interaction
        detach_policy(policy_arn)
        policy_arn = create_actor_policy(testuser2_actor_id, policy_name)

        # Wait for policy update to propagate
        print("Waiting for updated IAM policy to propagate...")
        time.sleep(10)

        user2_prompt1 = "My name is Mary and my favorite food is pasta."
        response3 = invoke_agent({"prompt": user2_prompt1}, user2_session_id, testuser2_token)
        print(f'User 2 prompt: "{user2_prompt1}"')
        print(f'User 2 response: "{response3}"')

        # Wait for session to terminate
        print("\nWaiting 75 seconds for session to terminate...")
        time.sleep(75)

        # Step 4: Change policy back to user 1, then user 2 tries to recall (should fail)
        print("\n" + "-" * 50)
        print("STEP 4: Change policy to User 1, User 2 tries to recall (should fail)")
        print("-" * 50)

        # Change policy back to user 1
        detach_policy(policy_arn)
        policy_arn = create_actor_policy(testuser1_actor_id, policy_name)

        # Wait for policy update to propagate
        print("Waiting for updated IAM policy to propagate…")
        time.sleep(10)

        user2_prompt2 = "What is my name and favorite food?"
        response4 = invoke_agent({"prompt": user2_prompt2}, user2_session_id, testuser2_token)
        print(f'User 2 prompt: "{user2_prompt2}"')
        print(f'User 2 response: "{response4}"')
        print("\n Agent should not have access to User 2 Memory as the policy is giving access only to User 1 events")

    finally:
        # Clean up - remove the policy
        detach_policy(policy_arn)


test_user_memory_isolation_with_iam()


# ## Key Concepts
#
# In this tutorial, you've learned several important concepts for building memory-enabled agents with Bedrock AgentCore:
#
# 1. **Memory Integration**: How to use AgentCore Memory to store conversation history across sessions, enabling your agent to maintain context over time even when sessions expire.
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
# If you no longer need the resources created in this tutorial, you can clean them up to avoid unnecessary AWS charges.


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
try:
    memory_client.delete_memory_and_wait(memory_id=MEMORY_ID)
    print(f"✅ Deleted memory resource: {MEMORY_ID}")
except Exception as e:
    print(f"❌ Error deleting memory resource: {e}")

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


# 5. Function to delete IAM role and all its versions
def delete_iam_role(role_identifier, region=REGION):
    """
    Deletes an IAM role including all attached policies and versions

    Args:
        role_identifier (str): The ARN or name of the IAM role
        region (str): AWS region
    """
    try:
        iam_client = boto3.client("iam", region_name=region)

        # Determine if the identifier is an ARN or a role name
        if role_identifier.startswith("arn:aws:iam::"):
            # Extract role name from ARN
            role_name = role_identifier.split("/")[-1]
        else:
            role_name = role_identifier

        print(f"Attempting to delete IAM role: {role_name}")

        # 1. Detach all managed policies
        attached_policies = iam_client.list_attached_role_policies(RoleName=role_name)
        for policy in attached_policies.get("AttachedPolicies", []):
            iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
            print(f"✅ Detached managed policy: {policy['PolicyArn']}")

        # 2. Delete all inline policies
        inline_policies = iam_client.list_role_policies(RoleName=role_name)
        for policy_name in inline_policies.get("PolicyNames", []):
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            print(f"✅ Deleted inline policy: {policy_name}")

        # 3. Delete any instance profiles associated with the role
        instance_profiles = iam_client.list_instance_profiles_for_role(RoleName=role_name)
        for profile in instance_profiles.get("InstanceProfiles", []):
            iam_client.remove_role_from_instance_profile(
                InstanceProfileName=profile["InstanceProfileName"], RoleName=role_name
            )
            print(f"✅ Removed role from instance profile: {profile['InstanceProfileName']}")

        # 4. Finally delete the role
        iam_client.delete_role(RoleName=role_name)
        print(f"✅ Successfully deleted IAM role: {role_name}")

    except Exception as e:
        print(f"❌ Error deleting IAM role: {e}")


delete_iam_role(role_arn)

print("\n✅ Cleanup complete")
