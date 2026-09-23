#!/usr/bin/env python3
"""
Interactive deployment script for A2A Multi-Agent Incident Response System.
This script collects all required parameters and stores them in .a2a.config
"""

import sys
import uuid
import yaml
import subprocess
import json
import time
import re
import getpass
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock


class Colors:
    """ANSI color codes for terminal output"""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    END = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


# Thread-safe print lock for parallel deployments
print_lock = Lock()


def print_header(text: str, thread_safe: bool = False):
    """Print a formatted header"""
    output = f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.END}\n"
    output += f"{Colors.HEADER}{Colors.BOLD}{text.center(70)}{Colors.END}\n"
    output += f"{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.END}\n"

    if thread_safe:
        with print_lock:
            print(output, end="")
    else:
        print(output, end="")


def print_info(text: str, thread_safe: bool = False):
    """Print info message"""
    output = f"{Colors.CYAN}ℹ {text}{Colors.END}"
    if thread_safe:
        with print_lock:
            print(output)
    else:
        print(output)


def print_success(text: str, thread_safe: bool = False):
    """Print success message"""
    output = f"{Colors.GREEN}✓ {text}{Colors.END}"
    if thread_safe:
        with print_lock:
            print(output)
    else:
        print(output)


def print_warning(text: str, thread_safe: bool = False):
    """Print warning message"""
    output = f"{Colors.YELLOW}⚠ {text}{Colors.END}"
    if thread_safe:
        with print_lock:
            print(output)
    else:
        print(output)


def print_error(text: str, thread_safe: bool = False):
    """Print error message"""
    output = f"{Colors.RED}✗ {text}{Colors.END}"
    if thread_safe:
        with print_lock:
            print(output)
    else:
        print(output)


def get_input(prompt: str, default: Optional[str] = None, required: bool = True) -> str:
    """Get user input with optional default value"""
    if default:
        display_prompt = f"{Colors.BLUE}{prompt} [{Colors.GREEN}{default}{Colors.BLUE}]: {Colors.END}"
    else:
        display_prompt = f"{Colors.BLUE}{prompt}: {Colors.END}"

    while True:
        value = input(display_prompt).strip()

        if value:
            return value
        elif default:
            return default
        elif not required:
            return ""
        else:
            print_error("This field is required. Please provide a value.")


def get_secret(prompt: str, required: bool = True) -> str:
    """Get sensitive input (like API keys)"""
    display_prompt = f"{Colors.BLUE}{prompt}: {Colors.END}"

    while True:
        value = getpass.getpass(display_prompt).strip()

        if value:
            return value
        elif not required:
            return ""
        else:
            print_error("This field is required. Please provide a value.")


def generate_bucket_name(account_id: str = None) -> str:
    """Generate a unique S3 bucket name"""
    unique_id = str(uuid.uuid4())[:8]
    # Include account ID for better uniqueness if available
    if account_id:
        return f"a2a-smithy-models-{account_id}-{unique_id}"
    return f"a2a-smithy-models-{unique_id}"


def generate_cognito_domain_name(account_id: str = None) -> str:
    """Generate a unique Cognito domain name"""
    unique_id = str(uuid.uuid4())[:8]
    # Include account ID for better uniqueness if available
    if account_id:
        return f"agentcore-m2m-{account_id}-{unique_id}"
    return f"agentcore-m2m-{unique_id}"


def validate_bucket_name(bucket_name: str) -> Tuple[bool, str]:
    """Validate S3 bucket name according to AWS rules"""
    if not bucket_name:
        return (False, "Bucket name cannot be empty")

    if len(bucket_name) < 3 or len(bucket_name) > 63:
        return (False, "Bucket name must be between 3 and 63 characters")

    if not bucket_name[0].isalnum() or not bucket_name[-1].isalnum():
        return (False, "Bucket name must begin and end with a letter or number")

    # Check for invalid characters and patterns
    if not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$", bucket_name):
        return (
            False,
            "Bucket name must contain only lowercase letters, numbers, and hyphens",
        )

    if ".." in bucket_name or ".-" in bucket_name or "-." in bucket_name:
        return (
            False,
            "Bucket name cannot contain consecutive periods or period-hyphen combinations",
        )

    return (True, "Valid bucket name")


def check_s3_bucket_exists(bucket_name: str, region: str) -> bool:
    """Check if S3 bucket already exists"""
    success, output = run_command(["aws", "s3api", "head-bucket", "--bucket", bucket_name, "--region", region])
    return success


def validate_cognito_domain_name(domain_name: str) -> Tuple[bool, str]:
    """Validate Cognito User Pool domain name"""
    if not domain_name:
        return (False, "Domain name cannot be empty")

    if len(domain_name) < 1 or len(domain_name) > 63:
        return (False, "Domain name must be between 1 and 63 characters")

    if not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$", domain_name):
        return (
            False,
            "Domain name must contain only lowercase letters, numbers, and hyphens, and must start and end with alphanumeric",
        )

    return (True, "Valid domain name")


def validate_stack_name(stack_name: str) -> Tuple[bool, str]:
    """Validate CloudFormation stack name"""
    if not stack_name:
        return (False, "Stack name cannot be empty")

    if len(stack_name) > 128:
        return (False, "Stack name must be 128 characters or fewer")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9\-]*$", stack_name):
        return (
            False,
            "Stack name must start with a letter and contain only alphanumeric characters and hyphens",
        )

    return (True, "Valid stack name")


def load_existing_config(config_path: Path) -> Dict[str, Any]:
    """Load existing configuration if it exists"""
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(config: Dict[str, Any], config_path: Path):
    """Save configuration to YAML file"""
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    print_success(f"Configuration saved to {config_path}")


def run_command(cmd: list, capture_output: bool = True, timeout: int = 10) -> Tuple[bool, str]:
    """Run a shell command and return (success, output)"""
    try:
        result = subprocess.run(cmd, capture_output=capture_output, text=True, timeout=timeout, check=False)
        return (result.returncode == 0, result.stdout.strip() if capture_output else "")
    except subprocess.TimeoutExpired:
        return (False, f"Command timed out after {timeout} seconds")
    except FileNotFoundError:
        return (False, f"Command not found: {cmd[0]}")
    except Exception as e:
        return (False, str(e))


def check_aws_cli() -> bool:
    """Check if AWS CLI is installed"""
    success, output = run_command(["aws", "--version"])
    if success:
        print_success(f"AWS CLI is installed: {output.split()[0]}")
        return True
    else:
        print_error("AWS CLI is not installed")
        print_info("Install AWS CLI: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html")
        return False


def check_aws_credentials() -> bool:
    """Check if AWS credentials are configured and valid"""
    success, output = run_command(["aws", "sts", "get-caller-identity"])
    if success:
        try:
            identity = json.loads(output)
            print_success("AWS credentials are valid")
            print_info(f"  Account: {identity.get('Account', 'N/A')}")
            print_info(f"  User/Role: {identity.get('Arn', 'N/A').split('/')[-1]}")
            return True
        except json.JSONDecodeError:
            print_error("Failed to parse AWS identity")
            return False
    else:
        print_error("AWS credentials are not configured or invalid")
        print_info("Configure AWS CLI: aws configure")
        return False


def check_aws_region() -> Tuple[bool, Optional[str]]:
    """Check if AWS region is configured and is us-west-2"""
    success, output = run_command(["aws", "configure", "get", "region"])
    if success and output:
        region = output.strip()
        if region == "us-west-2":
            print_success("AWS region is correctly set to us-west-2")
            return (True, region)
        else:
            print_error(f"AWS region is set to '{region}' but must be 'us-west-2'")
            print_info("This solution is only supported in us-west-2")
            print_info("Change region: aws configure set region us-west-2")
            return (False, region)
    else:
        print_error("AWS region is not configured")
        print_info("Configure region: aws configure set region us-west-2")
        return (False, None)


def check_bedrock_model_access() -> bool:
    """Check if Bedrock model access is enabled"""
    print_info("Checking Bedrock model access...")
    success, output = run_command(["aws", "bedrock", "list-foundation-models", "--region", "us-west-2"])
    if success:
        print_success("Bedrock API is accessible")
        return True
    else:
        print_warning("Could not verify Bedrock access (this may be a permissions issue)")
        return True  # Don't fail on this check, just warn


def run_pre_checks() -> Tuple[bool, Optional[str]]:
    """Run all pre-deployment checks and return (success, account_id)"""
    print_header("Pre-Deployment Checks")
    print_info("Verifying prerequisites...\n")

    checks_passed = True
    account_id = None

    # Check AWS CLI
    if not check_aws_cli():
        checks_passed = False

    print()

    # Check AWS credentials and get account ID
    success, output = run_command(["aws", "sts", "get-caller-identity"])
    if success:
        try:
            identity = json.loads(output)
            account_id = identity.get("Account")
            print_success("AWS credentials are valid")
            print_info(f"  Account: {account_id or 'N/A'}")
            print_info(f"  User/Role: {identity.get('Arn', 'N/A').split('/')[-1]}")
        except json.JSONDecodeError:
            print_error("Failed to parse AWS identity")
            checks_passed = False
    else:
        print_error("AWS credentials are not configured or invalid")
        print_info("Configure AWS CLI: aws configure")
        checks_passed = False

    print()

    # Check AWS region
    region_ok, region = check_aws_region()
    if not region_ok:
        checks_passed = False

    print()

    # Check Bedrock access (warning only)
    check_bedrock_model_access()

    print()

    if not checks_passed:
        print_error("Pre-deployment checks failed. Please fix the issues above before continuing.")
        return (False, None)

    print_success("All pre-deployment checks passed!")
    return (True, account_id)


def collect_deployment_parameters(account_id: str = None) -> Dict[str, Any]:
    """Interactively collect all deployment parameters"""

    config_path = Path(".a2a.config")
    existing_config = load_existing_config(config_path)

    print_header("A2A Multi-Agent Incident Response - Deployment Configuration")

    print_info("This script will help you configure all parameters for deployment.")
    print_info("Press Enter to accept default values (shown in green brackets).\n")

    # Check if config exists
    if existing_config:
        print_warning(f"Found existing configuration at {config_path}")
        use_existing = get_input(
            "Do you want to use existing values as defaults? (yes/no)",
            default="yes",
            required=True,
        ).lower() in ["yes", "y"]
        print()
    else:
        use_existing = False

    config = {}

    # AWS Configuration (region is fixed to us-west-2)
    print_header("AWS Configuration")
    config["aws"] = {
        "region": "us-west-2",  # Fixed to us-west-2 as verified in pre-checks
        "bedrock_model_id": get_input(
            "Bedrock Model ID",
            default=(
                existing_config.get("aws", {}).get(
                    "bedrock_model_id",
                    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                )
                if use_existing
                else "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
            ),
            required=True,
        ),
    }
    print_info("Region is fixed to us-west-2 (verified in pre-checks)")

    # Stack Names with validation
    print_header("CloudFormation Stack Names")
    config["stacks"] = {}

    stack_names = {
        "cognito": ("Cognito Stack Name", "cognito-stack-a2a"),
        "monitoring_agent": ("Monitoring Agent Stack Name", "monitor-agent-a2a"),
        "web_search_agent": ("Web Search Agent Stack Name", "web-search-agent-a2a"),
        "host_agent": ("Host Agent Stack Name", "host-agent-a2a"),
    }

    for key, (prompt, default_name) in stack_names.items():
        while True:
            stack_name = get_input(
                prompt,
                default=(existing_config.get("stacks", {}).get(key, default_name) if use_existing else default_name),
                required=True,
            )
            is_valid, message = validate_stack_name(stack_name)
            if is_valid:
                config["stacks"][key] = stack_name
                break
            else:
                print_error(f"Invalid stack name: {message}")

    # Cognito Domain Name with validation
    print_header("Cognito Configuration")
    default_cognito_domain = (
        existing_config.get("cognito", {}).get("domain_name")
        if use_existing
        else generate_cognito_domain_name(account_id)
    )

    while True:
        domain_name = get_input(
            "Cognito User Pool Domain Name",
            default=default_cognito_domain,
            required=True,
        )
        is_valid, message = validate_cognito_domain_name(domain_name)
        if is_valid:
            config["cognito"] = {"domain_name": domain_name}
            print_info("This unique domain prevents conflicts with existing Cognito User Pools")
            break
        else:
            print_error(f"Invalid domain name: {message}")

    # Admin User Configuration
    print()
    print_info("Admin User Configuration for Cognito User Pool")
    print_info("This user will be created automatically in the user pool")
    print()

    admin_email = get_input(
        "Admin User Email",
        default=(existing_config.get("cognito", {}).get("admin_email") if use_existing else ""),
        required=True,
    )

    # Validate email format
    import re

    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    while not re.match(email_pattern, admin_email):
        print_error("Invalid email format. Please enter a valid email address.")
        admin_email = get_input("Admin User Email", required=True)

    config["cognito"]["admin_email"] = admin_email

    print_info("Admin password (optional - leave empty for auto-generated temporary password)")
    admin_password = get_secret(
        "Admin User Password (press Enter to skip)",
        required=False,
    )

    config["cognito"]["admin_password"] = admin_password if admin_password else ""

    # S3 Bucket for Smithy Models with validation
    print_header("S3 Configuration")
    default_bucket = (
        existing_config.get("s3", {}).get("smithy_models_bucket") if use_existing else generate_bucket_name(account_id)
    )

    while True:
        bucket_name = get_input("S3 Bucket Name for Smithy Models", default=default_bucket, required=True)
        is_valid, message = validate_bucket_name(bucket_name)
        if is_valid:
            # Check if bucket already exists
            if check_s3_bucket_exists(bucket_name, "us-west-2"):
                print_warning(f"Bucket '{bucket_name}' already exists. You can use it if you own it.")
                use_existing_bucket = get_input(
                    "Use this existing bucket? (yes/no)", default="yes", required=True
                ).lower() in ["yes", "y"]
                if use_existing_bucket:
                    config["s3"] = {"smithy_models_bucket": bucket_name}
                    break
                else:
                    continue
            else:
                config["s3"] = {"smithy_models_bucket": bucket_name}
                break
        else:
            print_error(f"Invalid bucket name: {message}")

    # GitHub Configuration
    print_header("GitHub Configuration")
    config["github"] = {
        "url": get_input(
            "GitHub Repository URL",
            default=(
                existing_config.get("github", {}).get(
                    "url",
                    "https://github.com/awslabs/amazon-bedrock-agentcore-samples.git",
                )
                if use_existing
                else "https://github.com/awslabs/amazon-bedrock-agentcore-samples.git"
            ),
            required=True,
        ),
        # Agent directories are taken from CloudFormation defaults - not configurable
        "monitoring_agent_directory": "monitoring_agent",
        "web_search_agent_directory": "web_search_openai_agents",
        "host_agent_directory": "host_adk_agent",
    }
    print_info(
        "Agent directories will use CloudFormation defaults (monitoring_agent, web_search_openai_agents, host_adk_agent)"
    )

    # API Keys
    print_header("API Keys Configuration")
    print_warning("API keys will be stored in .a2a.config - keep this file secure!")
    print_info("Input is hidden for security. Paste your key and press Enter.\n")

    # Check if we should ask for API keys
    ask_for_keys = True
    if use_existing and existing_config.get("api_keys"):
        print_info("Existing API keys found in configuration.")
        update_keys = get_input("Do you want to update API keys? (yes/no)", default="no", required=True).lower() in [
            "yes",
            "y",
        ]
        ask_for_keys = update_keys
        print()

    if ask_for_keys:
        config["api_keys"] = {
            "openai": get_secret("OpenAI API Key", required=True),
            "openai_model": get_input(
                "OpenAI Model ID",
                default=(
                    existing_config.get("api_keys", {}).get("openai_model", "gpt-4o-2024-08-06")
                    if use_existing
                    else "gpt-4o-2024-08-06"
                ),
                required=True,
            ),
            "tavily": get_secret("Tavily API Key", required=True),
            "google": get_secret("Google API Key (for ADK)", required=True),
            "google_model": get_input(
                "Google Model ID",
                default=(
                    existing_config.get("api_keys", {}).get("google_model", "gemini-2.5-flash")
                    if use_existing
                    else "gemini-2.5-flash"
                ),
                required=True,
            ),
        }
    else:
        config["api_keys"] = existing_config.get("api_keys", {})

    return config


def display_configuration(config: Dict[str, Any]):
    """Display the collected configuration"""
    print_header("Configuration Summary")

    print(f"{Colors.BOLD}AWS Configuration:{Colors.END}")
    print(f"  Region: {config['aws']['region']}")
    print(f"  Bedrock Model ID: {config['aws']['bedrock_model_id']}")

    print(f"\n{Colors.BOLD}CloudFormation Stacks:{Colors.END}")
    print(f"  Cognito: {config['stacks']['cognito']}")
    print(f"  Monitoring Agent: {config['stacks']['monitoring_agent']}")
    print(f"  Web Search Agent: {config['stacks']['web_search_agent']}")
    print(f"  Host Agent: {config['stacks']['host_agent']}")

    print(f"\n{Colors.BOLD}Cognito Configuration:{Colors.END}")
    print(f"  User Pool Domain: {config['cognito']['domain_name']}")
    print(f"  Admin User Email: {config['cognito']['admin_email']}")
    if config["cognito"].get("admin_password"):
        print(f"  Admin User Password: {'*' * 20} (configured)")
    else:
        print("  Admin User Password: (auto-generated temporary password will be sent via email)")

    print(f"\n{Colors.BOLD}S3 Configuration:{Colors.END}")
    print(f"  Smithy Models Bucket: {config['s3']['smithy_models_bucket']}")

    print(f"\n{Colors.BOLD}GitHub Configuration:{Colors.END}")
    print(f"  Repository URL: {config['github']['url']}")
    print(f"  Monitoring Agent Dir: {config['github']['monitoring_agent_directory']}")
    print(f"  Web Search Agent Dir: {config['github']['web_search_agent_directory']}")
    print(f"  Host Agent Dir: {config['github']['host_agent_directory']}")

    print(f"\n{Colors.BOLD}API Keys:{Colors.END}")
    print(f"  OpenAI API Key: {'*' * 20} (configured)")
    print(f"  Tavily API Key: {'*' * 20} (configured)")
    print(f"  Google API Key: {'*' * 20} (configured)")

    print()


def wait_for_stack(stack_name: str, region: str, operation: str = "create", thread_safe: bool = False) -> bool:
    """Wait for CloudFormation stack operation to complete"""
    print_info(
        f"Waiting for stack '{stack_name}' to complete {operation}...",
        thread_safe=thread_safe,
    )

    max_wait_time = 1800  # 30 minutes
    wait_interval = 15  # 15 seconds
    elapsed_time = 0

    while elapsed_time < max_wait_time:
        success, output = run_command(
            [
                "aws",
                "cloudformation",
                "describe-stacks",
                "--stack-name",
                stack_name,
                "--region",
                region,
                "--query",
                "Stacks[0].StackStatus",
                "--output",
                "text",
            ]
        )

        if success:
            status = output.strip()

            # Check for completion statuses
            if operation == "create" and status == "CREATE_COMPLETE":
                print_success(
                    f"Stack '{stack_name}' created successfully!",
                    thread_safe=thread_safe,
                )
                return True
            elif operation == "create" and status == "CREATE_FAILED":
                print_error(f"Stack '{stack_name}' creation failed!", thread_safe=thread_safe)
                return False
            elif operation == "create" and status == "ROLLBACK_COMPLETE":
                print_error(
                    f"Stack '{stack_name}' creation failed and rolled back!",
                    thread_safe=thread_safe,
                )
                return False
            elif operation == "create" and status == "ROLLBACK_IN_PROGRESS":
                print_warning(
                    f"Stack '{stack_name}' is rolling back... Status: {status}",
                    thread_safe=thread_safe,
                )
            else:
                print_info(
                    f"[{stack_name}] Status: {status} (waiting...)",
                    thread_safe=thread_safe,
                )

        time.sleep(wait_interval)
        elapsed_time += wait_interval

    print_error(
        f"Timeout waiting for stack '{stack_name}' (waited {max_wait_time}s)",
        thread_safe=thread_safe,
    )
    return False


def create_s3_bucket_and_upload(config: Dict[str, Any]) -> bool:
    """Create S3 bucket and upload Smithy model"""
    print_header("Step 0: Create S3 Bucket and Upload Smithy Model")

    bucket_name = config["s3"]["smithy_models_bucket"]
    region = config["aws"]["region"]

    # Check if bucket already exists
    if check_s3_bucket_exists(bucket_name, region):
        print_info(f"Bucket '{bucket_name}' already exists, skipping creation")
    else:
        print_info(f"Creating S3 bucket: {bucket_name}")
        success, output = run_command(["aws", "s3", "mb", f"s3://{bucket_name}", "--region", region])

        if success:
            print_success(f"S3 bucket '{bucket_name}' created successfully!")
        else:
            print_error(f"Failed to create S3 bucket: {output}")
            return False

    # Upload Smithy model
    smithy_model_path = "cloudformation/smithy-models/monitoring-service.json"
    s3_key = "smithy-models/monitoring-service.json"

    if not Path(smithy_model_path).exists():
        print_error(f"Smithy model file not found: {smithy_model_path}")
        return False

    print_info(f"Uploading Smithy model to s3://{bucket_name}/{s3_key}")
    success, output = run_command(
        [
            "aws",
            "s3",
            "cp",
            smithy_model_path,
            f"s3://{bucket_name}/{s3_key}",
            "--region",
            region,
        ]
    )

    if success:
        print_success("Smithy model uploaded successfully!")
        return True
    else:
        print_error(f"Failed to upload Smithy model: {output}")
        return False


def upload_template_to_s3(
    template_file: str, bucket_name: str, region: str, thread_safe: bool = False
) -> Optional[str]:
    """Upload CloudFormation template to S3 and return the S3 URL"""
    template_name = Path(template_file).name
    s3_key = f"cloudformation-templates/{template_name}"
    s3_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{s3_key}"

    print_info(f"Uploading template {template_name} to S3...", thread_safe=thread_safe)

    success, output = run_command(
        [
            "aws",
            "s3",
            "cp",
            template_file,
            f"s3://{bucket_name}/{s3_key}",
            "--region",
            region,
        ]
    )

    if success:
        print_success(f"Template uploaded to S3: {s3_key}", thread_safe=thread_safe)
        return s3_url
    else:
        print_error(f"Failed to upload template to S3: {output}", thread_safe=thread_safe)
        return None


def deploy_stack(
    stack_name: str,
    template_file: str,
    parameters: list,
    region: str,
    bucket_name: Optional[str] = None,
    description: str = "",
    thread_safe: bool = False,
) -> bool:
    """Generic function to deploy a CloudFormation stack (always via S3)"""
    if description:
        print_info(description, thread_safe=thread_safe)

    print_info(f"Creating CloudFormation stack: {stack_name}", thread_safe=thread_safe)

    # Always use S3 for consistency and to avoid size limits
    if not bucket_name:
        print_error(
            "S3 bucket required for stack deployment but not provided.",
            thread_safe=thread_safe,
        )
        return False

    print_info("Uploading template to S3 before deployment", thread_safe=thread_safe)
    s3_url = upload_template_to_s3(template_file, bucket_name, region, thread_safe)
    if not s3_url:
        return False

    cmd = (
        [
            "aws",
            "cloudformation",
            "create-stack",
            "--stack-name",
            stack_name,
            "--template-url",
            s3_url,
            "--parameters",
        ]
        + parameters
        + [
            "--capabilities",
            "CAPABILITY_IAM",
            "--region",
            region,
        ]
    )

    success, output = run_command(cmd)

    if success:
        print_success(f"Stack creation initiated: {stack_name}", thread_safe=thread_safe)
        return wait_for_stack(stack_name, region, "create", thread_safe=thread_safe)
    else:
        if "AlreadyExistsException" in output:
            print_warning(f"Stack '{stack_name}' already exists", thread_safe=thread_safe)
            return True
        print_error(f"Failed to create stack: {output}", thread_safe=thread_safe)
        return False


def deploy_cognito_stack(config: Dict[str, Any]) -> bool:
    """Deploy Cognito CloudFormation stack"""
    print_header("Step 1: Deploy Cognito Stack")

    parameters = [
        f"ParameterKey=DomainName,ParameterValue={config['cognito']['domain_name']}",
        f"ParameterKey=AdminUserEmail,ParameterValue={config['cognito']['admin_email']}",
    ]

    # Only add AdminUserPassword if provided
    if config["cognito"].get("admin_password"):
        parameters.append(f"ParameterKey=AdminUserPassword,ParameterValue={config['cognito']['admin_password']}")

    return deploy_stack(
        stack_name=config["stacks"]["cognito"],
        template_file="cloudformation/cognito.yaml",
        parameters=parameters,
        region=config["aws"]["region"],
        bucket_name=config["s3"]["smithy_models_bucket"],
        description=f"Using Cognito domain: {config['cognito']['domain_name']}, Admin user: {config['cognito']['admin_email']}",
    )


def deploy_monitoring_agent(config: Dict[str, Any]) -> bool:
    """Deploy Monitoring Agent CloudFormation stack"""
    print_header("Step 2: Deploy Monitoring Agent")

    return deploy_stack(
        stack_name=config["stacks"]["monitoring_agent"],
        template_file="cloudformation/monitoring_agent.yaml",
        parameters=[
            f"ParameterKey=GitHubURL,ParameterValue={config['github']['url']}",
            f"ParameterKey=CognitoStackName,ParameterValue={config['stacks']['cognito']}",
            f"ParameterKey=SmithyModelS3Bucket,ParameterValue={config['s3']['smithy_models_bucket']}",
            f"ParameterKey=BedrockModelId,ParameterValue={config['aws']['bedrock_model_id']}",
        ],
        region=config["aws"]["region"],
        bucket_name=config["s3"]["smithy_models_bucket"],
    )


def deploy_web_search_agent(config: Dict[str, Any]) -> bool:
    """Deploy Web Search Agent CloudFormation stack"""
    print_header("Step 3: Deploy Web Search Agent")

    return deploy_stack(
        stack_name=config["stacks"]["web_search_agent"],
        template_file="cloudformation/web_search_agent.yaml",
        parameters=[
            f"ParameterKey=OpenAIKey,ParameterValue={config['api_keys']['openai']}",
            f"ParameterKey=OpenAIModelId,ParameterValue={config['api_keys']['openai_model']}",
            f"ParameterKey=TavilyAPIKey,ParameterValue={config['api_keys']['tavily']}",
            f"ParameterKey=GitHubURL,ParameterValue={config['github']['url']}",
            f"ParameterKey=CognitoStackName,ParameterValue={config['stacks']['cognito']}",
        ],
        region=config["aws"]["region"],
        bucket_name=config["s3"]["smithy_models_bucket"],
    )


def deploy_host_agent(config: Dict[str, Any]) -> bool:
    """Deploy Host Agent CloudFormation stack"""
    print_header("Step 4: Deploy Host Agent")

    return deploy_stack(
        stack_name=config["stacks"]["host_agent"],
        template_file="cloudformation/host_agent.yaml",
        parameters=[
            f"ParameterKey=GoogleApiKey,ParameterValue={config['api_keys']['google']}",
            f"ParameterKey=GoogleModelId,ParameterValue={config['api_keys']['google_model']}",
            f"ParameterKey=GitHubURL,ParameterValue={config['github']['url']}",
            f"ParameterKey=CognitoStackName,ParameterValue={config['stacks']['cognito']}",
        ],
        region=config["aws"]["region"],
        bucket_name=config["s3"]["smithy_models_bucket"],
    )


def deploy_agent_parallel(
    agent_name: str,
    config: Dict[str, Any],
    stack_key: str,
    template_file: str,
    parameters: list,
) -> Tuple[str, bool]:
    """Deploy an agent stack in parallel (thread-safe)"""
    try:
        print_header(f"Deploying {agent_name}", thread_safe=True)

        success = deploy_stack(
            stack_name=config["stacks"][stack_key],
            template_file=template_file,
            parameters=parameters,
            region=config["aws"]["region"],
            bucket_name=config["s3"]["smithy_models_bucket"],
            thread_safe=True,
        )

        return (agent_name, success)
    except Exception as e:
        print_error(f"Error deploying {agent_name}: {str(e)}", thread_safe=True)
        return (agent_name, False)


def deploy_agents_parallel(config: Dict[str, Any]) -> bool:
    """Deploy all three agent stacks in parallel"""
    print_header("Steps 2-4: Deploy Agent Stacks (Parallel)")
    print_info("Deploying Monitoring, Web Search, and Host agents in parallel...")
    print_warning("This is faster but may produce interleaved output\n")

    # Prepare deployment tasks
    tasks = [
        (
            "Monitoring Agent",
            config,
            "monitoring_agent",
            "cloudformation/monitoring_agent.yaml",
            [
                f"ParameterKey=GitHubURL,ParameterValue={config['github']['url']}",
                f"ParameterKey=CognitoStackName,ParameterValue={config['stacks']['cognito']}",
                f"ParameterKey=SmithyModelS3Bucket,ParameterValue={config['s3']['smithy_models_bucket']}",
                f"ParameterKey=BedrockModelId,ParameterValue={config['aws']['bedrock_model_id']}",
            ],
        ),
        (
            "Web Search Agent",
            config,
            "web_search_agent",
            "cloudformation/web_search_agent.yaml",
            [
                f"ParameterKey=OpenAIKey,ParameterValue={config['api_keys']['openai']}",
                f"ParameterKey=OpenAIModelId,ParameterValue={config['api_keys']['openai_model']}",
                f"ParameterKey=TavilyAPIKey,ParameterValue={config['api_keys']['tavily']}",
                f"ParameterKey=GitHubURL,ParameterValue={config['github']['url']}",
                f"ParameterKey=CognitoStackName,ParameterValue={config['stacks']['cognito']}",
            ],
        ),
        (
            "Host Agent",
            config,
            "host_agent",
            "cloudformation/host_agent.yaml",
            [
                f"ParameterKey=GoogleApiKey,ParameterValue={config['api_keys']['google']}",
                f"ParameterKey=GoogleModelId,ParameterValue={config['api_keys']['google_model']}",
                f"ParameterKey=GitHubURL,ParameterValue={config['github']['url']}",
                f"ParameterKey=CognitoStackName,ParameterValue={config['stacks']['cognito']}",
            ],
        ),
    ]

    # Deploy agents in parallel using ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all deployment tasks
        future_to_agent = {executor.submit(deploy_agent_parallel, *task): task[0] for task in tasks}

        # Collect results as they complete
        for future in as_completed(future_to_agent):
            agent_name = future_to_agent[future]
            try:
                name, success = future.result()
                results[name] = success
                if success:
                    print_success(f"✓ {name} deployment completed", thread_safe=True)
                else:
                    print_error(f"✗ {name} deployment failed", thread_safe=True)
            except Exception as e:
                print_error(f"Exception deploying {agent_name}: {str(e)}", thread_safe=True)
                results[agent_name] = False

    # Check if all deployments succeeded
    all_success = all(results.values())

    print()
    if all_success:
        print_success("All agent stacks deployed successfully!")
    else:
        print_error("One or more agent stacks failed to deploy")
        for agent, success in results.items():
            status = "✓" if success else "✗"
            print_info(f"  {status} {agent}: {'Success' if success else 'Failed'}")

    return all_success


def print_cleanup_instructions():
    """Print instructions to run cleanup after deployment failure"""
    print()
    print_header("Deployment Failed - Cleanup Required")
    print_error("Deployment has failed and may have left partial resources.")
    print_warning("You should clean up any created resources before retrying deployment.\n")

    print_info("To clean up all created resources, run:")
    print(f"  {Colors.GREEN}uv run cleanup.py{Colors.END}\n")

    print_info("After cleanup, you can retry deployment by running:")
    print(f"  {Colors.GREEN}python3 deploy.py{Colors.END}")
    print()


def run_deployment(config: Dict[str, Any], parallel: bool = True) -> bool:
    """Run all deployment steps"""
    print_header("Starting Deployment")

    if parallel:
        print_warning("Using parallel deployment - approximately 7-10 minutes to complete")
    else:
        print_warning("Using sequential deployment - approximately 10-15 minutes to complete")

    print_info("You can monitor progress in the AWS CloudFormation console\n")

    # Step 0: Create S3 bucket and upload Smithy model
    if not create_s3_bucket_and_upload(config):
        print_error("Failed at Step 0: S3 bucket creation/upload")
        print_cleanup_instructions()
        return False

    print()

    # Step 1: Deploy Cognito stack
    if not deploy_cognito_stack(config):
        print_error("Failed at Step 1: Cognito stack deployment")
        print_cleanup_instructions()
        return False

    print()

    # Steps 2-4: Deploy agent stacks
    if parallel:
        # Deploy all three agents in parallel
        if not deploy_agents_parallel(config):
            print_error("Failed at Steps 2-4: Agent stack deployments")
            print_cleanup_instructions()
            return False
    else:
        # Deploy agents sequentially (original behavior)
        # Step 2: Deploy Monitoring Agent
        if not deploy_monitoring_agent(config):
            print_error("Failed at Step 2: Monitoring Agent deployment")
            print_cleanup_instructions()
            return False

        print()

        # Step 3: Deploy Web Search Agent
        if not deploy_web_search_agent(config):
            print_error("Failed at Step 3: Web Search Agent deployment")
            print_cleanup_instructions()
            return False

        print()

        # Step 4: Deploy Host Agent
        if not deploy_host_agent(config):
            print_error("Failed at Step 4: Host Agent deployment")
            print_cleanup_instructions()
            return False

    print()
    print_header("Deployment Complete!")
    print_success("All stacks have been deployed successfully!")
    print_info("\nNext steps:")
    print_info("1. Test individual agents: uv run test/connect_agent.py --agent <monitor|websearch|host>")
    print_info("2. Run the React frontend: cd frontend && npm install && ./setup-env.sh && npm run dev")
    print_info("3. Use A2A Inspector or ADK Web for debugging")

    return True


def main():
    """Main entry point"""
    try:
        # Run pre-deployment checks
        checks_passed, account_id = run_pre_checks()
        if not checks_passed:
            sys.exit(1)

        # Collect parameters
        config = collect_deployment_parameters(account_id)

        # Display configuration
        display_configuration(config)

        # Confirm and save
        print_header("Save Configuration")
        confirm = get_input(
            "Save this configuration to .a2a.config? (yes/no)",
            default="yes",
            required=True,
        ).lower() in ["yes", "y"]

        if confirm:
            config_path = Path(".a2a.config")
            save_config(config, config_path)

            # Add to .gitignore
            gitignore_path = Path(".gitignore")
            gitignore_content = ""
            if gitignore_path.exists():
                with open(gitignore_path, "r") as f:
                    gitignore_content = f.read()

            if ".a2a.config" not in gitignore_content:
                with open(gitignore_path, "a") as f:
                    f.write("\n# A2A Deployment Configuration\n.a2a.config\n")
                print_success("Added .a2a.config to .gitignore")

            print()
            print_success("Configuration complete!")

            # Ask if user wants to deploy now
            print_header("Deploy Now?")
            deploy_now = get_input(
                "Do you want to start the deployment now? (yes/no)",
                default="yes",
                required=True,
            ).lower() in ["yes", "y"]

            if deploy_now:
                print()

                # Ask about parallel deployment
                use_parallel = get_input(
                    "Use parallel deployment for faster execution? (yes/no)",
                    default="yes",
                    required=True,
                ).lower() in ["yes", "y"]

                print()

                if run_deployment(config, parallel=use_parallel):
                    sys.exit(0)
                else:
                    sys.exit(1)
            else:
                print_info("\nDeployment skipped. You can run this script again to deploy.")
                print_info("Or manually run the AWS CLI commands for each stack.")

        else:
            print_warning("Configuration not saved. Exiting.")
            sys.exit(0)

    except KeyboardInterrupt:
        print_error("\n\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print_error(f"An error occurred: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
