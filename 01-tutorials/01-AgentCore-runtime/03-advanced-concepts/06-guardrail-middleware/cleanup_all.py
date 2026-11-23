#!/usr/bin/env python3
"""
Cleanup all resources for Simple Agent with Guardrail
Using the correct API from the notebook example
"""

import boto3
import time
import os

# Ensure we're using the correct region
AWS_REGION = os.environ.get('AWS_REGION', 'us-west-2')

def cleanup_all():
    """Clean up all resources"""
    
    print("🧹 Cleaning up all resources...")
    print(f"📍 Region: {AWS_REGION}")
    print("=" * 60)
    
    # Initialize clients with explicit region
    ssm = boto3.client('ssm', region_name=AWS_REGION)
    bedrock = boto3.client('bedrock', region_name=AWS_REGION)
    
    # Use bedrock-agentcore-control for runtime deletion (as per notebook)
    agentcore_control_client = boto3.client(
        'bedrock-agentcore-control',
        region_name=AWS_REGION
    )
    
    ecr_client = boto3.client('ecr', region_name=AWS_REGION)
    iam = boto3.client('iam')  # IAM is global
    
    # 1. Delete AgentCore Runtime Endpoints and Runtimes
    print("\n1. Finding and deleting AgentCore Runtime Endpoints and Runtimes...")
    
    # First check if we have any stored runtime IDs in SSM
    print("\n📍 Step 1a: Checking for stored runtime information...")
    stored_runtime_id = None
    
    try:
        response = ssm.get_parameter(Name='/simple_agent/runtime/agent_id')
        stored_runtime_id = response['Parameter']['Value']
        print(f"📋 Found stored runtime ID in Parameter Store: {stored_runtime_id}")
        
        # Try to check if this runtime still exists
        try:
            runtime_details = agentcore_control_client.get_agent_runtime(
                agentRuntimeId=stored_runtime_id
            )
            print(f"   ✅ Runtime still exists: {stored_runtime_id}")
        except Exception as e:
            if 'ResourceNotFoundException' in str(e):
                print(f"   ℹ️ Runtime no longer exists: {stored_runtime_id}")
                stored_runtime_id = None
            else:
                print(f"   ⚠️ Error checking runtime: {e}")
    except:
        print("ℹ️ No stored runtime ID found in Parameter Store")
    
    endpoints_deleted = 0
    
    # Note: list_agent_runtime_endpoints requires a runtime ID, so we can only check if we have runtimes
    print("\n📍 Step 1b: Checking for AgentCore Runtime Endpoints...")
    print("ℹ️ Note: Endpoint listing requires a runtime ID")
    
    # Now handle runtimes
    print("\n📍 Step 1b: Deleting AgentCore Runtimes...")
    runtime_deleted = False
    runtimes_to_delete = []
    
    try:
        # List all agent runtimes using the correct API
        print("🔍 Listing all agent runtimes...")
        
        # Initial request
        response = agentcore_control_client.list_agent_runtimes()
        # The response key can be 'agentRuntimeSummaries' or 'agentRuntimes' depending on the API version
        all_runtimes = response.get('agentRuntimeSummaries', response.get('agentRuntimes', []))
        
        # Handle pagination
        while 'nextToken' in response:
            response = agentcore_control_client.list_agent_runtimes(
                nextToken=response['nextToken']
            )
            all_runtimes.extend(response.get('agentRuntimeSummaries', response.get('agentRuntimes', [])))
        
        print(f"📋 Found {len(all_runtimes)} runtime(s) total")
        
        # Display all runtimes for debugging
        for runtime in all_runtimes:
            runtime_id = runtime.get('agentRuntimeId', '')
            runtime_name = runtime.get('agentRuntimeName', '')
            runtime_status = runtime.get('status', '')
            
            print(f"   Runtime: {runtime_id} (Name: {runtime_name}, Status: {runtime_status})")
            
            # Check if this is our runtime (be generous with matching)
            if ('simple' in runtime_id.lower() or 
                'simple' in runtime_name.lower() or
                runtime_id.lower().startswith('simple_agent') or
                'guardrail' in runtime_name.lower() or  # Also check for guardrail-related runtimes
                'middleware' in runtime_name.lower()):
                runtimes_to_delete.append(runtime)
        
        # Process runtimes for deletion
        for runtime in runtimes_to_delete:
            runtime_id = runtime.get('agentRuntimeId', '')
            runtime_name = runtime.get('agentRuntimeName', '')
            runtime_status = runtime.get('status', '')
            
            print(f"\n🎯 Processing runtime for deletion: {runtime_id}")
            print(f"   Name: {runtime_name}")
            print(f"   Status: {runtime_status}")
            
            # Get runtime details
            try:
                runtime_details = agentcore_control_client.get_agent_runtime(
                    agentRuntimeId=runtime_id
                )
                print(f"   Details retrieved successfully")
                
                # Check if runtime has endpoints that need deletion first
                try:
                    print(f"   🔍 Checking for endpoints...")
                    endpoint_response = agentcore_control_client.list_agent_runtime_endpoints(
                        agentRuntimeId=runtime_id
                    )
                    runtime_endpoints = endpoint_response.get('agentRuntimeEndpointSummaries', [])
                    
                    # Handle pagination for endpoints
                    while 'nextToken' in endpoint_response:
                        endpoint_response = agentcore_control_client.list_agent_runtime_endpoints(
                            agentRuntimeId=runtime_id,
                            nextToken=endpoint_response['nextToken']
                        )
                        runtime_endpoints.extend(endpoint_response.get('agentRuntimeEndpointSummaries', []))
                    
                    if runtime_endpoints:
                        print(f"   📍 Found {len(runtime_endpoints)} endpoint(s) for this runtime")
                        
                        for endpoint in runtime_endpoints:
                            endpoint_id = endpoint.get('agentRuntimeEndpointId', '')
                            endpoint_name = endpoint.get('agentRuntimeEndpointName', '')
                            
                            print(f"      Deleting endpoint: {endpoint_id} ({endpoint_name})")
                            
                            try:
                                # Get endpoint details first
                                endpoint_details = agentcore_control_client.get_agent_runtime_endpoint(
                                    agentRuntimeId=runtime_id,
                                    agentRuntimeEndpointId=endpoint_id
                                )
                                
                                # Delete the endpoint
                                agentcore_control_client.delete_agent_runtime_endpoint(
                                    agentRuntimeId=runtime_id,
                                    agentRuntimeEndpointId=endpoint_id
                                )
                                print(f"      ✅ Deleted endpoint: {endpoint_id}")
                                time.sleep(2)
                            except Exception as e:
                                print(f"      ⚠️ Could not delete endpoint {endpoint_id}: {e}")
                    else:
                        print(f"   ℹ️ No endpoints found for this runtime")
                
                except Exception as e:
                    print(f"   ℹ️ Could not check/delete endpoints: {e}")
                
            except Exception as e:
                print(f"   ⚠️ Could not get runtime details: {e}")
            
            # Now delete the runtime
            try:
                print(f"   🗑️ Attempting to delete runtime...")
                runtime_delete_response = agentcore_control_client.delete_agent_runtime(
                    agentRuntimeId=runtime_id
                )
                print(f"   ✅ Successfully deleted AgentCore Runtime: {runtime_id}")
                runtime_deleted = True
                time.sleep(5)  # Wait between runtime deletions
            except Exception as e:
                print(f"   ⚠️ Could not delete runtime {runtime_id}: {e}")
        
        if runtime_deleted:
            print("\n⏳ Waiting for runtime deletion to complete...")
            time.sleep(20)
        elif not runtimes_to_delete:
            print("\nℹ️ No matching agent runtimes found to delete")
            
    except Exception as e:
        print(f"⚠️ Error listing/deleting runtimes: {e}")
        
        # Fallback to Parameter Store method
        print("\n📍 Trying Parameter Store fallback method...")
        
        try:
            response = ssm.get_parameter(Name='/simple_agent/runtime/agent_id')
            agent_id = response['Parameter']['Value']
            print(f"📋 Found agent ID in Parameter Store: {agent_id}")
            
            # Try to get runtime details first
            try:
                runtime_details = agentcore_control_client.get_agent_runtime(
                    agentRuntimeId=agent_id
                )
                print(f"   Runtime exists with ID: {agent_id}")
                
                # Delete any endpoints first
                try:
                    endpoint_response = agentcore_control_client.list_agent_runtime_endpoints(
                        agentRuntimeId=agent_id
                    )
                    for endpoint in endpoint_response.get('agentRuntimeEndpointSummaries', []):
                        endpoint_id = endpoint.get('agentRuntimeEndpointId', '')
                        try:
                            agentcore_control_client.delete_agent_runtime_endpoint(
                                agentRuntimeId=agent_id,
                                agentRuntimeEndpointId=endpoint_id
                            )
                            print(f"   ✅ Deleted endpoint: {endpoint_id}")
                        except:
                            pass
                except:
                    pass
                
                # Delete the runtime
                runtime_delete_response = agentcore_control_client.delete_agent_runtime(
                    agentRuntimeId=agent_id
                )
                print(f"✅ Deleted AgentCore Runtime: {agent_id}")
                runtime_deleted = True
                print("⏳ Waiting for deletion to complete...")
                time.sleep(20)
            except Exception as e:
                print(f"⚠️ Could not delete runtime {agent_id}: {e}")
                
        except Exception as e:
            print(f"ℹ️ No agent ID in Parameter Store or error: {e}")
    
    if not runtime_deleted and not endpoints_deleted:
        print("\n⚠️ No AgentCore Runtimes or Endpoints were deleted")
        print("   Please check the AWS Console for any remaining resources")
        print(f"   Console: https://console.aws.amazon.com/bedrock/home?region={AWS_REGION}#/agent-core")
    
    # 2. Delete Guardrail - Find ALL guardrails and delete the right ones
    print("\n2. Finding and deleting Guardrails...")
    guardrails_deleted = 0
    
    # Known guardrail IDs and names
    KNOWN_GUARDRAIL_IDS = ['fkducf9q8z1a']  # web-search-mcp-guardrail
    KNOWN_GUARDRAIL_NAMES = ['web-search-mcp-guardrail', 'GuardrailMiddlewareTutorial', 'simple-agent-guardrail']
    
    try:
        # List all guardrails
        response = bedrock.list_guardrails(maxResults=100)
        guardrails = response.get('guardrails', [])
        
        print(f"📋 Found {len(guardrails)} guardrail(s)")
        
        for guardrail in guardrails:
            name = guardrail.get('name', '')
            guardrail_id = guardrail.get('id')
            
            # Delete if it matches our patterns
            should_delete = False
            
            # Check by ID
            if guardrail_id in KNOWN_GUARDRAIL_IDS:
                should_delete = True
                print(f"🎯 Found by ID: {name} ({guardrail_id})")
            
            # Check by name patterns
            name_lower = name.lower()
            for known_name in KNOWN_GUARDRAIL_NAMES:
                if known_name.lower() in name_lower or name_lower in known_name.lower():
                    should_delete = True
                    print(f"🎯 Found by name: {name} ({guardrail_id})")
                    break
            
            # Also check for tutorial/middleware related names
            if any(x in name_lower for x in ['guardrailmiddleware', 'middleware', 'tutorial', 'simple-agent', 'simple_agent']):
                should_delete = True
                print(f"🎯 Found by pattern: {name} ({guardrail_id})")
            
            if should_delete:
                try:
                    bedrock.delete_guardrail(guardrailIdentifier=guardrail_id)
                    print(f"✅ Deleted Guardrail: {name} (ID: {guardrail_id})")
                    guardrails_deleted += 1
                except Exception as e:
                    print(f"⚠️  Could not delete guardrail {guardrail_id}: {e}")
        
        if guardrails_deleted == 0:
            print("\n📝 All guardrails (none matched our patterns):")
            for g in guardrails:
                print(f"   - {g.get('name')} (ID: {g.get('id')})")
    
    except Exception as e:
        print(f"⚠️  Could not list guardrails: {e}")
    
    # 3. Delete Parameter Store entries
    print("\n3. Deleting Parameter Store entries...")
    params_deleted = 0
    
    # List all parameters and delete ones related to simple_agent
    try:
        paginator = ssm.get_paginator('describe_parameters')
        for page in paginator.paginate():
            for param in page['Parameters']:
                param_name = param['Name']
                if 'simple_agent' in param_name or 'simple-agent' in param_name:
                    try:
                        # Special note for Tavily API key
                        if 'tavily_api_key' in param_name:
                            print(f"🔑 Deleting Tavily API key: {param_name}")
                        ssm.delete_parameter(Name=param_name)
                        print(f"✅ Deleted parameter: {param_name}")
                        params_deleted += 1
                    except:
                        pass
        
        # Also explicitly try to delete the Tavily API key if it wasn't found in the list
        try:
            ssm.delete_parameter(Name='/simple_agent/tavily_api_key')
            print(f"✅ Deleted Tavily API key parameter")
            params_deleted += 1
        except:
            pass  # It's OK if it doesn't exist
        
        if params_deleted == 0:
            print("ℹ️  No parameters found to delete")
            
    except Exception as e:
        print(f"ℹ️  Could not list/delete parameters: {e}")
    
    # 4. Delete ECR repositories  
    print("\n4. Deleting ECR repositories...")
    repos_deleted = 0
    
    try:
        response = ecr_client.describe_repositories()
        for repo in response.get('repositories', []):
            repo_name = repo['repositoryName']
            
            # Check if it's related to our agent
            if any(x in repo_name.lower() for x in ['simple_agent', 'simple-agent', 'simpleagent', 'bedrock-agentcore-simple']):
                try:
                    ecr_client.delete_repository(repositoryName=repo_name, force=True)
                    print(f"✅ Deleted ECR repository: {repo_name}")
                    repos_deleted += 1
                except Exception as e:
                    print(f"ℹ️  Could not delete ECR repo {repo_name}: {e}")
        
        if repos_deleted == 0:
            print("ℹ️  No ECR repositories found to delete")
            
    except Exception as e:
        print(f"ℹ️  Could not list ECR repositories: {e}")
    
    # 5. Delete IAM roles
    print("\n5. Cleaning up IAM roles...")
    roles_deleted = 0
    
    try:
        paginator = iam.get_paginator('list_roles')
        
        for page in paginator.paginate():
            for role in page['Roles']:
                role_name = role['RoleName']
                role_name_lower = role_name.lower()
                
                # Check if it's related to our agent
                should_delete = False
                
                # Direct name matches
                if any(x in role_name_lower for x in ['simple-agent', 'simple_agent', 'simpleagent']):
                    should_delete = True
                
                # BedrockAgentCore SDK roles with our IDs
                elif 'bedrockagentcore' in role_name_lower and any(x in role_name_lower for x in ['9d6fae7ebe', 'icr44egxtf']):
                    should_delete = True
                
                # Roles with simple in SDK name
                elif 'amazonbedrockagentcoresdk' in role_name_lower and 'simple' in role_name_lower:
                    should_delete = True
                
                if should_delete:
                    try:
                        # Delete inline policies
                        response = iam.list_role_policies(RoleName=role_name)
                        for policy_name in response.get('PolicyNames', []):
                            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
                        
                        # Detach managed policies
                        response = iam.list_attached_role_policies(RoleName=role_name)
                        for policy in response.get('AttachedPolicies', []):
                            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy['PolicyArn'])
                        
                        # Delete role
                        iam.delete_role(RoleName=role_name)
                        print(f"✅ Deleted IAM role: {role_name}")
                        roles_deleted += 1
                    except Exception as e:
                        pass
        
        if roles_deleted == 0:
            print("ℹ️  No IAM roles found to delete")
            
    except Exception as e:
        print(f"ℹ️  Could not clean IAM roles: {e}")
    
    # 6. Show summary
    print("\n" + "=" * 60)
    print("📋 Cleanup Summary")
    print("=" * 60)
    
    print(f"\n🔍 Please verify in AWS Console ({AWS_REGION}):")
    print("1. Bedrock AgentCore Runtimes:")
    print(f"   https://console.aws.amazon.com/bedrock/home?region={AWS_REGION}#/agent-core")
    print("2. Bedrock Guardrails:")
    print(f"   https://console.aws.amazon.com/bedrock/home?region={AWS_REGION}#/guardrails")
    print("3. ECR Repositories:")
    print(f"   https://console.aws.amazon.com/ecr/repositories?region={AWS_REGION}")
    print("4. Parameter Store:")
    print(f"   https://console.aws.amazon.com/systems-manager/parameters/?region={AWS_REGION}")
    
    print("\n✅ Cleanup script completed!")
    print("ℹ️  Check the console links above to verify all resources are deleted")
    print("=" * 60)

if __name__ == "__main__":
    try:
        cleanup_all()
    except Exception as e:
        print(f"\n❌ Cleanup failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
