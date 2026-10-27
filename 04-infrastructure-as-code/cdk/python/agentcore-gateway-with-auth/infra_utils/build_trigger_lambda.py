import json
import boto3
import time
import traceback
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
    """Custom Resource Lambda to trigger CodeBuild project."""
    
    try:
        request_type = event['RequestType']
        properties = event['ResourceProperties']
        
        if request_type == 'Create':
            project_name = properties['ProjectName']
            
            codebuild = boto3.client('codebuild')
            
            # Start build
            response = codebuild.start_build(projectName=project_name)
            build_id = response['build']['id']
            
            print(f"Started CodeBuild: {build_id}")
            
            # Wait for build to complete
            max_wait_time = context.get_remaining_time_in_millis() / 1000 - 30
            start_time = time.time()
            
            while True:
                if time.time() - start_time > max_wait_time:
                    cfnresponse.send(event, context, cfnresponse.FAILED, {
                        'BuildId': build_id,
                        'Status': 'TIMEOUT'
                    }, build_id, reason="Build timeout")
                    return
                
                time.sleep(30)
                
                build_response = codebuild.batch_get_builds(ids=[build_id])
                build_status = build_response['builds'][0]['buildStatus']
                
                print(f"Build status: {build_status}")
                
                if build_status == 'SUCCEEDED':
                    cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                        'BuildId': build_id,
                        'Status': 'SUCCEEDED'
                    }, build_id)
                    return
                elif build_status in ['FAILED', 'FAULT', 'TIMED_OUT', 'STOPPED']:
                    cfnresponse.send(event, context, cfnresponse.FAILED, {
                        'BuildId': build_id,
                        'Status': build_status
                    }, build_id, reason=f"Build {build_status}")
                    return
            
        elif request_type == 'Delete':
            print("Delete event - no action needed")
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            
    except Exception as e:
        print(f"Error: {str(e)}")
        traceback.print_exc()
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': str(e)
        })
