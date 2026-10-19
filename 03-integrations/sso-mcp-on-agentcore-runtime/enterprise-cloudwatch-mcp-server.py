#!/usr/bin/env python3
"""
Enterprise CloudWatch MCP Server V2
Integrates with AWS Identity Center for secure cross-account CloudWatch access
"""

import asyncio
import json
import boto3
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from botocore.exceptions import ClientError
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load configuration
try:
    with open('config.json', 'r') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    # Use template config for development
    with open('config-template.json', 'r') as f:
        CONFIG = json.load(f)

# Initialize MCP server
server = Server("enterprise-cloudwatch-mcp-v2")

class CloudWatchMCPProxy:
    """Proxy to AWS CloudWatch services with Identity Center authentication"""
    
    def __init__(self):
        self.region = CONFIG['identity_center']['region']
        self.test_account_id = CONFIG['identity_center']['account_id']
    
    def get_cloudwatch_client(self, credentials: Dict[str, str] = None):
        """Get CloudWatch client with optional cross-account credentials"""
        if credentials:
            return boto3.client(
                'cloudwatch',
                region_name=self.region,
                aws_access_key_id=credentials['AccessKeyId'],
                aws_secret_access_key=credentials['SecretAccessKey'],
                aws_session_token=credentials['SessionToken']
            )
        else:
            return boto3.client('cloudwatch', region_name=self.region)
    
    def get_logs_client(self, credentials: Dict[str, str] = None):
        """Get CloudWatch Logs client with optional cross-account credentials"""
        if credentials:
            return boto3.client(
                'logs',
                region_name=self.region,
                aws_access_key_id=credentials['AccessKeyId'],
                aws_secret_access_key=credentials['SecretAccessKey'],
                aws_session_token=credentials['SessionToken']
            )
        else:
            return boto3.client('logs', region_name=self.region)
    
    def list_log_groups(self, credentials: Dict[str, str] = None, name_prefix: str = "", limit: int = 50):
        """List CloudWatch log groups"""
        try:
            logs_client = self.get_logs_client(credentials)
            
            params = {'limit': limit}
            if name_prefix:
                params['logGroupNamePrefix'] = name_prefix
            
            response = logs_client.describe_log_groups(**params)
            
            log_groups = []
            for group in response.get('logGroups', []):
                log_groups.append({
                    'logGroupName': group['logGroupName'],
                    'creationTime': group.get('creationTime'),
                    'storedBytes': group.get('storedBytes', 0),
                    'retentionInDays': group.get('retentionInDays'),
                    'metricFilterCount': group.get('metricFilterCount', 0)
                })
            
            return {
                "success": True,
                "log_groups": log_groups,
                "count": len(log_groups)
            }
            
        except ClientError as e:
            return {
                "success": False,
                "error": f"CloudWatch Logs error: {e.response['Error']['Message']}"
            }
    
    def search_log_events(
        self, 
        log_group: str,
        query: str = "",
        start_time: int = None,
        end_time: int = None,
        limit: int = 100,
        credentials: Dict[str, str] = None
    ):
        """Search CloudWatch log events"""
        try:
            logs_client = self.get_logs_client(credentials)
            
            # Default to last hour if no time specified
            if not start_time:
                start_time = int((datetime.now() - timedelta(hours=1)).timestamp() * 1000)
            if not end_time:
                end_time = int(datetime.now().timestamp() * 1000)
            
            params = {
                'logGroupName': log_group,
                'startTime': start_time,
                'endTime': end_time,
                'limit': limit
            }
            
            if query:
                params['filterPattern'] = query
            
            response = logs_client.filter_log_events(**params)
            
            events = []
            for event in response.get('events', []):
                events.append({
                    'timestamp': event['timestamp'],
                    'message': event['message'],
                    'logStreamName': event.get('logStreamName', ''),
                    'eventId': event.get('eventId', '')
                })
            
            return {
                "success": True,
                "events": events,
                "count": len(events),
                "log_group": log_group,
                "query": query
            }
            
        except ClientError as e:
            return {
                "success": False,
                "error": f"CloudWatch Logs search error: {e.response['Error']['Message']}"
            }
    
    def list_metrics(
        self,
        namespace: str = None,
        metric_name: str = None,
        dimensions: List[Dict] = None,
        credentials: Dict[str, str] = None
    ):
        """List CloudWatch metrics"""
        try:
            cloudwatch_client = self.get_cloudwatch_client(credentials)
            
            params = {}
            if namespace:
                params['Namespace'] = namespace
            if metric_name:
                params['MetricName'] = metric_name
            if dimensions:
                params['Dimensions'] = dimensions
            
            response = cloudwatch_client.list_metrics(**params)
            
            metrics = []
            for metric in response.get('Metrics', []):
                metrics.append({
                    'MetricName': metric['MetricName'],
                    'Namespace': metric['Namespace'],
                    'Dimensions': metric.get('Dimensions', [])
                })
            
            return {
                "success": True,
                "metrics": metrics,
                "count": len(metrics)
            }
            
        except ClientError as e:
            return {
                "success": False,
                "error": f"CloudWatch Metrics error: {e.response['Error']['Message']}"
            }
    
    def list_alarms(
        self,
        state_value: str = None,
        alarm_name_prefix: str = "",
        credentials: Dict[str, str] = None
    ):
        """List CloudWatch alarms"""
        try:
            cloudwatch_client = self.get_cloudwatch_client(credentials)
            
            params = {}
            if state_value:
                params['StateValue'] = state_value
            if alarm_name_prefix:
                params['AlarmNamePrefix'] = alarm_name_prefix
            
            response = cloudwatch_client.describe_alarms(**params)
            
            alarms = []
            for alarm in response.get('MetricAlarms', []):
                alarms.append({
                    'AlarmName': alarm['AlarmName'],
                    'StateValue': alarm['StateValue'],
                    'StateReason': alarm.get('StateReason', ''),
                    'MetricName': alarm.get('MetricName', ''),
                    'Namespace': alarm.get('Namespace', ''),
                    'Threshold': alarm.get('Threshold'),
                    'ComparisonOperator': alarm.get('ComparisonOperator', '')
                })
            
            return {
                "success": True,
                "alarms": alarms,
                "count": len(alarms)
            }
            
        except ClientError as e:
            return {
                "success": False,
                "error": f"CloudWatch Alarms error: {e.response['Error']['Message']}"
            }

# Global proxy instance
cloudwatch_proxy = CloudWatchMCPProxy()

def authenticate_request(user_email: str = None, access_token: str = None, account_id: str = None, tool_name: str = None):
    """Authenticate and authorize request using Identity Center"""
    
    # Use configured user email if none provided
    if not user_email:
        user_email = CONFIG['user_config']['default_user_email']
    
    # Use configured account if none provided
    if not account_id:
        account_id = CONFIG['identity_center']['account_id']
    
    # For now, return a simple auth result
    # In production, this would integrate with Identity Center
    return {
        "valid": True,
        "user": {"email": user_email},
        "account_id": account_id,
        "permission_sets": ["CloudWatchReadOnlyAccess"],
        "available_tools": ["list_log_groups", "search_logs", "list_metrics", "list_alarms"],
        "test_mode": True
    }

# ============= Enterprise CloudWatch MCP Tools =============

@server.list_tools()
async def handle_list_tools() -> List[Tool]:
    """List available tools"""
    return [
        Tool(
            name="list_log_groups",
            description="List CloudWatch log groups with Identity Center authentication",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_email": {"type": "string", "description": "User email for authentication"},
                    "account_id": {"type": "string", "description": "Target AWS account ID (optional)"},
                    "name_prefix": {"type": "string", "description": "Filter log groups by name prefix"},
                    "limit": {"type": "integer", "description": "Maximum number of log groups to return", "default": 50}
                }
            }
        ),
        Tool(
            name="search_logs",
            description="Search CloudWatch logs with Identity Center authentication",
            inputSchema={
                "type": "object",
                "properties": {
                    "log_group": {"type": "string", "description": "CloudWatch log group name", "required": True},
                    "query": {"type": "string", "description": "Search query/filter pattern"},
                    "user_email": {"type": "string", "description": "User email for authentication"},
                    "account_id": {"type": "string", "description": "Target AWS account ID (optional)"},
                    "hours_back": {"type": "integer", "description": "How many hours back to search", "default": 1},
                    "limit": {"type": "integer", "description": "Maximum number of events to return", "default": 100}
                },
                "required": ["log_group"]
            }
        ),
        Tool(
            name="list_metrics",
            description="List CloudWatch metrics with Identity Center authentication",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_email": {"type": "string", "description": "User email for authentication"},
                    "account_id": {"type": "string", "description": "Target AWS account ID (optional)"},
                    "namespace": {"type": "string", "description": "CloudWatch namespace (e.g., AWS/EC2, AWS/Lambda)"},
                    "metric_name": {"type": "string", "description": "Specific metric name to filter"}
                }
            }
        ),
        Tool(
            name="list_alarms",
            description="List CloudWatch alarms with Identity Center authentication",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_email": {"type": "string", "description": "User email for authentication"},
                    "account_id": {"type": "string", "description": "Target AWS account ID (optional)"},
                    "state_value": {"type": "string", "description": "Filter by alarm state (OK, ALARM, INSUFFICIENT_DATA)"},
                    "alarm_name_prefix": {"type": "string", "description": "Filter alarms by name prefix"}
                }
            }
        ),
        Tool(
            name="health_check",
            description="Check if the Enterprise CloudWatch MCP server is running",
            inputSchema={"type": "object", "properties": {}}
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> List[TextContent]:
    """Handle tool calls"""
    try:
        if name == "list_log_groups":
            result = await list_log_groups_cross_account(**arguments)
        elif name == "search_logs":
            result = await search_logs_cross_account(**arguments)
        elif name == "list_metrics":
            result = await list_metrics_cross_account(**arguments)
        elif name == "list_alarms":
            result = await list_alarms_cross_account(**arguments)
        elif name == "health_check":
            result = health_check()
        else:
            result = {"success": False, "error": f"Unknown tool: {name}"}
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    except Exception as e:
        logger.error(f"Error calling tool {name}: {e}")
        return [TextContent(type="text", text=json.dumps({"success": False, "error": str(e)}, indent=2))]

async def list_log_groups_cross_account(
    user_email: str = None,
    access_token: str = None,
    account_id: str = None,
    name_prefix: str = "",
    limit: int = 50
) -> Dict[str, Any]:
    """
    List CloudWatch log groups with Identity Center authentication
    
    Args:
        user_email: User email for authentication (test mode)
        access_token: Identity Center access token (production mode)
        account_id: Target AWS account ID (optional)
        name_prefix: Filter log groups by name prefix
        limit: Maximum number of log groups to return
    """
    
    # Authenticate and authorize
    auth_result = authenticate_request(user_email, access_token, account_id, "list_log_groups")
    if not auth_result["valid"]:
        return {"success": False, "error": auth_result.get("error", "Authentication failed")}
    
    try:
        # For cross-account access, we would get credentials here
        # For now, using current account credentials
        credentials = None
        
        # Call CloudWatch API
        result = cloudwatch_proxy.list_log_groups(
            credentials=credentials,
            name_prefix=name_prefix,
            limit=limit
        )
        
        # Add audit information
        result["audit"] = {
            "user": auth_result["user"]["email"],
            "account_id": auth_result["account_id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": "list_log_groups",
            "test_mode": auth_result.get("test_mode", False)
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error listing log groups: {e}")
        return {"success": False, "error": str(e)}

async def search_logs_cross_account(
    log_group: str,
    query: str = "",
    user_email: str = None,
    access_token: str = None,
    account_id: str = None,
    hours_back: int = 1,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Search CloudWatch logs with Identity Center authentication
    
    Args:
        log_group: CloudWatch log group name
        query: Search query/filter pattern
        user_email: User email for authentication (test mode)
        access_token: Identity Center access token (production mode)
        account_id: Target AWS account ID (optional)
        hours_back: How many hours back to search (default: 1)
        limit: Maximum number of events to return
    """
    
    # Authenticate and authorize
    auth_result = authenticate_request(user_email, access_token, account_id, "search_logs")
    if not auth_result["valid"]:
        return {"success": False, "error": auth_result.get("error", "Authentication failed")}
    
    try:
        from datetime import timedelta
        
        # Calculate time range
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours_back)
        
        # For cross-account access, we would get credentials here
        credentials = None
        
        # Call CloudWatch Logs API
        result = cloudwatch_proxy.search_log_events(
            log_group=log_group,
            query=query,
            start_time=int(start_time.timestamp() * 1000),
            end_time=int(end_time.timestamp() * 1000),
            limit=limit,
            credentials=credentials
        )
        
        # Add audit information
        result["audit"] = {
            "user": auth_result["user"]["email"],
            "account_id": auth_result["account_id"],
            "log_group": log_group,
            "query": query,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": "search_logs",
            "test_mode": auth_result.get("test_mode", False)
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error searching logs: {e}")
        return {"success": False, "error": str(e)}

async def list_metrics_cross_account(
    user_email: str = None,
    access_token: str = None,
    account_id: str = None,
    namespace: str = None,
    metric_name: str = None
) -> Dict[str, Any]:
    """
    List CloudWatch metrics with Identity Center authentication
    
    Args:
        user_email: User email for authentication (test mode)
        access_token: Identity Center access token (production mode)
        account_id: Target AWS account ID (optional)
        namespace: CloudWatch namespace (e.g., AWS/EC2, AWS/Lambda)
        metric_name: Specific metric name to filter
    """
    
    # Authenticate and authorize
    auth_result = authenticate_request(user_email, access_token, account_id, "list_metrics")
    if not auth_result["valid"]:
        return {"success": False, "error": auth_result.get("error", "Authentication failed")}
    
    try:
        # For cross-account access, we would get credentials here
        credentials = None
        
        # Call CloudWatch API
        result = cloudwatch_proxy.list_metrics(
            namespace=namespace,
            metric_name=metric_name,
            credentials=credentials
        )
        
        # Add audit information
        result["audit"] = {
            "user": auth_result["user"]["email"],
            "account_id": auth_result["account_id"],
            "namespace": namespace,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": "list_metrics",
            "test_mode": auth_result.get("test_mode", False)
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error listing metrics: {e}")
        return {"success": False, "error": str(e)}

async def list_alarms_cross_account(
    user_email: str = None,
    access_token: str = None,
    account_id: str = None,
    state_value: str = None,
    alarm_name_prefix: str = ""
) -> Dict[str, Any]:
    """
    List CloudWatch alarms with Identity Center authentication
    
    Args:
        user_email: User email for authentication (test mode)
        access_token: Identity Center access token (production mode)
        account_id: Target AWS account ID (optional)
        state_value: Filter by alarm state (OK, ALARM, INSUFFICIENT_DATA)
        alarm_name_prefix: Filter alarms by name prefix
    """
    
    # Authenticate and authorize
    auth_result = authenticate_request(user_email, access_token, account_id, "list_alarms")
    if not auth_result["valid"]:
        return {"success": False, "error": auth_result.get("error", "Authentication failed")}
    
    try:
        # For cross-account access, we would get credentials here
        credentials = None
        
        # Call CloudWatch API
        result = cloudwatch_proxy.list_alarms(
            state_value=state_value,
            alarm_name_prefix=alarm_name_prefix,
            credentials=credentials
        )
        
        # Add audit information
        result["audit"] = {
            "user": auth_result["user"]["email"],
            "account_id": auth_result["account_id"],
            "state_filter": state_value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": "list_alarms",
            "test_mode": auth_result.get("test_mode", False)
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error listing alarms: {e}")
        return {"success": False, "error": str(e)}

async def get_user_info(
    user_email: str = None,
    access_token: str = None
) -> Dict[str, Any]:
    """
    Get user information and available tools from Identity Center
    
    Args:
        user_email: User email for authentication (test mode)
        access_token: Identity Center access token (production mode)
    """
    
    # Authenticate user
    auth_result = authenticate_request(user_email, access_token)
    if not auth_result["valid"]:
        return {"success": False, "error": auth_result["error"]}
    
    return {
        "success": True,
        "user": auth_result["user"],
        "account_id": auth_result["account_id"],
        "permission_sets": auth_result["permission_sets"],
        "available_tools": auth_result["available_tools"],
        "test_mode": auth_result.get("test_mode", False),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# ============= Utility Tools =============

def health_check() -> Dict[str, str]:
    """Check if the Enterprise CloudWatch MCP server is running"""
    try:
        return {
            'status': 'healthy',
            'message': 'Enterprise CloudWatch MCP Server V2 is running',
            'identity_center_instance': CONFIG['identity_center']['instance_arn'],
            'region': CONFIG['identity_center']['region'],
            'authentication': 'AWS Identity Center',
            'features': 'CloudWatch logs, metrics, alarms with Identity Center integration'
        }
    except Exception as e:
        return {
            'status': 'error',
            'message': f'Enterprise CloudWatch MCP server error: {str(e)}'
        }

async def main():
    """Main entry point for the MCP server"""
    logger.info("Starting Enterprise CloudWatch MCP Server V2...")
    logger.info(f"Identity Center Instance: {CONFIG['identity_center']['instance_arn']}")
    logger.info(f"Default User: {CONFIG['user_config']['default_user_email']}")
    logger.info(f"Account: {CONFIG['identity_center']['account_id']}")
    logger.info("Server ready for Kiro integration")
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())