import json
import os
import boto3

def handler(event, context):
    """Lambda handler for getting all user tickets (IAM auth)."""
    try:
        body = event
        
        user_id = body.get('user_id')
        status_filter = body.get('status_filter')
        
        if not user_id:
            error_msg = f'Missing required field: user_id={user_id}'
            return {
                'statusCode': 400,
                'body': json.dumps({'error': error_msg})
            }
        
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(os.environ['TABLE_NAME'])
        
        # Build filter expression
        filter_expression = "Requestor = :requestor"
        expression_values = {':requestor': user_id}
        
        if status_filter:
            status_upper = status_filter.upper()
            if status_upper in ['PENDING', 'APPROVED', 'REJECTED']:
                filter_expression += " AND ApprovalStatus = :status"
                expression_values[':status'] = status_upper
            elif status_upper in ['NOT_STARTED', 'IN_PROGRESS', 'COMPLETED']:
                filter_expression += " AND ImplementationStatus = :status"
                expression_values[':status'] = status_upper
        
        response = table.scan(
            FilterExpression=filter_expression,
            ExpressionAttributeValues=expression_values
        )
        
        tickets = response.get('Items', [])
        # Sort by creation date newest first
        tickets.sort(key=lambda x: x.get('CreatedOn', ''), reverse=True)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': True,
                'tickets': tickets,
                'count': str(len(tickets))
            })
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal error: {str(e)}'})
        }
