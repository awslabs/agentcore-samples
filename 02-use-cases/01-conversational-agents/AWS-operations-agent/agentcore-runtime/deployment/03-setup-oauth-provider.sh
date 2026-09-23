#!/bin/bash

# Setup OAuth2 Credential Provider for AgentCore
# Run this BEFORE deploying agents so they have OAuth capability from day 1

set -e  # Exit on any error

echo "🔧 AgentCore OAuth2 Credential Provider Setup"
echo "=============================================="

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

# Load static configuration
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
OKTA_DOMAIN_STATIC=$(get_yaml_value "domain" "${CONFIG_DIR}/static-config.yaml")

echo -e "${BLUE}📝 This script will:${NC}"
echo "   1. Prompt for your Okta credentials (secure input)"
echo "   2. Create OAuth2 credential provider in AgentCore"
echo "   3. Update configuration files with provider details"
echo "   4. Prepare for agent deployment"
echo ""

# Function to verify prerequisites
verify_prerequisites() {
    echo -e "${BLUE}🔍 Verifying prerequisites...${NC}"
    
    # Check if prerequisites.sh has been run
    if ! aws iam get-role --role-name bac-execution-role &> /dev/null; then
        echo -e "${RED}❌ IAM role not found: bac-execution-role${NC}"
        echo "   Please run ./prerequisites.sh first"
        return 1
    fi
    
    # Check ECR repositories
    local repos=("bac-runtime-repo-diy" "bac-runtime-repo-sdk")
    for repo in "${repos[@]}"; do
        if ! aws ecr describe-repositories --repository-names "$repo" --region "$REGION" &> /dev/null; then
            echo -e "${RED}❌ ECR repository not found: $repo${NC}"
            echo "   Please run ./prerequisites.sh first"
            return 1
        fi
    done
    
    echo -e "${GREEN}✅ Prerequisites verified${NC}"
    return 0
}

# Function to collect Okta credentials securely
collect_okta_credentials() {
    echo -e "${BLUE}🔐 Okta Credential Collection${NC}"
    echo -e "${BLUE}=============================${NC}"
    echo "Please provide your Okta application credentials:"
    echo ""
    
    # Use Okta domain from static config or prompt if not found
    if [[ -n "$OKTA_DOMAIN_STATIC" ]]; then
        OKTA_DOMAIN="$OKTA_DOMAIN_STATIC"
        echo "Using Okta Domain from config: $OKTA_DOMAIN"
    else
        echo -n "Okta Domain (e.g., trial-7575566.okta.com): "
        read OKTA_DOMAIN
        
        if [[ -z "$OKTA_DOMAIN" ]]; then
            echo -e "${RED}❌ Okta domain is required${NC}"
            return 1
        fi
    fi
    
    # Collect Client ID
    echo -n "Client ID: "
    read OKTA_CLIENT_ID
    
    if [[ -z "$OKTA_CLIENT_ID" ]]; then
        echo -e "${RED}❌ Client ID is required${NC}"
        return 1
    fi
    
    # Collect Client Secret (hidden input)
    echo -n "Client Secret (input will be hidden): "
    read -s OKTA_CLIENT_SECRET
    echo ""  # New line after hidden input
    
    if [[ -z "$OKTA_CLIENT_SECRET" ]]; then
        echo -e "${RED}❌ Client secret is required${NC}"
        return 1
    fi
    
    # Collect custom scope
    echo ""
    echo -e "${BLUE}ℹ️  Custom Scope Configuration:${NC}"
    echo "   • This scope must be created in your Okta Authorization Server"
    echo "   • Go to: Security > API > Authorization Servers > [your-server] > Scopes"
    echo "   • Create a custom scope (e.g., 'api') if it doesn't exist"
    echo ""
    echo -n "Custom Scope (default: api): "
    read OKTA_SCOPE
    OKTA_SCOPE=${OKTA_SCOPE:-api}
    
    echo ""
    echo -e "${GREEN}✅ Credentials collected${NC}"
    echo "   Domain: $OKTA_DOMAIN"
    echo "   Client ID: $OKTA_CLIENT_ID"
    echo "   Client Secret: [HIDDEN]"
    echo "   Scope: $OKTA_SCOPE"
    echo ""
    
    return 0
}

# Function to create OAuth2 credential provider
create_oauth_provider() {
    echo -e "${BLUE}🔧 Creating OAuth2 Credential Provider${NC}"
    echo -e "${BLUE}=====================================${NC}"
    
    local provider_name="bac-identity-provider-okta"
    local well_known_url="https://${OKTA_DOMAIN}/oauth2/default/.well-known/openid-configuration"
    
    echo "   Provider Name: $provider_name"
    echo "   Domain: $OKTA_DOMAIN"
    echo "   Discovery URL: $well_known_url"
    echo "   Client ID: $OKTA_CLIENT_ID"
    echo ""
    
    # Check if provider already exists
    if aws bedrock-agentcore-control get-oauth2-credential-provider --name "$provider_name" --region "$REGION" &> /dev/null; then
        echo -e "${YELLOW}⚠️  Provider already exists, updating configuration...${NC}"
        
        # Update existing provider with correct configuration
        local update_output
        if update_output=$(aws bedrock-agentcore-control update-oauth2-credential-provider \
            --name "$provider_name" \
            --credential-provider-vendor "CustomOauth2" \
            --oauth2-provider-config-input "{
                \"customOauth2ProviderConfig\": {
                    \"oauthDiscovery\": {
                        \"discoveryUrl\": \"$well_known_url\"
                    },
                    \"clientId\": \"$OKTA_CLIENT_ID\",
                    \"clientSecret\": \"$OKTA_CLIENT_SECRET\"
                }
            }" \
            --region "$REGION" 2>&1); then
            
            echo -e "${GREEN}✅ OAuth2 credential provider updated successfully${NC}"
        else
            echo -e "${RED}❌ Failed to update OAuth2 credential provider${NC}"
            echo "$update_output"
            return 1
        fi
    else
        echo "   Creating new OAuth2 credential provider..."
        
        # Create new provider using AWS CLI (more reliable than SDK)
        local create_output
        if create_output=$(aws bedrock-agentcore-control create-oauth2-credential-provider \
            --name "$provider_name" \
            --credential-provider-vendor "CustomOauth2" \
            --oauth2-provider-config-input "{
                \"customOauth2ProviderConfig\": {
                    \"oauthDiscovery\": {
                        \"discoveryUrl\": \"$well_known_url\"
                    },
                    \"clientId\": \"$OKTA_CLIENT_ID\",
                    \"clientSecret\": \"$OKTA_CLIENT_SECRET\"
                }
            }" \
            --region "$REGION" 2>&1); then
            
            echo -e "${GREEN}✅ OAuth2 credential provider created successfully${NC}"
        else
            echo -e "${RED}❌ Failed to create OAuth2 credential provider${NC}"
            echo "$create_output"
            return 1
        fi
    fi
    
    # Get provider details for configuration update
    local provider_details
    if provider_details=$(aws bedrock-agentcore-control get-oauth2-credential-provider \
        --name "$provider_name" \
        --region "$REGION" 2>&1); then
        
        # Extract ARN from the response using multiple approaches for reliability
        # First try with jq if available
        if command -v jq >/dev/null 2>&1; then
            PROVIDER_ARN=$(echo "$provider_details" | jq -r '.credentialProviderArn' 2>/dev/null)
        fi
        
        # Fallback: Extract ARN using grep and sed (handle escaped JSON)
        if [[ -z "$PROVIDER_ARN" || "$PROVIDER_ARN" == "null" ]]; then
            # Look for the credentialProviderArn field in the JSON response
            PROVIDER_ARN=$(echo "$provider_details" | grep -o 'credentialProviderArn[^,}]*' | sed 's/.*: *["\\"]*\([^"\\]*\).*/\1/' | head -1)
        fi
        
        # Additional fallback: try a different pattern
        if [[ -z "$PROVIDER_ARN" ]]; then
            PROVIDER_ARN=$(echo "$provider_details" | sed -n 's/.*"credentialProviderArn": *"\([^"]*\)".*/\1/p' | head -1)
        fi
        
        # Final fallback: extract from the escaped JSON string
        if [[ -z "$PROVIDER_ARN" ]]; then
            PROVIDER_ARN=$(echo "$provider_details" | sed -n 's/.*\\\"credentialProviderArn\\\":\\\"\\([^\\]*\\)\\\".*/\1/p' | head -1)
        fi
        
        PROVIDER_NAME="$provider_name"
        
        echo "   Name: $PROVIDER_NAME"
        echo "   ARN: $PROVIDER_ARN"
        
        # Validate that we got an ARN
        if [[ -z "$PROVIDER_ARN" ]]; then
            echo -e "${YELLOW}⚠️  Warning: Could not extract ARN from response${NC}"
            echo "   Response: $provider_details"
        fi
        
        return 0
    else
        echo -e "${RED}❌ Failed to get provider details${NC}"
        echo "$provider_details"
        return 1
    fi
}

# Function to update configuration files
update_config_files() {
    echo -e "${BLUE}📝 Updating configuration files${NC}"
    echo -e "${BLUE}===============================${NC}"
    
    
    # Update dynamic-config.yaml to include OAuth info (without secrets)
    local dynamic_config="${CONFIG_DIR}/dynamic-config.yaml"
    
    if [[ -f "$dynamic_config" ]]; then
        # Update OAuth provider section in dynamic config
        if grep -q "oauth_provider:" "$dynamic_config"; then
            # Use sed to update the oauth_provider section (using | as delimiter to handle ARN with /)
            sed -i '' \
                -e "s|provider_name: \"\"|provider_name: \"$PROVIDER_NAME\"|" \
                -e "s|provider_arn: \"\"|provider_arn: \"$PROVIDER_ARN\"|" \
                -e "s|scopes: \[\]|scopes: [\"$OKTA_SCOPE\"]|" \
                "$dynamic_config"
            
            echo -e "${GREEN}✅ Updated: dynamic-config.yaml${NC}"
            
            # Validate the updates
            if [[ -n "$PROVIDER_ARN" ]]; then
                if grep -q "provider_arn: \"$PROVIDER_ARN\"" "$dynamic_config"; then
                    echo -e "${GREEN}   ✓ Provider ARN updated successfully${NC}"
                else
                    echo -e "${YELLOW}   ⚠️  Provider ARN may not have been updated correctly${NC}"
                fi
            else
                echo -e "${YELLOW}   ⚠️  Provider ARN was empty - config may need manual update${NC}"
            fi
        else
            echo -e "${YELLOW}⚠️  oauth_provider section not found in dynamic-config.yaml${NC}"
        fi
    fi
    
    return 0
}

# Function to show next steps
show_next_steps() {
    echo -e "${GREEN}🎉 OAuth2 Setup Complete!${NC}"
    echo -e "${GREEN}=========================${NC}"
    echo ""
    echo -e "${BLUE}📋 What was created:${NC}"
    echo "   • OAuth2 credential provider: $PROVIDER_NAME"
    echo "   • Updated: config/dynamic-config.yaml"
    echo ""
    echo -e "${BLUE}🚀 Next Steps:${NC}"
    echo "   1. Deploy DIY agent: ./deploy-diy.sh"
    echo "   2. Deploy SDK agent: ./deploy-sdk.sh"
    echo "   3. Create runtimes: python3 deploy-diy-runtime.py"
    echo "   4. Create runtimes: python3 deploy-sdk-runtime.py"
    echo ""
    echo -e "${BLUE}💻 Using OAuth in your agents:${NC}"
    echo "   @requires_access_token("
    echo "       provider_name=\"$PROVIDER_NAME\","
    echo "       scopes=[\"$OKTA_SCOPE\"],"
    echo "       auth_flow=\"M2M\""
    echo "   )"
    echo "   async def my_function(*, access_token: str):"
    echo "       # access_token contains your Okta OAuth2 token"
    echo ""
    echo -e "${BLUE}🔒 Security Note:${NC}"
    echo "   • Credentials are stored securely in AgentCore Identity"
    echo "   • No secrets are saved in configuration files"
    echo "   • Tokens are automatically managed and refreshed"
}

# Main execution
main() {
    echo -e "${BLUE}Step 2: OAuth2 Credential Provider Setup${NC}"
    echo "Run this BEFORE deploying agents"
    echo ""
    
    # Verify prerequisites
    if ! verify_prerequisites; then
        exit 1
    fi
    
    echo ""
    
    # Collect Okta credentials
    if ! collect_okta_credentials; then
        exit 1
    fi
    
    # Create OAuth2 credential provider
    if ! create_oauth_provider; then
        exit 1
    fi
    
    echo ""
    
    # Update configuration files
    if ! update_config_files; then
        exit 1
    fi
    
    echo ""
    
    # Show next steps
    show_next_steps
}

# Run main function
main "$@"
