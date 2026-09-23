import json
import subprocess
import sys
import os
import shutil
import zipfile
import base64

# Install required packages at runtime
print("Installing required packages...")
subprocess.check_call(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "boto3",
        "uv",
        "requests",
        "-t",
        "/tmp/packages",  # nosec B108 - Lambda ephemeral storage
    ]
)
sys.path.insert(0, "/tmp/packages")  # nosec B108 - Lambda ephemeral storage

# Add uv to PATH and set cache directory
os.environ["PATH"] = f"/tmp/packages/bin:{os.environ.get('PATH', '')}"  # nosec B108
os.environ["UV_CACHE_DIR"] = "/tmp/.uv_cache"  # nosec B108 - Lambda ephemeral storage
os.environ["UV_PYTHON_INSTALL_DIR"] = "/tmp/.uv_python"  # nosec B108 - Lambda ephemeral storage
os.environ["HOME"] = "/tmp"  # nosec B108 - Lambda ephemeral storage

import boto3  # noqa: E402
import requests  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402

# Get configuration from environment variables (all required)
REGION = region = os.environ["AWS_REGION"]
AGENT_NAME = os.environ["AGENT_NAME"]
BUCKET_NAME = os.environ["BUCKET_NAME"]
QUEUE_URL = os.environ["QUEUE_URL"]
ROLE_ARN = os.environ["ROLE_ARN"]
AGENT_CONFIG_TABLE_NAME = os.environ["AGENT_CONFIG_TABLE_NAME"]
AGENT_DETAILS_TABLE_NAME = os.environ["AGENT_DETAILS_TABLE_NAME"]

if not AGENT_CONFIG_TABLE_NAME or not AGENT_DETAILS_TABLE_NAME:
    raise ValueError(
        "AGENT_CONFIG_TABLE_NAME and AGENT_DETAILS_TABLE_NAME environment variables are required. "
        "This Lambda must be configured with the correct DynamoDB table names."
    )

s3_client = boto3.client("s3", region_name=REGION)
bedrock_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
bedrock_runtime_client = boto3.client("bedrock-agentcore", region_name=REGION)
ce_client = boto3.client("ce")


def check_and_activate_cost_allocation_tags():
    """
    Check if tenantId tag is activated for cost allocation and activate it if needed.
    Also activates other project tags for better cost tracking.
    """
    tags_to_activate = ["tenantId", "Project", "Environment", "CostCenter"]

    try:
        print("Checking cost allocation tag status...")

        response = ce_client.list_cost_allocation_tags(Status="Active", MaxResults=100)

        active_tags = {tag["TagKey"] for tag in response.get("CostAllocationTags", [])}
        print(f"Currently active cost allocation tags: {active_tags}")

        # Check which tags need to be activated
        tags_to_enable = [tag for tag in tags_to_activate if tag not in active_tags]

        if tags_to_enable:
            print(f"Activating cost allocation tags: {tags_to_enable}")

            # Activate each tag
            for tag_key in tags_to_enable:
                try:
                    ce_client.update_cost_allocation_tags_status(
                        CostAllocationTagsStatus=[{"TagKey": tag_key, "Status": "Active"}]
                    )
                    print(f"✓ Activated cost allocation tag: {tag_key}")
                except Exception as e:
                    print(f"⚠ Warning: Could not activate tag '{tag_key}': {str(e)}")

            print("✓ Cost allocation tags activated. They will appear in Cost Explorer within 24 hours.")
        else:
            print("✓ All required cost allocation tags are already active")

    except Exception as e:
        # Don't fail the deployment if cost allocation tag activation fails
        print(f"⚠ Warning: Could not check/activate cost allocation tags: {str(e)}")
        print("  This is not critical - deployment will continue")
        print("  You can manually activate tags in AWS Billing Console > Cost Allocation Tags")


def fetch_from_github(repo, file_path, branch="main", token=None):
    """
    Fetch a file from GitHub repository using GitHub API

    Args:
        repo: Repository in format 'owner/repo'
        file_path: Path to file in repo (e.g., 'templates/main.py')
        branch: Branch name (default: 'main')
        token: GitHub personal access token (optional, for private repos)

    Returns:
        File content as string
    """
    print(f"Fetching from GitHub: {repo}/{file_path} (branch: {branch})")

    url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    params = {"ref": branch}
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AWS-Lambda-Agent-Builder",
    }

    if token:
        headers["Authorization"] = f"token {token}"

    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()

        # GitHub API returns content as base64 encoded
        if "content" in data:
            content = base64.b64decode(data["content"]).decode("utf-8")
            print(f"Successfully fetched file ({len(content)} bytes)")
            return content
        else:
            raise Exception(f"No content found in response: {data}")

    except requests.exceptions.RequestException as e:
        print(f"Error fetching from GitHub: {str(e)}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text}")
        raise Exception(f"Failed to fetch from GitHub: {str(e)}")


def fetch_tool_catalog(repo, branch="main", token=None):
    """
    Fetch the tool catalog from GitHub repository

    Args:
        repo: Repository in format 'owner/repo'
        branch: Branch name (default: 'main')
        token: GitHub personal access token (optional)

    Returns:
        Tool catalog as dictionary
    """
    try:
        catalog_content = fetch_from_github(repo, "catalog.json", branch, token)
        catalog = json.loads(catalog_content)
        print(f"Loaded tool catalog with {len(catalog.get('tools', []))} tools")
        return catalog
    except Exception as e:
        print(f"Failed to fetch tool catalog: {str(e)}")
        return {"tools": []}


def fetch_tool_code(repo, tool_path, branch="main", token=None):
    """
    Fetch tool code from GitHub repository

    Args:
        repo: Repository in format 'owner/repo'
        tool_path: Path to tool file (e.g., 'tools/web-search/tool.py')
        branch: Branch name
        token: GitHub personal access token (optional)

    Returns:
        Tool code as string
    """
    try:
        tool_code = fetch_from_github(repo, tool_path, branch, token)
        print(f"Fetched tool code from {tool_path}")
        return tool_code
    except Exception as e:
        print(f"Failed to fetch tool code from {tool_path}: {str(e)}")
        return None


def compose_agent_with_tools(base_template, selected_tools, tools_repo, branch="main", token=None):
    """
    Compose agent code by injecting selected tools into base template

    Args:
        base_template: Base agent template code
        selected_tools: List of selected tool configurations
        tools_repo: GitHub repository containing tools
        branch: Branch name
        token: GitHub token

    Returns:
        Composed agent code with tools injected
    """
    print(f"Composing agent with {len(selected_tools)} tools")

    # Fetch tool catalog to get tool paths
    catalog = fetch_tool_catalog(tools_repo, branch, token)
    catalog_tools = {tool["id"]: tool for tool in catalog.get("tools", [])}

    # Collect tool codes and dependencies
    tool_codes = []
    all_dependencies = set()
    tool_names = []

    for selected_tool in selected_tools:
        tool_id = selected_tool.get("id")
        tool_config = selected_tool.get("config", {})

        if tool_id not in catalog_tools:
            print(f"Warning: Tool '{tool_id}' not found in catalog, skipping")
            continue

        tool_info = catalog_tools[tool_id]
        tool_path = tool_info.get("path")

        if not tool_path:
            print(f"Warning: No path specified for tool '{tool_id}', skipping")
            continue

        # Fetch tool code
        tool_code = fetch_tool_code(tools_repo, tool_path, branch, token)

        if tool_code:
            # Inject tool configuration if needed
            if tool_config:
                config_json = json.dumps(tool_config)
                tool_code = f"# Tool configuration: {config_json}\n{tool_code}"

            tool_codes.append(tool_code)
            tool_names.append(tool_id)

            # Collect dependencies
            config_path = tool_info.get("configPath")
            if config_path:
                try:
                    config_content = fetch_from_github(tools_repo, config_path, branch, token)
                    tool_metadata = json.loads(config_content)
                    dependencies = tool_metadata.get("dependencies", [])
                    all_dependencies.update(dependencies)
                except Exception as e:
                    print(f"Could not fetch tool config for {tool_id}: {str(e)}")

    # Find the injection point in base template
    # Look for a marker like "# TOOLS_INJECTION_POINT" or inject before agent initialization
    tools_section = "\n\n# ========== INJECTED TOOLS ==========\n"
    tools_section += f"# Tools: {', '.join(tool_names)}\n\n"
    tools_section += "\n\n".join(tool_codes)
    tools_section += "\n# ========== END INJECTED TOOLS ==========\n\n"

    # Inject tools before agent initialization
    if "agent = Agent(" in base_template:
        # Find agent initialization and inject tools before it
        parts = base_template.split("agent = Agent(")
        composed_code = parts[0] + tools_section + "agent = Agent(" + parts[1]

        # Update agent initialization to include tools
        # Find the tools parameter or add it
        if "tools=[" in composed_code:
            print("Agent already has tools parameter, appending to it")
        else:
            # Add tools parameter to Agent initialization
            # Extract function names from tool codes
            tool_function_names = []
            for tool_code in tool_codes:
                # Look for @tool decorator followed by def function_name
                import re

                matches = re.findall(r"@tool\s+def\s+(\w+)\s*\(", tool_code)
                tool_function_names.extend(matches)

            if tool_function_names:
                # Find the Agent initialization line - look for the closing parenthesis on the same or next line
                agent_init_start = composed_code.find("agent = Agent(")
                # Find the end of the line containing Agent(
                line_end = composed_code.find("\n", agent_init_start)
                if line_end == -1:
                    line_end = len(composed_code)

                # Check if the closing ) is on the same line
                agent_line = composed_code[agent_init_start:line_end]
                if ")" in agent_line:
                    # Single line Agent initialization
                    close_paren = composed_code.find(")", agent_init_start)
                    tools_param = f", tools=[{', '.join(tool_function_names)}]"
                    composed_code = composed_code[:close_paren] + tools_param + composed_code[close_paren:]
                    print(f"Added tools parameter with functions: {tool_function_names}")
                else:
                    print("Warning: Multi-line Agent initialization detected, tools may not be added correctly")
    else:
        # No agent initialization found, append tools at the end
        composed_code = base_template + tools_section
        print("No agent initialization found, appended tools at the end")

    print(f"Agent composition complete. Added {len(tool_codes)} tools")
    print(f"Additional dependencies needed: {list(all_dependencies)}")

    return composed_code, list(all_dependencies)


# Default template (fallback if GitHub fetch fails)
MAIN_PY_TEMPLATE = """from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
import boto3
from datetime import datetime
import uuid
import json
import os

region=os.environ['AWS_REGION']

app = BedrockAgentCoreApp(debug=True)

# Deployment-time configuration (injected during build)
QUEUE_URL = 'QUEUE_URL_VALUE'
TENANT_ID = 'TENANT_ID_VALUE'
AGENT_RUNTIME_ID = 'AGENT_RUNTIME_ID_VALUE'
MODEL_ID = 'MODEL_ID_VALUE'
SYSTEM_PROMPT = '''SYSTEM_PROMPT_VALUE'''
AGENT_CONFIG_TABLE_NAME = 'AGENT_CONFIG_TABLE_NAME_VALUE'

# Initialize clients
sqs = boto3.client('sqs', region_name=region)
dynamodb = boto3.resource('dynamodb', region_name=region)
config_table = dynamodb.Table(AGENT_CONFIG_TABLE_NAME)

# Initialize agent with deployment-time config
agent = Agent(
    model=MODEL_ID,
    system_prompt=SYSTEM_PROMPT
)

def get_runtime_config():
    \"\"\"Fetch runtime configuration from DynamoDB\"\"\"
    try:
        response = config_table.get_item(
            Key={
                'tenantId': TENANT_ID,
                'agentRuntimeId': AGENT_RUNTIME_ID
            }
        )
        if 'Item' in response:
            config = response['Item']
            app.logger.info(f"Loaded runtime config: {config}")
            return config
        else:
            app.logger.info("No runtime config found, using defaults")
            return {}
    except Exception as e:
        app.logger.error(f"Failed to load runtime config: {str(e)}")
        return {}

def send_usage_to_sqs(input_tokens, output_tokens, total_tokens, user_message, response_message, tenant_id):
    try:
        message_body = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.utcnow().isoformat(),
            'tenant_id': tenant_id,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'total_tokens': total_tokens,
            'user_message': user_message,
            'response_message': response_message
        }
        
        response = sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(message_body)
        )
        
        app.logger.info(f"Sent usage to SQS for tenant {tenant_id}: {input_tokens} input, {output_tokens} output tokens")
        app.logger.info(f"SQS MessageId: {response['MessageId']}")
    except Exception as e:
        app.logger.error(f"Failed to send usage to SQS: {str(e)}")


@app.entrypoint
def invoke(payload):
    \"\"\"Your AI agent function with runtime configuration support\"\"\"
    # Load runtime config (cached after first call)
    runtime_config = get_runtime_config()
    
    # Accept both 'message' and 'prompt' for compatibility
    user_message = payload.get("message") or payload.get("prompt", "Hello! How can I help you today?")
    app.logger.info(f"User message from tenant {TENANT_ID}: {user_message}")
    app.logger.info(f"Full payload received: {payload}")
    
    # Apply runtime config overrides if present
    if runtime_config:
        # Check for feature flags
        if runtime_config.get('enabled', True) == False:
            return {"result": "Agent is currently disabled for maintenance"}
    
    result = agent(user_message)

    if hasattr(result, 'metrics'):
        input_tokens = result.metrics.accumulated_usage.get('inputTokens', 0)
        output_tokens = result.metrics.accumulated_usage.get('outputTokens', 0)
        total_tokens = result.metrics.accumulated_usage.get('totalTokens', 0)
        
        send_usage_to_sqs(input_tokens, output_tokens, total_tokens, user_message, result.message, TENANT_ID)

    return {"result": result.message}

if __name__ == "__main__":
    app.run()
"""


def build_agent_project(
    tenant_id="default",
    config=None,
    agent_runtime_id="pending",
    template_config=None,
    tools_config=None,
):
    print(f"Building agent project for tenant: {tenant_id}")
    print(f"Configuration: {config}")
    print(f"Template config: {template_config}")
    print(f"Tools config: {tools_config}")

    # Set default configuration values
    if config is None:
        config = {}

    model_id = config.get("modelId", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
    system_prompt = config.get("systemPrompt", "You are a helpful AI assistant.")

    project_dir = "/tmp/agentcore_runtime_direct_deploy"  # nosec B108 - Lambda ephemeral storage
    package_file = "deployment.zip"
    additional_dependencies = []

    # Clean up if exists
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)

    os.makedirs(project_dir)

    # Fetch template from GitHub if configured
    main_content = None
    if template_config and template_config.get("source") == "github":
        try:
            repo = template_config.get("repo")
            file_path = template_config.get("path", "main.py")
            branch = template_config.get("branch", "main")
            token = template_config.get("token")

            if repo:
                print(f"Fetching template from GitHub repository: {repo}")
                main_content = fetch_from_github(repo, file_path, branch, token)
                print("Successfully fetched template from GitHub")

                # Compose agent with tools if tools are selected
                if tools_config and tools_config.get("selected"):
                    selected_tools = tools_config.get("selected", [])
                    tools_repo = tools_config.get("repo", repo)  # Use same repo or different one
                    tools_branch = tools_config.get("branch", branch)

                    print(f"Composing agent with {len(selected_tools)} tools")
                    main_content, additional_dependencies = compose_agent_with_tools(
                        main_content, selected_tools, tools_repo, tools_branch, token
                    )
                    print(f"Agent composition complete with tools: {[t.get('id') for t in selected_tools]}")
            else:
                print("No repository specified, using default template")
        except Exception as e:
            print(f"Failed to fetch template from GitHub: {str(e)}")
            print("Falling back to default template")
            main_content = None

    # Use default template if GitHub fetch failed or not configured
    if main_content is None:
        print("Using default template")
        main_content = MAIN_PY_TEMPLATE

    # Replace placeholders with configuration values
    main_content = main_content.replace("QUEUE_URL_VALUE", QUEUE_URL)
    main_content = main_content.replace("TENANT_ID_VALUE", tenant_id)
    main_content = main_content.replace("AGENT_RUNTIME_ID_VALUE", agent_runtime_id)
    main_content = main_content.replace("MODEL_ID_VALUE", model_id)
    main_content = main_content.replace("SYSTEM_PROMPT_VALUE", system_prompt.replace("'", "\\'"))
    main_content = main_content.replace("AGENT_CONFIG_TABLE_NAME_VALUE", AGENT_CONFIG_TABLE_NAME)

    main_path = os.path.join(project_dir, "main.py")
    with open(main_path, "w") as f:
        f.write(main_content)

    print(f"Created main.py for tenant {tenant_id} with config:")
    print(f"  Model: {model_id}")
    print(f"  System Prompt: {system_prompt[:50]}...")
    if template_config and template_config.get("source") == "github":
        print(f"  Template Source: GitHub ({template_config.get('repo')})")

    # Initialize UV project
    print("Initializing UV project...")
    subprocess.run(
        ["uv", "init", project_dir, "--python", "3.13"],
        check=True,
        cwd="/tmp",  # nosec B108
    )

    # Add dependencies (base + additional from tools)
    print("Adding dependencies...")
    base_dependencies = ["bedrock-agentcore", "strands-agents", "boto3"]
    all_dependencies = base_dependencies + additional_dependencies

    print(f"Installing dependencies: {all_dependencies}")
    subprocess.run(["uv", "add"] + all_dependencies, cwd=project_dir, check=True)

    # Install for Lambda
    print("Installing dependencies for Lambda...")
    deployment_dir = os.path.join(project_dir, "deployment_package")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            "3.13",
            "--target=deployment_package",
            "--only-binary=:all:",
            "-r",
            "pyproject.toml",
        ],
        cwd=project_dir,
        check=True,
    )

    # Create deployment package
    print("Creating deployment package...")
    package_path = os.path.join(project_dir, package_file)

    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # Add all files from deployment_package
        for root, dirs, files in os.walk(deployment_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, deployment_dir)
                zip_file.write(file_path, arcname)

        # Add main.py
        zip_file.write(main_path, "main.py")

    print(f"Deployment package created: {package_path}")
    return package_path


def delete_existing_agent(agent_name):
    try:
        list_response = bedrock_client.list_agent_runtimes()
        for runtime in list_response.get("agentRuntimes", []):
            if runtime["agentRuntimeName"] == agent_name:
                agent_runtime_id = runtime["agentRuntimeId"]
                print(f"Deleting existing agent: {agent_runtime_id}")
                bedrock_client.delete_agent_runtime(agentRuntimeId=agent_runtime_id)
                wait_for_deletion(agent_runtime_id)
                print(f"Existing agent deleted: {agent_runtime_id}")
                break
    except Exception as e:
        print(f"Error checking/deleting existing agent: {str(e)}")


def wait_for_agent_ready(agent_runtime_id, max_attempts=60):
    for attempt in range(max_attempts):
        try:
            response = bedrock_client.get_agent_runtime(agentRuntimeId=agent_runtime_id)
            status = response["status"]
            print(f"Attempt {attempt + 1}/{max_attempts}: Status = {status}")

            if status == "READY":
                print("Agent is READY!")
                return
            elif status in ["FAILED", "CREATE_FAILED"]:
                raise Exception(f"Agent entered failed state: {status}")

            time.sleep(5)
        except Exception as e:
            if "ResourceNotFoundException" in str(e):
                time.sleep(5)
            else:
                raise

    raise Exception("Timeout waiting for agent to be ready")


def wait_for_deletion(agent_runtime_id, max_attempts=30):
    for attempt in range(max_attempts):
        try:
            bedrock_client.get_agent_runtime(agentRuntimeId=agent_runtime_id)
            print(f"Waiting for deletion... attempt {attempt + 1}")
            time.sleep(5)
        except Exception:
            print("Agent deleted successfully")
            return


def test_agent_endpoint(agent_runtime_id, agent_runtime_arn, max_retries=3):
    """Test the agent endpoint with a simple invocation"""

    # Wait a bit for the agent runtime to fully initialize
    print("Waiting 10 seconds for agent runtime to fully initialize...")
    time.sleep(10)

    for attempt in range(max_retries):
        try:
            print(f"Testing agent endpoint (attempt {attempt + 1}/{max_retries}): {agent_runtime_id}")
            print(f"Agent ARN: {agent_runtime_arn}")

            # Test payload - adjust based on your agent's expected input
            test_payload = {"message": "Hello, this is a test message", "test": True}

            print(f"Sending test payload: {json.dumps(test_payload)}")

            # Invoke the agent with correct parameters
            response = bedrock_runtime_client.invoke_agent_runtime(
                agentRuntimeArn=agent_runtime_arn,
                payload=json.dumps(test_payload).encode("utf-8"),
                contentType="application/json",
            )

            # Parse the response
            if "body" in response:
                body = response["body"].read()
                if isinstance(body, bytes):
                    body = body.decode("utf-8")

                print("✓ Agent responded successfully")
                print(f"Response: {body}")

                return {
                    "success": True,
                    "response": body,
                    "message": "Agent endpoint is working correctly",
                    "attempts": attempt + 1,
                }
            else:
                print("✓ Agent invoked successfully")
                print(f"Response metadata: {response.get('ResponseMetadata', {})}")

                return {
                    "success": True,
                    "response": str(response),
                    "message": "Agent endpoint invoked successfully",
                    "attempts": attempt + 1,
                }

        except Exception as e:
            error_msg = f"✗ Agent endpoint test failed (attempt {attempt + 1}/{max_retries}): {str(e)}"
            print(error_msg)

            # Check if it's a runtime initialization timeout
            if "Runtime initialization time exceeded" in str(e):
                if attempt < max_retries - 1:
                    wait_time = 30
                    print(f"Runtime still initializing. Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
            elif attempt < max_retries - 1:
                print("Waiting 10 seconds before retry...")
                time.sleep(10)
                continue

            # Last attempt failed
            import traceback

            traceback.print_exc()

            return {
                "success": False,
                "error": str(e),
                "message": "Agent endpoint test failed after retries - agent may still be functional",
                "attempts": attempt + 1,
            }

    return {
        "success": False,
        "error": "Max retries exceeded",
        "message": "Agent endpoint test failed after all retries",
        "attempts": max_retries,
    }


def lambda_handler(event, context):
    package_file = "deployment.zip"

    try:
        # Check and activate cost allocation tags for billing
        print("=" * 60)
        print("Checking Cost Allocation Tags")
        print("=" * 60)
        check_and_activate_cost_allocation_tags()

        # Extract tenantId, config, and template from event
        tenant_id = event.get("tenantId", "default")
        config = event.get("config", {})
        template_config = event.get("template", {})
        print(f"Processing request for tenant: {tenant_id}")
        print(f"Configuration: {json.dumps(config, indent=2)}")
        print(f"Template configuration: {json.dumps(template_config, indent=2)}")

        # Generate unique agent name based on tenant ID and timestamp
        # Format: agentcore_{tenantId}_{timestamp}
        safe_tenant_id = tenant_id.replace("-", "_").replace(".", "_")[:20]  # Sanitize and limit length
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        agent_name = f"agentcore_{safe_tenant_id}_{timestamp}"
        print(f"Generated unique agent name: {agent_name}")

        # Extract tools configuration
        tools_config = event.get("tools", {})

        # Step 1: Build the agent project with config, template, and tools
        print("=" * 60)
        print("STEP 1: Building agent project")
        print("=" * 60)
        # Note: agent_runtime_id will be updated after creation
        package_path = build_agent_project(tenant_id, config, "pending", template_config, tools_config)

        # Step 2: Upload to S3
        print("=" * 60)
        print("STEP 2: Uploading to S3")
        print("=" * 60)
        s3_key = f"{agent_name}/{package_file}"
        print(f"Uploading {package_path} to s3://{BUCKET_NAME}/{s3_key}")
        s3_client.upload_file(package_path, BUCKET_NAME, s3_key)
        print("Upload complete!")

        # Step 3: Delete existing agent if it exists (optional - skip for unique names)
        # Since we're using unique names per deployment, we don't need to delete
        # But we'll keep the function for backwards compatibility
        print("=" * 60)
        print("STEP 3: Checking for existing agents (skipped - using unique names)")
        print("=" * 60)
        # delete_existing_agent(agent_name)  # Commented out since each deployment gets unique name

        # Step 4: Create new agent
        print("=" * 60)
        print("STEP 4: Creating agent runtime")
        print("=" * 60)
        print(f"Creating agent runtime: {agent_name}")
        print(f"Using S3 package: s3://{BUCKET_NAME}/{s3_key}")

        response = bedrock_client.create_agent_runtime(
            agentRuntimeName=agent_name,
            agentRuntimeArtifact={
                "codeConfiguration": {
                    "code": {"s3": {"bucket": BUCKET_NAME, "prefix": s3_key}},
                    "runtime": "PYTHON_3_13",
                    "entryPoint": ["main.py"],
                }
            },
            networkConfiguration={"networkMode": "PUBLIC"},
            roleArn=ROLE_ARN,
            tags={"tenantId": tenant_id, "environment": "production"},
        )

        agent_runtime_id = response["agentRuntimeId"]
        agent_runtime_arn = response["agentRuntimeArn"]

        print(f"Agent created: {agent_runtime_id}")
        print(f"Agent ARN: {agent_runtime_arn}")
        print(f"Tagged with tenantId: {tenant_id}")

        print(f"Note: Agent built with placeholder runtime ID. Runtime config will use tenantId: {tenant_id}")

        # Step 5: Wait for agent to be ready
        print("=" * 60)
        print("STEP 5: Waiting for agent to be ready")
        print("=" * 60)
        wait_for_agent_ready(agent_runtime_id)

        # Step 6: Test the agent endpoint
        print("=" * 60)
        print("STEP 6: Testing agent endpoint")
        print("=" * 60)
        test_result = test_agent_endpoint(agent_runtime_id, agent_runtime_arn)

        # Construct the agent endpoint URL
        agent_endpoint_url = (
            f"https://bedrock-agentcore-runtime.{REGION}.amazonaws.com/agents/{agent_runtime_id}/invoke"
        )

        # Store agent details in DynamoDB for later retrieval
        try:
            dynamodb = boto3.resource("dynamodb")
            agent_table = dynamodb.Table(AGENT_DETAILS_TABLE_NAME)
            config_table = dynamodb.Table(AGENT_CONFIG_TABLE_NAME)

            # Convert config to DynamoDB-compatible format (Float -> Decimal)
            from decimal import Decimal

            def convert_floats(obj):
                if isinstance(obj, float):
                    return Decimal(str(obj))
                elif isinstance(obj, dict):
                    return {k: convert_floats(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_floats(item) for item in obj]
                return obj

            deployment_config = convert_floats(config)

            # Store agent details
            agent_table.put_item(
                Item={
                    "tenantId": tenant_id,
                    "agentName": agent_name,
                    "agentRuntimeId": agent_runtime_id,
                    "agentRuntimeArn": agent_runtime_arn,
                    "agentEndpointUrl": agent_endpoint_url,
                    "status": "READY",
                    "deployedAt": datetime.now().isoformat(),
                    "s3_uri": f"s3://{BUCKET_NAME}/{s3_key}",
                    "deploymentConfig": deployment_config,  # Store deployment-time config
                    "templateSource": template_config.get("source", "default") if template_config else "default",
                    "templateRepo": template_config.get("repo", "") if template_config else "",
                    "templatePath": template_config.get("path", "") if template_config else "",
                    "tools": tools_config if tools_config else {},
                }
            )
            print(f"✓ Stored agent details in DynamoDB for tenant: {tenant_id}")

            # Store initial runtime configuration (can be updated later without redeployment)
            runtime_config = {
                "tenantId": tenant_id,
                "agentRuntimeId": agent_runtime_id,
                "enabled": True,  # Feature flag
                "rateLimit": int(config.get("rateLimit", 100)),  # Requests per minute
                "customSettings": config.get("customSettings", {}),
                "updatedAt": datetime.now().isoformat(),
            }

            config_table.put_item(Item=runtime_config)
            print(f"✓ Stored runtime configuration in DynamoDB for tenant: {tenant_id}")

        except Exception as e:
            print(f"Warning: Failed to store agent details in DynamoDB: {str(e)}")
            # Don't fail the deployment if DynamoDB write fails

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Agent built, deployed, and tested successfully",
                    "tenantId": tenant_id,
                    "bucket": BUCKET_NAME,
                    "s3_key": s3_key,
                    "s3_uri": f"s3://{BUCKET_NAME}/{s3_key}",
                    "agentRuntimeId": agent_runtime_id,
                    "agentRuntimeArn": agent_runtime_arn,
                    "agentEndpointUrl": agent_endpoint_url,
                    "test_result": test_result,
                    "templateSource": template_config.get("source", "default") if template_config else "default",
                    "templateRepo": template_config.get("repo", "") if template_config else "",
                    "tools": [t.get("id") for t in tools_config.get("selected", [])] if tools_config else [],
                }
            ),
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback

        traceback.print_exc()
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
