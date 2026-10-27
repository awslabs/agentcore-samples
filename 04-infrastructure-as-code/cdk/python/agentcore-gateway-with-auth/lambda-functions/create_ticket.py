import json
import os
import boto3
import uuid
from datetime import datetime

def handler(event, context):
    """Lambda handler for creating tickets (IAM auth)."""
    try:
        body = event
        
        user_id = body.get('user_id')
        description = body.get('description')
        comment = body.get('comment', '')
        
        if not user_id or not description:
            error_msg = f'Missing required fields: user_id={user_id}, description={description}'
            return {
                'statusCode': 400,
                'body': json.dumps({'error': error_msg})
            }
        
        if len(description.strip()) < 10:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Description must be at least 10 characters'})
            }
        
        request_id = f"REQ-{str(uuid.uuid4())[:8].upper()}"
        timestamp = datetime.utcnow().isoformat()
        
        ticket_item = {
            'RequestId': request_id,
            'Requestor': user_id.strip(),
            'Request': description.strip(),
            'Comment': comment.strip(),
            'ApprovalStatus': 'PENDING',
            'ImplementationStatus': 'NOT_STARTED',
            'CreatedOn': timestamp,
            'UpdatedOn': timestamp
        }
        
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(os.environ['TABLE_NAME'])
        table.put_item(Item=ticket_item)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': True,
                'request_id': request_id,
                'message': f'Ticket {request_id} created successfully'
            })
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal error: {str(e)}'})
        }
