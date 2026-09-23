#!/bin/bash

# Delete OAuth2 Credential Provider for AgentCore
# This script reads the provider name from dynamic config and deletes it

set -e  # Exit on any error

echo "🗑️  AgentCore OAuth2 Credential Provider Deletion"
echo "================================================="

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
STATIC_CONFIG="${CONFIG_DIR}/static-config.yaml"
DYNAMIC_CONFIG="${CONFIG_DIR}/dynamic-config.yaml"

if [[ ! -f "$STATIC_CONFIG" ]]; then
    echo -e "${RED}❌ Static config file not found: $STATIC_CONFIG${NC}"
    exit 1
fi

if [[ ! -f "$DYNAMIC_CONFIG" ]]; then
    echo -e "${RED}❌ Dynamic config file not found: $DYNAMIC_CONFIG${NC}"
    exit 1
fi

# Extract values from YAML (fallback method if yq not available)
get_yaml_value() {
    local key="$1"
    local file="$2"
    # Handle nested YAML keys with proper indentation
    grep "  $key:" "$file" | head -1 | sed 's/.*: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' | xargs
}

# Get region from static config
REGION=$(get_yaml_value "region" "$STATIC_CONFIG")

# Get provider details from dynamic config
PROVIDER_NAME=$(get_yaml_value "provider_name" "$DYNAMIC_CONFIG")
PROVIDER_ARN=$(get_yaml_value "provider_arn" "$DYNAMIC_CONFIG")

echo -e "${BLUE}📋 Configuration Details:${NC}"
echo "   Region: $REGION"
echo "   Provider Name: $PROVIDER_NAME"
echo "   Provider ARN: $PROVIDER_ARN"
echo ""

# Validate we have the necessary information
if [[ -z "$PROVIDER_NAME" || "$PROVIDER_NAME" == '""' || "$PROVIDER_NAME" == "''" ]]; then
    echo -e "${YELLOW}⚠️  No OAuth provider found in dynamic config${NC}"
    echo "   The provider_name field is empty or not set"
    echo "   Nothing to delete."
    exit 0
fi

if [[ -z "$REGION" ]]; then
    echo -e "${RED}❌ Region not found in static config${NC}"
    exit 1
fi

# Function to delete OAuth2 credential provider
delete_oauth_provider() {
    echo -e "${BLUE}🗑️  Deleting OAuth2 Credential Provider${NC}"
    echo -e "${BLUE}=====================================${NC}"
    
    echo "   Provider Name: $PROVIDER_NAME"
    echo "   Region: $REGION"
    echo ""
    
    # Check if provider exists
    echo "🔍 Checking if provider exists..."
    if aws bedrock-agentcore-control get-oauth2-credential-provider --name "$PROVIDER_NAME" --region "$REGION" &> /dev/null; then
        echo -e "${GREEN}✓ Provider found${NC}"
        
        # Delete the provider
        echo "🗑️  Deleting OAuth2 credential provider..."
        local delete_output
        if delete_output=$(aws bedrock-agentcore-control delete-oauth2-credential-provider \
            --name "$PROVIDER_NAME" \
            --region "$REGION" 2>&1); then
            
            echo -e "${GREEN}✅ OAuth2 credential provider deleted successfully${NC}"
            return 0
        else
            echo -e "${RED}❌ Failed to delete OAuth2 credential provider${NC}"
            echo "$delete_output"
            return 1
        fi
    else
        echo -e "${YELLOW}⚠️  Provider not found in AWS${NC}"
        echo "   Provider '$PROVIDER_NAME' does not exist or has already been deleted"
        return 0
    fi
}

# Function to update configuration files
update_config_files() {
    echo -e "${BLUE}📝 Updating configuration files${NC}"
    echo -e "${BLUE}===============================${NC}"
    
    # Reset OAuth provider section in dynamic config
    if [[ -f "$DYNAMIC_CONFIG" ]]; then
        echo "🧹 Cleaning up OAuth provider configuration..."
        
        if command -v yq >/dev/null 2>&1; then
            # Use yq for proper YAML manipulation
            yq eval ".oauth_provider.provider_name = \"\"" -i "$DYNAMIC_CONFIG"
            yq eval ".oauth_provider.provider_arn = \"\"" -i "$DYNAMIC_CONFIG"
            yq eval ".oauth_provider.scopes = []" -i "$DYNAMIC_CONFIG"
        else
            # Fallback: manual update using sed (handle both quoted and unquoted values)
            sed -i.bak "s|provider_name: .*|provider_name: \"\"|" "$DYNAMIC_CONFIG"
            sed -i.bak "s|provider_arn: .*|provider_arn: \"\"|" "$DYNAMIC_CONFIG"
            
            # Handle scopes array - replace any existing scopes with empty array
            sed -i.bak "/^  scopes:/,/^[^ ]/ {
                /^  scopes:/ {
                    c\\
  scopes: []
                }
                /^  - / d
            }" "$DYNAMIC_CONFIG"
            
            # Remove backup file
            rm -f "${DYNAMIC_CONFIG}.bak"
        fi
        
        echo -e "${GREEN}✅ Updated: dynamic-config.yaml${NC}"
        echo "   • provider_name reset to empty"
        echo "   • provider_arn reset to empty"
        echo "   • scopes reset to []"
    else
        echo -e "${YELLOW}⚠️  Dynamic configuration file not found: $DYNAMIC_CONFIG${NC}"
    fi
    
    return 0
}

# Function to show completion message
show_completion() {
    echo ""
    echo -e "${GREEN}🎉 OAuth2 Provider Deletion Complete!${NC}"
    echo -e "${GREEN}====================================${NC}"
    echo ""
    echo -e "${BLUE}📋 What was cleaned up:${NC}"
    echo "   • OAuth2 credential provider: $PROVIDER_NAME"
    echo "   • Updated: config/dynamic-config.yaml"
    echo ""
    echo -e "${BLUE}🚀 Next Steps:${NC}"
    echo "   • You can now run ./03-setup-oauth-provider.sh to create a new provider"
    echo "   • Or continue with other deployment steps"
    echo ""
    echo -e "${BLUE}🔒 Security Note:${NC}"
    echo "   • All OAuth credentials have been removed from AWS"
    echo "   • Configuration files have been reset to empty values"
}

# Main execution
main() {
    echo -e "${BLUE}Deleting OAuth2 Credential Provider${NC}"
    echo ""
    
    # Delete OAuth2 credential provider
    if ! delete_oauth_provider; then
        exit 1
    fi
    
    echo ""
    
    # Update configuration files
    if ! update_config_files; then
        exit 1
    fi
    
    # Show completion message
    show_completion
}

# Run main function
main "$@"
