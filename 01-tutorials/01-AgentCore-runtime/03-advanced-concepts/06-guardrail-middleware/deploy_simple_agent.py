#!/usr/bin/env python3
"""
Deploy Simple Agent with Dynamic Guardrail Creation
Simplified and optimized version
"""

import boto3
import json
import os
import time
from bedrock_agentcore_starter_toolkit import Runtime

# Initialize AWS clients
bedrock = boto3.client('bedrock')
ssm = boto3.client('ssm')
iam = boto3.client('iam')

def create_or_get_guardrail():
    """Create or get existing guardrail from SSM Parameter Store"""
    
    # Check if guardrail ID already exists in Parameter Store
    try:
        response = ssm.get_parameter(Name='/simple_agent/guardrail_id')
        guardrail_id = response['Parameter']['Value']
        print(f"✅ Found existing guardrail in Parameter Store: {guardrail_id}")
        
        # Verify it still exists
        try:
            bedrock.get_guardrail(guardrailIdentifier=guardrail_id)
            print(f"✅ Guardrail {guardrail_id} is valid and active")
            return guardrail_id
        except:
            print(f"⚠️ Guardrail {guardrail_id} no longer exists, creating new one...")
    except:
        print("🛡️ No existing guardrail found, creating new one...")
    
    # Create new guardrail
    try:
        response = bedrock.create_guardrail(
            name='simple-agent-guardrail',
            description='Guardrail for Simple Agent middleware demo',
            topicPolicyConfig={
                'topicsConfig': [
                    {
                        'name': 'Harmful Content',
                        'definition': 'Content that promotes harm, hate, or violence',
                        'examples': [
                            'I hate you',
                            'Violence against others',
                            'Harmful instructions'
                        ],
                        'type': 'DENY'
                    }
                ]
            },
            contentPolicyConfig={
                'filtersConfig': [
                    {'type': 'HATE', 'inputStrength': 'HIGH', 'outputStrength': 'HIGH'},
                    {'type': 'INSULTS', 'inputStrength': 'HIGH', 'outputStrength': 'HIGH'},
                    {'type': 'SEXUAL', 'inputStrength': 'HIGH', 'outputStrength': 'HIGH'},
                    {'type': 'VIOLENCE', 'inputStrength': 'HIGH', 'outputStrength': 'HIGH'},
                    {'type': 'MISCONDUCT', 'inputStrength': 'HIGH', 'outputStrength': 'HIGH'},
                    {'type': 'PROMPT_ATTACK', 'inputStrength': 'HIGH', 'outputStrength': 'NONE'}
                ]
            },
            blockedInputMessaging="Your message was blocked due to policy violations.",
            blockedOutputsMessaging="The response was blocked due to policy violations."
        )
        
        guardrail_id = response['guardrailId']
        guardrail_version = response['version']
        
        print(f"✅ Created guardrail: {guardrail_id} (version: {guardrail_version})")
        
        # Store in Parameter Store for future use
        ssm.put_parameter(
            Name='/simple_agent/guardrail_id',
            Value=guardrail_id,
            Type='String',
            Description='Guardrail ID for simple agent middleware',
            Overwrite=True
        )
        
        print("⏳ Waiting 5 seconds for guardrail to be ready...")
        time.sleep(5)
        
        return guardrail_id
        
    except Exception as e:
        print(f"❌ Failed to create guardrail: {e}")
        raise


def add_bedrock_permissions_to_role(role_name):
    """Add Bedrock Guardrail permissions to the execution role"""
    
    print(f"🔐 Adding Bedrock Guardrail permissions to role: {role_name}")
    
    # Define the policy for Bedrock Guardrail operations
    guardrail_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:ApplyGuardrail",
                    "bedrock:GetGuardrail"
                ],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ssm:GetParameter",
                    "ssm:GetParameters"
                ],
                "Resource": [
                    "arn:aws:ssm:*:*:parameter/simple_agent/*",
                    "arn:aws:ssm:*:*:parameter/simple_agent/tavily_api_key"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "kms:Decrypt"
                ],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "kms:ViaService": "ssm.*.amazonaws.com"
                    }
                }
            }
        ]
    }
    
    try:
        # Add the inline policy to the role
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName='BedrockGuardrailAndSSMAccess',
            PolicyDocument=json.dumps(guardrail_policy)
        )
        print(f"✅ Successfully added Bedrock and SSM permissions to role: {role_name}")
        return True
    except Exception as e:
        print(f"❌ Failed to add permissions to role {role_name}: {e}")
        return False

def save_tavily_api_key():
    """Save Tavily API key to SSM Parameter Store if available"""
    
    # Check for Tavily API key in environment
    tavily_api_key = os.environ.get('TAVILY_API_KEY')
    
    if tavily_api_key:
        print("🔑 Saving Tavily API key to SSM Parameter Store...")
        try:
            ssm.put_parameter(
                Name='/simple_agent/tavily_api_key',
                Value=tavily_api_key,
                Type='SecureString',
                Description='Tavily API key for web search functionality',
                Overwrite=True
            )
            print("✅ Tavily API key saved to SSM Parameter Store")
            return True
        except Exception as e:
            print(f"⚠️ Could not save Tavily API key to SSM: {e}")
            return False
    else:
        print("ℹ️ No TAVILY_API_KEY found in environment")
        print("   To enable web search, set: export TAVILY_API_KEY=your-api-key")
        return False

def deploy_simple_agent():
    """Deploy the simple agent with guardrail middleware"""
    
    print("🚀 Deploying Simple Agent with Guardrail Middleware")
    print("=" * 60)
    
    # Check required files
    if not os.path.exists('simple_agent.py'):
        print("❌ Required file missing: simple_agent.py")
        return None
    
    if not os.path.exists('simple_requirements.txt'):
        print("❌ Required file missing: simple_requirements.txt")
        return None
    
    print("✅ All required files found\n")
    
    # Step 1: Create or get guardrail and store in SSM
    guardrail_id = create_or_get_guardrail()
    print(f"📋 Guardrail ID: {guardrail_id}\n")
    
    # Step 2: Save Tavily API key if available
    save_tavily_api_key()
    print()
    
    # Step 3: Configure and launch the runtime (simple_agent.py already reads from SSM)
    print("📝 Note: simple_agent.py will read guardrail ID and Tavily API key from SSM at runtime")
    
    print("\n⚙️ Initializing AgentCore Runtime...")
    agentcore_runtime = Runtime()
    
    print("⚙️ Configuring runtime...")
    agentcore_runtime.configure(
        entrypoint="simple_agent.py",
        requirements_file="simple_requirements.txt",
        auto_create_execution_role=True
    )
    
    print("\n🚀 Launching agent runtime...")
    print("This may take a few minutes...\n")
    
    launch_result = agentcore_runtime.launch(auto_update_on_conflict=True)
    
    print("✅ Launch completed!\n")
    
    # Step 4: Add proper IAM permissions to the execution role
    print("🔐 Setting up IAM permissions...")
    
    # Find the execution role that was created
    execution_role_name = None
    
    # Try to get it from launch result first
    if hasattr(launch_result, 'execution_role'):
        execution_role_arn = launch_result.execution_role
        if execution_role_arn:
            execution_role_name = execution_role_arn.split('/')[-1]
            print(f"📍 Got execution role from launch result: {execution_role_name}")
    
    # If not found, search for the most recently created AmazonBedrockAgentCoreSDKRuntime role
    if not execution_role_name:
        try:
            print("🔍 Searching for the most recently created execution role...")
            paginator = iam.get_paginator('list_roles')
            
            # Collect all matching roles with their creation dates
            matching_roles = []
            for page in paginator.paginate():
                for role in page['Roles']:
                    role_name = role['RoleName']
                    # Look for the SDK-created runtime role
                    if 'AmazonBedrockAgentCoreSDKRuntime-us-west-2' in role_name:
                        matching_roles.append({
                            'name': role_name,
                            'created': role.get('CreateDate')
                        })
            
            # Sort by creation date and get the most recent
            if matching_roles:
                matching_roles.sort(key=lambda x: x['created'] if x['created'] else '', reverse=True)
                execution_role_name = matching_roles[0]['name']
                print(f"🔍 Found most recent execution role: {execution_role_name}")
                print(f"   Created at: {matching_roles[0]['created']}")
                
                # Show all found roles for debugging
                if len(matching_roles) > 1:
                    print(f"   (Found {len(matching_roles)} total AmazonBedrockAgentCoreSDKRuntime roles)")
                    
        except Exception as e:
            print(f"⚠️ Error finding execution role: {e}")
    
    # Add permissions to the role
    if execution_role_name:
        print(f"\n🎯 Updating IAM role: {execution_role_name}")
        success = add_bedrock_permissions_to_role(execution_role_name)
        if success:
            print("⏳ Waiting 20 seconds for IAM permissions to propagate...")
            time.sleep(20)
            print("✅ IAM permissions should now be active")
        else:
            print("❌ Failed to add IAM permissions automatically")
            print("   Please manually add the following permissions to the role:")
            print(f"   Role name: {execution_role_name}")
            print("   Required permissions:")
            print("   - bedrock:ApplyGuardrail")
            print("   - bedrock:GetGuardrail")
            print("   - ssm:GetParameter (for /simple_agent/* parameters)")
            print("   - kms:Decrypt (for SecureString parameters)")
    else:
        print("⚠️ Could not find execution role - you may need to add permissions manually")
        print("   Look for the most recent AmazonBedrockAgentCoreSDKRuntime-us-west-2-* role")
        print("   Add these permissions to your execution role:")
        print("   - bedrock:ApplyGuardrail")
        print("   - bedrock:GetGuardrail")
        print("   - ssm:GetParameter (for /simple_agent/* parameters)")
        print("   - kms:Decrypt (for SecureString parameters)")
    
    # Step 5: Store runtime configuration
    print("\n💾 Storing runtime configuration...")
    
    try:
        ssm.put_parameter(Name='/simple_agent/runtime/agent_arn', Value=launch_result.agent_arn, Type='String', Overwrite=True)
        ssm.put_parameter(Name='/simple_agent/runtime/agent_id', Value=launch_result.agent_id, Type='String', Overwrite=True)
        print("✅ Configuration stored")
    except Exception as e:
        print(f"⚠️ Could not store configuration: {e}")
    
    # Print summary
    print("\n" + "=" * 60)
    print("🎉 Deployment Completed Successfully!")
    print("=" * 60)
    print(f"📍 Agent ARN: {launch_result.agent_arn}")
    print(f"📍 Agent ID: {launch_result.agent_id}")
    print(f"🛡️ Guardrail ID: {guardrail_id}")
    print(f"📍 ECR URI: {launch_result.ecr_uri}")
    print("\n🏗️ Architecture:")
    print("   User/Web UI → AgentCore Runtime → CORS Middleware → Guardrail Middleware → Strands Agent")
    print("\n📝 Configuration stored in SSM Parameter Store:")
    print(f"   - /simple_agent/guardrail_id: {guardrail_id}")
    print(f"   - /simple_agent/runtime/agent_id: {launch_result.agent_id}")
    print(f"   - /simple_agent/runtime/agent_arn: {launch_result.agent_arn}")
    if os.environ.get('TAVILY_API_KEY'):
        print(f"   - /simple_agent/tavily_api_key: ****** (SecureString)")
    print("\n📖 Next steps:")
    print("   1. Test the agent: python test_simple_agent.py")
    print("   2. Launch UI: streamlit run app.py")
    print("   3. Cleanup when done: python cleanup_all.py")
    
    return launch_result

if __name__ == "__main__":
    try:
        # Clean up any leftover configuration files
        if os.path.exists('.bedrock_agentcore.yaml'):
            os.remove('.bedrock_agentcore.yaml')
            print("🧹 Cleaned up old configuration file\n")
        
        result = deploy_simple_agent()
        if result:
            print("\n✅ All done!")
        else:
            print("\n❌ Deployment failed")
            exit(1)
    except Exception as e:
        print(f"\n❌ Deployment failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
