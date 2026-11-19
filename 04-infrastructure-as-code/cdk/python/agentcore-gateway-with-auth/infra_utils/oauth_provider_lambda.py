import json
import boto3
import time
import urllib3
import traceback

class cfnresponse:
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    
    @staticmethod
    def send(event, context, responseStatus, responseData, physicalResourceId=None, noEcho=False, reason=None):
        responseUrl = event['ResponseURL']
        
        responseBody = {
            'Status': responseStatus,
            'Reason': reason or f"See CloudWatch Log Stream: {context.log_stream_name}",
            'PhysicalResourceId': physicalResourceId or context.log_stream_name,
            'StackId': event['StackId'],
            'RequestId': event['RequestId'],
            'LogicalResourceId': event['LogicalResourceId'],
            'NoEcho': noEcho,
            'Data': responseData
        }
        
        json_responseBody = json.dumps(responseBody)
        
        headers = {
            'content-type': '',
            'content-length': str(len(json_responseBody))
        }
        
        try:
            http = urllib3.PoolManager()
            response = http.request('PUT', responseUrl, headers=headers, body=json_responseBody)
            print(f"Status code: {response.status}")
        except Exception as e:
            print(f"send(..) failed: {e}")

def handler(event, context):
    """Custom Resource Lambda to create OAuth2 credential provider for AgentCore Gateway."""
    
    try:
        request_type = event['RequestType']
        properties = event['ResourceProperties']
        
        # Initialize boto3 clients
        gateway_client = boto3.client("bedrock-agentcore-control")
        secrets_client = boto3.client('secretsmanager')
        
        if request_type == 'Create':
            # Extract User Pool ID and region
            user_pool_id = properties['UserPoolId']
            print(f"DEBUG: UserPoolId from properties: {user_pool_id}")
            
            # User Pool ID format: e.g. us-east-1_xxxx
            region = user_pool_id.split('_')[0]
            
            # Use cognito-idp endpoint
            discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
            print(f"DEBUG: Generated discovery URL: {discovery_url}")
            
            # Retrieve client secret from Secrets Manager
            secret_arn = properties['SecretArn']
            print(f"DEBUG: Retrieving secret from Secrets Manager: {secret_arn}")
            
            secret_response = secrets_client.get_secret_value(SecretId=secret_arn)
            client_secret = secret_response['SecretString']
            print(f"DEBUG: Successfully retrieved secret from Secrets Manager")
            
            # Create OAuth2 credential provider
            provider_name = f"tickets-oauth-provider-{int(time.time())}"
            response = gateway_client.create_oauth2_credential_provider(
                name=provider_name,
                credentialProviderVendor="CustomOauth2",
                oauth2ProviderConfigInput={
                    'customOauth2ProviderConfig': {
                        'clientId': properties['ClientId'],
                        'clientSecret': client_secret,
                        'oauthDiscovery': {
                            'discoveryUrl': discovery_url
                        }
                    }
                }
            )
            
            provider_arn = response["credentialProviderArn"]
            
            print(f"Created OAuth2 provider: {provider_arn}")
            
            # Return success with provider ARN, store name for deletion
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'ProviderArn': provider_arn
            }, provider_name)
            
        elif request_type == 'Delete':
            # Get provider name from physical resource ID
            provider_name = event['PhysicalResourceId']
            
            try:
                # Delete OAuth2 credential provider
                gateway_client.delete_oauth2_credential_provider(
                    name=provider_name
                )
                print(f"Deleted OAuth2 provider: {provider_name}")
            except Exception as e:
                print(f"Error deleting OAuth2 provider: {e}")
            
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            
        else:  # Update
            # For updates, return existing provider ARN
            provider_name = event['PhysicalResourceId']
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'ProviderArn': ''
            }, provider_name)
            
    except Exception as e:
        print(f"Error: {str(e)}")
        traceback.print_exc()
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': str(e)
        })
