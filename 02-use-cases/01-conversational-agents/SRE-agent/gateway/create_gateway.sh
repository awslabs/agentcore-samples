#!/bin/bash

# DevOps Multi-Agent Demo Gateway Creation Script for Cognito
# Creates gateway with multiple OpenAPI targets for K8s, Logs, Metrics, and Runbooks APIs
# Uses allowedClients instead of allowedAudience for Cognito

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Function to generate UUID for bucket naming
_generate_bucket_name() {
    # Generate UUID and format as sreagent-{uuid}
    # Format complies with S3 naming restrictions:
    # - 3-63 characters
    # - lowercase letters, numbers, hyphens only
    # - starts and ends with letter/number
    if command -v uuidgen &> /dev/null; then
        # Use uuidgen if available (macOS, Linux with uuid-runtime)
        UUID=$(uuidgen | tr '[:upper:]' '[:lower:]')
    else
        # Fall back to Python for UUID generation
        UUID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
    fi
    echo "sreagent-${UUID}"
}

# Function to create S3 bucket if needed
_create_s3_bucket() {
    local bucket_name=$1
    local region=$2

    echo "🪣 Checking S3 bucket configuration..."

    # If bucket name is empty or contains placeholder text, create one
    if [ -z "$bucket_name" ] || [ "$bucket_name" = "your-agentcore-schemas-bucket" ] || [ "$bucket_name" = "your-bucket-name" ]; then
        echo "ℹ️  S3 bucket not configured, creating bucket automatically..."
        BUCKET_NAME=$(_generate_bucket_name)

        echo "🔨 Creating S3 bucket: ${BUCKET_NAME}"

        # Create bucket with region-appropriate command
        if [ "$region" = "us-east-1" ]; then
            # us-east-1 doesn't need LocationConstraint
            if aws s3api create-bucket \
                --bucket "${BUCKET_NAME}" \
                --region "${region}"; then
                echo "✅ S3 bucket created successfully: ${BUCKET_NAME}"
            else
                echo "❌ Failed to create S3 bucket"
                exit 1
            fi
        else
            # Other regions need LocationConstraint
            if aws s3api create-bucket \
                --bucket "${BUCKET_NAME}" \
                --region "${region}" \
                --create-bucket-configuration LocationConstraint="${region}"; then
                echo "✅ S3 bucket created successfully: ${BUCKET_NAME}"
            else
                echo "❌ Failed to create S3 bucket"
                exit 1
            fi
        fi

        echo "${BUCKET_NAME}"
    else
        echo "✅ Using configured S3 bucket: ${bucket_name}"
        echo "${bucket_name}"
    fi
}

# Check if config.yaml exists in the script directory
if [ ! -f "${SCRIPT_DIR}/config.yaml" ]; then
    echo "Error: config.yaml not found in ${SCRIPT_DIR}!"
    echo "Please create config.yaml from config.yaml.example and update with your values"
    exit 1
fi

# Function to read value from YAML
get_config() {
    local key=$1
    local line=$(grep "^${key}:" "${SCRIPT_DIR}/config.yaml" | cut -d':' -f2-)
    local result
    
    # Remove leading whitespace
    line=$(echo "$line" | sed 's/^[ \t]*//')
    
    # Handle quoted values - extract content between first pair of quotes, ignore comments after
    if echo "$line" | grep -q '^".*"'; then
        result=$(echo "$line" | sed 's/^"\([^"]*\)".*/\1/')
    else
        # Handle unquoted values - extract everything before comment or end of line, trim trailing whitespace
        result=$(echo "$line" | sed 's/[ \t]*#.*//' | sed 's/[ \t]*$//')
    fi
    
    # For critical AWS identifiers, remove all whitespace to prevent copy-paste errors
    case "$key" in
        account_id|role_name|user_pool_id|client_id|s3_bucket|credential_provider_name)
            result=$(echo "$result" | tr -d ' \t')
            ;;
    esac
    
    echo "$result"
}

# Read configuration from config.yaml
ACCOUNT_ID=$(get_config "account_id")
REGION=$(get_config "region")
ROLE_NAME=$(get_config "role_name")
ENDPOINT_URL=$(get_config "endpoint_url")
CREDENTIAL_PROVIDER_ENDPOINT_URL=$(get_config "credential_provider_endpoint_url")
USER_POOL_ID=$(get_config "user_pool_id")
CLIENT_ID=$(get_config "client_id")
S3_BUCKET=$(get_config "s3_bucket")
S3_PATH_PREFIX=$(get_config "s3_path_prefix")
PROVIDER_ARN=$(get_config "provider_arn")
GATEWAY_NAME=$(get_config "gateway_name")
GATEWAY_DESCRIPTION=$(get_config "gateway_description")
TARGET_DESCRIPTION=$(get_config "target_description")
CREDENTIAL_PROVIDER_NAME=$(get_config "credential_provider_name")

# Create S3 bucket if not configured, or use the configured one
S3_BUCKET=$(_create_s3_bucket "$S3_BUCKET" "$REGION" 2>/dev/null | tail -1)

# Extract Cognito region from User Pool ID (format: region_poolId)
COGNITO_REGION=$(echo "$USER_POOL_ID" | cut -d'_' -f1)

# Construct derived values using Cognito region instead of AWS region
DISCOVERY_URL="https://cognito-idp.${COGNITO_REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"

# Define API schema filenames
API_SCHEMAS=(
    "k8s_api.yaml"
    "logs_api.yaml"
    "metrics_api.yaml"
    "runbooks_api.yaml"
)

# Build S3 URIs dynamically from configuration
S3_URIS=()
for schema in "${API_SCHEMAS[@]}"; do
    S3_URIS+=("s3://${S3_BUCKET}/${S3_PATH_PREFIX}/${schema}")
done

# Define corresponding descriptions for each API
TARGET_DESCRIPTIONS=(
    "Kubernetes Analysis API for cluster monitoring and troubleshooting"
    "Application Logs API for log search and analysis"
    "Application Metrics API for performance monitoring"
    "DevOps Runbooks API for incident response and troubleshooting guides"
)

# Display configuration (with sensitive values partially hidden)
echo "Loaded configuration from config.yaml:"
echo "  Gateway Name: ${GATEWAY_NAME}"
echo "  Region: ${REGION}"
echo "  Account ID: ${ACCOUNT_ID:0:4}****"
echo "  S3 Bucket: ${S3_BUCKET} (auto-created if not configured)"
echo "  S3 Path Prefix: ${S3_PATH_PREFIX}"
echo "  Provider ARN: ${PROVIDER_ARN}"
echo ""

# Load environment variables from .env file
if [ -f "${SCRIPT_DIR}/.env" ]; then
    echo "📋 Loading environment variables from gateway/.env file..."
    # Source the .env file safely
    set -a  # automatically export all variables
    source "${SCRIPT_DIR}/.env"
    set +a  # stop automatically exporting
else
    echo "⚠️  No .env file found in gateway directory. Using default API key from config."
fi

# Create credential provider with parameters
echo "🔑 Creating API key credential provider..."

# Check if BACKEND_API_KEY is set
if [ -z "$BACKEND_API_KEY" ]; then
    echo "❌ Error: BACKEND_API_KEY not found in environment variables"
    echo "Please set BACKEND_API_KEY in your .env file"
    exit 1
fi

cd "${SCRIPT_DIR}"
if python create_credentials_provider.py \
    --credential-provider-name "${CREDENTIAL_PROVIDER_NAME}" \
    --api-key "${BACKEND_API_KEY}" \
    --region "${REGION}" \
    --endpoint-url "${CREDENTIAL_PROVIDER_ENDPOINT_URL}"; then
    echo "✅ Credential provider created successfully!"
    
    # Read the generated ARN from .credentials_provider file
    if [ -f "${SCRIPT_DIR}/.credentials_provider" ]; then
        GENERATED_PROVIDER_ARN=$(cat "${SCRIPT_DIR}/.credentials_provider")
        echo "📄 Using generated provider ARN: ${GENERATED_PROVIDER_ARN}"
        # Override the ARN from config with the generated one
        PROVIDER_ARN="${GENERATED_PROVIDER_ARN}"
    else
        echo "⚠️  Warning: .credentials_provider file not found, using ARN from config"
    fi
else
    echo "❌ Failed to create credential provider"
    exit 1
fi

echo ""

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI is not installed or not in PATH"
    echo "Please install AWS CLI to upload OpenAPI schema files to S3"
    exit 1
fi

# Upload OpenAPI schema files to S3
echo "📤 Uploading OpenAPI schema files to S3..."
OPENAPI_SPECS_DIR="${SCRIPT_DIR}/../backend/openapi_specs"

if [ ! -d "$OPENAPI_SPECS_DIR" ]; then
    echo "❌ OpenAPI specs directory not found: $OPENAPI_SPECS_DIR"
    exit 1
fi

# Upload each schema file
upload_success=true
for schema in "${API_SCHEMAS[@]}"; do
    local_file="${OPENAPI_SPECS_DIR}/${schema}"
    s3_key="${S3_PATH_PREFIX}/${schema}"
    
    if [ ! -f "$local_file" ]; then
        echo "❌ Schema file not found: $local_file"
        upload_success=false
        continue
    fi
    
    file_size=$(ls -lh "$local_file" | awk '{print $5}')
    echo "📁 Uploading ${schema} (${file_size}) to s3://${S3_BUCKET}/${s3_key}"
    
    # Upload with metadata and force overwrite
    if aws s3 cp "$local_file" "s3://${S3_BUCKET}/${s3_key}" \
        --region "${REGION}" \
        --metadata "source=sre-agent,timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --content-type "application/x-yaml"; then
        echo "✅ Successfully uploaded ${schema}"
    else
        echo "❌ Failed to upload ${schema}"
        upload_success=false
    fi
done

if [ "$upload_success" = false ]; then
    echo "❌ Some files failed to upload. Please check your AWS credentials and S3 bucket permissions."
    exit 1
fi

echo "✅ All OpenAPI schema files uploaded successfully!"
echo ""

# Generate Cognito access token
echo "Generating Cognito access token..."
echo "Make sure your .env file is configured with COGNITO_* variables"
cd "${SCRIPT_DIR}"
python generate_token.py

echo ""
# Build the command with multiple S3 URIs and descriptions
echo "Creating AgentCore Gateway with multiple S3 targets for DevOps Multi-Agent Demo..."
echo "APIs to be configured:"
for i in "${!S3_URIS[@]}"; do
    api_name=$(basename "${S3_URIS[$i]}" .yaml)
    echo "  $((i+1)). ${api_name^^} API: ${S3_URIS[$i]}"
done
echo ""

# Construct the command with all S3 URIs and descriptions
CMD=(python main.py "${GATEWAY_NAME}")
CMD+=(--region "${REGION}")
CMD+=(--endpoint-url "${ENDPOINT_URL}")
CMD+=(--role-arn "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}")
CMD+=(--discovery-url "${DISCOVERY_URL}")
CMD+=(--allowed-clients "${CLIENT_ID}")
CMD+=(--description-for-gateway "${GATEWAY_DESCRIPTION}")

# Add all S3 URIs
for s3_uri in "${S3_URIS[@]}"; do
    CMD+=(--s3-uri "${s3_uri}")
done

# Add all target descriptions
for description in "${TARGET_DESCRIPTIONS[@]}"; do
    CMD+=(--description-for-target "${description}")
done

# Add remaining flags
CMD+=(--create-s3-target)
CMD+=(--provider-arn "${PROVIDER_ARN}")
CMD+=(--save-gateway-url)
CMD+=(--delete-gateway-if-exists)
CMD+=(--output-json)

# Execute the command
echo "Executing command:"
echo "${CMD[@]}"
echo ""
cd "${SCRIPT_DIR}"
"${CMD[@]}"

echo ""
echo "📁 Access token saved to .access_token"
echo "🔗 Gateway URL saved to .gateway_uri"
echo "🎉 DevOps Multi-Agent Demo Gateway creation completed!"
echo ""
echo "📊 Summary:"
echo "   - OpenAPI schemas uploaded to S3: ${#API_SCHEMAS[@]} files"
echo "   - Gateway created with ${#S3_URIS[@]} API targets"
echo "   - APIs: Kubernetes, Logs, Metrics, Runbooks"
echo "   - All targets configured with Cognito authentication"
echo "   - Ready for MCP integration with AgentCore Gateway"