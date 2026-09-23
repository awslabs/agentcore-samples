#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# deploy_all.sh - Automated deployment for Identity Multi-Agent Financial Assistant
#
# This script deploys all components:
#   1. ECR repositories for container images
#   2. Container images (build and push)
#   3. AgentCore runtimes (3 agents: coordinator, profile, accounts)
#   4. Auth0/Okta JWT authorizer configuration
#
# Usage:
#   ./deploy_all.sh
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate permissions
#   - Docker installed and running
#   - Auth0 tenant configured (see docs/auth0_configuration.md)
#   - Environment variables set (see .env.example)

set -e

echo "=============================================="
echo "Identity Multi-Agent Financial Assistant"
echo "Automated Deployment Script"
echo "=============================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load environment variables
if [ -f ".env" ]; then
    echo "Loading configuration from .env..."
    set -a
    source .env
    set +a
else
    echo "ERROR: .env file not found"
    echo "Please copy .env.example to .env and configure your settings"
    exit 1
fi

# Validate required variables
REQUIRED_VARS=(
    "AWS_REGION"
    "AUTH0_DOMAIN"
    "AUTH0_CLIENT_ID"
    "AUTH0_AUDIENCE"
)

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo "ERROR: Required environment variable $var is not set"
        exit 1
    fi
done

# Get AWS Account ID
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query 'Account' --output text 2>/dev/null)}"
if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo "ERROR: Could not determine AWS Account ID"
    echo "Please set AWS_ACCOUNT_ID or ensure AWS credentials are configured"
    exit 1
fi

echo "Configuration:"
echo "  AWS Account: $AWS_ACCOUNT_ID"
echo "  AWS Region: $AWS_REGION"
echo "  Auth0 Domain: $AUTH0_DOMAIN"
echo ""

# Agent definitions (v0.6: streamlined to 3 agents)
# Using parallel arrays for bash 3.2 compatibility
AGENT_NAMES=(coordinator customer_profile accounts)
AGENT_REPOS=(coordinator-agent customer-profile-agent accounts-agent)

VERSION_TAG="v$(date +%Y%m%d%H%M%S)"
echo "Deployment version: $VERSION_TAG"
echo ""

# ============================================
# Phase 1: Create ECR Repositories
# ============================================
echo "Phase 1: Creating ECR repositories..."
echo ""

for i in "${!AGENT_NAMES[@]}"; do
    agent_name="${AGENT_NAMES[$i]}"
    repo_name="${AGENT_REPOS[$i]}"

    # Check if repo exists
    if aws ecr describe-repositories --repository-names "$repo_name" --region "$AWS_REGION" > /dev/null 2>&1; then
        echo "  [EXISTS] $repo_name"
    else
        echo "  [CREATE] $repo_name"
        aws ecr create-repository \
            --repository-name "$repo_name" \
            --region "$AWS_REGION" \
            --image-scanning-configuration scanOnPush=true \
            --tags Key=Project,Value=identity-multi-agent Key=ManagedBy,Value=deploy_all.sh \
            > /dev/null 2>&1
    fi
done

echo ""

# ============================================
# Phase 2: Build and Push Container Images
# ============================================
echo "Phase 2: Building and pushing container images..."
echo ""

# Login to ECR
echo "Logging into ECR..."
aws ecr get-login-password --region "$AWS_REGION" | \
    docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com" 2>/dev/null

for i in "${!AGENT_NAMES[@]}"; do
    agent_name="${AGENT_NAMES[$i]}"
    repo_name="${AGENT_REPOS[$i]}"
    agent_dir="agents/$agent_name"

    if [ ! -d "$agent_dir" ]; then
        echo "  [SKIP] $agent_name - directory not found"
        continue
    fi

    echo "  Building $agent_name..."

    # Resolve agent runtime ID if this agent already exists
    runtime_name="${agent_name}_agent"
    agent_runtime_id=$(aws bedrock-agentcore-control list-agent-runtimes \
        --region "$AWS_REGION" \
        --query "agentRuntimes[?agentRuntimeName=='$runtime_name'].agentRuntimeId" \
        --output text 2>/dev/null)
    # Fall back to the agent name if no runtime ID found yet
    agent_runtime_id="${agent_runtime_id:-$runtime_name}"

    # Determine build context and dockerfile based on agent
    if [ "$agent_name" = "coordinator" ]; then
        build_context="."
        dockerfile_flag="-f agents/coordinator/Dockerfile"
    else
        build_context="$agent_dir"
        dockerfile_flag=""
    fi

    # Build image with ARM64 for Graviton
    docker build \
        --platform linux/arm64 \
        --build-arg AWS_ACCOUNT_ID="$AWS_ACCOUNT_ID" \
        --build-arg AGENT_RUNTIME_ID="$agent_runtime_id" \
        $dockerfile_flag \
        -t "$repo_name:$VERSION_TAG" \
        -t "$repo_name:latest" \
        "$build_context" \
        > /dev/null 2>&1

    # Tag for ECR
    docker tag "$repo_name:$VERSION_TAG" "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${repo_name}:${VERSION_TAG}"
    docker tag "$repo_name:latest" "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${repo_name}:latest"

    # Push to ECR
    docker push "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${repo_name}:${VERSION_TAG}" > /dev/null 2>&1
    docker push "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${repo_name}:latest" > /dev/null 2>&1

    echo "  [PUSHED] $repo_name:$VERSION_TAG"
done

echo ""

# ============================================
# Phase 3: Create/Update IAM Roles
# ============================================
echo "Phase 3: Setting up IAM roles..."
echo ""

TRUST_POLICY='{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "bedrock-agentcore.amazonaws.com"
            },
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {
                    "aws:SourceAccount": "'"$AWS_ACCOUNT_ID"'"
                }
            }
        }
    ]
}'

EXECUTION_POLICY='{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:'"$AWS_REGION"':'"$AWS_ACCOUNT_ID"':log-group:/aws/bedrock-agentcore/*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream"
            ],
            "Resource": "arn:aws:bedrock:'"$AWS_REGION"'::foundation-model/*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:BatchCheckLayerAvailability"
            ],
            "Resource": "arn:aws:ecr:'"$AWS_REGION"':'"$AWS_ACCOUNT_ID"':repository/*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "secretsmanager:GetSecretValue"
            ],
            "Resource": "arn:aws:secretsmanager:'"$AWS_REGION"':'"$AWS_ACCOUNT_ID"':secret:agentcore/*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
                "xray:GetSamplingRules",
                "xray:GetSamplingTargets"
            ],
            "Resource": "*"
        }
    ]
}'

for i in "${!AGENT_NAMES[@]}"; do
    agent_name="${AGENT_NAMES[$i]}"
    role_name="AgentCore-${agent_name}-ExecutionRole"

    # Check if role exists
    if aws iam get-role --role-name "$role_name" > /dev/null 2>&1; then
        echo "  [EXISTS] $role_name"
    else
        echo "  [CREATE] $role_name"

        # Create role
        aws iam create-role \
            --role-name "$role_name" \
            --assume-role-policy-document "$TRUST_POLICY" \
            --tags Key=Project,Value=identity-multi-agent Key=ManagedBy,Value=deploy_all.sh \
            > /dev/null 2>&1

        # Create and attach policy
        policy_name="AgentCore-${agent_name}-ExecutionPolicy"
        policy_arn=$(aws iam create-policy \
            --policy-name "$policy_name" \
            --policy-document "$EXECUTION_POLICY" \
            --query 'Policy.Arn' \
            --output text 2>/dev/null) || true

        if [ -n "$policy_arn" ]; then
            aws iam attach-role-policy \
                --role-name "$role_name" \
                --policy-arn "$policy_arn" \
                > /dev/null 2>&1
        fi

        # Wait for role propagation
        sleep 10
    fi
done

echo ""

# ============================================
# Phase 4: Deploy AgentCore Runtimes
# ============================================
echo "Phase 4: Deploying AgentCore runtimes..."
echo ""

AUTH0_DISCOVERY_URL="https://${AUTH0_DOMAIN}/.well-known/openid-configuration"

# Store agent IDs in parallel array
declare -a AGENT_IDS

for i in "${!AGENT_NAMES[@]}"; do
    agent_name="${AGENT_NAMES[$i]}"
    repo_name="${AGENT_REPOS[$i]}"
    role_name="AgentCore-${agent_name}-ExecutionRole"
    runtime_name="${agent_name}_agent"

    container_uri="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${repo_name}:${VERSION_TAG}"
    role_arn="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${role_name}"

    echo "  Deploying $runtime_name..."

    # Check if runtime exists
    existing_id=$(aws bedrock-agentcore-control list-agent-runtimes \
        --region "$AWS_REGION" \
        --query "agentRuntimes[?agentRuntimeName=='$runtime_name'].agentRuntimeId" \
        --output text 2>/dev/null)

    # Build JSON parameters for complex nested structures
    authorizer_json=$(cat <<AUTHEOF
{"customJWTAuthorizer":{"discoveryUrl":"$AUTH0_DISCOVERY_URL","allowedAudience":["$AUTH0_AUDIENCE"],"allowedClients":["$AUTH0_CLIENT_ID"]}}
AUTHEOF
)

    # Build environment variables JSON for this agent
    env_vars_flag=""
    if [ "$agent_name" = "coordinator" ]; then
        # Coordinator needs sub-agent IDs as environment variables
        # These will be resolved after all agents are created (Phase 4b)
        # For now, use any previously known IDs from the environment
        coord_env_vars="{}"
        if [ -n "$PROFILE_AGENT_ID" ] && [ -n "$ACCOUNTS_AGENT_ID" ]; then
            coord_env_vars='{"PROFILE_AGENT_ID":"'"$PROFILE_AGENT_ID"'","ACCOUNTS_AGENT_ID":"'"$ACCOUNTS_AGENT_ID"'"}'
        fi
        env_vars_flag="--environment-variables $coord_env_vars"
    fi

    if [ -n "$existing_id" ] && [ "$existing_id" != "None" ]; then
        echo "    Updating existing runtime: $existing_id"

        aws bedrock-agentcore-control update-agent-runtime \
            --agent-runtime-id "$existing_id" \
            --agent-runtime-artifact '{"containerConfiguration":{"containerUri":"'"$container_uri"'"}}' \
            --role-arn "$role_arn" \
            --network-configuration '{"networkMode":"PUBLIC"}' \
            --authorizer-configuration "$authorizer_json" \
            --request-header-configuration '{"requestHeaderAllowlist":["Authorization"]}' \
            $env_vars_flag \
            --region "$AWS_REGION"

        AGENT_IDS[$i]="$existing_id"
    else
        echo "    Creating new runtime..."

        new_id=$(aws bedrock-agentcore-control create-agent-runtime \
            --agent-runtime-name "$runtime_name" \
            --agent-runtime-artifact '{"containerConfiguration":{"containerUri":"'"$container_uri"'"}}' \
            --role-arn "$role_arn" \
            --network-configuration '{"networkMode":"PUBLIC"}' \
            --authorizer-configuration "$authorizer_json" \
            --request-header-configuration '{"requestHeaderAllowlist":["Authorization"]}' \
            $env_vars_flag \
            --region "$AWS_REGION" \
            --query 'agentRuntimeId' \
            --output text)

        AGENT_IDS[$i]="$new_id"
    fi

    echo "    Agent ID: ${AGENT_IDS[$i]}"
done

echo ""

# ============================================
# Phase 5: Wait for agents to be READY
# ============================================
echo "Phase 5: Waiting for agents to be ready..."
echo ""

wait_for_agent() {
    local agent_id=$1
    local max_wait=180
    local waited=0

    while [ $waited -lt $max_wait ]; do
        status=$(aws bedrock-agentcore-control get-agent-runtime \
            --agent-runtime-id "$agent_id" \
            --region "$AWS_REGION" \
            --query 'status' \
            --output text 2>/dev/null)

        if [ "$status" = "READY" ]; then
            return 0
        elif [ "$status" = "FAILED" ]; then
            return 1
        fi

        sleep 10
        waited=$((waited + 10))
    done

    return 1
}

all_ready=true
for i in "${!AGENT_NAMES[@]}"; do
    agent_name="${AGENT_NAMES[$i]}"
    agent_id="${AGENT_IDS[$i]}"
    echo -n "  $agent_name: "

    if wait_for_agent "$agent_id"; then
        echo "READY"
    else
        echo "FAILED"
        all_ready=false
    fi
done

echo ""

# ============================================
# Phase 6: Generate .env updates
# ============================================
echo "Phase 6: Generating environment configuration..."
echo ""

echo "Add the following to your .env file:"
echo ""
echo "# Agent Runtime IDs (generated by deploy_all.sh)"
for i in "${!AGENT_NAMES[@]}"; do
    agent_name="${AGENT_NAMES[$i]}"
    agent_id="${AGENT_IDS[$i]}"
    var_name=$(echo "${agent_name}_AGENT_ID" | tr '[:lower:]' '[:upper:]')
    echo "${var_name}=${agent_id}"
done

echo ""

# ============================================
# Summary
# ============================================
echo "=============================================="
if $all_ready; then
    echo "DEPLOYMENT SUCCESSFUL"
    echo ""
    echo "All 3 agents deployed and ready:"
    for i in "${!AGENT_NAMES[@]}"; do
        agent_name="${AGENT_NAMES[$i]}"
        agent_id="${AGENT_IDS[$i]}"
        echo "  - $agent_name: $agent_id"
    done
    echo ""
    echo "Next steps:"
    echo "  1. Update .env with the agent IDs shown above"
    echo "  2. Run: cd client/streamlit_app && streamlit run app.py"
    echo "  3. Open http://localhost:8501 and login with Auth0"
    echo ""
    echo "Note: Old agents (transactions, cards, loans) should be manually deleted:"
    echo "  aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id <id> --region $AWS_REGION"
else
    echo "DEPLOYMENT INCOMPLETE"
    echo ""
    echo "Some agents failed to reach READY state."
    echo "Check CloudWatch logs for details:"
    echo "  aws logs tail /aws/bedrock-agentcore/runtimes/<agent-id>-DEFAULT --follow"
fi
echo "=============================================="
