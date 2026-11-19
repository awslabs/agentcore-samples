#!/usr/bin/env python3
"""
Deploy Gateway + Cognito + Lambda Tools
CloudFormation templates embedded - no separate files needed!
"""
import boto3
import json
import time
import sys
from pathlib import Path

# Global region variable - set from command line
REGION = None

def get_boto_client(service_name):
    """Create boto3 client with default profile"""
    # Explicitly set profile to default to avoid role assumption
    session = boto3.Session(profile_name='default', region_name=REGION)
    return session.client(service_name)

def deploy_cognito():
    """Deploy Cognito User Pool with M2M client"""
    print("\n" + "="*60)
    print("Deploying Cognito User Pool")
    print("="*60)
    
    cfn = get_boto_client('cloudformation')
    stack_name = "employee-commute-cognito"
    
    # CloudFormation template embedded
    template = """
AWSTemplateFormatVersion: '2010-09-09'
Description: Cognito User Pool for Employee Commute Advisor

Resources:
  UserPool:
    Type: AWS::Cognito::UserPool
    Properties:
      UserPoolName: employee-commute-advisor-pool
  
  ResourceServer:
    Type: AWS::Cognito::UserPoolResourceServer
    Properties:
      UserPoolId: !Ref UserPool
      Identifier: !Join
        - '-'
        - - 'employee-commute-resource-server'
          - !Select [0, !Split ['-', !Select [2, !Split ['/', !Ref 'AWS::StackId']]]]
      Name: Employee Commute Resource Server
      Scopes:
        - ScopeName: 'read'
          ScopeDescription: 'Read access to commute data'
      
  AppClient:
    Type: AWS::Cognito::UserPoolClient
    DependsOn: ResourceServer
    Properties:
      UserPoolId: !Ref UserPool
      ClientName: employee-commute-machine-client
      GenerateSecret: true
      ExplicitAuthFlows:
        - ALLOW_REFRESH_TOKEN_AUTH
      AllowedOAuthFlows:
        - client_credentials
      AllowedOAuthScopes:
        - !Join
          - ''
          - - 'employee-commute-resource-server-'
            - !Select [0, !Split ['-', !Select [2, !Split ['/', !Ref 'AWS::StackId']]]]
            - '/read'
      AllowedOAuthFlowsUserPoolClient: true
      SupportedIdentityProviders:
        - COGNITO
      AccessTokenValidity: 60
      RefreshTokenValidity: 1
      TokenValidityUnits:
        AccessToken: minutes
        RefreshToken: days

  UserPoolDomain:
    Type: AWS::Cognito::UserPoolDomain
    Properties:
      Domain: !Sub 'employee-commute-${AWS::AccountId}'
      UserPoolId: !Ref UserPool

  CognitoParameters:
    Type: AWS::SSM::Parameter
    Properties:
      Name: /app/commute-advisor/agentcore/machine_client_id
      Type: String
      Value: !Ref AppClient
      
  UserPoolIdParameter:
    Type: AWS::SSM::Parameter
    Properties:
      Name: /app/commute-advisor/agentcore/userpool_id
      Type: String
      Value: !Ref UserPool
      
  ProviderNameParameter:
    Type: AWS::SSM::Parameter
    Properties:
      Name: /app/commute-advisor/agentcore/cognito_provider
      Type: String
      Value: employee-commute-cognito-provider

Outputs:
  UserPoolId:
    Value: !Ref UserPool
  ClientId:
    Value: !Ref AppClient
"""
    
    try:
        cfn.describe_stacks(StackName=stack_name)
        print("Stack exists, skipping...")
        return True
    except:
        print("Creating Cognito stack...")
        cfn.create_stack(
            StackName=stack_name,
            TemplateBody=template,
            Capabilities=['CAPABILITY_IAM']
        )
        
        waiter = cfn.get_waiter('stack_create_complete')
        waiter.wait(StackName=stack_name)
        print("✅ Cognito deployed")
        return True

def deploy_lambda_tools():
    """Deploy Lambda functions (TomTom Traffic + Weather API)"""
    print("\n" + "="*60)
    print("Deploying Lambda Tools")
    print("="*60)
    
    # Read Lambda function code
    tomtom_code = (Path(__file__).parent.parent / 'lambda_functions' / 'tomtom_traffic_realtime.py').read_text()
    weather_code = (Path(__file__).parent.parent / 'lambda_functions' / 'weatherapi_forecast.py').read_text()
    
    template = f"""
AWSTemplateFormatVersion: '2010-09-09'
Description: Lambda Tools for Employee Commute Advisor

Resources:
  LambdaRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
      Policies:
        - PolicyName: SSMAccess
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action: ssm:GetParameter
                Resource: '*'

  TomTomFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: TomTomTrafficFunction
      Runtime: python3.12
      Handler: index.lambda_handler
      Role: !GetAtt LambdaRole.Arn
      Timeout: 30
      Code:
        ZipFile: |
{chr(10).join('          ' + line for line in tomtom_code.split(chr(10)))}

  WeatherFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: WeatherAPIFunction
      Runtime: python3.12
      Handler: index.lambda_handler
      Role: !GetAtt LambdaRole.Arn
      Timeout: 30
      Code:
        ZipFile: |
{chr(10).join('          ' + line for line in weather_code.split(chr(10)))}

Outputs:
  TomTomArn:
    Value: !GetAtt TomTomFunction.Arn
  WeatherArn:
    Value: !GetAtt WeatherFunction.Arn
"""
    
    cfn = get_boto_client('cloudformation')
    stack_name = "employee-commute-advisor-support"
    
    try:
        cfn.describe_stacks(StackName=stack_name)
        print("Stack exists, updating...")
        cfn.update_stack(
            StackName=stack_name,
            TemplateBody=template,
            Capabilities=['CAPABILITY_IAM']
        )
        waiter = cfn.get_waiter('stack_update_complete')
    except Exception as e:
        if 'does not exist' in str(e):
            print("Creating Lambda tools stack...")
            cfn.create_stack(
                StackName=stack_name,
                TemplateBody=template,
                Capabilities=['CAPABILITY_IAM']
            )
            waiter = cfn.get_waiter('stack_create_complete')
        elif 'No updates' in str(e):
            print("✅ Lambda tools already up to date")
            return True
        else:
            raise
    
    waiter.wait(StackName=stack_name)
    print("✅ Lambda tools deployed")
    return True

def deploy_gateway():
    """Deploy AgentCore Gateway"""
    print("\n" + "="*60)
    print("Deploying AgentCore Gateway")
    print("="*60)
    
    gateway_control = get_boto_client('bedrock-agentcore-control')
    cfn = get_boto_client('cloudformation')
    iam = get_boto_client('iam')
    cognito = get_boto_client('cognito-idp')
    ssm = get_boto_client('ssm')
    
    # Get values from CloudFormation stacks
    try:
        # Get Cognito details
        cognito_stack = cfn.describe_stacks(StackName='employee-commute-cognito')
        cognito_outputs = {o['OutputKey']: o['OutputValue'] for o in cognito_stack['Stacks'][0]['Outputs']}
        user_pool_id = cognito_outputs['UserPoolId']
        client_id = cognito_outputs['ClientId']
        
        # Get Lambda ARNs
        lambda_stack = cfn.describe_stacks(StackName='employee-commute-advisor-support')
        lambda_outputs = {o['OutputKey']: o['OutputValue'] for o in lambda_stack['Stacks'][0]['Outputs']}
        tomtom_lambda_arn = lambda_outputs['TomTomArn']
        weather_lambda_arn = lambda_outputs['WeatherArn']
        
        # Get account ID
        account_id = tomtom_lambda_arn.split(':')[4]
        
        print(f"User Pool: {user_pool_id}")
        print(f"Client ID: {client_id}")
        print(f"TomTom Lambda ARN: {tomtom_lambda_arn}")
        print(f"Weather Lambda ARN: {weather_lambda_arn}")
        
    except Exception as e:
        print(f"❌ Error getting stack outputs: {e}")
        return False
    
    # Create Gateway execution role
    try:
        role_name = "EmployeeCommuteGatewayRole"
        try:
            role = iam.get_role(RoleName=role_name)
            gateway_role_arn = role['Role']['Arn']
            print(f"Using existing role: {gateway_role_arn}")
        except:
            print(f"Creating IAM role: {role_name}")
            assume_role_policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }
            
            role_response = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(assume_role_policy),
                Description="Execution role for Employee Commute Gateway"
            )
            gateway_role_arn = role_response['Role']['Arn']
            
            # Attach policy
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaRole'
            )
            print(f"✅ Created role: {gateway_role_arn}")
            time.sleep(10)  # Wait for role to propagate
            
    except Exception as e:
        print(f"❌ Error with IAM role: {e}")
        return False
    
    # Create Gateway
    try:
        # Use unique gateway name with timestamp to avoid conflicts
        gateway_name = f"employee-commute-gateway-{int(time.time() % 1000000)}"
        
        # Get Cognito discovery URL
        cognito_domain = f"employee-commute-{account_id}"
        discovery_url = f"https://cognito-idp.{REGION}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
        
        auth_config = {
            "customJWTAuthorizer": {
                "allowedClients": [client_id],
                "discoveryUrl": discovery_url
            }
        }
        
        # Check if an employee-commute gateway already exists
        gateway_id = None
        gateway_url = None
        try:
            gateways = gateway_control.list_gateways()
            # Use correct key: 'gateways' not 'gatewayList'
            for gw in gateways.get('gateways', []):
                if 'employee-commute-gateway' in gw.get('gatewayName', ''):
                    gateway_id = gw['gatewayId']
                    gateway_url = gw.get('gatewayUrl')
                    print(f"✅ Using existing gateway: {gateway_id}")
                    break
        except Exception as e:
            print(f"Note: Could not list existing gateways: {e}")
        
        # Create if doesn't exist
        if not gateway_id:
            print(f"Creating gateway: {gateway_name}")
            try:
                create_response = gateway_control.create_gateway(
                    name=gateway_name,
                    roleArn=gateway_role_arn,
                    protocolType="MCP",
                    authorizerType="CUSTOM_JWT",
                    authorizerConfiguration=auth_config,
                    description="Employee Commute Advisor Gateway"
                )
                
                gateway_id = create_response['gatewayId']
                gateway_url = create_response['gatewayUrl']
                print(f"✅ Gateway created: {gateway_id}")
            except Exception as create_error:
                if 'ConflictException' in str(create_error):
                    print(f"⚠️  Gateway name conflict, retrying with new name...")
                    # Try again with a different timestamp
                    gateway_name = f"employee-commute-gateway-{int(time.time())}"
                    create_response = gateway_control.create_gateway(
                        name=gateway_name,
                        roleArn=gateway_role_arn,
                        protocolType="MCP",
                        authorizerType="CUSTOM_JWT",
                        authorizerConfiguration=auth_config,
                        description="Employee Commute Advisor Gateway"
                    )
                    gateway_id = create_response['gatewayId']
                    gateway_url = create_response['gatewayUrl']
                    print(f"✅ Gateway created: {gateway_id}")
                else:
                    raise
        
        # Wait for ACTIVE
        print("Waiting for gateway to become ACTIVE...")
        for i in range(30):
            get_response = gateway_control.get_gateway(gatewayIdentifier=gateway_id)
            status = get_response['status']
            
            if status in ['ACTIVE', 'READY']:
                print(f"✅ Gateway is {status}")
                break
            elif status in ['FAILED', 'DELETING', 'DELETED']:
                print(f"❌ Gateway failed with status: {status}")
                return False
            
            time.sleep(10)
        
        # Create targets with proper MCP tool schemas
        print("Creating gateway targets...")
        
        # Define MCP tool schema for TomTom traffic function
        tomtom_tool_schema = [{
            "name": "calculate_commute_time",
            "description": "Calculate travel time between two addresses with real-time traffic conditions using TomTom APIs",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "from_address": {
                        "type": "string",
                        "description": "Origin address (e.g., '123 Main St, San Francisco, CA')"
                    },
                    "to_address": {
                        "type": "string",
                        "description": "Destination address (e.g., '456 Market St, San Francisco, CA')"
                    },
                    "departure_time": {
                        "type": "string",
                        "description": "Departure time in ISO format (optional, defaults to current time)"
                    }
                },
                "required": ["from_address", "to_address"]
            }
        }]
        
        # Define MCP tool schema for Weather API function
        weather_tool_schema = [{
            "name": "get_weather_forecast",
            "description": "Get weather forecast for a location including conditions that could impact commute",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Location (address, city, or coordinates)"
                    },
                    "forecast_days": {
                        "type": "integer",
                        "description": "Number of forecast days (1-3, default: 1)"
                    }
                },
                "required": ["location"]
            }
        }]
        
        tomtom_target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": tomtom_lambda_arn,
                    "toolSchema": {"inlinePayload": tomtom_tool_schema}
                }
            }
        }
        
        weather_target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": weather_lambda_arn,
                    "toolSchema": {"inlinePayload": weather_tool_schema}
                }
            }
        }
        
        credential_config = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
        
        # Create TomTom target
        gateway_control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name="TomTomTraffic",
            description="TomTom Traffic Lambda",
            targetConfiguration=tomtom_target_config,
            credentialProviderConfigurations=credential_config
        )
        print("✅ TomTom gateway target created")
        
        # Create Weather target
        gateway_control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name="WeatherAPI",
            description="Weather API Lambda",
            targetConfiguration=weather_target_config,
            credentialProviderConfigurations=credential_config
        )
        print("✅ Weather gateway target created")
        
        # Save to SSM
        ssm.put_parameter(
            Name='/app/employee-commute-advisor/agentcore/gateway_url',
            Value=gateway_url,
            Type='String',
            Overwrite=True
        )
        
        # Save Cognito secret
        try:
            cognito_response = cognito.describe_user_pool_client(
                UserPoolId=user_pool_id,
                ClientId=client_id
            )
            client_secret = cognito_response['UserPoolClient']['ClientSecret']
            
            ssm.put_parameter(
                Name='/app/commute-advisor/agentcore/cognito_secret',
                Value=client_secret,
                Type='SecureString',
                Overwrite=True
            )
            print("✅ Saved Cognito secret to SSM")
        except Exception as e:
            print(f"⚠️  Could not save Cognito secret: {e}")
        
        print(f"✅ Gateway URL saved to SSM")
        print(f"\nGateway URL: {gateway_url}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error creating gateway: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    global REGION
    
    # Get region from command line argument
    if len(sys.argv) > 1:
        REGION = sys.argv[1]
    else:
        # Default to us-west-2 for backward compatibility
        REGION = "us-west-2"
        print("⚠️  No region specified, defaulting to us-west-2")
    
    print("="*60)
    print("Employee Commute Advisor - Gateway Deployment")
    print("="*60)
    print(f"Region: {REGION}")
    
    # Deploy in order
    success = (
        deploy_cognito() and
        deploy_lambda_tools() and
        deploy_gateway()
    )
    
    if success:
        print("\n" + "="*60)
        print("✅ GATEWAY DEPLOYMENT COMPLETE")
        print("="*60)
        return 0
    else:
        print("\n❌ Deployment failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
