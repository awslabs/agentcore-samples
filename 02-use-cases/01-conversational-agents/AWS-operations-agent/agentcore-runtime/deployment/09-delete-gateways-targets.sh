#!/bin/bash

# Delete all AgentCore Gateways and Targets
echo "🗑️  Deleting all AgentCore Gateways and Targets..."

# Configuration - Get project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"  # Go up two levels to reach AgentCore root
RUNTIME_DIR="$(dirname "$SCRIPT_DIR")"  # agentcore-runtime directory
CONFIG_DIR="${PROJECT_DIR}/config"

# Load configuration from YAML (fallback if yq not available)
if command -v yq >/dev/null 2>&1; then
    REGION=$(yq eval '.aws.region' "${CONFIG_DIR}/static-config.yaml")
    ACCOUNT_ID=$(yq eval '.aws.account_id' "${CONFIG_DIR}/static-config.yaml")
else
    echo "⚠️  yq not found, using default values from existing config"
    # Fallback: extract from YAML using grep/sed
    REGION=$(grep "region:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*region: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
    ACCOUNT_ID=$(grep "account_id:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*account_id: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
fi

echo "📝 Configuration:"
echo "   Region: $REGION"
echo "   Account ID: $ACCOUNT_ID"
echo ""

# Get AWS credentials
echo "🔐 Getting AWS credentials..."
if [ -n "$AWS_PROFILE" ]; then
    echo "Using AWS profile: $AWS_PROFILE"
else
    echo "Using default AWS credentials"
fi

# Use configured AWS profile if specified in static config
AWS_PROFILE_CONFIG=$(grep "aws_profile:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*aws_profile: *["'\'']*\([^"'\''#]*\)["'\'']*.*$/\1/' | xargs 2>/dev/null)
if [[ -n "$AWS_PROFILE_CONFIG" && "$AWS_PROFILE_CONFIG" != "\"\"" && "$AWS_PROFILE_CONFIG" != "''" ]]; then
    echo "Using configured AWS profile: $AWS_PROFILE_CONFIG"
    export AWS_PROFILE="$AWS_PROFILE_CONFIG"
fi

# Path to gateway operations scripts
GATEWAY_OPS_DIR="${RUNTIME_DIR}/gateway-ops-scripts"

# Function to check if Python scripts are available
check_gateway_scripts() {
    if [[ ! -d "$GATEWAY_OPS_DIR" ]]; then
        echo "❌ Gateway operations scripts not found: $GATEWAY_OPS_DIR"
        return 1
    fi
    
    local required_scripts=("list-gateways.py" "list-targets.py" "delete-gateway.py" "delete-target.py")
    for script in "${required_scripts[@]}"; do
        if [[ ! -f "${GATEWAY_OPS_DIR}/${script}" ]]; then
            echo "⚠️  Script not found: ${GATEWAY_OPS_DIR}/${script} (will use AWS CLI fallback)"
        fi
    done
    
    echo "✅ Gateway operations directory found"
    return 0
}

# Check if gateway operations scripts are available
echo "🔍 Checking gateway operations scripts..."
if ! check_gateway_scripts; then
    echo "❌ Gateway operations scripts not available"
    echo "   Expected location: $GATEWAY_OPS_DIR"
    exit 1
fi

# Activate virtual environment to ensure Python dependencies are available
echo "🐍 Activating Python virtual environment..."
cd "${PROJECT_DIR}" && source .venv/bin/activate

# Load current gateway configuration if available
CURRENT_GATEWAY_ID=""
CURRENT_GATEWAY_ARN=""
CURRENT_GATEWAY_URL=""

if command -v yq >/dev/null 2>&1; then
    CURRENT_GATEWAY_ID=$(yq eval '.gateway.id' "${CONFIG_DIR}/dynamic-config.yaml" 2>/dev/null)
    CURRENT_GATEWAY_ARN=$(yq eval '.gateway.arn' "${CONFIG_DIR}/dynamic-config.yaml" 2>/dev/null)
    CURRENT_GATEWAY_URL=$(yq eval '.gateway.url' "${CONFIG_DIR}/dynamic-config.yaml" 2>/dev/null)
else
    CURRENT_GATEWAY_ID=$(grep "id:" "${CONFIG_DIR}/dynamic-config.yaml" | head -1 | sed 's/.*id: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' 2>/dev/null)
    CURRENT_GATEWAY_ARN=$(grep "arn:" "${CONFIG_DIR}/dynamic-config.yaml" | head -1 | sed 's/.*arn: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' 2>/dev/null)
    CURRENT_GATEWAY_URL=$(grep "url:" "${CONFIG_DIR}/dynamic-config.yaml" | head -1 | sed 's/.*url: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' 2>/dev/null)
fi

# Check if we have a configured gateway
if [[ -n "$CURRENT_GATEWAY_ID" && "$CURRENT_GATEWAY_ID" != "null" && "$CURRENT_GATEWAY_ID" != '""' ]]; then
    echo "📋 Found configured gateway in dynamic config:"
    echo "   • Gateway ID: $CURRENT_GATEWAY_ID"
    echo "   • Gateway ARN: $CURRENT_GATEWAY_ARN"
    echo "   • Gateway URL: $CURRENT_GATEWAY_URL"
    echo ""
fi

# Change to gateway ops directory
cd "$GATEWAY_OPS_DIR"

# Function to delete all targets for a gateway
delete_gateway_targets() {
    local gateway_id="$1"
    local gateway_name="$2"
    
    echo "🎯 Deleting targets for gateway: $gateway_name ($gateway_id)"
    
    # Try to use Python script first, fallback to AWS CLI
    if [[ -f "list-targets.py" ]]; then
        echo "   📋 Listing targets using Python script..."
        TARGETS_RESPONSE=$(python3 list-targets.py --gateway-id "$gateway_id" 2>/dev/null || echo "")
        
        if [[ -n "$TARGETS_RESPONSE" ]]; then
            echo "$TARGETS_RESPONSE"
            
            # Extract target IDs (this is a simple approach, might need adjustment based on actual output format)
            TARGET_IDS=$(echo "$TARGETS_RESPONSE" | grep -o "Target ID: [^[:space:]]*" | cut -d' ' -f3 || echo "")
            
            if [[ -n "$TARGET_IDS" ]]; then
                echo "   🗑️  Found targets to delete:"
                for target_id in $TARGET_IDS; do
                    echo "      • $target_id"
                    
                    if [[ -f "delete-target.py" ]]; then
                        echo "      🗑️  Deleting target $target_id using Python script..."
                        python3 delete-target.py --gateway-id "$gateway_id" --target-id "$target_id" --force 2>/dev/null || echo "      ⚠️  Failed to delete target $target_id"
                    else
                        echo "      🗑️  Deleting target $target_id using AWS CLI..."
                        aws bedrock-agentcore-control delete-gateway-target \
                            --gateway-identifier "$gateway_id" \
                            --target-identifier "$target_id" \
                            --region "$REGION" 2>/dev/null || echo "      ⚠️  Failed to delete target $target_id"
                    fi
                done
            else
                echo "   ✅ No targets found for gateway $gateway_name"
            fi
        else
            echo "   ⚠️  Could not list targets for gateway $gateway_name"
        fi
    else
        echo "   📋 Listing targets using AWS CLI..."
        TARGETS_JSON=$(aws bedrock-agentcore-control list-gateway-targets \
            --gateway-identifier "$gateway_id" \
            --region "$REGION" \
            --output json 2>/dev/null || echo "{}")
        
        TARGET_IDS=$(echo "$TARGETS_JSON" | jq -r '.items[]?.targetId // empty' 2>/dev/null || echo "")
        
        if [[ -n "$TARGET_IDS" ]]; then
            echo "   🗑️  Found targets to delete:"
            for target_id in $TARGET_IDS; do
                echo "      • $target_id"
                echo "      🗑️  Deleting target $target_id..."
                aws bedrock-agentcore-control delete-gateway-target \
                    --gateway-identifier "$gateway_id" \
                    --target-identifier "$target_id" \
                    --region "$REGION" 2>/dev/null || echo "      ⚠️  Failed to delete target $target_id"
            done
        else
            echo "   ✅ No targets found for gateway $gateway_name"
        fi
    fi
}

# Function to delete specific configured gateway
delete_configured_gateway() {
    local gateway_id="$1"
    echo "🏗️  Deleting configured gateway: $gateway_id"
    
    # Delete targets first
    delete_gateway_targets "$gateway_id" "configured-gateway"
    
    # Delete gateway
    echo "   🗑️  Deleting gateway $gateway_id..."
    if [[ -f "delete-gateway.py" ]]; then
        python3 delete-gateway.py --gateway-id "$gateway_id" --force 2>/dev/null || echo "   ⚠️  Failed to delete gateway $gateway_id"
    else
        aws bedrock-agentcore-control delete-gateway \
            --gateway-identifier "$gateway_id" \
            --region "$REGION" 2>/dev/null || echo "   ⚠️  Failed to delete gateway $gateway_id"
    fi
    
    echo "✅ Configured gateway deletion completed"
}

# Function to delete all gateways
delete_all_gateways() {
    echo "🏗️  Deleting all gateways..."
    
    # Try to use Python script first, fallback to AWS CLI
    if [[ -f "list-gateways.py" ]]; then
        echo "📋 Listing gateways using Python script..."
        GATEWAYS_RESPONSE=$(python3 list-gateways.py 2>/dev/null || echo "")
        
        if [[ -n "$GATEWAYS_RESPONSE" ]]; then
            echo "$GATEWAYS_RESPONSE"
            
            # Extract gateway IDs and names (this is a simple approach, might need adjustment)
            GATEWAY_INFO=$(echo "$GATEWAYS_RESPONSE" | grep -E "Gateway ID:|Name:" | paste - - | sed 's/.*Gateway ID: *\([^[:space:]]*\).*Name: *\([^[:space:]]*\).*/\1 \2/' || echo "")
            
            if [[ -n "$GATEWAY_INFO" ]]; then
                echo ""
                echo "🗑️  Found gateways to delete:"
                while read -r gateway_id gateway_name; do
                    if [[ -n "$gateway_id" && "$gateway_id" != "null" ]]; then
                        echo "   • $gateway_name ($gateway_id)"
                        
                        # Delete targets first
                        delete_gateway_targets "$gateway_id" "$gateway_name"
                        
                        # Delete gateway
                        echo "   🗑️  Deleting gateway $gateway_name ($gateway_id)..."
                        if [[ -f "delete-gateway.py" ]]; then
                            python3 delete-gateway.py --gateway-id "$gateway_id" --force 2>/dev/null || echo "   ⚠️  Failed to delete gateway $gateway_id"
                        else
                            aws bedrock-agentcore-control delete-gateway \
                                --gateway-identifier "$gateway_id" \
                                --region "$REGION" 2>/dev/null || echo "   ⚠️  Failed to delete gateway $gateway_id"
                        fi
                    fi
                done <<< "$GATEWAY_INFO"
            else
                echo "✅ No gateways found to delete"
            fi
        else
            echo "⚠️  Could not list gateways using Python script, trying AWS CLI..."
        fi
    fi
    
    # Fallback to AWS CLI if Python script didn't work
    if [[ -z "$GATEWAYS_RESPONSE" ]] || [[ "$GATEWAYS_RESPONSE" == "" ]]; then
        echo "📋 Listing gateways using AWS CLI..."
        GATEWAYS_JSON=$(aws bedrock-agentcore-control list-gateways \
            --region "$REGION" \
            --output json 2>/dev/null || echo "{}")
        
        GATEWAY_IDS=$(echo "$GATEWAYS_JSON" | jq -r '.items[]?.gatewayId // empty' 2>/dev/null || echo "")
        
        if [[ -n "$GATEWAY_IDS" ]]; then
            echo ""
            echo "🗑️  Found gateways to delete:"
            for gateway_id in $GATEWAY_IDS; do
                # Get gateway name
                GATEWAY_NAME=$(echo "$GATEWAYS_JSON" | jq -r ".items[] | select(.gatewayId == \"$gateway_id\") | .name // \"Unknown\"" 2>/dev/null || echo "Unknown")
                echo "   • $GATEWAY_NAME ($gateway_id)"
                
                # Delete targets first
                delete_gateway_targets "$gateway_id" "$GATEWAY_NAME"
                
                # Delete gateway
                echo "   🗑️  Deleting gateway $GATEWAY_NAME ($gateway_id)..."
                aws bedrock-agentcore-control delete-gateway \
                    --gateway-identifier "$gateway_id" \
                    --region "$REGION" 2>/dev/null || echo "   ⚠️  Failed to delete gateway $gateway_id"
            done
        else
            echo "✅ No gateways found to delete"
        fi
    fi
}

# Main execution
if [[ -n "$CURRENT_GATEWAY_ID" && "$CURRENT_GATEWAY_ID" != "null" && "$CURRENT_GATEWAY_ID" != '""' ]]; then
    echo "🤔 Deletion Options:"
    echo "   1. Delete ONLY the configured gateway ($CURRENT_GATEWAY_ID)"
    echo "   2. Delete ALL gateways in the account"
    echo ""
    read -p "Choose option (1/2) or Cancel (N): " -n 1 -r
    echo ""
    
    if [[ $REPLY =~ ^[1]$ ]]; then
        echo ""
        echo "⚠️  WARNING: This will delete the configured gateway and its targets!"
        echo "   Gateway ID: $CURRENT_GATEWAY_ID"
        echo "   This action cannot be undone."
        echo ""
        read -p "Are you sure you want to continue? (y/N): " -n 1 -r
        echo ""
        
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo ""
            echo "🚀 Starting configured gateway deletion..."
            echo ""
            delete_configured_gateway "$CURRENT_GATEWAY_ID"
        else
            echo ""
            echo "❌ Deletion cancelled by user"
            echo ""
            exit 0
        fi
    elif [[ $REPLY =~ ^[2]$ ]]; then
        echo ""
        echo "⚠️  WARNING: This will delete ALL AgentCore Gateways and Targets in the account!"
        echo "   This action cannot be undone."
        echo ""
        read -p "Are you sure you want to continue? (y/N): " -n 1 -r
        echo ""
        
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo ""
            echo "🚀 Starting deletion of all gateways..."
            echo ""
            delete_all_gateways
        else
            echo ""
            echo "❌ Deletion cancelled by user"
            echo ""
            exit 0
        fi
    else
        echo ""
        echo "❌ Deletion cancelled by user"
        echo ""
        exit 0
    fi
else
    echo "⚠️  WARNING: This will delete ALL AgentCore Gateways and Targets!"
    echo "   No configured gateway found in dynamic config."
    echo "   This action cannot be undone."
    echo ""
    read -p "Are you sure you want to continue? (y/N): " -n 1 -r
    echo ""

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo ""
        echo "🚀 Starting deletion process..."
        echo ""
        
        delete_all_gateways
    else
        echo ""
        echo "❌ Deletion cancelled by user"
        echo ""
        exit 0
    fi
fi

# Cleanup function (called after successful deletion)
cleanup_dynamic_config() {
    echo ""
    echo "🧹 Cleaning up dynamic configuration..."
    
    # Clear gateway information from dynamic config
    DYNAMIC_CONFIG="${CONFIG_DIR}/dynamic-config.yaml"
    if command -v yq >/dev/null 2>&1; then
        yq eval ".gateway.arn = \"\"" -i "$DYNAMIC_CONFIG"
        yq eval ".gateway.id = \"\"" -i "$DYNAMIC_CONFIG"
        yq eval ".gateway.url = \"\"" -i "$DYNAMIC_CONFIG"
    else
        # Fallback: manual update using sed (handle double quotes in YAML)
        sed -i.bak 's|arn: ".*"|arn: ""|' "$DYNAMIC_CONFIG"
        sed -i.bak 's|id: ".*"|id: ""|' "$DYNAMIC_CONFIG"
        sed -i.bak 's|url: ".*"|url: ""|' "$DYNAMIC_CONFIG"
        rm -f "${DYNAMIC_CONFIG}.bak"
    fi
    
    echo "✅ Dynamic configuration cleared"
}

# Call cleanup and show completion message
cleanup_dynamic_config

echo ""
echo "🎉 Gateway and Target Deletion Complete!"
echo "======================================"
echo ""
echo "✅ AgentCore Gateway and Target deletion completed"
echo "✅ Dynamic configuration has been cleared"
echo ""
echo "📋 What was deleted:"
echo "   • Selected AgentCore Gateway(s)"
echo "   • All Gateway Targets"
echo "   • Gateway configuration from dynamic config"
echo ""
echo "💡 Note:"
echo "   • MCP Lambda function is still deployed"
echo "   • IAM roles are still available"
echo "   • OAuth provider is still configured"
echo ""
echo "🚀 To recreate gateways and targets:"
echo "   Run ./04-create-gateway-targets.sh"
echo ""

# Return to original directory
cd "${SCRIPT_DIR}"
