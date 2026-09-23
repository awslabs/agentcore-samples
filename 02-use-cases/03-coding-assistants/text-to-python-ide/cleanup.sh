#!/bin/bash

# Cleanup script for AgentCore Code Interpreter
# Stops processes, deletes AWS resources, and removes all local files

echo "🧹 Cleaning up AgentCore Code Interpreter..."
echo ""

# Stop running processes
echo "⏹  Stopping running processes..."
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
lsof -ti:3000 | xargs kill -9 2>/dev/null || true

# ── Delete AWS resources ──
echo ""
echo "☁️  Tearing down AWS resources..."
echo "------------------------------------------------------------"

# Load .env for AWS_PROFILE and AWS_REGION
if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^$' | grep '=' | xargs)
fi

# Use project venv if available, otherwise create a temp one
if [ -d "venv" ] && [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ -d "/tmp/cleanup_venv" ]; then
    source /tmp/cleanup_venv/bin/activate
else
    python3 -m venv /tmp/cleanup_venv
    source /tmp/cleanup_venv/bin/activate
    pip install boto3 bedrock-agentcore -q
fi

# Delete all AWS resources — discovers by name if JSON files are missing
python3 -c "
import boto3, json, os

profile = os.getenv('AWS_PROFILE', 'default')
region = os.getenv('AWS_REGION', 'us-east-1')
session = boto3.Session(profile_name=profile, region_name=region)
cp = session.client('bedrock-agentcore-control')

# 1. Delete Runtime (from JSON or by listing)
runtime_id = ''
if os.path.exists('runtime_info.json'):
    with open('runtime_info.json') as f:
        runtime_id = json.load(f).get('runtime_id', '')
if not runtime_id:
    for rt in cp.list_agent_runtimes().get('agentRuntimes', []):
        if rt.get('agentRuntimeName') == 'text_to_python_ide':
            runtime_id = rt['agentRuntimeId']
            break

if runtime_id:
    print(f'🗑️  Deleting AgentCore Runtime ({runtime_id})...')
    try:
        cp.delete_agent_runtime(agentRuntimeId=runtime_id)
        print('   ✅ Runtime deletion initiated')
    except Exception as e:
        if 'ResourceNotFoundException' in str(e):
            print('   ✅ Runtime already deleted')
        else:
            print(f'   ⚠️  Runtime delete failed: {e}')
else:
    print('   ℹ️  No runtime found to delete')

# 2. Delete Memory (from JSON or by listing)
memory_id = ''
if os.path.exists('memory_info.json'):
    with open('memory_info.json') as f:
        memory_id = json.load(f).get('memory_id', '')
if not memory_id:
    for m in cp.list_memories().get('memories', []):
        mid = m.get('id', '')
        if 'text_to_python_ide' in mid:
            memory_id = mid
            break

if memory_id:
    print(f'🗑️  Deleting AgentCore Memory ({memory_id})...')
    try:
        cp.delete_memory(memoryId=memory_id)
        print('   ✅ Memory deletion initiated')
    except Exception as e:
        if 'ResourceNotFoundException' in str(e):
            print('   ✅ Memory already deleted')
        else:
            print(f'   ⚠️  Memory delete failed: {e}')
else:
    print('   ℹ️  No memory found to delete')

# 3. Delete Guardrail (from JSON or by listing)
guardrail_id = ''
if os.path.exists('guardrail_info.json'):
    with open('guardrail_info.json') as f:
        guardrail_id = json.load(f).get('guardrail_id', '')
if not guardrail_id:
    bedrock = session.client('bedrock', region_name=region)
    for g in bedrock.list_guardrails().get('guardrails', []):
        if g.get('name') == 'text_to_python_ide_guardrail':
            guardrail_id = g['id']
            break

if guardrail_id:
    print(f'🗑️  Deleting Bedrock Guardrail ({guardrail_id})...')
    try:
        bedrock = session.client('bedrock', region_name=region)
        bedrock.delete_guardrail(guardrailIdentifier=guardrail_id)
        print('   ✅ Guardrail deleted')
    except Exception as e:
        if 'ResourceNotFoundException' in str(e):
            print('   ✅ Guardrail already deleted')
        else:
            print(f'   ⚠️  Guardrail delete failed: {e}')
else:
    print('   ℹ️  No guardrail found to delete')

# 4. Delete ECR Repository
print('🗑️  Deleting ECR repository...')
try:
    ecr = session.client('ecr')
    ecr.delete_repository(repositoryName='bedrock-agentcore-text-to-python-ide', force=True)
    print('   ✅ ECR repo deleted')
except Exception as e:
    if 'RepositoryNotFoundException' in str(e):
        print('   ✅ ECR repo already deleted')
    else:
        print(f'   ⚠️  ECR delete failed: {e}')

# 5. Delete IAM Role
print('🗑️  Deleting IAM role...')
try:
    iam = session.client('iam')
    role_name = 'AgentCoreTextToPythonIDERole'
    attached = iam.list_attached_role_policies(RoleName=role_name).get('AttachedPolicies', [])
    for p in attached:
        iam.detach_role_policy(RoleName=role_name, PolicyArn=p['PolicyArn'])
    iam.delete_role(RoleName=role_name)
    print('   ✅ IAM role deleted')
except Exception as e:
    if 'NoSuchEntity' in str(e):
        print('   ✅ IAM role already deleted')
    else:
        print(f'   ⚠️  IAM role delete failed: {e}')

print('')
print('✅ All AWS resources cleaned up')
"

deactivate
rm -rf /tmp/cleanup_venv

echo "------------------------------------------------------------"

# ── Local file cleanup ──
echo "📄 Cleaning up log files..."
rm -f backend.log frontend.log agentcore_runtime.log *.pid

echo "🗑  Cleaning up temporary files..."
find . -name "*.pyc" -delete 2>/dev/null
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true

echo "🌐 Cleaning up frontend build files..."
rm -rf frontend/build
rm -rf frontend/.eslintcache

echo "📦 Cleaning up node_modules..."
rm -rf frontend/node_modules

echo "🐍 Cleaning up virtual environment..."
rm -rf venv

echo "📝 Removing generated config files..."
rm -f guardrail_info.json memory_info.json runtime_info.json
rm -f .env

echo ""
echo "✅ Cleanup completed!"
echo ""
echo "To restart from scratch:"
echo "  ./start.sh"
