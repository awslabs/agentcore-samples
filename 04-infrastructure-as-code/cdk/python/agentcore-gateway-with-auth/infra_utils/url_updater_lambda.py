import json
import boto3
import urllib3

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
    """Custom Resource Lambda to update API Gateway URL in processed YAML."""
    
    try:
        request_type = event['RequestType']
        properties = event['ResourceProperties']
        
        if request_type == 'Create' or request_type == 'Update':
            bucket = properties['S3Bucket']
            key = properties['S3Key']
            api_gateway_url = properties['APIGatewayUrl']
            
            api_base_url = api_gateway_url.rstrip('/')
            
            print(f"Updating YAML at s3://{bucket}/{key}")
            print(f"API Gateway URL: {api_base_url}")
            
            # Read existing YAML from S3
            s3 = boto3.client('s3')
            response = s3.get_object(Bucket=bucket, Key=key)
            yaml_content = response['Body'].read().decode('utf-8')
            
            # Replace API Gateway URL placeholder
            updated_yaml = yaml_content.replace(
                "PLACEHOLDER_API_GATEWAY_URL",
                api_base_url
            )
            
            # Verify replacement
            if "PLACEHOLDER_API_GATEWAY_URL" in updated_yaml:
                raise Exception("Failed to replace PLACEHOLDER_API_GATEWAY_URL")
            
            if api_base_url not in updated_yaml:
                raise Exception(f"API Gateway URL {api_base_url} not found in updated YAML")
            
            # Save updated YAML
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=updated_yaml.encode('utf-8'),
                ContentType='application/x-yaml'
            )
            
            print("Successfully updated YAML with API Gateway URL")
            
            s3_uri = f"s3://{bucket}/{key}"
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'UpdatedYAMLUri': s3_uri,
                'S3Key': key
            }, s3_uri)
            
        elif request_type == 'Delete':
            print("Delete event - no action needed")
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': str(e)
        })
