#!/bin/bash

################################################################################
# CloudFormation Stack Cleanup Script
#
# This script deletes all resources created by the Customer Support VPC stack
# EXCEPT the VPC stack itself. The VPC stack is retained for reuse.
################################################################################

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
STACK_NAME="customer-support-vpc"
REGION="us-west-2"
DELETE_VPC=false
DELETE_S3=false

################################################################################
# Helper Functions
################################################################################

print_info() {
    echo -e "${BLUE}ℹ ${NC}$1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

show_help() {
    cat << EOF
CloudFormation Stack Cleanup Script

Usage: $0 [OPTIONS]

Options:
  -s, --stack-name NAME     CloudFormation stack name (default: customer-support-vpc)
  -r, --region REGION       AWS region (default: us-east-1)
  --delete-vpc              Also delete the VPC stack (default: keep VPC)
  --delete-s3               Also delete the S3 bucket with templates (default: keep bucket)
  -h, --help                Show this help message

Examples:
  # Delete all stacks except VPC and S3 bucket
  $0

  # Delete all stacks including VPC
  $0 --delete-vpc

  # Delete everything including S3 bucket
  $0 --delete-vpc --delete-s3

  # Custom stack name and region
  $0 --stack-name my-stack --region us-west-2

Description:
  This script deletes CloudFormation nested stacks in the correct order:
  1. Agent Server Stack
  2. Gateway Stack
  3. MCP Server Stack
  4. DynamoDB Stack
  5. Aurora PostgreSQL Stack
  6. Cognito Stack
  7. VPC Stack (only if --delete-vpc flag is used)

  The VPC stack is kept by default to allow faster redeployment.
  Use --delete-vpc to remove it completely.

EOF
    exit 0
}

################################################################################
# Parse Command Line Arguments
################################################################################

while [[ $# -gt 0 ]]; do
    case $1 in
        -s|--stack-name)
            STACK_NAME="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        --delete-vpc)
            DELETE_VPC=true
            shift
            ;;
        --delete-s3)
            DELETE_S3=true
            shift
            ;;
        -h|--help)
            show_help
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

################################################################################
# Cleanup Functions
################################################################################

wait_for_stack_deletion() {
    local stack_name=$1
    print_info "Waiting for stack deletion to complete: $stack_name"

    aws cloudformation wait stack-delete-complete \
        --stack-name "$stack_name" \
        --region "$REGION" 2>/dev/null || {
        print_warning "Stack deletion wait timed out or failed for: $stack_name"
        return 1
    }

    print_success "Stack deleted: $stack_name"
    return 0
}

delete_nested_stack() {
    local nested_stack_name=$1
    local description=$2

    print_header "Deleting $description"

    # Check if nested stack exists
    if ! aws cloudformation describe-stacks \
        --stack-name "$nested_stack_name" \
        --region "$REGION" &>/dev/null; then
        print_warning "Stack not found, skipping: $nested_stack_name"
        return 0
    fi

    print_info "Deleting stack: $nested_stack_name"

    aws cloudformation delete-stack \
        --stack-name "$nested_stack_name" \
        --region "$REGION"

    wait_for_stack_deletion "$nested_stack_name"
}

empty_s3_bucket() {
    local bucket_name=$1

    print_info "Emptying S3 bucket: $bucket_name"

    # Check if bucket exists
    if ! aws s3 ls "s3://$bucket_name" --region "$REGION" &>/dev/null; then
        print_warning "Bucket not found: $bucket_name"
        return 0
    fi

    # Delete all object versions
    print_info "Deleting object versions..."
    aws s3api list-object-versions \
        --bucket "$bucket_name" \
        --region "$REGION" \
        --query 'Versions[].{Key:Key,VersionId:VersionId}' \
        --output json 2>/dev/null | \
    jq -r '.[] | "--key \"\(.Key)\" --version-id \(.VersionId)"' | \
    while read -r args; do
        eval aws s3api delete-object --bucket "$bucket_name" --region "$REGION" $args 2>/dev/null || true
    done

    # Delete all delete markers
    print_info "Deleting delete markers..."
    aws s3api list-object-versions \
        --bucket "$bucket_name" \
        --region "$REGION" \
        --query 'DeleteMarkers[].{Key:Key,VersionId:VersionId}' \
        --output json 2>/dev/null | \
    jq -r '.[] | "--key \"\(.Key)\" --version-id \(.VersionId)"' | \
    while read -r args; do
        eval aws s3api delete-object --bucket "$bucket_name" --region "$REGION" $args 2>/dev/null || true
    done

    # Delete any remaining current objects
    print_info "Deleting remaining objects..."
    aws s3 rm "s3://$bucket_name" --recursive --region "$REGION" 2>/dev/null || true

    print_success "Bucket emptied: $bucket_name"
}

delete_s3_bucket() {
    local bucket_name=$1

    print_header "Deleting S3 Bucket"

    empty_s3_bucket "$bucket_name"

    print_info "Deleting bucket: $bucket_name"
    aws s3api delete-bucket \
        --bucket "$bucket_name" \
        --region "$REGION" 2>/dev/null || {
        print_warning "Failed to delete bucket: $bucket_name"
        return 1
    }

    print_success "Bucket deleted: $bucket_name"
}

get_nested_stack_id() {
    local parent_stack=$1
    local logical_id=$2

    aws cloudformation describe-stack-resources \
        --stack-name "$parent_stack" \
        --logical-resource-id "$logical_id" \
        --region "$REGION" \
        --query 'StackResources[0].PhysicalResourceId' \
        --output text 2>/dev/null || echo ""
}

get_template_bucket() {
    local stack_name=$1

    # Get TemplateBaseURL parameter from stack
    local template_url=$(aws cloudformation describe-stacks \
        --stack-name "$stack_name" \
        --region "$REGION" \
        --query 'Stacks[0].Parameters[?ParameterKey==`TemplateBaseURL`].ParameterValue' \
        --output text 2>/dev/null || echo "")

    if [[ -n "$template_url" ]]; then
        # Extract bucket name from S3 URL (supports multiple formats)
        # Format 1: https://bucket-name.s3.region.amazonaws.com/path
        # Format 2: https://s3.region.amazonaws.com/bucket-name/path
        local bucket=""

        # Try format 1: bucket-name.s3.region.amazonaws.com
        bucket=$(echo "$template_url" | sed -n 's|^https://\([^.]*\)\.s3\..*|\1|p')

        # If empty, try format 2: s3.region.amazonaws.com/bucket-name
        if [[ -z "$bucket" ]]; then
            bucket=$(echo "$template_url" | sed -n 's|^https://s3[^/]*/\([^/]*\).*|\1|p')
        fi

        echo "$bucket"
    else
        echo ""
    fi
}

################################################################################
# Main Cleanup Process
################################################################################

print_header "Customer Support VPC Stack Cleanup"

print_info "Stack Name: $STACK_NAME"
print_info "Region: $REGION"
print_info "Delete VPC: $DELETE_VPC"
print_info "Delete S3: $DELETE_S3"

# Confirm deletion
echo ""
read -p "Are you sure you want to delete these resources? (yes/no): " confirm
if [[ "$confirm" != "yes" ]]; then
    print_warning "Cleanup cancelled"
    exit 0
fi

# Check if master stack exists
if ! aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" &>/dev/null; then
    print_error "Master stack not found: $STACK_NAME"
    exit 1
fi

# Get S3 bucket name before deleting stacks
TEMPLATE_BUCKET=$(get_template_bucket "$STACK_NAME")

if [[ -n "$TEMPLATE_BUCKET" && "$TEMPLATE_BUCKET" != "None" ]]; then
    print_info "Found template bucket: $TEMPLATE_BUCKET"
else
    print_warning "No template bucket found in stack parameters"
fi

# Delete nested stacks in reverse dependency order

# 1. Delete Agent Server Stack (depends on everything)
AGENT_STACK=$(get_nested_stack_id "$STACK_NAME" "AgentServerStack")
if [[ -n "$AGENT_STACK" && "$AGENT_STACK" != "None" ]]; then
    delete_nested_stack "$AGENT_STACK" "Agent Server Stack"
fi

# 2. Delete Gateway Stack (depends on VPC, Aurora, DynamoDB, Cognito)
GATEWAY_STACK=$(get_nested_stack_id "$STACK_NAME" "GatewayStack")
if [[ -n "$GATEWAY_STACK" && "$GATEWAY_STACK" != "None" ]]; then
    delete_nested_stack "$GATEWAY_STACK" "Gateway Stack"
fi

# 3. Delete MCP Server Stack (depends on VPC, Cognito, DynamoDB)
MCP_STACK=$(get_nested_stack_id "$STACK_NAME" "MCPServerStack")
if [[ -n "$MCP_STACK" && "$MCP_STACK" != "None" ]]; then
    delete_nested_stack "$MCP_STACK" "MCP Server Stack"
fi

# 4. Delete DynamoDB Stack (depends on VPC)
DYNAMODB_STACK=$(get_nested_stack_id "$STACK_NAME" "DynamoDBStack")
if [[ -n "$DYNAMODB_STACK" && "$DYNAMODB_STACK" != "None" ]]; then
    delete_nested_stack "$DYNAMODB_STACK" "DynamoDB Stack"
fi

# 5. Delete Aurora Stack (depends on VPC)
AURORA_STACK=$(get_nested_stack_id "$STACK_NAME" "AuroraStack")
if [[ -n "$AURORA_STACK" && "$AURORA_STACK" != "None" ]]; then
    delete_nested_stack "$AURORA_STACK" "Aurora PostgreSQL Stack"
fi

# 6. Delete Cognito Stack (no dependencies)
COGNITO_STACK=$(get_nested_stack_id "$STACK_NAME" "CognitoStack")
if [[ -n "$COGNITO_STACK" && "$COGNITO_STACK" != "None" ]]; then
    delete_nested_stack "$COGNITO_STACK" "Cognito Stack"
fi

# 7. Optionally delete VPC Stack
if [[ "$DELETE_VPC" == true ]]; then
    VPC_STACK=$(get_nested_stack_id "$STACK_NAME" "VPCStack")
    if [[ -n "$VPC_STACK" && "$VPC_STACK" != "None" ]]; then
        delete_nested_stack "$VPC_STACK" "VPC Stack"
    fi

    # Delete master stack if VPC is also deleted
    print_header "Deleting Master Stack"
    print_info "Deleting master stack: $STACK_NAME"
    aws cloudformation delete-stack \
        --stack-name "$STACK_NAME" \
        --region "$REGION"
    wait_for_stack_deletion "$STACK_NAME"
else
    print_warning "VPC stack retained. Use --delete-vpc to remove it."
    print_info "Master stack retained (contains VPC): $STACK_NAME"
fi

# Optionally delete S3 bucket
if [[ "$DELETE_S3" == true && -n "$TEMPLATE_BUCKET" ]]; then
    delete_s3_bucket "$TEMPLATE_BUCKET"
else
    if [[ -n "$TEMPLATE_BUCKET" ]]; then
        print_warning "S3 bucket retained: $TEMPLATE_BUCKET"
        print_info "Use --delete-s3 to remove the S3 bucket"
    fi
fi

################################################################################
# Completion
################################################################################

print_header "Cleanup Complete"

if [[ "$DELETE_VPC" == true ]]; then
    print_success "All stacks deleted successfully"
else
    print_success "All stacks deleted except VPC stack"
    print_info "VPC stack retained for reuse: $STACK_NAME"
    print_info "To delete VPC, run: $0 --delete-vpc"
fi

if [[ "$DELETE_S3" == true ]]; then
    print_success "S3 bucket deleted"
else
    if [[ -n "$TEMPLATE_BUCKET" ]]; then
        print_info "S3 bucket retained: $TEMPLATE_BUCKET"
        print_info "To delete bucket, run: $0 --delete-s3"
    fi
fi

echo ""
print_info "Cleanup completed successfully!"
