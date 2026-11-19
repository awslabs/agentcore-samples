#!/usr/bin/env python3
"""
Deploy AgentCore Runtime
Uses AgentCore SDK - no Dockerfile or config file needed!
"""
from bedrock_agentcore_starter_toolkit import Runtime
from boto3.session import Session
import os
import time
import sys

def deploy_runtime(region=None):
    """Deploy agent to AgentCore Runtime using SDK"""
    print("="*60)
    print("Deploying AgentCore Runtime")
    print("="*60)
    
    # Change to parent directory where main.py is located
    from pathlib import Path
    parent_dir = Path(__file__).parent.parent
    os.chdir(parent_dir)
    print(f"Working directory: {os.getcwd()}")
    
    # Force SDK to use default profile by setting environment variable
    os.environ['AWS_PROFILE'] = 'default'
    
    # Use provided region or fall back to session region
    if region is None:
        boto_session = Session(profile_name='default')
        region = boto_session.region_name or "us-west-2"
        print(f"⚠️  No region specified, using: {region}")
    
    # Clean up old config files that might reference deleted agents
    import shutil
    from pathlib import Path
    
    config_files = [
        '.bedrock_agentcore.yaml',
        '.agentcore_config.yaml',
        'agent.config',
        '.agent_config'
    ]
    
    for config_file in config_files:
        config_path = Path(config_file)
        if config_path.exists():
            print(f"Removing old config: {config_file}")
            config_path.unlink()
    
    # Also clean hidden directory
    hidden_dir = Path('.bedrock_agentcore')
    if hidden_dir.exists():
        print(f"Removing old config directory: {hidden_dir}")
        shutil.rmtree(hidden_dir)
    
    # Initialize Runtime
    agentcore_runtime = Runtime()
    agent_name = "employee_commute_advisor"
    
    print(f"\nConfiguring Runtime for agent: {agent_name}")
    print(f"Region: {region}")
    
    # Configure (generates Dockerfile automatically)
    response = agentcore_runtime.configure(
        entrypoint="main.py",
        auto_create_execution_role=True,
        auto_create_ecr=True,
        requirements_file="requirements.txt",
        region=region,
        agent_name=agent_name
    )
    
    print("✅ Configuration complete")
    print(f"   Entry point: main.py")
    print(f"   Execution role: Auto-created")
    print(f"   ECR: Auto-created")
    
    # Launch to AgentCore
    print("\nLaunching to AgentCore Runtime...")
    print("(This may take a few minutes to build and deploy)")
    
    launch_result = agentcore_runtime.launch(auto_update_on_conflict=True)
    
    print("✅ Launch initiated")
    
    # Wait for deployment
    print("\nWaiting for deployment to complete...")
    status_response = agentcore_runtime.status()
    status = status_response.endpoint['status']
    end_status = ['READY', 'CREATE_FAILED', 'DELETE_FAILED', 'UPDATE_FAILED']
    
    while status not in end_status:
        print(f"   Status: {status}")
        time.sleep(10)
        status_response = agentcore_runtime.status()
        status = status_response.endpoint['status']
    
    if status == 'READY':
        print("\n" + "="*60)
        print("✅ RUNTIME DEPLOYED SUCCESSFULLY")
        print("="*60)
        print(f"\nAgent ARN: {launch_result.agent_arn}")
        print(f"Agent ID: {launch_result.agent_id}")
        print(f"ECR URI: {launch_result.ecr_uri}")
        
        # Add SSM permissions to the Runtime execution role
        import boto3
        import json
        
        print("\nAdding SSM permissions to Runtime execution role...")
        try:
            iam = boto3.Session(profile_name='default').client('iam', region_name=region)
            
            # Get the execution role name from the agent ARN
            # The role name follows pattern: AmazonBedrockAgentCoreSDKRuntime-{region}-{hash}
            runtime_id = launch_result.agent_id
            
            # List roles and find the one for this runtime
            roles = iam.list_roles()
            runtime_role_name = None
            
            for role in roles['Roles']:
                role_name = role['RoleName']
                # Match pattern: AmazonBedrockAgentCoreSDKRuntime-{region}-*
                if f'AmazonBedrockAgentCoreSDKRuntime-{region}' in role_name:
                    runtime_role_name = role_name
                    print(f"Found Runtime role: {runtime_role_name}")
                    break
            
            if runtime_role_name:
                # Get AWS account ID for precise resource ARNs
                sts = boto3.Session(profile_name='default').client('sts', region_name=region)
                account_id = sts.get_caller_identity()['Account']
                
                # Create inline policy for SSM and Secrets Manager access
                runtime_policy = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ssm:GetParameter",
                                "ssm:GetParameters"
                            ],
                            "Resource": f"arn:aws:ssm:{region}:{account_id}:parameter/app/employee-commute-advisor/*"
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "secretsmanager:GetSecretValue"
                            ],
                            "Resource": [
                                f"arn:aws:secretsmanager:{region}:{account_id}:secret:employee-commute-*",
                                f"arn:aws:secretsmanager:{region}:{account_id}:secret:bedrock-agentcore-identity!default/oauth2/employee-commute-cognito-provider-*"
                            ]
                        }
                    ]
                }
                
                iam.put_role_policy(
                    RoleName=runtime_role_name,
                    PolicyName='EmployeeCommuteRuntimeAccess',
                    PolicyDocument=json.dumps(runtime_policy)
                )
                print("✅ Added SSM and Secrets Manager permissions to Runtime role")
            else:
                print("⚠️  Could not find Runtime execution role")
                print("   You may need to add SSM permissions manually")
                
        except Exception as e:
            print(f"⚠️  Error adding SSM permissions: {e}")
            print("   Runtime will work but may need SSM permissions added manually")
        
        # Save runtime details to SSM for invoker to use
        ssm = boto3.Session(profile_name='default').client('ssm', region_name=region)
        
        try:
            ssm.put_parameter(
                Name='/app/employee-commute-advisor/agentcore/runtime_id',
                Value=launch_result.agent_id,
                Type='String',
                Overwrite=True
            )
            ssm.put_parameter(
                Name='/app/employee-commute-advisor/agentcore/runtime_arn',
                Value=launch_result.agent_arn,
                Type='String',
                Overwrite=True
            )
            print("✅ Saved runtime details to SSM")
        except Exception as e:
            print(f"⚠️  Could not save to SSM: {e}")
        
        return True
    else:
        print(f"\n❌ Deployment failed with status: {status}")
        return False

if __name__ == "__main__":
    # Get region from command line argument
    region = None
    if len(sys.argv) > 1:
        region = sys.argv[1]
        print(f"Using region from argument: {region}")
    
    success = deploy_runtime(region)
    sys.exit(0 if success else 1)
