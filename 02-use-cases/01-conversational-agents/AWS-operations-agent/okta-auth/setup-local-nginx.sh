#!/bin/bash
# Bedrock AgentCore Gateway - Okta PKCE Local Development Setup

PROJECT_DIR="<project directory path>"

echo "🚀 Setting up Bedrock AgentCore Gateway Okta PKCE Local Development Environment"

# Check for nginx
if ! command -v nginx &> /dev/null; then
    echo "❌ nginx not found. Please install nginx first."
    echo "   macOS: brew install nginx"
    echo "   Ubuntu: sudo apt-get install nginx"
    exit 1
fi

# Check if nginx is running
if ! pgrep nginx > /dev/null; then
    echo "⚠️ nginx is not running. Starting nginx..."
    sudo nginx
fi

# Start nginx with our configuration
echo "🔄 Starting nginx with Okta PKCE configuration..."
sudo nginx -c "$PROJECT_DIR/nginx/okta-local.conf"

# Check if nginx started successfully
if [ $? -eq 0 ]; then
    echo "✅ nginx started successfully with Okta PKCE configuration"
    echo "🌐 Open http://localhost:8080/okta-auth/ in your browser"
else
    echo "❌ Failed to start nginx with Okta PKCE configuration"
    exit 1
fi

echo ""
echo "🔧 Next steps:"
echo "1. Update iframe-oauth-flow.html with your Okta settings:"
echo "   - domain: YOUR_OKTA_DOMAIN.okta.com"
echo "   - clientId: YOUR_CLIENT_ID"
echo "   - bedrock_agentcore URLs: YOUR_GATEWAY_ID and YOUR_REGION"
echo ""
echo "2. Update Okta redirect URIs to include:"
echo "   - http://localhost:8080/okta-auth/"
echo ""
echo "3. To stop nginx when done:"
echo "   sudo nginx -s stop"
echo ""
echo "4. To view nginx logs:"
echo "   tail -f /usr/local/var/log/nginx/error.log"
echo ""
