import json
import boto3
import time
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
    """Custom Resource Lambda to process YAML template with Lambda ARNs."""
    
    try:
        request_type = event['RequestType']
        properties = event['ResourceProperties']
        
        if request_type == 'Create':
            # Get ARN values from properties
            get_ticket_lambda_arn = properties['GetTicketLambdaArn']
            update_ticket_lambda_arn = properties['UpdateTicketLambdaArn']
            user_pool_arn = properties['UserPoolArn']
            
            # Read template and replace placeholders
            template_content = properties['TemplateContent']
            
            # Replace placeholders for each endpoint
            processed_yaml = template_content.replace(
                "PLACEHOLDER_USER_POOL_ARN", user_pool_arn
            )
            
            # Replace Lambda ARNs for specific paths
            lines = processed_yaml.split('\n')
            result_lines = []
            in_get_path = False
            in_update_path = False
            
            for line in lines:
                if '/tickets/get:' in line:
                    in_get_path = True
                    in_update_path = False
                elif '/tickets/update:' in line:
                    in_update_path = True
                    in_get_path = False
                elif line.strip().startswith('/') and ':' in line:
                    in_get_path = False
                    in_update_path = False
                
                if 'PLACEHOLDER_LAMBDA_ARN' in line:
                    if in_get_path:
                        line = line.replace('PLACEHOLDER_LAMBDA_ARN', get_ticket_lambda_arn)
                    elif in_update_path:
                        line = line.replace('PLACEHOLDER_LAMBDA_ARN', update_ticket_lambda_arn)
                
                result_lines.append(line)
            
            processed_yaml = '\n'.join(result_lines)
            
            # Upload to S3
            s3 = boto3.client('s3')
            bucket = properties['S3Bucket']
            key = f"processed-yaml/{int(time.time())}/tickets_api.yaml"
            
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=processed_yaml.encode('utf-8'),
                ContentType='application/x-yaml'
            )
            
            s3_uri = f"s3://{bucket}/{key}"
            
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'ProcessedYAMLUri': s3_uri,
                'S3Key': key
            }, s3_uri)
            
        elif request_type == 'Delete':
            # Clean up S3 object
            try:
                s3 = boto3.client('s3')
                bucket = properties['S3Bucket']
                s3_uri = event['PhysicalResourceId']
                key = s3_uri.split(f"{bucket}/")[1]
                s3.delete_object(Bucket=bucket, Key=key)
            except Exception as e:
                print(f"Error deleting S3 object: {e}")
            
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            
        else:  # Update
            s3_uri = event['PhysicalResourceId']
            key = s3_uri.split('/')[-1]
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'ProcessedYAMLUri': s3_uri,
                'S3Key': key
            }, s3_uri)
            
    except Exception as e:
        print(f"Error: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': str(e)
        })
