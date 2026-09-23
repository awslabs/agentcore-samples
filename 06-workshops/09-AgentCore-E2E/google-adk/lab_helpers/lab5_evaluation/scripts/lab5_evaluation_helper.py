#!/usr/bin/env python3
"""
Lab 5 Evaluation Helper — Strands-based agent setup for evaluation data generation.

This script replicates Labs 1-4 using Strands agents with COMPLETELY DIFFERENT
resource names to avoid conflicts with the Google ADK workshop labs.

Workshop labs use:
  - Memory: "CustomerSupportMemory"
  - Gateway: "customersupport-gw"
  - Runtime agent: "customer_support_agent"
  - SSM prefix: "/app/customersupport/agentcore/"
  - IAM role: "CustomerSupportAssistantBedrockAgentCoreRole-{region}"
  - Cognito pool: "customer-support-pool"

This script uses:
  - Memory: "EvalSupportMemory"
  - Gateway: "evalsupport-gw"
  - Runtime agent: "eval_support_agent"
  - SSM prefix: "/app/evalsupport/agentcore/"
  - IAM role: "EvalSupportAgentCoreRole-{region}"
  - Cognito pool: reuses existing (read-only)

Usage:
    python lab5_evaluation_helper.py setup      # Create all resources (Labs 1-4)
    python lab5_evaluation_helper.py test       # Single invocation test
    python lab5_evaluation_helper.py generate   # Generate data for 30 minutes
    python lab5_evaluation_helper.py cleanup    # Tear down eval resources
"""

import argparse
import json
import os
import random
import time
import uuid

import boto3
from boto3.session import Session

# ---------------------------------------------------------------------------
# Constants — all names are prefixed with "eval" to avoid conflicts
# ---------------------------------------------------------------------------
EVAL_MEMORY_NAME = "EvalSupportMemory"
EVAL_GATEWAY_NAME = "evalsupport-gw"
EVAL_AGENT_NAME = "eval_support_agent"
EVAL_SSM_PREFIX = "/app/evalsupport/agentcore"
EVAL_ROLE_NAME_TEMPLATE = "EvalSupportAgentCoreRole-{region}"
EVAL_POLICY_NAME_TEMPLATE = "EvalSupportAgentCorePolicy-{region}"
EVAL_ACTOR_ID = "eval_customer_001"

MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

boto_session = Session()
REGION = boto_session.region_name
ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]


# ---------------------------------------------------------------------------
# SSM helpers (self-contained, no import from lab_helpers.utils)
# ---------------------------------------------------------------------------
ssm_client = boto3.client("ssm", region_name=REGION)


def put_ssm(name, value):
    ssm_client.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)


def get_ssm(name):
    return ssm_client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def delete_ssm(name):
    try:
        ssm_client.delete_parameter(Name=name)
    except ssm_client.exceptions.ParameterNotFound:
        pass


# ---------------------------------------------------------------------------
# Step 1 — Create Strands Agent tools (Lab 1 equivalent)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a helpful and professional customer support assistant for an electronics e-commerce company.
Your role is to:
- Provide accurate information using the tools available to you
- Support the customer with technical information and product specifications.
- Be friendly, patient, and understanding with customers
- Always offer additional help after answering questions
- If you can't help with something, direct customers to the appropriate contact

You have access to the following tools:
1. get_return_policy() - For warranty and return policy questions
2. get_product_info() - To get information about a specific product
3. get_technical_support() - To search the technical support knowledge base
Always use the appropriate tool to get accurate, up-to-date information."""


def _get_return_policy(product_category: str) -> str:
    """Return policy lookup (mock data)."""
    policies = {
        "smartphones": {
            "window": "30 days",
            "warranty": "1-year manufacturer warranty",
        },
        "laptops": {
            "window": "30 days",
            "warranty": "1-year manufacturer warranty, extended options",
        },
        "accessories": {
            "window": "30 days",
            "warranty": "90-day manufacturer warranty",
        },
    }
    p = policies.get(product_category.lower(), {"window": "30 days", "warranty": "Standard warranty"})
    return f"Return Policy - {product_category}: Window: {p['window']}, Warranty: {p['warranty']}"


def _get_product_info(product_type: str) -> str:
    """Product info lookup (mock data)."""
    products = {
        "laptops": "Intel/AMD, 8-64GB RAM, SSD, Thunderbolt, 1yr warranty",
        "smartphones": "5G, 128GB-1TB, water resistant, wireless charging, 1yr warranty",
        "headphones": "BT 5.0+, ANC, 20-40hr battery, 1yr warranty",
        "monitors": "4K/1440p, 60-240Hz, HDR, 3yr warranty",
    }
    info = products.get(product_type.lower(), "Contact technical support for details.")
    return f"Product Info - {product_type}: {info}"


def _get_technical_support(issue_description: str) -> str:
    """KB retrieval via Bedrock Knowledge Base."""
    try:
        kb_id = ssm_client.get_parameter(Name=f"/{ACCOUNT_ID}-{REGION}/kb/knowledge-base-id")["Parameter"]["Value"]
        bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": issue_description},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 3}},
        )
        results = response.get("retrievalResults", [])
        texts = [r.get("content", {}).get("text", "") for r in results if r.get("score", 0) >= 0.4]
        return "\n\n".join(texts) if texts else "No relevant documentation found."
    except Exception as e:
        return f"KB lookup error: {e}"


# ---------------------------------------------------------------------------
# Step 2 — Create AgentCore Memory (Lab 2 equivalent)
# ---------------------------------------------------------------------------
def create_eval_memory():
    """Create or retrieve the eval memory resource."""
    from bedrock_agentcore.memory import MemoryClient
    from bedrock_agentcore.memory.constants import StrategyType

    client = MemoryClient(region_name=REGION)

    # Check if already exists
    try:
        memory_id = get_ssm(f"{EVAL_SSM_PREFIX}/memory_id")
        client.gmcp_client.get_memory(memoryId=memory_id)
        print(f"✅ Eval memory already exists: {memory_id}")
        return memory_id, client
    except Exception:
        pass

    strategies = [
        {
            StrategyType.USER_PREFERENCE.value: {
                "name": "EvalCustomerPreferences",
                "description": "Captures eval customer preferences",
                "namespaces": ["eval/customer/{actorId}/preferences"],
            }
        },
        {
            StrategyType.SEMANTIC.value: {
                "name": "EvalCustomerSemantic",
                "description": "Stores eval facts from conversations",
                "namespaces": ["eval/customer/{actorId}/semantic"],
            }
        },
    ]

    print("⏳ Creating eval memory resource (this may take a couple of minutes)...")
    response = client.create_memory_and_wait(
        name=EVAL_MEMORY_NAME,
        description="Eval support agent memory — isolated from workshop labs",
        strategies=strategies,
        event_expiry_days=30,
    )
    memory_id = response["id"]
    put_ssm(f"{EVAL_SSM_PREFIX}/memory_id", memory_id)
    print(f"✅ Eval memory created: {memory_id}")
    return memory_id, client


# ---------------------------------------------------------------------------
# Step 3 — Create AgentCore Gateway (Lab 3 equivalent)
# ---------------------------------------------------------------------------
def create_eval_gateway():
    """Create or retrieve the eval gateway."""
    # Reuse the existing Cognito pool (read-only, same account)
    try:
        from lab_helpers.utils import get_or_create_cognito_pool

        cognito_config = get_or_create_cognito_pool(refresh_token=True)
    except ImportError:
        # Fallback: read from SSM if lab_helpers not on path
        cognito_config = {
            "client_id": get_ssm("/app/customersupport/agentcore/client_id"),
            "discovery_url": get_ssm("/app/customersupport/agentcore/discovery_url"),
            "bearer_token": get_ssm("/app/customersupport/agentcore/bearer_token"),
        }

    gateway_client = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # Check if already exists
    try:
        gw_id = get_ssm(f"{EVAL_SSM_PREFIX}/gateway_id")
        gw = gateway_client.get_gateway(gatewayIdentifier=gw_id)
        print(f"✅ Eval gateway already exists: {gw_id}")
        return {
            "id": gw_id,
            "gateway_url": gw["gatewayUrl"],
            "gateway_arn": gw["gatewayArn"],
        }, cognito_config
    except Exception:
        pass

    # Get the gateway IAM role (reuse the one from workshop prereqs)
    gateway_role_arn = get_ssm("/app/customersupport/agentcore/gateway_iam_role")

    auth_config = {
        "customJWTAuthorizer": {
            "allowedClients": [cognito_config["client_id"]],
            "discoveryUrl": cognito_config["discovery_url"],
        }
    }

    print(f"⏳ Creating eval gateway '{EVAL_GATEWAY_NAME}'...")
    create_response = gateway_client.create_gateway(
        name=EVAL_GATEWAY_NAME,
        roleArn=gateway_role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration=auth_config,
    )
    gw_id = create_response["gatewayId"]

    # Wait for gateway to be ready
    while True:
        gw = gateway_client.get_gateway(gatewayIdentifier=gw_id)
        status = gw.get("status", "CREATING")
        if status == "ACTIVE":
            break
        if status in ("CREATE_FAILED", "FAILED"):
            raise RuntimeError(f"Gateway creation failed: {status}")
        print(f"   Gateway status: {status}, waiting...")
        time.sleep(10)

    put_ssm(f"{EVAL_SSM_PREFIX}/gateway_id", gw_id)
    print(f"✅ Eval gateway created: {gw_id}")

    # Add Lambda target (reuse the same Lambda from workshop prereqs)
    lambda_arn = get_ssm("/app/customersupport/agentcore/lambda_arn")
    api_spec_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "prerequisite",
        "lambda",
        "api_spec.json",
    )
    with open(api_spec_path) as f:
        api_spec = json.load(f)

    target_config = {
        "mcp": {
            "lambda": {
                "lambdaArn": lambda_arn,
                "toolSchema": {"inlinePayload": api_spec},
            }
        }
    }

    print("⏳ Adding Lambda target to eval gateway...")
    gateway_client.create_gateway_target(
        gatewayIdentifier=gw_id,
        name="eval-lambda-target",
        targetConfiguration=target_config,
    )

    # Wait for target to be ready
    time.sleep(5)
    targets = gateway_client.list_gateway_targets(gatewayIdentifier=gw_id)
    for t in targets.get("items", []):
        while True:
            tgt = gateway_client.get_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"])
            ts = tgt.get("status", "CREATING")
            if ts == "ACTIVE":
                break
            if ts in ("CREATE_FAILED", "FAILED"):
                raise RuntimeError(f"Target creation failed: {ts}")
            print(f"   Target status: {ts}, waiting...")
            time.sleep(10)

    print("✅ Lambda target added to eval gateway")

    return {
        "id": gw_id,
        "gateway_url": gw["gatewayUrl"],
        "gateway_arn": gw["gatewayArn"],
    }, cognito_config


# ---------------------------------------------------------------------------
# Step 4 — Create IAM Role + Deploy to AgentCore Runtime (Lab 4 equivalent)
# ---------------------------------------------------------------------------
def create_eval_execution_role():
    """Create the IAM execution role for the eval runtime agent."""
    iam = boto3.client("iam")
    role_name = EVAL_ROLE_NAME_TEMPLATE.format(region=REGION)
    policy_name = EVAL_POLICY_NAME_TEMPLATE.format(region=REGION)

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:*"},
                },
            }
        ],
    }

    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": [f"arn:aws:ecr:{REGION}:{ACCOUNT_ID}:repository/*"],
            },
            {
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": [
                    f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
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
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:workload-identity-directory/default/workload-identity/{EVAL_AGENT_NAME}-*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ApplyGuardrail",
                    "bedrock:Retrieve",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetMemoryRecord",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                ],
                "Resource": [f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:*"],
            },
            {
                "Effect": "Allow",
                "Action": ["ssm:GetParameter"],
                "Resource": [f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/*"],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:InvokeGateway",
                ],
                "Resource": [f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:gateway/*"],
            },
        ],
    }

    try:
        existing = iam.get_role(RoleName=role_name)
        print(f"✅ Eval IAM role already exists: {role_name}")
        role_arn = existing["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="IAM role for eval AgentCore runtime agent",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"✅ Created eval IAM role: {role_name}")

    policy_arn = f"arn:aws:iam::{ACCOUNT_ID}:policy/{policy_name}"
    try:
        iam.get_policy(PolicyArn=policy_arn)
    except iam.exceptions.NoSuchEntityException:
        iam.create_policy(
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document),
            Description="Policy for eval AgentCore runtime agent",
        )
        print(f"✅ Created eval IAM policy: {policy_name}")

    try:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
    except Exception:
        pass

    put_ssm(f"{EVAL_SSM_PREFIX}/runtime_execution_role_arn", role_arn)
    return role_arn


def write_eval_runtime_entrypoint(memory_id, gateway_id):
    """Write the runtime entrypoint file for the eval agent (Strands-based)."""
    entrypoint_path = os.path.join(os.path.dirname(__file__), "eval_runtime_entrypoint.py")
    code = f'''#!/usr/bin/env python3
"""Eval agent runtime entrypoint — Strands-based, isolated from workshop labs."""
import os
import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

REGION = boto3.session.Session().region_name
ACTOR_ID = "eval_customer_001"

memory_id = os.environ.get("EVAL_MEMORY_ID", "{memory_id}")
gateway_id = os.environ.get("EVAL_GATEWAY_ID", "{gateway_id}")

memory_client = MemoryClient(region_name=REGION)
model = BedrockModel(model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0")

ssm = boto3.client("ssm", region_name=REGION)

SYSTEM_PROMPT = """You are a helpful customer support assistant for an electronics e-commerce company.
Use the tools available to provide accurate information about products, returns, warranties, and technical support."""


@tool
def get_return_policy(product_category: str) -> str:
    """Get return policy for a product category."""
    policies = {{
        "smartphones": "30-day return, 1yr warranty, free return shipping",
        "laptops": "30-day return, 1yr warranty + extended options, free return shipping",
        "accessories": "30-day return, 90-day warranty",
    }}
    return policies.get(product_category.lower(), "30-day return, standard warranty")


@tool
def get_product_info(product_type: str) -> str:
    """Get product specifications."""
    products = {{
        "laptops": "Intel/AMD, 8-64GB RAM, SSD, Thunderbolt, 1yr warranty",
        "smartphones": "5G, 128GB-1TB, water resistant, wireless charging",
        "headphones": "BT 5.0+, ANC, 20-40hr battery",
        "monitors": "4K/1440p, 60-240Hz, HDR, 3yr warranty",
    }}
    return products.get(product_type.lower(), "Contact support for details.")


@tool
def get_technical_support(issue_description: str) -> str:
    """Search the knowledge base for technical support."""
    try:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        kb_id = ssm.get_parameter(Name=f"/{{account_id}}-{{REGION}}/kb/knowledge-base-id")["Parameter"]["Value"]
        bedrock_rt = boto3.client("bedrock-agent-runtime", region_name=REGION)
        resp = bedrock_rt.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={{"text": issue_description}},
            retrievalConfiguration={{"vectorSearchConfiguration": {{"numberOfResults": 3}}}},
        )
        results = resp.get("retrievalResults", [])
        texts = [r.get("content", {{}}).get("text", "") for r in results if r.get("score", 0) >= 0.4]
        return "\\n".join(texts) if texts else "No relevant docs found."
    except Exception as e:
        return f"KB error: {{e}}"


app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload, context=None):
    """Eval agent entrypoint."""
    user_input = payload.get("prompt", "")
    actor_id = payload.get("actor_id", ACTOR_ID)
    request_headers = context.request_headers or {{}}
    auth_header = request_headers.get("Authorization", "")

    gateway_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gw = gateway_client.get_gateway(gatewayIdentifier=gateway_id)
    gateway_url = gw["gatewayUrl"]

    if not (gateway_url and auth_header):
        return "Error: Missing gateway URL or authorization header"

    try:
        mcp_client = MCPClient(
            lambda: streamablehttp_client(
                url=gateway_url, headers={{"Authorization": auth_header}}
            )
        )

        with mcp_client:
            tools = [get_return_policy, get_product_info, get_technical_support] + mcp_client.list_tools_sync()

            # Retrieve memory context
            all_context = []
            for ns_type, ns in {{"preferences": f"eval/customer/{{actor_id}}/preferences/",
                                "semantic": f"eval/customer/{{actor_id}}/semantic/"}}.items():
                try:
                    memories = memory_client.retrieve_memories(
                        memory_id=memory_id, namespace=ns, query=user_input, top_k=3
                    )
                    for m in memories:
                        if isinstance(m, dict):
                            txt = m.get("content", {{}}).get("text", "").strip()
                            if txt:
                                all_context.append(f"[{{ns_type.upper()}}] {{txt}}")
                except Exception:
                    pass

            enriched = f"Customer Context:\\n" + "\\n".join(all_context) + f"\\n\\n{{user_input}}" if all_context else user_input

            agent = Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)
            response = agent(enriched)
            result_text = response.message["content"][0]["text"]

            # Save to memory
            try:
                memory_client.create_event(
                    memory_id=memory_id, actor_id=actor_id,
                    session_id=str(context.session_id),
                    messages=[(user_input, "USER"), (result_text, "ASSISTANT")],
                )
            except Exception:
                pass

            return result_text
    except Exception as e:
        return f"Error: {{e}}"


if __name__ == "__main__":
    app.run()
'''
    with open(entrypoint_path, "w") as f:
        f.write(code)
    print(f"✅ Wrote eval runtime entrypoint: {entrypoint_path}")
    return entrypoint_path


def deploy_eval_runtime(memory_id, gateway_id):
    """Deploy the eval agent to AgentCore Runtime."""
    from bedrock_agentcore_starter_toolkit import Runtime

    execution_role_arn = create_eval_execution_role()
    entrypoint_path = write_eval_runtime_entrypoint(memory_id, gateway_id)

    # Requirements file for the runtime container
    req_path = os.path.join(os.path.dirname(__file__), "requirements.txt")

    agentcore_runtime = Runtime()

    # Reuse existing Cognito for JWT auth
    client_id = get_ssm("/app/customersupport/agentcore/client_id")
    discovery_url = get_ssm("/app/customersupport/agentcore/discovery_url")

    print("⏳ Configuring eval runtime deployment...")
    agentcore_runtime.configure(
        entrypoint=entrypoint_path,
        execution_role=execution_role_arn,
        auto_create_ecr=True,
        requirements_file=req_path,
        region=REGION,
        agent_name=EVAL_AGENT_NAME,
        authorizer_configuration={
            "customJWTAuthorizer": {
                "allowedClients": [client_id],
                "discoveryUrl": discovery_url,
            }
        },
    )

    print("⏳ Launching eval runtime (building container + deploying)...")
    launch_result = agentcore_runtime.launch(
        env_vars={
            "EVAL_MEMORY_ID": memory_id,
            "EVAL_GATEWAY_ID": gateway_id,
        }
    )
    agent_arn = launch_result.agent_arn
    put_ssm(f"{EVAL_SSM_PREFIX}/runtime_arn", agent_arn)
    print(f"✅ Eval runtime launched: {agent_arn}")

    # Wait for READY
    while True:
        status_response = agentcore_runtime.status()
        status = status_response.endpoint["status"]
        if status == "READY":
            break
        if status in ("CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"):
            raise RuntimeError(f"Eval runtime deployment failed: {status}")
        print(f"   Runtime status: {status}, waiting...")
        time.sleep(15)

    print("✅ Eval runtime is READY")
    return agentcore_runtime, agent_arn


# ---------------------------------------------------------------------------
# Test — single invocation
# ---------------------------------------------------------------------------
def test_single_invocation(agentcore_runtime=None):
    """Test the eval agent with a single invocation."""
    if agentcore_runtime is None:
        from bedrock_agentcore_starter_toolkit import Runtime

        agentcore_runtime = Runtime()
        # Re-configure to point at existing eval agent
        execution_role_arn = get_ssm(f"{EVAL_SSM_PREFIX}/runtime_execution_role_arn")
        client_id = get_ssm("/app/customersupport/agentcore/client_id")
        discovery_url = get_ssm("/app/customersupport/agentcore/discovery_url")
        entrypoint_path = os.path.join(os.path.dirname(__file__), "eval_runtime_entrypoint.py")
        req_path = os.path.join(os.path.dirname(__file__), "requirements.txt")
        agentcore_runtime.configure(
            entrypoint=entrypoint_path,
            execution_role=execution_role_arn,
            auto_create_ecr=True,
            requirements_file=req_path,
            region=REGION,
            agent_name=EVAL_AGENT_NAME,
            authorizer_configuration={
                "customJWTAuthorizer": {
                    "allowedClients": [client_id],
                    "discoveryUrl": discovery_url,
                }
            },
        )

    # Get bearer token
    try:
        from lab_helpers.utils import get_or_create_cognito_pool

        cognito = get_or_create_cognito_pool(refresh_token=True)
        bearer_token = cognito["bearer_token"]
    except ImportError:
        bearer_token = get_ssm("/app/customersupport/agentcore/bearer_token")

    session_id = str(uuid.uuid4())
    test_query = "What is the return policy for laptops?"

    print(f"\n🧪 Testing eval agent with: '{test_query}'")
    response = agentcore_runtime.invoke(
        {"prompt": test_query, "actor_id": EVAL_ACTOR_ID},
        bearer_token=bearer_token,
        session_id=session_id,
    )
    result = response.get("response", response)
    print(f"✅ Response: {result}")
    return True


# ---------------------------------------------------------------------------
# Generate data — invoke the agent for 30 minutes with varied prompts
# ---------------------------------------------------------------------------
EVAL_PROMPTS = [
    "What is the return policy for smartphones?",
    "Tell me about laptop specifications",
    "I need help with my headphones not connecting via Bluetooth",
    "What monitors do you recommend for gaming?",
    "My laptop screen is flickering, what should I do?",
    "Can I return an opened accessory?",
    "What's the warranty on a smartphone?",
    "How do I check my warranty status? Serial number ABC12345",
    "Search the web for latest laptop reviews 2025",
    "I bought a monitor last week and it has dead pixels",
    "What are the specs for your headphones?",
    "How long is the return window for laptops?",
    "My phone battery drains too fast, any tips?",
    "Do you offer extended warranty for monitors?",
    "I need technical support for installing RAM on my laptop",
    "What's the difference between your smartphone models?",
    "Can I get a refund if my accessory is defective?",
    "Tell me about your noise cancelling headphones",
    "How do I set up dual monitors?",
    "What's the process for returning a laptop?",
    "I want to check warranty for serial MNO33333333",
    "Search the web for how to fix overheating laptop",
    "What gaming accessories do you sell?",
    "My headphones have low volume on one side",
    "Do laptops come with pre-installed software?",
    "What's the best monitor for photo editing?",
    "How do I transfer data to my new smartphone?",
    "Is there a student discount on laptops?",
    "My monitor won't turn on after a power outage",
    "What USB-C accessories are compatible with your laptops?",
]


def generate_eval_data(duration_minutes=30, agentcore_runtime=None):
    """Invoke the eval agent repeatedly for the specified duration."""
    if agentcore_runtime is None:
        from bedrock_agentcore_starter_toolkit import Runtime

        agentcore_runtime = Runtime()
        execution_role_arn = get_ssm(f"{EVAL_SSM_PREFIX}/runtime_execution_role_arn")
        client_id = get_ssm("/app/customersupport/agentcore/client_id")
        discovery_url = get_ssm("/app/customersupport/agentcore/discovery_url")
        entrypoint_path = os.path.join(os.path.dirname(__file__), "eval_runtime_entrypoint.py")
        req_path = os.path.join(os.path.dirname(__file__), "requirements.txt")
        agentcore_runtime.configure(
            entrypoint=entrypoint_path,
            execution_role=execution_role_arn,
            auto_create_ecr=True,
            requirements_file=req_path,
            region=REGION,
            agent_name=EVAL_AGENT_NAME,
            authorizer_configuration={
                "customJWTAuthorizer": {
                    "allowedClients": [client_id],
                    "discoveryUrl": discovery_url,
                }
            },
        )

    try:
        from lab_helpers.utils import get_or_create_cognito_pool

        cognito = get_or_create_cognito_pool(refresh_token=True)
        bearer_token = cognito["bearer_token"]
    except ImportError:
        bearer_token = get_ssm("/app/customersupport/agentcore/bearer_token")

    end_time = time.time() + (duration_minutes * 60)
    invocation_count = 0
    error_count = 0

    print(f"\n🚀 Generating eval data for {duration_minutes} minutes...")
    print(f"   Start: {time.strftime('%H:%M:%S')}")
    print(f"   End:   {time.strftime('%H:%M:%S', time.localtime(end_time))}\n")

    while time.time() < end_time:
        prompt = random.choice(EVAL_PROMPTS)
        session_id = str(uuid.uuid4())
        invocation_count += 1

        try:
            response = agentcore_runtime.invoke(
                {"prompt": prompt, "actor_id": EVAL_ACTOR_ID},
                bearer_token=bearer_token,
                session_id=session_id,
            )
            result = response.get("response", str(response))
            preview = result[:80].replace("\n", " ") if result else "(empty)"
            print(f"   [{invocation_count}] ✅ {prompt[:50]}... → {preview}...")
        except Exception as e:
            error_count += 1
            print(f"   [{invocation_count}] ❌ {prompt[:50]}... → Error: {e}")

            # Refresh token if auth error
            if "401" in str(e) or "unauthorized" in str(e).lower():
                try:
                    from lab_helpers.utils import get_or_create_cognito_pool

                    cognito = get_or_create_cognito_pool(refresh_token=True)
                    bearer_token = cognito["bearer_token"]
                    print("   🔄 Refreshed bearer token")
                except Exception:
                    pass

        # Small delay between invocations to avoid throttling
        time.sleep(random.uniform(2, 5))

    print("\n📊 Data generation complete:")
    print(f"   Total invocations: {invocation_count}")
    print(f"   Errors: {error_count}")
    print(f"   Success rate: {((invocation_count - error_count) / max(invocation_count, 1)) * 100:.1f}%")


# ---------------------------------------------------------------------------
# Cleanup — tear down all eval-specific resources
# ---------------------------------------------------------------------------
def cleanup_eval_resources():
    """Delete all eval-specific resources to avoid lingering costs."""
    print("\n🧹 Cleaning up eval resources...\n")

    iam = boto3.client("iam")
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # 1. Delete runtime
    try:
        runtime_arn = get_ssm(f"{EVAL_SSM_PREFIX}/runtime_arn")
        runtime_id = runtime_arn.split(":")[-1].split("/")[-1]
        control = boto3.client("bedrock-agentcore-control", region_name=REGION)
        control.delete_agent_runtime(agentRuntimeId=runtime_id)
        print(f"✅ Deleted eval runtime: {runtime_id}")
        delete_ssm(f"{EVAL_SSM_PREFIX}/runtime_arn")
    except Exception as e:
        print(f"⚠️  Runtime cleanup: {e}")

    # 2. Delete gateway targets then gateway
    try:
        gw_id = get_ssm(f"{EVAL_SSM_PREFIX}/gateway_id")
        targets = gateway_client.list_gateway_targets(gatewayIdentifier=gw_id)
        for t in targets.get("items", []):
            gateway_client.delete_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"])
            print(f"   Deleted gateway target: {t['targetId']}")
        # Wait for targets to be deleted
        time.sleep(10)
        gateway_client.delete_gateway(gatewayIdentifier=gw_id)
        print(f"✅ Deleted eval gateway: {gw_id}")
        delete_ssm(f"{EVAL_SSM_PREFIX}/gateway_id")
    except Exception as e:
        print(f"⚠️  Gateway cleanup: {e}")

    # 3. Delete memory
    try:
        memory_id = get_ssm(f"{EVAL_SSM_PREFIX}/memory_id")
        from bedrock_agentcore.memory import MemoryClient

        mc = MemoryClient(region_name=REGION)
        mc.delete_memory(memory_id=memory_id)
        print(f"✅ Deleted eval memory: {memory_id}")
        delete_ssm(f"{EVAL_SSM_PREFIX}/memory_id")
    except Exception as e:
        print(f"⚠️  Memory cleanup: {e}")

    # 4. Delete IAM role + policy
    role_name = EVAL_ROLE_NAME_TEMPLATE.format(region=REGION)
    policy_name = EVAL_POLICY_NAME_TEMPLATE.format(region=REGION)
    policy_arn = f"arn:aws:iam::{ACCOUNT_ID}:policy/{policy_name}"
    try:
        iam.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        iam.delete_policy(PolicyArn=policy_arn)
        iam.delete_role(RoleName=role_name)
        print("✅ Deleted eval IAM role and policy")
        eval_ssm = f"{EVAL_SSM_PREFIX}/runtime_execution_role_arn"
        delete_ssm(eval_ssm)
    except Exception as e:
        print(f"⚠️  IAM cleanup: {e}")

    # 5. Clean up generated entrypoint file
    entrypoint_path = os.path.join(os.path.dirname(__file__), "eval_runtime_entrypoint.py")
    if os.path.exists(entrypoint_path):
        os.remove(entrypoint_path)
        print("✅ Removed eval_runtime_entrypoint.py")

    print("\n🧹 Eval cleanup complete!")


# ---------------------------------------------------------------------------
# Main — CLI interface
# ---------------------------------------------------------------------------
def setup_all():
    """Run the full setup: memory → gateway → deploy runtime."""
    print("=" * 60)
    print("  Lab 5 Evaluation Helper — Full Setup (Strands Agent)")
    print("=" * 60)

    # Step 1: Agent tools are defined inline (no separate setup needed)
    print("\n📦 Step 1: Agent tools ready (Strands @tool decorators)")

    # Step 2: Memory
    print("\n📦 Step 2: Creating eval memory...")
    memory_id, _ = create_eval_memory()

    # Step 3: Gateway
    print("\n📦 Step 3: Creating eval gateway...")
    gateway_info, cognito_config = create_eval_gateway()

    # Step 4: Deploy runtime
    print("\n📦 Step 4: Deploying eval agent to AgentCore Runtime...")
    agentcore_runtime, agent_arn = deploy_eval_runtime(memory_id, gateway_info["id"])

    print("\n" + "=" * 60)
    print("  ✅ Setup complete!")
    print(f"  Memory ID:    {memory_id}")
    print(f"  Gateway ID:   {gateway_info['id']}")
    print(f"  Runtime ARN:  {agent_arn}")
    print("=" * 60)

    return agentcore_runtime


def main():
    parser = argparse.ArgumentParser(
        description="Lab 5 Evaluation Helper — Strands agent setup for eval data generation"
    )
    parser.add_argument(
        "action",
        choices=["setup", "test", "generate", "cleanup"],
        help="setup: create all resources | test: single invocation | generate: 30min data gen | cleanup: tear down",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=30,
        help="Duration in minutes for data generation (default: 30)",
    )
    args = parser.parse_args()

    if args.action == "setup":
        setup_all()
    elif args.action == "test":
        test_single_invocation()
    elif args.action == "generate":
        generate_eval_data(duration_minutes=args.duration)
    elif args.action == "cleanup":
        cleanup_eval_resources()


if __name__ == "__main__":
    main()
