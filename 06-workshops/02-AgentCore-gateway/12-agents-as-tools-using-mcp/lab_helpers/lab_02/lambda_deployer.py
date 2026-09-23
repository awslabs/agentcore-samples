"""
Lab 02: Lambda function deployment and configuration helper

Handles:
1. Creating ECR repositories
2. Creating IAM execution roles
3. Attaching required policies
4. Storing all configuration in Parameter Store

Multi-account compatible: Each deployment stores its own values.
"""

import boto3
import json
from lab_helpers.constants import (
    PARAMETER_PATHS,
    LAMBDA_CONFIG,
    ECR_CONFIG,
    IAM_POLICIES,
)
from lab_helpers.parameter_store import put_parameter, store_workshop_metadata
from lab_helpers.config import MODEL_ID, AWS_REGION


def create_ecr_repository(repository_name, region_name=None):
    """
    Create ECR repository (or return existing)

    Args:
        repository_name: Name of repository (e.g., "aiml301-diagnostic-agent")
        region_name: AWS region

    Returns:
        ECR repository URI
    """
    if region_name is None:
        region_name = AWS_REGION

    ecr = boto3.client("ecr", region_name=region_name)
    boto3.client("sts", region_name=region_name).get_caller_identity()["Account"]  # noqa: F841

    try:
        # Check if repository exists
        response = ecr.describe_repositories(repositoryNames=[repository_name])
        repo_uri = response["repositories"][0]["repositoryUri"]
        print(f"✓ ECR Repository already exists: {repo_uri}")
        return repo_uri
    except ecr.exceptions.RepositoryNotFoundException:
        # Create new repository
        response = ecr.create_repository(repositoryName=repository_name)
        repo_uri = response["repository"]["repositoryUri"]
        print(f"✓ Created ECR Repository: {repo_uri}")
        return repo_uri


def create_lambda_execution_role(role_name, region_name=None):
    """
    Create Lambda execution role with required policies

    Args:
        role_name: Name of IAM role (e.g., "aiml301-diagnostic-lambda-role")
        region_name: AWS region

    Returns:
        Role ARN
    """
    if region_name is None:
        region_name = AWS_REGION

    iam = boto3.client("iam", region_name=region_name)

    # Trust policy: Allow Lambda service to assume this role
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        # Check if role exists
        role = iam.get_role(RoleName=role_name)
        role_arn = role["Role"]["Arn"]
        print(f"✓ IAM Role already exists: {role_arn}")
    except iam.exceptions.NoSuchEntityException:
        # Create new role
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Lambda execution role for AIML301 workshop agent",
        )
        role_arn = role["Role"]["Arn"]
        print(f"✓ Created IAM Role: {role_arn}")

    # Attach CloudWatch Logs policy (Lambda basic execution)
    try:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=IAM_POLICIES["cloudwatch_logs_policy"])
        print("✓ Attached CloudWatch Logs policy")
    except Exception as e:
        print(f"⚠ CloudWatch policy (may already be attached): {e}")

    # Attach Bedrock InvokeModel policy (includes all Bedrock actions for Strands agent)
    bedrock_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                    "aws-marketplace:Subscribe",
                    "aws-marketplace:ViewSubscriptions",
                ],
                "Resource": "*",
            }
        ],
    }

    try:
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="BedrockInvokePolicy",
            PolicyDocument=json.dumps(bedrock_policy),
        )
        print("✓ Attached Bedrock InvokeModel policy")
    except Exception as e:
        print(f"⚠ Bedrock policy update: {e}")

    return role_arn


def prepare_lambda_build_context(handler_code, build_dir="lambda_diagnostic_agent"):
    """
    Create Lambda build context with Dockerfile and requirements.txt

    Args:
        handler_code: Python code for app.py (lambda_handler function)
        build_dir: Directory to create build context in
    """
    import os

    # Create build directory
    os.makedirs(build_dir, exist_ok=True)

    # Generate Dockerfile from constants
    dockerfile_content = f"""FROM --platform=linux/amd64 {ECR_CONFIG["base_image"]}

# Copy requirements (to task root)
COPY requirements.txt ${{LAMBDA_TASK_ROOT}}/

# Install dependencies
RUN pip install --no-cache-dir -r ${{LAMBDA_TASK_ROOT}}/requirements.txt

# Copy Lambda handler and helper modules to task root
COPY app.py ${{LAMBDA_TASK_ROOT}}/
COPY lab_helpers ${{LAMBDA_TASK_ROOT}}/lab_helpers

# Set handler
CMD ["app.lambda_handler"]
"""

    # Requirements for Strands agent deployment
    # Includes bedrock-agentcore and strands-agents for tool orchestration
    requirements = """strands-agents==1.12.0
bedrock-agentcore>=0.1.0
bedrock-agentcore-starter-toolkit>=0.1.24
boto3==1.40.65
botocore==1.40.65
pydantic>=2.0
requests>=2.30
"""

    # Write files
    with open(f"{build_dir}/Dockerfile", "w") as f:
        f.write(dockerfile_content)

    with open(f"{build_dir}/requirements.txt", "w") as f:
        f.write(requirements)

    with open(f"{build_dir}/app.py", "w") as f:
        f.write(handler_code)

    return {
        "build_dir": build_dir,
        "dockerfile": f"{build_dir}/Dockerfile",
        "requirements": f"{build_dir}/requirements.txt",
        "handler": f"{build_dir}/app.py",
    }


def setup_lab_02_infrastructure(handler_code, region_name=None):
    """
    Complete Lab 02 infrastructure setup:
    1. Display Lambda specifications
    2. Create Lambda build context (Dockerfile, requirements.txt, app.py)
    3. Create ECR repository
    4. Create Lambda execution role
    5. Store all values in Parameter Store

    Args:
        handler_code: Python code for Lambda handler (app.py)
        region_name: AWS region (uses config.AWS_REGION if None)

    Returns:
        Dictionary with all created resources
    """
    if region_name is None:
        region_name = AWS_REGION

    print("=" * 70)
    print("SETTING UP LAB 02 INFRASTRUCTURE")
    print("=" * 70)
    print()

    # Display Lambda specifications
    print("Lambda Function Specifications:")
    print(f"  Memory: {LAMBDA_CONFIG['memory_size']}MB (2GB for Strands agent)")
    print(f"  Timeout: {LAMBDA_CONFIG['timeout']}s")
    print(f"  Base Image: {ECR_CONFIG['base_image']}")
    print()

    # Prepare Lambda build context (creates Dockerfile, requirements.txt, app.py)
    print("Preparing Lambda build context...")
    build_context = prepare_lambda_build_context(handler_code)
    print(f"✓ Created build directory: {build_context['build_dir']}")
    print("✓ Created Dockerfile")
    print("✓ Created requirements.txt")
    print("✓ Created app.py (Lambda handler)")
    print()

    # Get account ID
    sts = boto3.client("sts", region_name=region_name)
    account_id = sts.get_caller_identity()["Account"]
    print(f"AWS Account: {account_id}")
    print(f"AWS Region: {region_name}")
    print()

    # Store workshop metadata
    print("Storing workshop metadata...")
    store_workshop_metadata(account_id, region_name, region_name)
    print()

    # Create ECR repository
    print("Setting up ECR repository...")
    repository_name = "aiml301-diagnostic-agent"
    ecr_repository_uri = create_ecr_repository(repository_name, region_name)
    print()

    # Create Lambda execution role
    print("Setting up Lambda execution role...")
    role_name = "aiml301-diagnostic-lambda-role"
    lambda_role_arn = create_lambda_execution_role(role_name, region_name)
    print()

    # Store configuration in Parameter Store
    print("Storing configuration in Parameter Store...")
    put_parameter(
        PARAMETER_PATHS["lab_02"]["ecr_repository_uri"],
        ecr_repository_uri,
        description="ECR repository URI for Lab 02 diagnostic agent",
        region_name=region_name,
    )
    put_parameter(
        PARAMETER_PATHS["lab_02"]["ecr_repository_name"],
        repository_name,
        description="ECR repository name for Lab 02",
        region_name=region_name,
    )
    put_parameter(
        PARAMETER_PATHS["lab_02"]["lambda_role_arn"],
        lambda_role_arn,
        description="Lambda execution role ARN for Lab 02",
        region_name=region_name,
    )
    print()

    # Return configuration
    config = {
        "account_id": account_id,
        "region": region_name,
        "ecr_repository_uri": ecr_repository_uri,
        "ecr_repository_name": repository_name,
        "lambda_role_arn": lambda_role_arn,
        "lambda_memory": LAMBDA_CONFIG["memory_size"],
        "lambda_timeout": LAMBDA_CONFIG["timeout"],
    }

    print("=" * 70)
    print("LAB 02 INFRASTRUCTURE SETUP COMPLETE")
    print("=" * 70)
    print()
    print("Configuration Summary:")
    for key, value in config.items():
        print(f"  {key}: {value}")
    print()
    print("✓ All values stored in Parameter Store")
    print("✓ Ready for Lambda container deployment")
    print()

    return config


def get_lab_02_deployment_instructions(config):
    """
    Generate Docker and AWS CLI commands for Lambda deployment

    Args:
        config: Configuration dictionary from setup_lab_02_infrastructure

    Returns:
        Formatted string with deployment instructions
    """
    ecr_uri = config["ecr_repository_uri"]
    role_arn = config["lambda_role_arn"]
    region = config["region"]

    instructions = f"""
╔════════════════════════════════════════════════════════════════════╗
║        LAB 02: DOCKER BUILD & LAMBDA DEPLOYMENT STEPS             ║
╚════════════════════════════════════════════════════════════════════╝

📦 DOCKER BUILD (Run locally or in CI/CD):

1. Build Docker image:
   docker build --provenance=false -t aiml301-diagnostic-agent:latest ./lambda_diagnostic_agent/

2. Authenticate Docker to ECR:
   aws ecr get-login-password --region {region} | docker login --username AWS --password-stdin {ecr_uri.rsplit("/", 1)[0]}

3. Tag image:
   docker tag aiml301-diagnostic-agent:latest {ecr_uri}

4. Push to ECR:
   docker push {ecr_uri}

🚀 LAMBDA DEPLOYMENT (Run after Docker image is pushed):

5. Create Lambda function:
   aws lambda create-function \\
     --function-name aiml301-diagnostic-agent \\
     --role {role_arn} \\
     --code ImageUri={ecr_uri} \\
     --package-type Image \\
     --timeout {LAMBDA_CONFIG["timeout"]} \\
     --memory-size {LAMBDA_CONFIG["memory_size"]} \\
     --region {region}

6. Update Lambda environment variables (optional):
   aws lambda update-function-configuration \\
     --function-name aiml301-diagnostic-agent \\
     --environment Variables={{MODEL_ID={MODEL_ID},REGION={region}}} \\
     --region {region}

📝 NOTES:
   - Image URI: {ecr_uri}
   - Role ARN: {role_arn}
   - Memory: {LAMBDA_CONFIG["memory_size"]}MB (2GB for Strands agent)
   - Timeout: {LAMBDA_CONFIG["timeout"]}s
   - All values are stored in Parameter Store at: /aiml301/lab-02/*
"""

    return instructions


def show_lambda_config():
    """Display Lambda configuration constants"""
    print("Lambda Configuration Constants:")
    print(f"  Memory: {LAMBDA_CONFIG['memory_size']}MB")
    print(f"  Timeout: {LAMBDA_CONFIG['timeout']}s")
    print(f"  Ephemeral Storage: {LAMBDA_CONFIG['ephemeral_storage']}MB")
    print()
    print("Base Image:")
    print(f"  {ECR_CONFIG['base_image']}")
    print()
    print("Model ID (from config.py):")
    print(f"  {MODEL_ID}")


# ============================================================================
# ZIP DEPLOYMENT SUPPORT (VPC-friendly alternative to Docker)
# ============================================================================


def get_zip_deployment_instructions(config):
    """
    Generate instructions for ZIP-based Lambda deployment

    Args:
        config: Configuration dictionary

    Returns:
        Formatted string with ZIP deployment instructions
    """
    region = config["region"]
    role_arn = config["lambda_role_arn"]

    instructions = f"""
╔════════════════════════════════════════════════════════════════════╗
║          LAB 02: ZIP-BASED LAMBDA DEPLOYMENT (VPC-Friendly)       ║
╚════════════════════════════════════════════════════════════════════╝

📦 ZIP PACKAGE CREATION & DEPLOYMENT:

ONE-LINE DEPLOYMENT (recommended):
   bash lab_helpers/lab_02/deploy.sh

This handles everything:
   ✓ Creates IAM role
   ✓ Installs dependencies for Python 3.11
   ✓ Packages lib/ and lab_helpers/
   ✓ Creates ZIP (direct upload if <50MB, S3 if larger)
   ✓ Deploys Lambda function
   ✓ Saves configuration to Parameter Store

ALTERNATIVE: Using Python packager directly:
   from lab_helpers.lab_02.lambda_packager import setup_lambda_zip_deployment

   handler_code = '''... your app.py code ...'''
   requirements_content = '''... pip requirements ...'''

   result = setup_lambda_zip_deployment(handler_code, requirements_content)

🚀 ADVANTAGES OVER DOCKER:

✓ Works in SageMaker VPC mode (no Docker daemon needed)
✓ Faster deployment (8 min vs 12 min with Docker)
✓ No external network access required
✓ Simpler setup (Python + pip only)
✓ Package size: ~30-35 MB (well under 250 MB limit)

📊 DEPLOYMENT OPTIONS:

Size < 50 MB:  Direct ZIP upload to Lambda
Size > 50 MB:  S3 upload → Lambda
Our package:   ~30-35 MB (uses direct upload by default)

📝 CONFIGURATION:

   - Role ARN: {role_arn}
   - Region: {region}
   - Memory: {LAMBDA_CONFIG["memory_size"]}MB
   - Timeout: {LAMBDA_CONFIG["timeout"]}s
   - All values stored in Parameter Store at: /aiml301/lab-02/*
"""

    return instructions


def show_deployment_methods():
    """Display available deployment methods and their characteristics"""
    from lab_helpers.constants import DEPLOYMENT_METHODS

    print("\n" + "=" * 70)
    print("LAMBDA DEPLOYMENT METHODS")
    print("=" * 70)

    for method_name, method_info in DEPLOYMENT_METHODS.items():
        print(f"\n{method_name.upper()}:")
        print(f"  Description: {method_info['description']}")
        print(f"  Requires: {', '.join(method_info['requires'])}")
        print(f"  VPC-Compatible: {'✅ Yes' if method_info['vpc_compatible'] else '❌ No'}")
        print(f"  Size Limit: {method_info['size_limit']}")
