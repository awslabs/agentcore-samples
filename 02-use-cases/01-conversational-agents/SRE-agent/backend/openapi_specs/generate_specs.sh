#!/bin/bash

# Generate OpenAPI specification files from templates
# This script replaces template variables with actual values to create deployment-specific configs

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default backend domain (can be overridden by environment variable or config)
DEFAULT_BACKEND_DOMAIN="your-backend-domain.com"

# Try to get backend domain from environment variable first
BACKEND_DOMAIN="${BACKEND_DOMAIN:-$DEFAULT_BACKEND_DOMAIN}"

# Check if there's a .env file in the backend directory
ENV_FILE="$SCRIPT_DIR/../.env"
if [ -f "$ENV_FILE" ]; then
    echo "📋 Loading backend domain from $ENV_FILE..."
    # Source the .env file safely and extract BACKEND_DOMAIN
    BACKEND_DOMAIN_FROM_FILE=$(grep "^BACKEND_DOMAIN=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- | sed 's/^["'\'']//' | sed 's/["'\'']$//' || echo "")
    if [ -n "$BACKEND_DOMAIN_FROM_FILE" ]; then
        BACKEND_DOMAIN="$BACKEND_DOMAIN_FROM_FILE"
        echo "✅ Using backend domain from .env: $BACKEND_DOMAIN"
    fi
fi

# If still using default, check for gateway config
if [ "$BACKEND_DOMAIN" = "$DEFAULT_BACKEND_DOMAIN" ]; then
    GATEWAY_CONFIG="$SCRIPT_DIR/../../gateway/config.yaml"
    if [ -f "$GATEWAY_CONFIG" ]; then
        echo "📋 Checking gateway config for backend domain..."
        # Try to extract a domain from gateway config (this is optional)
        POTENTIAL_DOMAIN=$(grep "^gateway_name:" "$GATEWAY_CONFIG" 2>/dev/null | cut -d':' -f2- | sed 's/^ *"\?\(.*\)"\?$/\1/' | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g' | sed 's/^-\|-$//g' || echo "")
        if [ -n "$POTENTIAL_DOMAIN" ]; then
            BACKEND_DOMAIN="${POTENTIAL_DOMAIN}.example.com"
            echo "ℹ️  Generated domain from gateway name: $BACKEND_DOMAIN"
        fi
    fi
fi

echo ""
echo "🏗️  Generating OpenAPI specifications..."
echo "   Backend Domain: $BACKEND_DOMAIN"
echo ""

# List of template files to process
TEMPLATES=(
    "k8s_api.yaml.template"
    "logs_api.yaml.template" 
    "metrics_api.yaml.template"
    "runbooks_api.yaml.template"
)

# Process each template
for template in "${TEMPLATES[@]}"; do
    if [ ! -f "$SCRIPT_DIR/$template" ]; then
        echo "❌ Template file not found: $template"
        continue
    fi
    
    output_file="${template%.template}"
    echo "   📄 $template → $output_file"
    
    # Replace template variables with actual values
    sed "s|{{BACKEND_DOMAIN}}|$BACKEND_DOMAIN|g" "$SCRIPT_DIR/$template" > "$SCRIPT_DIR/$output_file"
done

echo ""
echo "✅ OpenAPI specifications generated successfully!"
echo ""
echo "💡 To customize the backend domain:"
echo "   export BACKEND_DOMAIN=your-actual-domain.com"
echo "   Or add BACKEND_DOMAIN=your-actual-domain.com to backend/.env"
echo ""
echo "📁 Generated files:"
for template in "${TEMPLATES[@]}"; do
    output_file="${template%.template}"
    if [ -f "$SCRIPT_DIR/$output_file" ]; then
        echo "   ✓ $output_file"
    fi
done 