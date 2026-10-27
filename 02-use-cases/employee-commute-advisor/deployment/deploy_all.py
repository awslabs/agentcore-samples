#!/usr/bin/env python3
"""
Deploy Complete Employee Commute Advisor System
Orchestrates deployment of all components
"""
import sys
import subprocess
import time
from pathlib import Path

def select_region():
    """Prompt user to select deployment region"""
    print("\n" + "="*80)
    print("SELECT DEPLOYMENT REGION")
    print("="*80)
    print("\nSupported AWS regions for Bedrock AgentCore:")
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

def run_script(script_name, description, region):
    """Run a deployment script and handle errors"""
    print("\n" + "="*80)
    print(f"STEP: {description}")
    print("="*80)
    
    script_path = Path(__file__).parent / script_name
    result = subprocess.run([sys.executable, str(script_path), region])
    
    if result.returncode != 0:
        print(f"\n❌ Failed: {description}")
        return False
    
    print(f"\n✅ Completed: {description}")
    return True

def create_oauth2_provider(region):
    """Create OAuth2 credential provider"""
    print("\n" + "="*80)
    print("STEP: Create OAuth2 Credential Provider")
    print("="*80)
    
    import boto3
    
    PROFILE = "default"
    
    try:
        session = boto3.Session(profile_name=PROFILE, region_name=region)
        cfn = session.client('cloudformation')
        cognito = session.client('cognito-idp')
        control_client = session.client('bedrock-agentcore-control')
        ssm = session.client('ssm')
        
        # Get Cognito details from CloudFormation
        print("Getting Cognito configuration from CloudFormation...")
        stack = cfn.describe_stacks(StackName='employee-commute-cognito')
        outputs = {o['OutputKey']: o['OutputValue'] for o in stack['Stacks'][0]['Outputs']}
        
        user_pool_id = outputs['UserPoolId']
        client_id = outputs['ClientId']  # Correct key is 'ClientId' not 'UserPoolClientId'
        
        # Get domain from user pool ID
        sts = session.client('sts')
        account_id = sts.get_caller_identity()['Account']
        user_pool_domain = f"employee-commute-{account_id}"
        
        # Get client secret
        print("Retrieving client secret...")
        client_response = cognito.describe_user_pool_client(
            UserPoolId=user_pool_id,
            ClientId=client_id
        )
        client_secret = client_response['UserPoolClient']['ClientSecret']
        
        # Construct OAuth2 endpoints  
        discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
        token_endpoint = f"https://{user_pool_domain}.auth.{region}.amazoncognito.com/oauth2/token"
        auth_endpoint = f"https://{user_pool_domain}.auth.{region}.amazoncognito.com/oauth2/authorize"
        
        # Create provider name
        provider_name = 'employee-commute-cognito-provider'
        
        print(f"Creating OAuth2 provider: {provider_name}")
        print(f"  Issuer (Discovery URL): {discovery_url}")
        print(f"  Token Endpoint: {token_endpoint}")
        print(f"  Auth Endpoint: {auth_endpoint}")
        
        # Create OAuth2 credential provider (CORRECT COMPLEX API)
        control_client.create_oauth2_credential_provider(
            name=provider_name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "oauthDiscovery": {
                        "authorizationServerMetadata": {
                            "issuer": discovery_url,
                            "authorizationEndpoint": auth_endpoint,
                            "tokenEndpoint": token_endpoint,
                            "responseTypes": ["code", "token"]
                        }
                    }
                }
            }
        )
        
        # Store provider name and region in SSM
        ssm.put_parameter(
            Name='/app/employee-commute-advisor/oauth2/provider_name',
            Value=provider_name,
            Type='String',
            Overwrite=True
        )
        
        ssm.put_parameter(
            Name='/app/employee-commute-advisor/config/region',
            Value=region,
            Type='String',
            Overwrite=True
        )
        
        print(f"✅ OAuth2 credential provider created: {provider_name}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to create OAuth2 provider: {e}")
        return False

def configure_tomtom_api_key(region):
    """Configure TomTom API key"""
    print("\n" + "="*80)
    print("STEP: Configure TomTom API Key")
    print("="*80)
    
    import boto3
    
    PROFILE = "default"
    
    # Prompt for API key
    print("\nPlease enter your TomTom API key.")
    print("(Get one for free at https://developer.tomtom.com/)")
    api_key = input("TomTom API Key: ").strip()
    
    if not api_key:
        print("❌ No API key provided")
        return False
    
    try:
        session = boto3.Session(profile_name=PROFILE, region_name=region)
        lambda_client = session.client('lambda')
        
        # Update Lambda environment variable
        print(f"Updating TomTomTrafficFunction in {region}...")
        lambda_client.update_function_configuration(
            FunctionName='TomTomTrafficFunction',
            Environment={
                'Variables': {
                    'TOMTOM_API_KEY': api_key
                }
            }
        )
        
        print("✅ TomTom API key configured successfully")
        return True
        
    except Exception as e:
        print(f"❌ Failed to configure API key: {e}")
        return False

def configure_weatherapi_key(region):
    """Configure WeatherAPI key"""
    print("\n" + "="*80)
    print("STEP: Configure WeatherAPI Key")
    print("="*80)
    
    import boto3
    
    PROFILE = "default"
    
    # Prompt for API key
    print("\nPlease enter your WeatherAPI key.")
    print("(Get one for free at https://www.weatherapi.com/ - 1M calls/month free)")
    api_key = input("WeatherAPI Key: ").strip()
    
    if not api_key:
        print("❌ No API key provided")
        return False
    
    try:
        session = boto3.Session(profile_name=PROFILE, region_name=region)
        lambda_client = session.client('lambda')
        
        # Update Lambda environment variable
        print(f"Updating WeatherAPIFunction in {region}...")
        lambda_client.update_function_configuration(
            FunctionName='WeatherAPIFunction',
            Environment={
                'Variables': {
                    'WEATHERAPI_KEY': api_key
                }
            }
        )
        
        print("✅ WeatherAPI key configured successfully")
        return True
        
    except Exception as e:
        print(f"❌ Failed to configure API key: {e}")
        return False

def test_deployment(region):
    """Test the deployed system"""
    print("\n" + "="*80)
    print("STEP: End-to-End Test")
    print("="*80)
    
    import boto3
    import json
    
    try:
        session = boto3.Session(profile_name='default', region_name=region)
        lambda_client = session.client('lambda')
        
        print("Testing with: Berkeley, CA → Stanford, CA")
        
        response = lambda_client.invoke(
            FunctionName='employee-commute-advisor-invoker',
            InvocationType='RequestResponse',
            Payload=json.dumps({
                'from_address': 'Berkeley, CA',
                'to_address': 'Stanford, CA'
            })
        )
        
        result = json.loads(response['Payload'].read())
        
        if result.get('statusCode') == 200:
            print("\n✅ TEST PASSED!")
            body = json.loads(result['body'])
            print(f"\nFrom: {body.get('from_address')}")
            print(f"To: {body.get('to_address')}")
            print(f"Email Sent: {body.get('email_sent')}")
            if body.get('email_message_id'):
                print(f"Email Message ID: {body['email_message_id']}")
            
            response_text = body.get('response', '')
            print("\nAgent Response Preview:")
            print(response_text[:400] + "..." if len(response_text) > 400 else response_text)
            
            print("\n⚠️  Check your email (omrsamer@amazon.com) for the notification!")
            print("   If you don't receive it, confirm the SNS subscription in your inbox.")
            return True
        else:
            print(f"\n❌ TEST FAILED - Status: {result.get('statusCode')}")
            print(json.dumps(result, indent=2))
            return False
            
    except Exception as e:
        print(f"\n❌ TEST FAILED - Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Deploy complete system"""
    print("="*80)
    print("EMPLOYEE COMMUTE ADVISOR - COMPLETE DEPLOYMENT")
    print("="*80)
    print("\nThis will deploy:")
    print("  1. Gateway (Cognito + Lambda Tools)")
    print("  2. OAuth2 Credential Provider")
    print("  3. AgentCore Runtime")
    print("  4. Lambda Invoker with SNS")
    print("  5. API Key Configuration (TomTom + WeatherAPI)")
    print("  6. End-to-end test")
    
    # Step 0: Select Region
    region = select_region()
    print(f"\n✅ Selected region: {region}")
    print("\nStarting deployment...\n")
    
    # Step 1: Deploy Gateway
    if not run_script("deploy_gateway.py", "Deploy Gateway + Cognito + Lambda", region):
        return 1
    
    # Wait for resources to stabilize
    print("\nWaiting 10 seconds for resources to stabilize...")
    time.sleep(10)
    
    # Step 2: Create OAuth2 Provider
    if not create_oauth2_provider(region):
        return 1
    
    time.sleep(5)
    
    # Step 3: Deploy Runtime
    if not run_script("deploy_runtime.py", "Deploy AgentCore Runtime", region):
        return 1
    
    time.sleep(5)
    
    # Step 4: Deploy Invoker
    if not run_script("deploy_invoker.py", "Deploy Lambda Invoker with SNS", region):
        return 1
    
    print("\n" + "="*80)
    print("⚠️  IMPORTANT: SNS EMAIL CONFIRMATION")
    print("="*80)
    print("\nAn SNS subscription confirmation email has been sent to: omrsamer@amazon.com")
    print("Please check your inbox (and spam folder) and click the confirmation link.")
    print("\nPress Enter once you've confirmed the subscription (or skip to test anyway)...")
    input()
    
    # Step 5: Configure API Keys
    tomtom_configured = configure_tomtom_api_key(region)
    if not tomtom_configured:
        print("\n⚠️  TomTom API key not configured. You can configure it later with:")
        print("  aws lambda update-function-configuration \\")
        print("    --function-name TomTomTrafficFunction \\")
        print("    --environment 'Variables={TOMTOM_API_KEY=YOUR_KEY}' \\")
        print(f"    --region {region}")
    
    weather_configured = configure_weatherapi_key(region)
    if not weather_configured:
        print("\n⚠️  WeatherAPI key not configured. You can configure it later with:")
        print("  aws lambda update-function-configuration \\")
        print("    --function-name WeatherAPIFunction \\")
        print("    --environment 'Variables={WEATHERAPI_KEY=YOUR_KEY}' \\")
        print(f"    --region {region}")
    
    time.sleep(5)
    
    # Step 6: Test
    if not test_deployment(region):
        print("\n" + "="*80)
        print("⚠️  DEPLOYMENT COMPLETED BUT TEST FAILED")
        print("="*80)
        print("\nPossible issues:")
        print("  1. API keys not valid (TomTom or WeatherAPI)")
        print("  2. OAuth2 provider not fully propagated (wait 1 minute and retry)")
        print("  3. SNS subscription not confirmed (check email)")
        print("\nCheck logs:")
        print("  aws logs tail /aws/lambda/employee-commute-advisor-invoker --since 5m --follow")
        print("  aws logs tail /aws/bedrock-agentcore-runtime/employee_commute_advisor --since 5m --follow")
        return 1
    
    # Success!
    print("\n" + "="*80)
    print("🎉 COMPLETE DEPLOYMENT SUCCESSFUL!")
    print("="*80)
    print("\nYour Employee Commute Advisor is ready!")
    print("\nTo test again:")
    print("  python test_lambda.py")
    print("\nTo invoke with custom addresses:")
    print("  aws lambda invoke --function-name employee-commute-advisor-invoker \\")
    print("    --payload '{\"from_address\": \"San Francisco, CA\", \"to_address\": \"San Jose, CA\"}' \\")
    print("    response.json")
    print("\nTo cleanup all resources:")
    print("  python cleanup.py")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
