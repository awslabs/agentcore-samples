import json
import os
import boto3

def handler(event, context):
    """Lambda handler for getting single ticket by ID (OAuth auth via API Gateway)."""
    try:
        # API Gateway format - body is JSON string
        if 'body' in event and isinstance(event['body'], str):
            body = json.loads(event['body'])
        else:
            body = event
        
        request_id = body.get('request_id')
        user_id = body.get('user_id')
        
        if not request_id or not user_id:
            error_msg = f'Missing required fields: request_id={request_id}, user_id={user_id}'
            return {
                'statusCode': 400,
                'body': json.dumps({'error': error_msg})
            }
        
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(os.environ['TABLE_NAME'])
        
        response = table.get_item(Key={'RequestId': request_id})
        
        if 'Item' not in response:
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'Ticket not found'})
            }
        
        ticket = response['Item']
        
        # Access control: users can only view their own tickets
        if ticket['Requestor'] != user_id:
            return {
                'statusCode': 403,
                'body': json.dumps({'error': 'Access denied: You can only view your own tickets'})
            }
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': True,
                'ticket': ticket
            })
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal error: {str(e)}'})
        }
