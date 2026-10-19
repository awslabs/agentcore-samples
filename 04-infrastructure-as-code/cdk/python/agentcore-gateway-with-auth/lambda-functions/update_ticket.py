import json
import os
import boto3
from datetime import datetime

def handler(event, context):
    """Lambda handler for updating tickets (OAuth auth via API Gateway)."""
    try:
        if 'body' in event and isinstance(event['body'], str):
            body = json.loads(event['body'])
        else:
            body = event
        
        request_id = body.get('request_id')
        user_id = body.get('user_id')
        updates = body.get('updates', {})
        
        if not request_id or not user_id or not updates:
            error_msg = f'Missing required fields: request_id={request_id}, user_id={user_id}, updates={updates}'
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
        
        # Access control: users can only update their own tickets
        if ticket['Requestor'] != user_id:
            return {
                'statusCode': 403,
                'body': json.dumps({'error': 'Access denied: You can only update your own tickets'})
            }
        
        # Build update expression
        update_expression = "SET UpdatedOn = :timestamp"
        expression_values = {':timestamp': datetime.utcnow().isoformat()}
        expression_attribute_names = {}
        
        allowed_fields = ['Comment', 'ApprovalStatus', 'ImplementationStatus']
        
        for field, value in updates.items():
            if field not in allowed_fields:
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': f'Field {field} cannot be updated'})
                }
            
            if field == 'ApprovalStatus' and value.upper() not in ['PENDING', 'APPROVED', 'REJECTED']:
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': 'Invalid approval status'})
                }
            
            if field == 'ImplementationStatus' and value.upper() not in ['NOT_STARTED', 'IN_PROGRESS', 'COMPLETED']:
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': 'Invalid implementation status'})
                }
            
            update_expression += f", #{field} = :{field.lower()}"
            expression_values[f':{field.lower()}'] = value.upper() if field.endswith('Status') else value
            expression_attribute_names[f'#{field}'] = field
        
        table.update_item(
            Key={'RequestId': request_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values,
            ExpressionAttributeNames=expression_attribute_names
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': True,
                'message': f'Ticket {request_id} updated successfully'
            })
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal error: {str(e)}'})
        }
