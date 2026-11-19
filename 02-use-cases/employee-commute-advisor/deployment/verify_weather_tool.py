#!/usr/bin/env python3
"""
Verify Weather Tool Setup
Checks if weather tool is properly configured and accessible
"""
import boto3
import json
import sys

# Global region variable - set from command line
REGION = None
PROFILE = "default"

def check_lambda_function():
    """Check if WeatherAPIFunction exists and is configured"""
    print("\n" + "="*80)
    print("1. Checking WeatherAPI Lambda Function")
    print("="*80)
    
    try:
        session = boto3.Session(profile_name=PROFILE, region_name=REGION)
        lambda_client = session.client('lambda')
        
        # Get function configuration
        response = lambda_client.get_function_configuration(
            FunctionName='WeatherAPIFunction'
        )
        
        print(f"✅ Function exists: {response['FunctionName']}")
        print(f"   Runtime: {response['Runtime']}")
        print(f"   Status: {response['State']}")
        
        # Check if API key is configured
        env_vars = response.get('Environment', {}).get('Variables', {})
        if 'WEATHERAPI_KEY' in env_vars:
            print("✅ API key is configured")
            return True, lambda_client
        else:
            print("❌ WEATHERAPI_KEY environment variable not set!")
            print("\nTo fix, run:")
            print("  aws lambda update-function-configuration \\")
            print("    --function-name WeatherAPIFunction \\")
            print("    --environment 'Variables={WEATHERAPI_KEY=YOUR_KEY}' \\")
            print(f"    --region {REGION}")
            return False, lambda_client
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False, None

def test_lambda_directly():
    """Test Lambda function directly"""
    print("\n" + "="*80)
    print("2. Testing Lambda Function Directly")
    print("="*80)
    
    try:
        session = boto3.Session(profile_name=PROFILE, region_name=REGION)
        lambda_client = session.client('lambda')
        
        test_payload = {
            'location': 'Dublin, Ireland',
            'forecast_days': 1
        }
        
        print(f"Invoking with payload: {test_payload}")
        
        response = lambda_client.invoke(
            FunctionName='WeatherAPIFunction',
            InvocationType='RequestResponse',
            Payload=json.dumps(test_payload)
        )
        
        result = json.loads(response['Payload'].read())
        
        if response['StatusCode'] == 200:
            body = json.loads(result.get('body', '{}'))
            
            if 'error' in body:
                print(f"❌ Lambda returned error: {body['error']}")
                return False
            else:
                print("✅ Lambda invocation successful!")
                print(f"   Location: {body.get('location', {}).get('name', 'N/A')}")
                print(f"   Temperature: {body.get('current_temp_c', 'N/A')}°C")
                print(f"   Conditions: {body.get('current_condition', 'N/A')}")
                print(f"   Commute Impact: {body.get('commute_impact', 'N/A')}")
                return True
        else:
            print(f"❌ Lambda invocation failed with status: {response['StatusCode']}")
            print(json.dumps(result, indent=2))
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_gateway_targets():
    """Check if Gateway has both targets"""
    print("\n" + "="*80)
    print("3. Checking Gateway Targets")
    print("="*80)
    
    try:
        session = boto3.Session(profile_name=PROFILE, region_name=REGION)
        ssm = session.client('ssm')
        gateway_control = session.client('bedrock-agentcore-control')
        
        # Get Gateway URL from SSM
        try:
            gateway_url = ssm.get_parameter(
                Name='/app/employee-commute-advisor/agentcore/gateway_url'
            )['Parameter']['Value']
            
            # Extract Gateway ID from URL
            gateway_id = gateway_url.split('//')[1].split('.')[0]
            print(f"✅ Gateway ID: {gateway_id}")
            print(f"   URL: {gateway_url}")
        except Exception as e:
            print(f"❌ Could not get Gateway URL from SSM: {e}")
            return False
        
        # List Gateway targets
        try:
            targets_response = gateway_control.list_gateway_targets(
                gatewayIdentifier=gateway_id
            )
            
            targets = targets_response.get('gatewayTargets', [])
            print(f"\n✅ Found {len(targets)} target(s):")
            
            has_tomtom = False
            has_weather = False
            
            for target in targets:
                name = target.get('gatewayTargetName', 'Unknown')
                status = target.get('status', 'Unknown')
                print(f"   - {name}: {status}")
                
                if 'tomtom' in name.lower() or 'traffic' in name.lower():
                    has_tomtom = True
                if 'weather' in name.lower():
                    has_weather = True
            
            if has_tomtom and has_weather:
                print("\n✅ Both TomTom and Weather targets found!")
                return True
            elif has_tomtom and not has_weather:
                print("\n❌ Weather target is MISSING!")
                print("\nTo fix, redeploy Gateway:")
                print("  cd 02-use-cases/employee-commute-advisor")
                print(f"  python deployment/deploy_gateway.py {REGION}")
                return False
            else:
                print("\n⚠️  Unexpected target configuration")
                return False
                
        except Exception as e:
            print(f"❌ Could not list Gateway targets: {e}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def check_runtime_tools():
    """Check if Runtime can see both tools"""
    print("\n" + "="*80)
    print("4. Checking Runtime Tool Access")
    print("="*80)
    
    print("This requires checking Runtime logs after an invocation.")
    print("Look for log entries showing available tools:")
    print("\n  aws logs tail /aws/bedrock-agentcore-runtime/employee_commute_advisor \\")
    print(f"    --since 10m --filter-pattern 'Available tools' --region {REGION}")
    print("\nYou should see both:")
    print("  - calculate_commute_time")
    print("  - get_weather_forecast")
    
    return True

def main():
    """Run all checks"""
    print("="*80)
    print("WEATHER TOOL VERIFICATION")
    print("="*80)
    print("\nThis script will check:")
    print("  1. WeatherAPIFunction Lambda exists and has API key")
    print("  2. Lambda function works when invoked directly")
    print("  3. Gateway has weather tool registered as a target")
    print("  4. Instructions for checking Runtime tool access")
    
    results = []
    
    # Check 1: Lambda function
    lambda_ok, lambda_client = check_lambda_function()
    results.append(("Lambda Function", lambda_ok))
    
    if not lambda_ok:
        print("\n" + "="*80)
        print("❌ CRITICAL: Lambda function not properly configured")
        print("="*80)
        print("\nFix the Lambda configuration first, then run this script again.")
        return 1
    
    # Check 2: Test Lambda directly
    test_ok = test_lambda_directly()
    results.append(("Lambda Direct Test", test_ok))
    
    if not test_ok:
        print("\n" + "="*80)
        print("❌ CRITICAL: Lambda function not working")
        print("="*80)
        print("\nPossible issues:")
        print("  1. Invalid WeatherAPI key")
        print("  2. WeatherAPI service down")
        print("  3. Lambda function code error")
        print("\nCheck Lambda logs:")
        print(f"  aws logs tail /aws/lambda/WeatherAPIFunction --since 5m --region {REGION}")
        return 1
    
    # Check 3: Gateway targets
    gateway_ok = check_gateway_targets()
    results.append(("Gateway Targets", gateway_ok))
    
    if not gateway_ok:
        print("\n" + "="*80)
        print("❌ ISSUE: Gateway missing weather target")
        print("="*80)
        return 1
    
    # Check 4: Runtime tool access
    check_runtime_tools()
    
    # Summary
    print("\n" + "="*80)
    print("VERIFICATION SUMMARY")
    print("="*80)
    
    all_ok = all(ok for _, ok in results)
    
    for check, ok in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"{status}: {check}")
    
    if all_ok:
        print("\n🎉 All checks passed!")
        print("\nIf the agent still can't use the weather tool:")
        print("  1. Redeploy Runtime to refresh tool list:")
        print(f"     python deployment/deploy_runtime.py {REGION}")
        print("  2. Wait 30 seconds for Runtime to stabilize")
        print("  3. Test again with Streamlit app")
    else:
        print("\n❌ Some checks failed. Fix the issues above and try again.")
    
    return 0 if all_ok else 1

if __name__ == "__main__":
    # Get region from command line argument
    if len(sys.argv) > 1:
        REGION = sys.argv[1]
    else:
        # Default to us-west-2 for backward compatibility
        REGION = "us-west-2"
        print(f"⚠️  No region specified, defaulting to {REGION}")
    
    print(f"Verifying weather tool in region: {REGION}\n")
    sys.exit(main())
