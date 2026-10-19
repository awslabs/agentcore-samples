#!/usr/bin/env python3
"""
Cleanup all deployed resources for Employee Commute Advisor
"""
import boto3
import time
import sys

# Global region variable - will be detected or specified
REGION = None
PROFILE = "default"

def detect_deployment_region():
    """Try to detect which region the solution was deployed in"""
    # Try to find the region by checking SSM in each supported region
    for region in ['us-west-2', 'us-east-1', 'eu-west-1']:
        try:
            ssm = boto3.Session(profile_name=PROFILE, region_name=region).client('ssm')
            response = ssm.get_parameter(Name='/app/employee-commute-advisor/config/region')
            detected_region = response['Parameter']['Value']
            print(f"✅ Detected deployment in region: {detected_region}")
            return detected_region
        except Exception:
            continue
    
    # If not found, return None
    return None

def select_region():
    """Prompt user to select the region to clean up"""
    print("\n" + "="*80)
    print("SELECT CLEANUP REGION")
    print("="*80)
    print("\nWhich region did you deploy to?")
    print("  1. us-east-1 (US East - N. Virginia)")
    print("  2. us-west-2 (US West - Oregon)")
    print("  3. eu-west-1 (Europe - Ireland)")
    
    while True:
        choice = input("\nSelect region (1-3): ").strip()
        if choice == "1":
            return "us-east-1"
        elif choice == "2":
            return "us-west-2"
        elif choice == "3":
            return "eu-west-1"
        else:
            print("❌ Invalid choice. Please enter 1, 2, or 3.")

def get_boto_client(service_name):
    """Create boto3 client"""
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    return session.client(service_name)

def cleanup_invoker():
    """Delete Lambda invoker stack"""
    print("\n" + "="*60)
    print("Cleaning up Lambda Invoker")
    print("="*60)
    
    cfn = get_boto_client('cloudformation')
    stack_name = "employee-commute-advisor-invoker"
    
    try:
        cfn.describe_stacks(StackName=stack_name)
        print(f"Deleting stack: {stack_name}")
        cfn.delete_stack(StackName=stack_name)
        
        print("Waiting for deletion...")
        waiter = cfn.get_waiter('stack_delete_complete')
        waiter.wait(StackName=stack_name)
        print("✅ Invoker stack deleted")
    except Exception as e:
        if 'does not exist' in str(e):
            print("✅ Invoker stack already deleted")
        else:
            print(f"⚠️  Error deleting invoker: {e}")

def cleanup_runtime():
    """Delete AgentCore Runtime"""
    print("\n" + "="*60)
    print("Cleaning up AgentCore Runtime")
    print("="*60)
    
    # Get runtime ID from SSM
    try:
        ssm = get_boto_client('ssm')
        response = ssm.get_parameter(Name='/app/employee-commute-advisor/agentcore/runtime_id')
        runtime_id = response['Parameter']['Value']
        
        control_client = get_boto_client('bedrock-agentcore-control')
        print(f"Deleting runtime: {runtime_id}")
        control_client.delete_agent_runtime(agentRuntimeId=runtime_id)
        
        # Delete SSM parameter
        ssm.delete_parameter(Name='/app/employee-commute-advisor/agentcore/runtime_id')
        print("✅ Runtime deleted")
    except Exception as e:
        if 'ParameterNotFound' in str(e) or 'ResourceNotFoundException' in str(e):
            print("✅ Runtime already deleted")
        else:
            print(f"⚠️  Error deleting runtime: {e}")

def cleanup_identity_provider():
    """Delete OAuth2 credential provider"""
    print("\n" + "="*60)
    print("Cleaning up Identity Provider")
    print("="*60)
    
    try:
        ssm = get_boto_client('ssm')
        response = ssm.get_parameter(Name='/app/employee-commute-advisor/oauth2/provider_name')
        provider_name = response['Parameter']['Value']
        
        control_client = get_boto_client('bedrock-agentcore-control')
        print(f"Deleting OAuth2 provider: {provider_name}")
        control_client.delete_oauth2_credential_provider(name=provider_name)
        
        # Delete SSM parameter
        ssm.delete_parameter(Name='/app/employee-commute-advisor/oauth2/provider_name')
        print("✅ Identity provider deleted")
    except Exception as e:
        if 'ParameterNotFound' in str(e) or 'ResourceNotFoundException' in str(e):
            print("✅ Identity provider already deleted")
        else:
            print(f"⚠️  Error deleting identity provider: {e}")

def cleanup_gateway():
    """Delete AgentCore Gateway and related resources"""
    print("\n" + "="*60)
    print("Cleaning up AgentCore Gateway")
    print("="*60)
    
    gateway_control = get_boto_client('bedrock-agentcore-control')
    iam = get_boto_client('iam')
    
    # Find and delete all employee-commute gateways
    try:
        gateways_response = gateway_control.list_gateways()
        gateway_ids = []
        
        # Find all gateways with 'employee-commute-gateway' in the name
        for gw in gateways_response.get('items', []):
            if 'employee-commute-gateway' in gw.get('name', ''):
                gateway_ids.append({
                    'id': gw['gatewayId'],
                    'name': gw.get('name')
                })
                print(f"Found gateway: {gw.get('name')} ({gw['gatewayId']})")
        
        if gateway_ids:
            for gateway_info in gateway_ids:
                gateway_id = gateway_info['id']
                gateway_name = gateway_info['name']
                print(f"\nDeleting gateway: {gateway_name}")
                
                # First delete all targets
                print("  Deleting gateway targets...")
                try:
                    targets = gateway_control.list_gateway_targets(gatewayIdentifier=gateway_id)
                    target_count = len(targets.get('items', []))
                    
                    for target in targets.get('items', []):
                        target_id = target.get('targetId')
                        target_name = target.get('name')
                        print(f"    Deleting target: {target_name} ({target_id})")
                        gateway_control.delete_gateway_target(
                            gatewayIdentifier=gateway_id,
                            targetId=target_id
                        )
                    
                    if target_count > 0:
                        print(f"  Waiting for {target_count} target(s) to be fully deleted...")
                        time.sleep(5)  # Wait for targets to be deleted
                        
                        # Verify targets are gone
                        for retry in range(10):
                            remaining = gateway_control.list_gateway_targets(gatewayIdentifier=gateway_id)
                            if len(remaining.get('items', [])) == 0:
                                print("  ✅ All gateway targets deleted")
                                break
                            print(f"    Still waiting... ({retry + 1}/10)")
                            time.sleep(3)
                    else:
                        print("  ✅ No targets to delete")
                        
                except Exception as e:
                    print(f"  ⚠️  Error deleting targets: {e}")
                
                # Now delete the gateway
                print(f"  Deleting gateway: {gateway_id}")
                try:
                    gateway_control.delete_gateway(gatewayIdentifier=gateway_id)
                    print(f"  ✅ Gateway {gateway_name} deleted")
                except Exception as e:
                    print(f"  ⚠️  Error deleting gateway {gateway_name}: {e}")
            print("✅ All employee-commute gateways deleted")
        else:
            print("✅ No employee-commute gateways found")
            
    except Exception as e:
        if 'ResourceNotFoundException' in str(e):
            print("✅ Gateway already deleted")
        else:
            print(f"⚠️  Error deleting gateway: {e}")
    
    # Delete IAM role
    try:
        role_name = "EmployeeCommuteGatewayRole"
        print(f"Deleting IAM role: {role_name}")
        
        # Detach policies first
        try:
            iam.detach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaRole'
            )
        except:
            pass
        
        iam.delete_role(RoleName=role_name)
        print("✅ IAM role deleted")
    except Exception as e:
        if 'NoSuchEntity' in str(e):
            print("✅ IAM role already deleted")
        else:
            print(f"⚠️  Error deleting IAM role: {e}")

def cleanup_lambda_stacks():
    """Delete Lambda and Cognito CloudFormation stacks"""
    print("\n" + "="*60)
    print("Cleaning up Lambda and Cognito Stacks")
    print("="*60)
    
    cfn = get_boto_client('cloudformation')
    
    stacks = [
        "employee-commute-advisor-support",  # Lambda functions
        "employee-commute-cognito"           # Cognito User Pool
    ]
    
    for stack_name in stacks:
        try:
            cfn.describe_stacks(StackName=stack_name)
            print(f"Deleting stack: {stack_name}")
            cfn.delete_stack(StackName=stack_name)
            
            print(f"Waiting for {stack_name} deletion...")
            waiter = cfn.get_waiter('stack_delete_complete')
            waiter.wait(StackName=stack_name)
            print(f"✅ {stack_name} deleted")
        except Exception as e:
            if 'does not exist' in str(e):
                print(f"✅ {stack_name} already deleted")
            else:
                print(f"⚠️  Error deleting {stack_name}: {e}")

def cleanup_secrets():
    """Delete secrets from Secrets Manager"""
    print("\n" + "="*60)
    print("Cleaning up Secrets")
    print("="*60)
    
    secrets_client = get_boto_client('secretsmanager')
    
    secrets = [
        'employee-commute-cognito-client-secret',
        'employee-commute-tomtom-api-key'
    ]
    
    for secret_name in secrets:
        # Use generic identifier for logging to avoid exposing secret names
        secret_identifier = f"secret_{secrets.index(secret_name) + 1}_of_{len(secrets)}"
        try:
            print(f"Deleting {secret_identifier}...")
            secrets_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True
            )
            print(f"✅ {secret_identifier} deleted")
        except Exception as e:
            if 'ResourceNotFoundException' in str(e):
                print(f"✅ {secret_identifier} already deleted")
            else:
                print(f"⚠️  Error deleting {secret_identifier}: {e}")

def main():
    """Main cleanup function"""
    global REGION
    
    print("="*60)
    print("EMPLOYEE COMMUTE ADVISOR - CLEANUP")
    print("="*60)
    
    # Determine region
    if len(sys.argv) > 1:
        # Region provided as command-line argument
        REGION = sys.argv[1]
        print(f"\nUsing region from command line: {REGION}")
    else:
        # Try to detect region automatically
        detected = detect_deployment_region()
        if detected:
            REGION = detected
            confirm = input(f"\nCleanup resources in {REGION}? (yes/no): ")
            if confirm.lower() != 'yes':
                print("\nPlease select the correct region:")
                REGION = select_region()
        else:
            print("\n⚠️  Could not automatically detect deployment region.")
            REGION = select_region()
    
    print(f"\n{'='*60}")
    print(f"CLEANUP REGION: {REGION}")
    print(f"{'='*60}")
    print("\nThis will delete all deployed resources:")
    print("  - Lambda Invoker (with SNS topic)")
    print("  - AgentCore Runtime")
    print("  - OAuth2 Identity Provider")
    print("  - AgentCore Gateway (with Cognito and Lambda)")
    print("  - Secrets Manager secrets")
    print()
    
    response = input("Are you sure you want to continue? (yes/no): ")
    if response.lower() != 'yes':
        print("Cleanup cancelled.")
        return
    
    # Cleanup in reverse order of deployment
    cleanup_invoker()
    time.sleep(2)
    
    cleanup_runtime()
    time.sleep(2)
    
    cleanup_identity_provider()
    time.sleep(2)
    
    cleanup_gateway()
    time.sleep(2)
    
    cleanup_lambda_stacks()
    time.sleep(2)
    
    cleanup_secrets()
    
    print("\n" + "="*60)
    print("✅ CLEANUP COMPLETE")
    print("="*60)
    print("\nAll resources have been deleted.")
    print("You can now redeploy from scratch if needed.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCleanup cancelled by user.")
        sys.exit(1)
