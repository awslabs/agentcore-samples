#!/usr/bin/env python3
"""
Deploy Lambda Invoker
CloudFormation template embedded - no separate files needed!
"""
import boto3
import json
import sys

# Global region variable - set from command line or function parameter
REGION = None
PROFILE = "default"
RUNTIME_ID = "employee_commute_advisor"  # Will be updated after runtime deployment

def get_boto_client(service_name):
    """Create boto3 client"""
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    return session.client(service_name)

def get_runtime_id():
    """Get runtime ID from SSM"""
    try:
        import boto3
        ssm = boto3.Session(profile_name='default').client('ssm', region_name=REGION)
        response = ssm.get_parameter(Name='/app/employee-commute-advisor/agentcore/runtime_id')
        return response['Parameter']['Value']
    except Exception as e:
        print(f"⚠️  Could not get runtime ID from SSM: {e}")
        print("    Make sure to deploy Runtime first: python deployment/deploy_runtime.py")
        return None

def deploy_invoker(region=None):
    """Deploy Lambda invoker with embedded CloudFormation"""
    global REGION
    
    # Set region globally
    if region:
        REGION = region
    elif REGION is None:
        REGION = "us-west-2"
        print(f"⚠️  No region specified, defaulting to us-west-2")
    
    print("="*60)
    print("Deploying Lambda Invoker with SNS Email Notification")
    print("="*60)
    print(f"Region: {REGION}")
    
    runtime_id = get_runtime_id()
    if not runtime_id:
        print("❌ Runtime ID not found in SSM. Deploy Runtime first.")
        return False
        
    print(f"\nRuntime ID from SSM: {runtime_id}")
    
    # CloudFormation template embedded
    template = f"""
AWSTemplateFormatVersion: '2010-09-09'
Description: Lambda invoker for Employee Commute Advisor with SNS notifications

Parameters:
  RuntimeId:
    Type: String
    Default: {runtime_id}
  EmailAddress:
    Type: String
    Default: omrsamer@amazon.com
    Description: Email address to receive commute notifications

Resources:
  CommuteTopic:
    Type: AWS::SNS::Topic
    Properties:
      TopicName: employee-commute-notifications
      DisplayName: Employee Commute Advisor Notifications

  CommuteEmailSubscription:
    Type: AWS::SNS::Subscription
    Properties:
      Protocol: email
      TopicArn: !Ref CommuteTopic
      Endpoint: !Ref EmailAddress

  InvokerRole:
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
        - PolicyName: InvokeRuntimePolicy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - bedrock-agentcore:InvokeAgentRuntime
                  - bedrock-agentcore:InvokeAgentRuntimeForUser
                  - bedrock-agentcore:GetRuntime
                Resource: 'arn:aws:bedrock-agentcore:{REGION}:*:runtime/*'
        - PolicyName: PublishToSNSPolicy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - sns:Publish
                Resource: !Ref CommuteTopic

  InvokerFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: employee-commute-advisor-invoker
      Runtime: python3.12
      Handler: index.lambda_handler
      Role: !GetAtt InvokerRole.Arn
      Timeout: 300
      MemorySize: 512
      Environment:
        Variables:
          RUNTIME_ID: !Ref RuntimeId
          AGENTCORE_REGION: {REGION}
          SNS_TOPIC_ARN: !Ref CommuteTopic
      Code:
        ZipFile: |
          import json, boto3, os
          from botocore.exceptions import ClientError
          
          def lambda_handler(event, context):
              agentcore_client = boto3.client('bedrock-agentcore')
              sns_client = boto3.client('sns')
              
              try:
                  runtime_id = os.environ['RUNTIME_ID']
                  region = os.environ.get('AGENTCORE_REGION', 'us-west-2')
                  sns_topic_arn = os.environ['SNS_TOPIC_ARN']
                  account_id = context.invoked_function_arn.split(':')[4]
                  runtime_arn = f'arn:aws:bedrock-agentcore:{{region}}:{{account_id}}:runtime/{{runtime_id}}'
                  
                  if isinstance(event, str):
                      event = json.loads(event)
                  
                  # Extract from_address and to_address from event
                  from_address = event.get('from_address', 'Berkeley, CA')
                  to_address = event.get('to_address', 'Stanford, CA')
                  
                  # Construct prompt from addresses
                  prompt = f'What is the commute time from {{from_address}} to {{to_address}}?'
                  session_id = event.get('sessionId', context.aws_request_id)
                  
                  # Invoke AgentCore Runtime
                  response = agentcore_client.invoke_agent_runtime(
                      agentRuntimeArn=runtime_arn,
                      runtimeSessionId=session_id,
                      payload=json.dumps({{'prompt': prompt}}),
                      runtimeUserId="lambda-invoker"
                  )
                  
                  # Parse agent response
                  agent_response = None
                  if 'response' in response:
                      response_body = response['response']
                      if hasattr(response_body, 'read'):
                          raw_data = response_body.read()
                          agent_response = raw_data.decode('utf-8') if isinstance(raw_data, bytes) else str(raw_data)
                      elif isinstance(response_body, bytes):
                          agent_response = response_body.decode('utf-8')
                      elif isinstance(response_body, str):
                          agent_response = response_body
                      else:
                          agent_response = str(response_body)
                  
                  agent_response = agent_response or "No response from agent"
                  
                  # Format email message
                  email_subject = f'Commute Advisory: {{from_address}} to {{to_address}}'
                  email_body = (
                      f"Employee Commute Advisory Report\\n\\n"
                      f"From: {{from_address}}\\n"
                      f"To: {{to_address}}\\n\\n"
                      f"Agent Analysis:\\n{{agent_response}}\\n\\n"
                      f"---\\n"
                      f"This is an automated notification from the Employee Commute Advisor system.\\n"
                      f"Session ID: {{session_id}}"
                  )
                  
                  # Send SNS email notification
                  sns_response = sns_client.publish(
                      TopicArn=sns_topic_arn,
                      Subject=email_subject,
                      Message=email_body
                  )
                  
                  return {{
                      'statusCode': 200,
                      'body': json.dumps({{
                          'response': agent_response,
                          'sessionId': session_id,
                          'runtimeId': runtime_id,
                          'from_address': from_address,
                          'to_address': to_address,
                          'email_sent': True,
                          'email_message_id': sns_response.get('MessageId')
                      }}),
                      'headers': {{'Content-Type': 'application/json'}}
                  }}
                  
              except ClientError as e:
                  error_msg = f"AWS Error: {{e.response['Error']['Code']}} - {{e.response['Error']['Message']}}"
                  print(error_msg)
                  return {{
                      'statusCode': 500,
                      'body': json.dumps({{
                          'error': e.response['Error']['Code'],
                          'message': e.response['Error']['Message']
                      }})
                  }}
              except Exception as e:
                  error_msg = f"Internal Error: {{str(e)}}"
                  print(error_msg)
                  return {{
                      'statusCode': 500,
                      'body': json.dumps({{
                          'error': 'InternalError',
                          'message': str(e)
                      }})
                  }}

Outputs:
  InvokerFunctionName:
    Value: !Ref InvokerFunction
  InvokerFunctionArn:
    Value: !GetAtt InvokerFunction.Arn
  SNSTopicArn:
    Value: !Ref CommuteTopic
    Description: SNS Topic ARN for commute notifications
  SNSTopicName:
    Value: !GetAtt CommuteTopic.TopicName
"""
    
    cfn = get_boto_client('cloudformation')
    stack_name = "employee-commute-advisor-invoker"
    
    try:
        cfn.describe_stacks(StackName=stack_name)
        print("Stack exists, updating...")
        cfn.update_stack(
            StackName=stack_name,
            TemplateBody=template,
            Parameters=[{'ParameterKey': 'RuntimeId', 'ParameterValue': runtime_id}],
            Capabilities=['CAPABILITY_IAM']
        )
        waiter = cfn.get_waiter('stack_update_complete')
    except Exception as e:
        if 'does not exist' in str(e):
            print("Creating stack...")
            cfn.create_stack(
                StackName=stack_name,
                TemplateBody=template,
                Parameters=[{'ParameterKey': 'RuntimeId', 'ParameterValue': runtime_id}],
                Capabilities=['CAPABILITY_IAM']
            )
            waiter = cfn.get_waiter('stack_create_complete')
        elif 'No updates' in str(e):
            print("✅ Invoker already up to date")
            return True
        else:
            raise
    
    print("Waiting for deployment...")
    waiter.wait(StackName=stack_name)
    
    # Get stack outputs
    stack_info = cfn.describe_stacks(StackName=stack_name)
    outputs = {o['OutputKey']: o['OutputValue'] for o in stack_info['Stacks'][0].get('Outputs', [])}
    
    print("\n" + "="*60)
    print("✅ INVOKER DEPLOYED SUCCESSFULLY")
    print("="*60)
    print(f"\nFunction: employee-commute-advisor-invoker")
    print(f"SNS Topic: {outputs.get('SNSTopicName', 'N/A')}")
    print(f"\n⚠️  IMPORTANT: Check your email (omrsamer@amazon.com) to confirm SNS subscription!")
    print("   You must click the confirmation link before emails can be sent.")
    return True

if __name__ == "__main__":
    # Get region from command line argument
    region = None
    if len(sys.argv) > 1:
        region = sys.argv[1]
        print(f"Using region from argument: {region}")
    
    success = deploy_invoker(region)
    sys.exit(0 if success else 1)
