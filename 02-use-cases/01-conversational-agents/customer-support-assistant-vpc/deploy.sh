#!/bin/bash

################################################################################
# CloudFormation Stack Deployment Script
#
# This script automates the deployment of the Customer Support VPC stack
# by uploading templates to S3 and creating the CloudFormation stack.
################################################################################

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
STACK_NAME_BASE="customer-support-vpc"
ENVIRONMENT="dev"
DB_USERNAME="postgres"
MODEL_ID="global.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION="us-west-2"
ADMIN_EMAIL=""
ADMIN_PASSWORD=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFN_DIR="${SCRIPT_DIR}/cloudformation"

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

check_prerequisites() {
    print_header "Checking Prerequisites"

    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        print_error "AWS CLI is not installed. Please install it first."
        echo "Visit: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
        exit 1
    fi
    print_success "AWS CLI found: $(aws --version | cut -d' ' -f1)"

    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        print_error "AWS credentials not configured. Run 'aws configure' first."
        exit 1
    fi

    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    print_success "AWS Account ID: ${AWS_ACCOUNT_ID}"

    # Check if CloudFormation directory exists
    if [ ! -d "$CFN_DIR" ]; then
        print_error "CloudFormation directory not found: $CFN_DIR"
        exit 1
    fi
    print_success "CloudFormation templates found"
}

create_s3_bucket() {
    print_header "Creating S3 Bucket for Templates"

    local bucket_name="$1"

    # Check if bucket exists
    if aws s3 ls "s3://${bucket_name}" 2>/dev/null; then
        print_warning "S3 bucket already exists: ${bucket_name}"
        return 0
    fi

    print_info "Creating S3 bucket: ${bucket_name}"

    # Create bucket (handle us-east-1 special case)
    if [ "$REGION" = "us-east-1" ]; then
        aws s3api create-bucket \
            --bucket "$bucket_name" \
            --region "$REGION"
    else
        aws s3api create-bucket \
            --bucket "$bucket_name" \
            --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION"
    fi

    print_success "S3 bucket created: ${bucket_name}"

    # Enable versioning
    print_info "Enabling bucket versioning..."
    aws s3api put-bucket-versioning \
        --bucket "$bucket_name" \
        --versioning-configuration Status=Enabled

    print_success "Bucket versioning enabled"

    # Enable encryption
    print_info "Enabling default encryption..."
    aws s3api put-bucket-encryption \
        --bucket "$bucket_name" \
        --server-side-encryption-configuration '{
            "Rules": [{
                "ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": "AES256"
                }
            }]
        }'

    print_success "Default encryption enabled"
}

upload_templates() {
    print_header "Uploading CloudFormation Templates to S3"

    local bucket_name="$1"

    # Upload nested stack templates
    print_info "Uploading nested stack templates..."
    aws s3 cp "$CFN_DIR/" "s3://${bucket_name}/cloudformation/" \
        --recursive \
        --exclude "customer-support-stack.yaml" \
        --region "$REGION"

    # Upload master stack
    print_info "Uploading master stack template..."
    aws s3 cp "$CFN_DIR/customer-support-stack.yaml" \
        "s3://${bucket_name}/" \
        --region "$REGION"

    print_success "All templates uploaded successfully"

    # List uploaded files
    print_info "Uploaded files:"
    aws s3 ls "s3://${bucket_name}/cloudformation/" --recursive
}

validate_template() {
    print_header "Validating CloudFormation Template"

    local bucket_name="$1"
    local template_url="https://${bucket_name}.s3.${REGION}.amazonaws.com/customer-support-stack.yaml"

    print_info "Validating template: ${template_url}"

    if aws cloudformation validate-template \
        --template-url "$template_url" \
        --region "$REGION" &> /dev/null; then
        print_success "Template validation successful"
        return 0
    else
        print_error "Template validation failed"
        return 1
    fi
}

deploy_stack() {
    print_header "Deploying CloudFormation Stack"

    local bucket_name="$1"
    local template_url="https://${bucket_name}.s3.${REGION}.amazonaws.com/customer-support-stack.yaml"
    local base_url="https://${bucket_name}.s3.${REGION}.amazonaws.com/cloudformation"

    print_info "Stack Name: ${STACK_NAME}"
    print_info "Region: ${REGION}"
    print_info "Environment: ${ENVIRONMENT}"
    print_info "Model ID: ${MODEL_ID}"
    print_info "Admin Email: ${ADMIN_EMAIL}"
    print_info "Template URL: ${template_url}"

    # Validate admin email and password
    if [ -z "$ADMIN_EMAIL" ]; then
        print_error "Admin email is required. Use --email option."
        exit 1
    fi

    if [ -z "$ADMIN_PASSWORD" ]; then
        print_error "Admin password is required. Use --password option."
        exit 1
    fi

    # Check if stack already exists
    if aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" &> /dev/null; then

        print_warning "Stack already exists: ${STACK_NAME}"
        read -p "Do you want to update the stack? (y/n): " -n 1 -r
        echo

        if [[ $REPLY =~ ^[Yy]$ ]]; then
            print_info "Updating stack..."
            aws cloudformation update-stack \
                --stack-name "$STACK_NAME" \
                --template-url "$template_url" \
                --parameters \
                    ParameterKey=TemplateBaseURL,ParameterValue="$base_url" \
                    ParameterKey=Environment,ParameterValue="$ENVIRONMENT" \
                    ParameterKey=DBMasterUsername,ParameterValue="$DB_USERNAME" \
                    ParameterKey=ModelID,ParameterValue="$MODEL_ID" \
                    ParameterKey=AdminUserEmail,ParameterValue="$ADMIN_EMAIL" \
                    ParameterKey=AdminUserPassword,ParameterValue="$ADMIN_PASSWORD" \
                --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
                --region "$REGION"

            print_info "Waiting for stack update to complete..."
            aws cloudformation wait stack-update-complete \
                --stack-name "$STACK_NAME" \
                --region "$REGION"

            print_success "Stack updated successfully!"
        else
            print_info "Update cancelled"
            exit 0
        fi
    else
        print_info "Creating new stack..."
        aws cloudformation create-stack \
            --stack-name "$STACK_NAME" \
            --template-url "$template_url" \
            --parameters \
                ParameterKey=TemplateBaseURL,ParameterValue="$base_url" \
                ParameterKey=Environment,ParameterValue="$ENVIRONMENT" \
                ParameterKey=DBMasterUsername,ParameterValue="$DB_USERNAME" \
                ParameterKey=ModelID,ParameterValue="$MODEL_ID" \
                ParameterKey=AdminUserEmail,ParameterValue="$ADMIN_EMAIL" \
                ParameterKey=AdminUserPassword,ParameterValue="$ADMIN_PASSWORD" \
            --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
            --region "$REGION" \
            --tags \
                Key=Project,Value=CustomerSupportVPC \
                Key=Environment,Value="$ENVIRONMENT" \
                Key=ManagedBy,Value=CloudFormation

        print_success "Stack creation initiated"
        print_info "Waiting for stack creation to complete (this may take 30-45 minutes)..."

        aws cloudformation wait stack-create-complete \
            --stack-name "$STACK_NAME" \
            --region "$REGION"

        print_success "Stack created successfully!"
    fi
}

get_stack_outputs() {
    print_header "Stack Outputs"

    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
        --output table
}

print_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Deploy Customer Support VPC CloudFormation Stack

OPTIONS:
    -b, --bucket BUCKET_NAME    S3 bucket name for templates (optional, auto-generated if not provided)
    -s, --stack STACK_NAME      CloudFormation stack base name (default: customer-support-vpc)
                                Note: Environment name will be appended automatically (e.g., customer-support-vpc-dev)
    -r, --region REGION         AWS region (default: us-west-2)
    -e, --env ENVIRONMENT       Environment name (default: dev, appended to stack name)
    -u, --db-user USERNAME      Database master username (default: postgres)
    -m, --model MODEL_ID        Bedrock model ID (default: global.anthropic.claude-haiku-4-5-20251001-v1:0)
    --email EMAIL               Admin user email (REQUIRED)
    --password PASSWORD         Admin user password (REQUIRED, min 8 chars with uppercase, lowercase, number, special char)
    -h, --help                  Show this help message

EXAMPLES:
    # Deploy dev environment (creates stack: customer-support-vpc-dev)
    $0 --email admin@example.com --password 'MyP@ssw0rd123'

    # Deploy production environment (creates stack: customer-support-vpc-prod)
    $0 --env prod --email admin@example.com --password 'MyP@ssw0rd123'

    # Deploy test environment with Haiku model (creates stack: customer-support-vpc-test)
    $0 --env test --email admin@example.com --password 'MyP@ssw0rd123' --model global.anthropic.claude-haiku-4-5-20251001-v1:0

    # Deploy to specific region with custom model (creates stack: customer-support-vpc-dev)
    $0 --region us-west-2 --model global.anthropic.claude-haiku-4-5-20251001-v1:0 --email admin@example.com --password 'MyP@ssw0rd123'

    # Full customization (creates stack: prod-support-prod)
    $0 --bucket customersupportvpc-prod \\
       --stack prod-support \\
       --env prod \\
       --region us-east-1 \\
       --model global.anthropic.claude-haiku-4-5-20251001-v1:0 \\
       --email admin@example.com \\
       --password 'MyP@ssw0rd123'

NOTE:
    - Admin email and password are REQUIRED for Cognito user pool
    - Password must be at least 8 characters with uppercase, lowercase, number, and special character
    - Stack name will automatically include environment suffix (e.g., -dev, -prod, -test)
    - If bucket name is not provided, a random S3-compliant name will be generated
      with prefix 'customersupportvpc-' followed by 12 random lowercase alphanumeric characters.

EOF
}

generate_bucket_name() {
    # Generate S3-compliant bucket name with customersupportvpc prefix
    # S3 naming rules: lowercase, numbers, hyphens, 3-63 chars, no underscores
    local random_suffix=$(head /dev/urandom | LC_ALL=C tr -dc 'a-z0-9' | head -c 12)
    echo "customersupportvpc-${random_suffix}"
}

################################################################################
# Main Script
################################################################################

main() {
    # Parse command line arguments
    BUCKET_NAME=""
    CUSTOM_STACK_NAME=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            -b|--bucket)
                BUCKET_NAME="$2"
                shift 2
                ;;
            -s|--stack)
                CUSTOM_STACK_NAME="$2"
                shift 2
                ;;
            -r|--region)
                REGION="$2"
                shift 2
                ;;
            -e|--env)
                ENVIRONMENT="$2"
                shift 2
                ;;
            -u|--db-user)
                DB_USERNAME="$2"
                shift 2
                ;;
            -m|--model)
                MODEL_ID="$2"
                shift 2
                ;;
            --email)
                ADMIN_EMAIL="$2"
                shift 2
                ;;
            --password)
                ADMIN_PASSWORD="$2"
                shift 2
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                print_usage
                exit 1
                ;;
        esac
    done

    # Set stack name with environment suffix
    if [ -n "$CUSTOM_STACK_NAME" ]; then
        STACK_NAME="${CUSTOM_STACK_NAME}-${ENVIRONMENT}"
    else
        STACK_NAME="${STACK_NAME_BASE}-${ENVIRONMENT}"
    fi

    # Generate bucket name if not provided
    if [ -z "$BUCKET_NAME" ]; then
        BUCKET_NAME=$(generate_bucket_name)
        print_info "Generated S3 bucket name: ${BUCKET_NAME}"
    fi

    # Validate required parameters
    if [ -z "$ADMIN_EMAIL" ] || [ -z "$ADMIN_PASSWORD" ]; then
        print_error "Admin email and password are required!"
        print_usage
        exit 1
    fi

    print_header "Customer Support VPC Stack Deployment"
    echo "Stack Name:   $STACK_NAME"
    echo "S3 Bucket:    $BUCKET_NAME"
    echo "Region:       $REGION"
    echo "Environment:  $ENVIRONMENT"
    echo "DB Username:  $DB_USERNAME"
    echo "Model ID:     $MODEL_ID"
    echo "Admin Email:  $ADMIN_EMAIL"
    echo "Password:     ******** (hidden)"
    echo ""

    read -p "Continue with deployment? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Deployment cancelled"
        exit 0
    fi

    # Execute deployment steps
    check_prerequisites
    create_s3_bucket "$BUCKET_NAME"
    upload_templates "$BUCKET_NAME"
    validate_template "$BUCKET_NAME"
    deploy_stack "$BUCKET_NAME"
    get_stack_outputs

    print_header "Deployment Complete"
    print_success "Stack deployed successfully!"
    print_info "Stack Name: ${STACK_NAME}"
    print_info "Region: ${REGION}"
    print_info ""
    print_info "View stack in AWS Console:"
    print_info "https://console.aws.amazon.com/cloudformation/home?region=${REGION}#/stacks/stackinfo?stackId=${STACK_NAME}"
}

# Run main function
main "$@"
