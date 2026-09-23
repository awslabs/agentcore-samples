#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# cleanup_all.sh - Automated cleanup for Identity Multi-Agent Financial Assistant
#
# This script removes all deployed components:
#   1. AgentCore runtimes (3 agents)
#   2. ECR repositories and images
#   3. IAM roles and policies (optional)
#   4. CloudWatch log groups (optional)
#
# Usage:
#   ./cleanup_all.sh              # Cleanup with confirmation prompts
#   ./cleanup_all.sh --force      # Skip confirmation prompts
#   ./cleanup_all.sh --dry-run    # Show what would be deleted
#   ./cleanup_all.sh --skip-iam   # Don't delete IAM roles
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate permissions
#   - AWS_PROFILE set in environment or .env file

set -e

echo "=============================================="
echo "Identity Multi-Agent Financial Assistant"
echo "Automated Cleanup Script"
echo "=============================================="
echo ""

# Parse arguments
DRY_RUN=false
FORCE=false
SKIP_IAM=false

for arg in "$@"; do
    case $arg in
        --dry-run)
            DRY_RUN=true
            echo "DRY RUN MODE - No resources will be deleted"
            echo ""
            ;;
        --force)
            FORCE=true
            ;;
        --skip-iam)
            SKIP_IAM=true
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load environment variables
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query 'Account' --output text 2>/dev/null)}"

if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo "ERROR: Could not determine AWS Account ID"
    exit 1
fi

echo "Configuration:"
echo "  AWS Account: $AWS_ACCOUNT_ID"
echo "  AWS Region: $AWS_REGION"
echo ""

# Agent definitions (v0.6: streamlined to 3 agents)
# Using parallel arrays for bash 3.2 compatibility (matches deploy_all.sh)
AGENT_NAMES=(coordinator customer_profile accounts)
AGENT_REPOS=(coordinator-agent customer-profile-agent accounts-agent)
AGENT_COUNT=${#AGENT_NAMES[@]}

# Confirmation prompt
if [ "$FORCE" = false ] && [ "$DRY_RUN" = false ]; then
    echo "WARNING: This will delete all deployed resources including:"
    echo "  - $AGENT_COUNT AgentCore runtimes"
    echo "  - $AGENT_COUNT ECR repositories (including all images)"
    if [ "$SKIP_IAM" = false ]; then
        echo "  - $AGENT_COUNT IAM roles and policies"
    fi
    echo ""
    read -p "Are you sure you want to continue? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Cleanup cancelled."
        exit 0
    fi
    echo ""
fi

# ============================================
# Phase 1: Delete AgentCore Runtimes
# ============================================
echo "Phase 1: Deleting AgentCore runtimes..."
echo ""

for i in "${!AGENT_NAMES[@]}"; do
    agent_name="${AGENT_NAMES[$i]}"
    runtime_name="${agent_name}_agent"

    # Find runtime ID
    agent_id=$(aws bedrock-agentcore-control list-agent-runtimes \
        --region "$AWS_REGION" \
        --query "agentRuntimeSummaries[?agentRuntimeName=='$runtime_name'].agentRuntimeId" \
        --output text 2>/dev/null)

    if [ -n "$agent_id" ] && [ "$agent_id" != "None" ]; then
        if [ "$DRY_RUN" = true ]; then
            echo "  [WOULD DELETE] AgentCore runtime: $runtime_name ($agent_id)"
        else
            echo "  [DELETE] AgentCore runtime: $runtime_name ($agent_id)"
            aws bedrock-agentcore-control delete-agent-runtime \
                --agent-runtime-id "$agent_id" \
                --region "$AWS_REGION" \
                > /dev/null 2>&1 || echo "    Warning: Delete may have failed"
        fi
    else
        echo "  [NOT FOUND] $runtime_name"
    fi
done

echo ""

# Wait for runtimes to be deleted
if [ "$DRY_RUN" = false ]; then
    echo "Waiting for runtimes to be deleted..."
    sleep 30
    echo ""
fi

# ============================================
# Phase 2: Delete ECR Repositories
# ============================================
echo "Phase 2: Deleting ECR repositories..."
echo ""

for i in "${!AGENT_NAMES[@]}"; do
    repo_name="${AGENT_REPOS[$i]}"

    # Check if repo exists
    if aws ecr describe-repositories --repository-names "$repo_name" --region "$AWS_REGION" > /dev/null 2>&1; then
        if [ "$DRY_RUN" = true ]; then
            echo "  [WOULD DELETE] ECR repository: $repo_name"
        else
            echo "  [DELETE] ECR repository: $repo_name"
            aws ecr delete-repository \
                --repository-name "$repo_name" \
                --force \
                --region "$AWS_REGION" \
                > /dev/null 2>&1 || echo "    Warning: Delete may have failed"
        fi
    else
        echo "  [NOT FOUND] $repo_name"
    fi
done

echo ""

# ============================================
# Phase 3: Delete IAM Roles (optional)
# ============================================
if [ "$SKIP_IAM" = false ]; then
    echo "Phase 3: Deleting IAM roles and policies..."
    echo ""

    for i in "${!AGENT_NAMES[@]}"; do
        agent_name="${AGENT_NAMES[$i]}"
        role_name="AgentCore-${agent_name}-ExecutionRole"
        policy_name="AgentCore-${agent_name}-ExecutionPolicy"

        # Check if role exists
        if aws iam get-role --role-name "$role_name" > /dev/null 2>&1; then
            if [ "$DRY_RUN" = true ]; then
                echo "  [WOULD DELETE] IAM role: $role_name"
            else
                echo "  [DELETE] IAM role: $role_name"

                # Detach all policies
                attached_policies=$(aws iam list-attached-role-policies \
                    --role-name "$role_name" \
                    --query 'AttachedPolicies[].PolicyArn' \
                    --output text 2>/dev/null)

                for policy_arn in $attached_policies; do
                    aws iam detach-role-policy \
                        --role-name "$role_name" \
                        --policy-arn "$policy_arn" \
                        > /dev/null 2>&1 || true
                done

                # Delete role
                aws iam delete-role \
                    --role-name "$role_name" \
                    > /dev/null 2>&1 || echo "    Warning: Role delete may have failed"

                # Try to delete the policy
                policy_arn="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${policy_name}"
                aws iam delete-policy \
                    --policy-arn "$policy_arn" \
                    > /dev/null 2>&1 || true
            fi
        else
            echo "  [NOT FOUND] $role_name"
        fi
    done

    echo ""
else
    echo "Phase 3: Skipping IAM cleanup (--skip-iam flag set)"
    echo ""
fi

# ============================================
# Phase 4: Delete CloudWatch Log Groups (optional)
# ============================================
echo "Phase 4: Cleaning up CloudWatch log groups..."
echo ""

for i in "${!AGENT_NAMES[@]}"; do
    agent_name="${AGENT_NAMES[$i]}"
    runtime_name="${agent_name}_agent"

    # Find and delete log groups matching the pattern
    log_groups=$(aws logs describe-log-groups \
        --log-group-name-prefix "/aws/bedrock-agentcore" \
        --region "$AWS_REGION" \
        --query "logGroups[?contains(logGroupName, '${runtime_name}')].logGroupName" \
        --output text 2>/dev/null)

    for log_group in $log_groups; do
        if [ "$DRY_RUN" = true ]; then
            echo "  [WOULD DELETE] Log group: $log_group"
        else
            echo "  [DELETE] Log group: $log_group"
            aws logs delete-log-group \
                --log-group-name "$log_group" \
                --region "$AWS_REGION" \
                > /dev/null 2>&1 || echo "    Warning: Delete may have failed"
        fi
    done
done

echo ""

# ============================================
# Summary
# ============================================
echo "=============================================="
if [ "$DRY_RUN" = true ]; then
    echo "DRY RUN COMPLETE"
    echo ""
    echo "Run without --dry-run to actually delete resources"
else
    echo "CLEANUP COMPLETE"
    echo ""
    echo "Deleted resources:"
    echo "  - AgentCore runtimes: $AGENT_COUNT"
    echo "  - ECR repositories: $AGENT_COUNT"
    if [ "$SKIP_IAM" = false ]; then
        echo "  - IAM roles/policies: $AGENT_COUNT"
    fi
    echo "  - CloudWatch log groups: cleaned"
    echo ""
    echo "Note: Some resources may take a few minutes to fully delete."
fi
echo "=============================================="
