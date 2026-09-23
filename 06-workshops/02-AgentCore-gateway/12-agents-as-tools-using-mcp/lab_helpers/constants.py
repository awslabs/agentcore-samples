"""
Central registry of all AWS Systems Manager Parameter Store paths
Used by deployment and retrieval helpers across all labs

This ensures parameter naming is consistent, discoverable, and version-controlled.
No hardcoded values in notebooks or helper functions.
"""

# Workshop-wide naming convention
WORKSHOP_NAME = "aiml301-sre-agentcore"
WORKSHOP_PREFIX = "/aiml301"

# Parameter Store path structure
PARAMETER_PATHS = {
    "workshop": {
        "account_id": "/aiml301/workshop/account-id",
        "region": "/aiml301/workshop/region",
    },
    # Lab 1: Prerequisites - Cognito Setup (for Labs 3-5 authentication)
    "cognito": {
        "user_pool_id": "/aiml301/cognito/user-pool-id",
        "user_pool_name": "/aiml301/cognito/user-pool-name",
        "user_pool_arn": "/aiml301/cognito/user-pool-arn",
        "domain": "/aiml301/cognito/domain",
        "token_endpoint": "/aiml301/cognito/token-endpoint",
        "user_auth_client_id": "/aiml301/cognito/user-auth-client-id",
        "user_auth_client_name": "/aiml301/cognito/user-auth-client-name",
        "m2m_client_id": "/aiml301/cognito/m2m-client-id",
        "m2m_client_secret": "/aiml301/cognito/m2m-client-secret",
        "m2m_client_name": "/aiml301/cognito/m2m-client-name",
        "resource_server_id": "/aiml301/cognito/resource-server-id",
        "resource_server_identifier": "/aiml301/cognito/resource-server-identifier",
        "test_user_email": "/aiml301/cognito/test-user-email",
        "test_user_password": "/aiml301/cognito/test-user-password",
        "approver_user_email": "/aiml301/cognito/approver-user-email",
        "approver_user_password": "/aiml301/cognito/approver-user-password",
    },
    # Lab 1.5: Memory Setup (created after Cognito, used by Labs 2-5)
    "memory": {
        "memory_id": "/aiml301/memory/id",
        "memory_name_prefix": "SREAgent_STM",
        "default_session_id": "/aiml301/memory/default-session-id",
    },
    # Lab 2: Diagnostics Agent
    "lab_02": {
        "ecr_repository_uri": "/aiml301/lab-02/ecr-repository-uri",
        "ecr_repository_name": "/aiml301/lab-02/ecr-repository-name",
        "lambda_role_arn": "/aiml301/lab-02/lambda-role-arn",
        "lambda_function_arn": "/aiml301/lab-02/lambda-function-arn",
        "lambda_function_name": "/aiml301/lab-02/lambda-function-name",
        "gateway_id": "/aiml301/lab-02/gateway-id",
        "gateway_url": "/aiml301/lab-02/gateway-url",
        "gateway_role_arn": "/aiml301/lab-02/gateway-role-arn",
    },
    # Lab 3: Remediation Agent (AgentCore Runtime + Gateway with M2M Auth)
    "lab_03": {
        # Code Interpreter Configuration
        "code_interpreter_id": "/aiml301_sre_agentcore/lab-03/code-interpreter-id",
        "code_interpreter_arn": "/aiml301_sre_agentcore/lab-03/code-interpreter-arn",
        "code_interpreter_role_arn": "/aiml301_sre_agentcore/lab-03/code-interpreter-role-arn",
        # Runtime Configuration
        "runtime_role_arn": "/aiml301_sre_agentcore/lab-03/runtime-role-arn",
        "runtime_id": "/aiml301_sre_agentcore/lab-03/runtime-id",
        "runtime_arn": "/aiml301_sre_agentcore/lab-03/runtime-arn",
        "runtime_config": "/aiml301_sre_agentcore/lab-03/runtime-config",
        # Gateway Configuration
        "gateway_role_arn": "/aiml301_sre_agentcore/lab-03/gateway-role-arn",
        "gateway_id": "/aiml301_sre_agentcore/lab-03/gateway-id",
        "gateway_config": "/aiml301_sre_agentcore/lab-03/gateway-config",
        # OAuth2 M2M Authentication
        "oauth2_provider_arn": "/aiml301/lab-03/oauth2-provider-arn",
        "oauth2_secret_arn": "/aiml301/lab-03/oauth2-secret-arn",
        "oauth2_config": "/aiml301/lab-03/oauth2-config",
        # Gateway Target (Runtime)
        "gateway_runtime_target": "/aiml301_sre_agentcore/lab-03/gateway-runtime-target",
        "gateway_m2m_target": "/aiml301/lab-03/gateway-m2m-target",
        "m2m_auth_config": "/aiml301/lab-03/m2m-auth-complete-config",
    },
    # Lab 3B: Remediation Agent with Fine-Grained Access Control
    "lab_03b": {
        "interceptor_function_arn": "/aiml301/lab-03b/interceptor-function-arn",
        "gateway_id": "/aiml301/lab-03b/gateway-id",
        "gateway_url": "/aiml301/lab-03b/gateway-url",
    },
    # Lab 4: Prevention Agent (AgentCore Runtime + Gateway with M2M Auth)
    "lab_04": {
        # Runtime Configuration
        "runtime_role_arn": "/aiml301_sre_agentcore/lab-04/runtime-role-arn",
        "runtime_id": "/aiml301_sre_agentcore/lab-04/runtime-id",
        "runtime_arn": "/aiml301_sre_agentcore/lab-04/runtime-arn",
        "runtime_config": "/aiml301_sre_agentcore/lab-04/runtime-config",
        # Gateway Configuration
        "gateway_role_arn": "/aiml301_sre_agentcore/lab-04/gateway-role-arn",
        "gateway_id": "/aiml301_sre_agentcore/lab-04/gateway-id",
        "gateway_config": "/aiml301_sre_agentcore/lab-04/gateway-config",
        # OAuth2 M2M Authentication
        "oauth2_provider_arn": "/aiml301/lab-04/oauth2-provider-arn",
        "oauth2_secret_arn": "/aiml301/lab-04/oauth2-secret-arn",
        "oauth2_config": "/aiml301/lab-04/oauth2-config",
        # Gateway Target (Runtime)
        "gateway_runtime_target": "/aiml301_sre_agentcore/lab-04/gateway-runtime-target",
        "gateway_m2m_target": "/aiml301/lab-04/gateway-m2m-target",
        "m2m_auth_config": "/aiml301/lab-04/m2m-auth-complete-config",
    },
    # Lab 5: Multi-Agent Orchestration (Supervisor Agent)
    "lab_05": {
        # Runtime Configuration
        "runtime_role_arn": "/aiml301_sre_agentcore/lab-05/runtime-role-arn",
        "runtime_id": "/aiml301_sre_agentcore/lab-05/runtime-id",
        "runtime_arn": "/aiml301_sre_agentcore/lab-05/runtime-arn",
        "runtime_config": "/aiml301_sre_agentcore/lab-05/runtime-config",
        # Gateway Configuration
        "gateway_role_arn": "/aiml301_sre_agentcore/lab-05/gateway-role-arn",
        "gateway_id": "/aiml301_sre_agentcore/lab-05/gateway-id",
        "gateway_url": "/aiml301_sre_agentcore/lab-05/gateway-url",
        "gateway_config": "/aiml301_sre_agentcore/lab-05/gateway-config",
        # Gateway Target (Supervisor Runtime)
        "gateway_runtime_target": "/aiml301_sre_agentcore/lab-05/gateway-runtime-target",
    },
    # Lab 6: Custom Interceptor (Optional)
    "lab_06": {
        "interceptor_role_arn": "/aiml301/lab-06/interceptor-role-arn",
    },
    # Lab 7: Memory Integration (Optional)
    "lab_07": {
        "memory_store_arn": "/aiml301/lab-07/memory-store-arn",
    },
}

# Lambda function configuration (constant specs)
LAMBDA_CONFIG = {
    "memory_size": 2048,  # MB (2GB for Strands agent + model inference)
    "timeout": 300,  # seconds (5 minutes for Strands agent reasoning)
    "ephemeral_storage": 512,  # MB (/tmp)
}

# ECR image configuration
ECR_CONFIG = {
    "base_image": "public.ecr.aws/lambda/python:3.12",
    "image_tag": "latest",
}

# IAM policy constants
IAM_POLICIES = {
    "cloudwatch_logs_policy": "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
}

# Bedrock-specific constants
BEDROCK_CONFIG = {
    "invoke_policy_action": "bedrock:InvokeModel",
    # MODEL_ID is maintained in config.py and imported by deployers as needed
}

# S3 configuration for ZIP-based Lambda deployment
S3_CONFIG = {
    "bucket_name": "aiml301-lambda-packages",
    "lambda_packages_prefix": "lambda-packages/",
    "default_object_prefix": "lambda-packages/diagnostic-agent.zip",
}

# Deployment method options
DEPLOYMENT_METHODS = {
    "docker": {
        "description": "Docker image → ECR → Lambda (original approach)",
        "requires": ["docker", "ecr"],
        "vpc_compatible": False,
        "size_limit": "10GB (container limit)",
    },
    "zip_direct": {
        "description": "ZIP file → Direct Lambda upload (<50MB)",
        "requires": ["python", "pip", "aws_cli"],
        "vpc_compatible": True,
        "size_limit": "50 MB (direct upload limit)",
    },
    "zip_s3": {
        "description": "ZIP file → S3 → Lambda (recommended)",
        "requires": ["python", "pip", "aws_cli", "s3"],
        "vpc_compatible": True,
        "size_limit": "250 MB (S3 limit)",
    },
}
