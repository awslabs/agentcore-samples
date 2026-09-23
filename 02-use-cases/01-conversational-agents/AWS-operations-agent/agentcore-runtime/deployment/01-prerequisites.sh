#!/bin/bash

# Prerequisites setup script for AgentCore deployment
# This script ensures all necessary AWS resources and configurations are in place

set -e  # Exit on any error

echo "🔧 AgentCore Prerequisites Setup"
echo "================================"
echo ""
echo -e "${YELLOW}⚠️  Platform Notice: This script has been tested on macOS only${NC}"
echo -e "${BLUE}   For other platforms, manual installation may be required${NC}"
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory and project paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"  # Go up two levels to reach AgentCore root
CONFIG_DIR="${PROJECT_DIR}/config"

# Load configuration
if [[ ! -f "${CONFIG_DIR}/static-config.yaml" ]]; then
    echo -e "${RED}❌ Config file not found: ${CONFIG_DIR}/static-config.yaml${NC}"
    exit 1
fi

# Extract values from YAML (fallback method if yq not available)
get_yaml_value() {
    local key="$1"
    local file="$2"
    # Handle nested YAML keys with proper indentation
    grep "  $key:" "$file" | head -1 | sed 's/.*: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' | xargs
}

REGION=$(get_yaml_value "region" "${CONFIG_DIR}/static-config.yaml")
ACCOUNT_ID=$(get_yaml_value "account_id" "${CONFIG_DIR}/static-config.yaml")
ROLE_NAME="bac-execution-role"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

echo -e "${BLUE}📝 Configuration:${NC}"
echo "   Region: $REGION"
echo "   Account ID: $ACCOUNT_ID"
echo "   Role ARN: $ROLE_ARN"
echo ""

# Function to check if command exists
check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo -e "${RED}❌ $1 is not installed${NC}"
        return 1
    else
        echo -e "${GREEN}✅ $1 is available${NC}"
        return 0
    fi
}

# Function to setup Python virtual environment and dependencies
setup_python_environment() {
    echo -e "${BLUE}🐍 Setting up Python virtual environment...${NC}"
    
    # Check if we're already in the AgentCore directory
    if [[ ! -f "${PROJECT_DIR}/requirements.txt" ]]; then
        echo -e "${RED}❌ requirements.txt not found in ${PROJECT_DIR}${NC}"
        return 1
    fi
    
    # Create virtual environment if it doesn't exist
    if [[ ! -d "${PROJECT_DIR}/.venv" ]]; then
        echo "   📦 Creating virtual environment..."
        if python3 -m venv "${PROJECT_DIR}/.venv"; then
            echo -e "${GREEN}✅ Virtual environment created${NC}"
        else
            echo -e "${RED}❌ Failed to create virtual environment${NC}"
            return 1
        fi
    else
        echo -e "${GREEN}✅ Virtual environment already exists${NC}"
    fi
    
    # Activate virtual environment and install dependencies
    echo "   📦 Installing Python dependencies..."
    cd "${PROJECT_DIR}"
    source .venv/bin/activate
    
    # Upgrade pip first
    pip install --upgrade pip > /dev/null 2>&1
    
    # Install requirements with better error handling
    echo "   📦 Installing Python dependencies..."
    
    # First, try to install bedrock-agentcore specifically
    echo "   🔧 Installing bedrock-agentcore SDK..."
    if pip install bedrock-agentcore>=0.1.1 --quiet; then
        echo -e "${GREEN}   ✅ bedrock-agentcore SDK installed${NC}"
    else
        echo -e "${RED}   ❌ Failed to install bedrock-agentcore SDK${NC}"
        echo -e "${BLUE}   This package is required for OAuth provider creation${NC}"
        return 1
    fi
    
    # Install all other requirements
    if pip install -r "${PROJECT_DIR}/requirements.txt" --quiet; then
        echo -e "${GREEN}✅ Python dependencies installed${NC}"
    else
        echo -e "${YELLOW}⚠️  Some Python dependencies may have failed to install${NC}"
        echo -e "${BLUE}   This is expected for packages like 'strands' that require compilation${NC}"
        echo -e "${BLUE}   Checking critical dependencies...${NC}"
        
        # Test if critical packages are available
        if python -c "import bedrock_agentcore" 2>/dev/null; then
            echo -e "${GREEN}   ✅ bedrock-agentcore is available${NC}"
        else
            echo -e "${RED}   ❌ bedrock-agentcore is not available${NC}"
            return 1
        fi
    fi
    
    # Install/upgrade AWS CLI in the virtual environment
    echo "   🔧 Installing latest AWS CLI in virtual environment..."
    if pip install --upgrade awscli > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Latest AWS CLI installed in virtual environment${NC}"
    else
        echo -e "${YELLOW}⚠️  Failed to install AWS CLI in virtual environment${NC}"
        echo -e "${BLUE}   Will use system AWS CLI${NC}"
    fi
    
    return 0
}

# Function to check AWS credentials
check_aws_credentials() {
    echo -e "${BLUE}🔐 Checking AWS credentials...${NC}"
    
    if ! aws sts get-caller-identity &> /dev/null; then
        echo -e "${RED}❌ AWS credentials not configured or invalid${NC}"
        echo "   Please configure AWS credentials using:"
        echo "   - aws configure"
        echo "   - aws sso login (if using SSO)"
        echo "   - Set AWS_PROFILE environment variable"
        return 1
    fi
    
    local caller_identity=$(aws sts get-caller-identity 2>/dev/null)
    local current_account=$(echo "$caller_identity" | grep -o '"Account": "[^"]*"' | cut -d'"' -f4)
    
    if [[ "$current_account" != "$ACCOUNT_ID" ]]; then
        echo -e "${YELLOW}⚠️  Warning: Current AWS account ($current_account) doesn't match config ($ACCOUNT_ID)${NC}"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi
    
    echo -e "${GREEN}✅ AWS credentials valid for account: $current_account${NC}"
    return 0
}

# Function to update existing role policies
update_existing_role_policies() {
    # Load permission policy from file and substitute account ID
    local permission_policy_file="${SCRIPT_DIR}/bac-permissions-policy.json"
    if [[ ! -f "$permission_policy_file" ]]; then
        echo -e "${RED}❌ Permission policy file not found: $permission_policy_file${NC}"
        return 1
    fi
    local permission_policy=$(sed "s/165938467517/$ACCOUNT_ID/g" "$permission_policy_file")
    
    # Update permission policy
    local policy_name="bac-execution-policy"
    if aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "$policy_name" \
        --policy-document "$permission_policy" &> /dev/null; then
        echo -e "${GREEN}✅ IAM role permissions updated${NC}"
    else
        echo -e "${YELLOW}⚠️  Warning: Failed to update permissions policy${NC}"
    fi
}

# Function to create IAM role
create_iam_role() {
    echo -e "${BLUE}🔑 Checking IAM role: $ROLE_NAME${NC}"
    
    if aws iam get-role --role-name "$ROLE_NAME" &> /dev/null; then
        echo -e "${GREEN}✅ IAM role already exists${NC}"
        echo "   📝 Updating role policies with current account ID..."
        update_existing_role_policies
        return 0
    fi
    
    echo "   📝 Creating IAM role..."
    
    # Load trust policy from file and substitute account ID
    local trust_policy_file="${SCRIPT_DIR}/bac-trust-policy.json"
    if [[ ! -f "$trust_policy_file" ]]; then
        echo -e "${RED}❌ Trust policy file not found: $trust_policy_file${NC}"
        return 1
    fi
    local trust_policy=$(sed "s/165938467517/$ACCOUNT_ID/g" "$trust_policy_file")
    
    # Create the role
    if aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document "$trust_policy" \
        --description "Execution role for AgentCore runtime" &> /dev/null; then
        echo -e "${GREEN}✅ IAM role created successfully${NC}"
    else
        echo -e "${RED}❌ Failed to create IAM role${NC}"
        return 1
    fi
    
    # Load permission policy from file and substitute account ID
    local permission_policy_file="${SCRIPT_DIR}/bac-permissions-policy.json"
    if [[ ! -f "$permission_policy_file" ]]; then
        echo -e "${RED}❌ Permission policy file not found: $permission_policy_file${NC}"
        return 1
    fi
    local permission_policy=$(sed "s/165938467517/$ACCOUNT_ID/g" "$permission_policy_file")
    
    # Attach permission policy
    local policy_name="bac-execution-policy"
    if aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "$policy_name" \
        --policy-document "$permission_policy" &> /dev/null; then
        echo -e "${GREEN}✅ IAM role permissions attached${NC}"
    else
        echo -e "${YELLOW}⚠️  Warning: Failed to attach permissions policy${NC}"
    fi
    
    return 0
}

# Function to check ECR repositories
check_ecr_repositories() {
    echo -e "${BLUE}📦 Checking ECR repositories...${NC}"
    
    local repos=("bac-runtime-repo-diy" "bac-runtime-repo-sdk")
    
    for repo in "${repos[@]}"; do
        if aws ecr describe-repositories --repository-names "$repo" --region "$REGION" &> /dev/null; then
            echo -e "${GREEN}✅ ECR repository exists: $repo${NC}"
        else
            echo "   📦 Creating ECR repository: $repo"
            if aws ecr create-repository --repository-name "$repo" --region "$REGION" &> /dev/null; then
                echo -e "${GREEN}✅ ECR repository created: $repo${NC}"
            else
                echo -e "${RED}❌ Failed to create ECR repository: $repo${NC}"
                return 1
            fi
        fi
    done
    
    return 0
}

# Function to validate config files
validate_config() {
    echo -e "${BLUE}📋 Validating configuration files...${NC}"
    
    # Check static-config.yaml
    if [[ ! -f "${CONFIG_DIR}/static-config.yaml" ]]; then
        echo -e "${RED}❌ Missing: static-config.yaml${NC}"
        return 1
    fi
    
    # Check dynamic-config.yaml exists (create if missing)
    if [[ ! -f "${CONFIG_DIR}/dynamic-config.yaml" ]]; then
        echo -e "${YELLOW}⚠️  Creating missing dynamic-config.yaml${NC}"
        # Create empty dynamic config if it doesn't exist
        cat > "${CONFIG_DIR}/dynamic-config.yaml" << 'EOF'
# Dynamic Configuration - Updated by deployment scripts only
# This file contains all configuration values that are generated/updated during deployment
gateway:
  id: ""
  arn: ""
  url: ""
oauth_provider:
  provider_name: ""
  provider_arn: ""
  domain: ""
  scopes: []
mcp_lambda:
  function_name: ""
  function_arn: ""
  role_arn: ""
  stack_name: ""
  gateway_execution_role_arn: ""
runtime:
  diy_agent:
    arn: ""
    ecr_uri: ""
    endpoint_arn: ""
  sdk_agent:
    arn: ""
    ecr_uri: ""
    endpoint_arn: ""
client:
  diy_runtime_endpoint: ""
  sdk_runtime_endpoint: ""
EOF
    fi
    
    # Validate required fields in static config
    local required_fields=("region" "account_id")
    for field in "${required_fields[@]}"; do
        if ! grep -q "$field:" "${CONFIG_DIR}/static-config.yaml"; then
            echo -e "${RED}❌ Missing required field in static config: $field${NC}"
            return 1
        fi
    done
    
    echo -e "${GREEN}✅ Configuration files valid${NC}"
    return 0
}

# Function to test AgentCore Identity permissions
test_agentcore_identity_permissions() {
    echo -e "${BLUE}🧪 Testing AgentCore Identity permissions...${NC}"
    
    # Check if we can list existing resources (basic permission test)
    if aws bedrock-agentcore-control list-workload-identities --region "$REGION" &> /dev/null; then
        echo -e "${GREEN}✅ AgentCore Identity list permissions working${NC}"
        
        # Check if we have the critical GetResourceOauth2Token permission
        # We can't directly test this without creating resources, so we'll note it
        echo -e "${BLUE}ℹ️  Note: GetResourceOauth2Token permission added for Okta integration${NC}"
        echo -e "${BLUE}   This enables OAuth2 token retrieval from external providers like Okta${NC}"
        
        return 0
    else
        echo -e "${YELLOW}⚠️  AgentCore Identity permissions may need time to propagate${NC}"
        echo -e "${BLUE}   If you encounter AccessDeniedException errors:${NC}"
        echo -e "${BLUE}   1. Wait 2-3 minutes for IAM changes to take effect${NC}"
        echo -e "${BLUE}   2. Re-run this script to verify permissions${NC}"
        return 1
    fi
}

# Function to show Okta integration status
show_okta_integration_status() {
    echo -e "${BLUE}🔗 Okta Integration Status${NC}"
    echo -e "${BLUE}=========================${NC}"
    
    if grep -q "okta:" "${CONFIG_DIR}/static-config.yaml"; then
        echo -e "${GREEN}✅ Okta configuration present in static-config.yaml${NC}"
        
        
        echo -e "${BLUE}📋 Okta Integration Requirements:${NC}"
        echo -e "${BLUE}   1. ✅ AgentCore Identity permissions (included in this setup)${NC}"
        echo -e "${BLUE}   2. ⚙️  Okta application with 'Client Credentials' grant enabled${NC}"
        echo -e "${BLUE}   3. 🎯 Custom 'api' scope created in Okta authorization server${NC}"
        echo -e "${BLUE}   4. 🔑 Valid client ID and secret in static-config.yaml${NC}"
        
    else
        echo -e "${YELLOW}⚠️  Okta configuration not found${NC}"
        echo -e "${BLUE}   Add okta section to ${CONFIG_DIR}/static-config.yaml with your Okta credentials${NC}"
    fi
}

# Main execution
main() {
    echo -e "${BLUE}🔍 Checking dependencies...${NC}"
    
    local deps_ok=true
    check_command "aws" || deps_ok=false
    check_command "docker" || deps_ok=false
    check_command "python3" || deps_ok=false
    
    # Check for bedrock-agentcore-control CLI (critical for OAuth provider setup)
    if aws bedrock-agentcore-control help &> /dev/null; then
        echo -e "${GREEN}✅ aws bedrock-agentcore-control is available${NC}"
    else
        echo -e "${RED}❌ aws bedrock-agentcore-control is not available${NC}"
        echo -e "${BLUE}   This CLI is required for OAuth provider creation${NC}"
        echo -e "${BLUE}   Please ensure you have the latest AWS CLI version${NC}"
        echo -e "${BLUE}   Run: aws --version (should be 2.15.0 or later)${NC}"
        deps_ok=false
    fi
    
    if command -v yq &> /dev/null; then
        echo -e "${GREEN}✅ yq is available (recommended)${NC}"
    else
        echo -e "${YELLOW}⚠️  yq not found (will use fallback parsing)${NC}"
    fi
    
    if [[ "$deps_ok" != true ]]; then
        echo -e "${RED}❌ Missing required dependencies${NC}"
        exit 1
    fi
    
    echo ""
    
    # Setup Python environment
    setup_python_environment || exit 1
    
    echo ""
    
    # Run checks
    validate_config || exit 1
    check_aws_credentials || exit 1
    create_iam_role || exit 1
    check_ecr_repositories || exit 1
    
    echo ""
    
    # Test AgentCore Identity permissions
    test_agentcore_identity_permissions
    
    echo ""
    
    # Show Okta integration status
    show_okta_integration_status
    
    echo ""
    echo -e "${GREEN}🎉 Prerequisites setup complete!${NC}"
    echo ""
    echo -e "${BLUE}📋 Next steps:${NC}"
    echo "   1. Deploy DIY agent: ./deploy-diy.sh"
    echo "   2. Deploy SDK agent: ./deploy-sdk.sh"
    echo "   3. Create runtimes: python3 deploy-diy-runtime.py"
    echo "   4. Create runtimes: python3 deploy-sdk-runtime.py"
    echo ""
    echo -e "${BLUE}🔗 For Okta integration:${NC}"
    echo "   5. Test Okta integration: cd src/auth && python okta_working_final.py"
    echo "   6. Verify AgentCore Identity + Okta OAuth2 token retrieval"
}

# Run main function
main "$@"