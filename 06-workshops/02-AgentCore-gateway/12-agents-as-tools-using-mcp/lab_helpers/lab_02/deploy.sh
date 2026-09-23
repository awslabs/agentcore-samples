#!/bin/bash
# Lab 02: ONE-LINE Lambda Deployment
# Usage: bash lab_helpers/lab_02/deploy.sh
# That's it! Everything is automatic.

set -e

# Navigate to workshop root directory (parent of lab_helpers)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
WORKSHOP_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"
cd "$WORKSHOP_ROOT"

echo "🚀 Lab 02: Lambda ZIP Deployment (VPC-friendly)"
echo "📂 Working directory: $(pwd)"
echo ""

# Verify prerequisites
command -v python3 &> /dev/null || { echo "❌ Python3 required"; exit 1; }
command -v aws &> /dev/null || { echo "❌ AWS CLI required"; exit 1; }

echo "✓ Prerequisites OK"
echo ""

# Create Lambda role if it doesn't exist
ROLE_NAME="aiml301-diagnostic-lambda-role"
if ! aws iam get-role --role-name "$ROLE_NAME" 2>/dev/null; then
    echo "→ Creating IAM role..."
    if ! aws iam create-role --role-name "$ROLE_NAME" \
        --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' 2>&1; then
        echo "❌ Failed to create IAM role"
        exit 1
    fi
    
    echo "→ Attaching policies..."
    aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" || echo "⚠️  Warning: Failed to attach AWSLambdaBasicExecutionRole"
    aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "arn:aws:iam::aws:policy/AmazonBedrockFullAccess" || echo "⚠️  Warning: Failed to attach AmazonBedrockFullAccess"
    aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "arn:aws:iam::aws:policy/AmazonEC2ReadOnlyAccess" || echo "⚠️  Warning: Failed to attach AmazonEC2ReadOnlyAccess"
    aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "arn:aws:iam::aws:policy/CloudWatchReadOnlyAccess" || echo "⚠️  Warning: Failed to attach CloudWatchReadOnlyAccess"
    
    sleep 2
    echo "✓ IAM role created"
fi

# Get role ARN and save to Parameter Store (using Python to ensure consistency with constants.py)
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)

# Use Python to save role ARN via put_parameter helper (ensures consistency with constants.py)
python3 << PYTHON_SAVE_ROLE
import sys
sys.path.insert(0, '.')
from lab_helpers.constants import PARAMETER_PATHS
from lab_helpers.parameter_store import put_parameter
from lab_helpers.config import AWS_REGION

role_arn = """$ROLE_ARN"""
param_path = PARAMETER_PATHS['lab_02']['lambda_role_arn']

try:
    put_parameter(
        param_path,
        role_arn,
        description="Lambda execution role ARN for Lab 02 diagnostic agent",
        region_name=AWS_REGION
    )
    print(f"✓ Role ARN saved to Parameter Store: {param_path}")
except Exception as e:
    print(f"⚠ Warning: Could not save role ARN to Parameter Store: {e}")
PYTHON_SAVE_ROLE

# Wait for IAM and Parameter Store propagation
sleep 5

echo "→ Deploying Lambda..."
echo ""

# Export role ARN for Python to access
export LAMBDA_ROLE_ARN="$ROLE_ARN"

# Deploy using Python (one simple call)
python3 << 'EOF'
import sys
import os
sys.path.insert(0, '.')

from lab_helpers.lab_02.lambda_packager import setup_lambda_zip_deployment
from lab_helpers.config import AWS_REGION, AWS_PROFILE, MODEL_ID, WORKSHOP_NAME
from lab_helpers.parameter_store import get_parameter
from lab_helpers.lab_01.fault_injection import initialize_fault_injection


# Get role ARN from environment
role_arn = os.environ.get('LAMBDA_ROLE_ARN', '')
if not role_arn:
    print("❌ Error: LAMBDA_ROLE_ARN not set")
    sys.exit(1)

# Handler code
handler_code = '''
import asyncio
import json
import os

def lambda_handler(event, context):
    try:
        # Add lib/ to Python path to find pip-installed packages (relative to handler location)
        import sys
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        lib_path = os.path.join(current_dir, 'lib')
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)

        from strands import Agent, tool
        from lab_helpers import mock_data, diagnostic_tools
        from lab_helpers.lab_01.fault_injection import initialize_fault_injection
        from lab_helpers.config import AWS_REGION, AWS_PROFILE, MODEL_ID, WORKSHOP_NAME

        MODEL_ID = os.getenv("MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")

        # Initialize AWS clients and retrieve infrastructure resource IDs from SSM

        resources = initialize_fault_injection(AWS_REGION, AWS_PROFILE)
        nginx_instance_id = resources.get('nginx_instance_id')
        app_instance_id = resources.get('app_instance_id')
        metrics_table_name = resources.get('metrics_table_name')
        incidents_table_name = resources.get('incidents_table_name')
        crm_activities_table_name = resources.get('crm_activities_table_name')
        crm_customers_table_name = resources.get('crm_customers_table_name')
        crm_deals_table_name = resources.get('crm_deals_table_name')

        table_names = [key for key in resources.keys() if key.endswith('_table_name')]

        @tool(description="Fetch CRM application logs to identify application errors and issues")
        def get_crm_app_logs(limit: int = 10):
            """Fetch recent crm application logs"""
            crm_app_logs = diagnostic_tools.fetch_crm_app_logs()
            return crm_app_logs

        @tool(description="Fetch EC2 application logs to identify application errors and issues")
        def get_ec2_logs():
            """Fetch recent EC2 application logs"""
            ec2_logs = diagnostic_tools.fetch_ec2_logs()
            return ec2_logs


        @tool(description="Fetch NGINX error logs")
        def get_nginx_error_logs():
            """Fetch NGINX error logs"""
            nginx_error_logs = diagnostic_tools.fetch_nginx_error_logs()
            return nginx_error_logs


        @tool(description="Fetch NGINX access logs")
        def get_nginx_access_logs():
            """Fetch NGINX access/error logs"""
            nginx_access_logs = diagnostic_tools.fetch_nginx_access_logs()
            return nginx_access_logs


        @tool(description="Fetch DynamoDB metrics to detect throttling and service issues")
        def get_dynamodb_metrics():
            """Fetch DynamoDB operation metrics"""
            #ddb_metrics = diagnostic_tools.fetch_dynamodb_metrics(table_name=metrics_table_name) + diagnostic_tools.fetch_dynamodb_metrics(table_name=incidents_table_name)
            ddb_metrics=""
            for table in table_names:
                ddb_metrics+=str(fetch_dynamodb_metrics(table_name=table))
            return ddb_metrics


        @tool(description="Fetch CloudWatch metrics (CPU) to analyze resource utilization for an instance")
        def get_cloudwatch_cpu_metrics():
            """Fetch CloudWatch CPU metrics"""
            cpu_metrics=diagnostic_tools.get_cpu_metrics(instance_id=nginx_instance_id) + get_cpu_metrics(instance_id=app_instance_id)
            return cpu_metrics


        @tool(description="Fetch CloudWatch metrics (memory) to analyze resource utilization an instance")
        def get_cloudwatch_memory_metrics():
            """Fetch CloudWatch memory metrics"""
            memory_metrics=diagnostic_tools.get_memory_metrics(instance_id=nginx_instance_id) + get_memory_metrics(instance_id=app_instance_id)
            return memory_metrics
        
        agent = Agent(name="system_diagnostics_agent", model=MODEL_ID, tools=[get_crm_app_logs, get_ec2_logs, get_nginx_error_logs, get_nginx_access_logs, get_dynamodb_metrics, get_cloudwatch_cpu_metrics, get_cloudwatch_memory_metrics], system_prompt="""
    You are an expert system diagnostics agent. Your role is to analyze system logs and metrics to identify issues and their high level root causes(including AWS resources such as ARNs,IDs etc. causing them).

When diagnosing system issues:
1. Start by gathering relevant logs (EC2, NGINX, DynamoDB)
2. Check CloudWatch metrics to understand resource utilization patterns
3. Correlate findings across and provide a fairly detailed but consize assessment with severity
4. Once the analysis is complete, in the end share the data sources or points(EC2s, tables etc.), based on which these insights were generated. 
""")

        agent_input = event.get("query", "Analyze system logs for issues")
        try:
            request_id = context.client_context.custom.get("bedrockAgentCoreAwsRequestId", context.aws_request_id)
        except (AttributeError, TypeError):
            request_id = context.aws_request_id

        async def run_agent():
            return await agent.invoke_async(agent_input)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            agent_response = loop.run_until_complete(run_agent())
        finally:
            loop.close()

        return {"status": "success", "request_id": request_id, "agent_input": agent_input, "response": str(agent_response), "type": "strands_agent_response"}

    except Exception as e:
        import traceback
        return {"status": "error", "error_message": str(e), "traceback": traceback.format_exc(), "request_id": context.aws_request_id if context else "unknown"}
'''

# Dependencies
requirements = '''strands-agents==1.14.0
bedrock-agentcore>=0.1.0
bedrock-agentcore-starter-toolkit==0.1.28
boto3==1.40.65
botocore==1.40.65
pydantic>=2.0
requests>=2.30
requests-aws4auth>=1.2.3
'''

# Deploy
result = setup_lambda_zip_deployment(handler_code, requirements, region_name=AWS_REGION)

if result['status'] == 'success':
    lmb = result['lambda']
    print()
    print("=" * 70)
    print("✅ DEPLOYMENT SUCCESSFUL")
    print("=" * 70)
    print()
    print(f"Lambda: {lmb['function_name']}")
    print(f"ARN: {lmb['function_arn']}")
    print(f"State: {lmb['state']}")
    print()
    print("Next: Continue with Section 9 in the notebook")
    print("=" * 70)
else:
    print(f"❌ Deployment failed: {result.get('error')}")
    sys.exit(1)
EOF
