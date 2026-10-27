# Enterprise CloudWatch MCP Server V2 - Demo Commands

## 🎯 Available Commands

This document provides a comprehensive list of natural language commands you can use with the Enterprise CloudWatch MCP Server V2 in Kiro IDE.

## 🔧 Health & Status Commands

### Server Health
```
"Check server health"
"Is the server working?"
"Show server status"
```
**Response**: Server status, version, and basic connectivity information

### User Information
```
"What's my user info?"
"Show my Identity Center details"
"Who am I logged in as?"
```
**Response**: Your Identity Center user details, permissions, and account information

## 📊 CloudWatch Logs Commands

### List Log Groups
```
"List my log groups"
"Show all log groups"
"What log groups do I have access to?"
"List log groups with prefix /aws/lambda"
```
**Response**: List of CloudWatch log groups with names and creation dates

### Search Logs
```
"Search logs in /aws/lambda/my-function for ERROR"
"Find errors in /aws/apigateway/my-api logs"
"Search for 'timeout' in /aws/ecs/my-cluster logs"
"Show recent logs from /aws/lambda/my-function"
```
**Response**: Log events matching your search criteria with timestamps

### Log Group Details
```
"Show details for log group /aws/lambda/my-function"
"Get log group info for /aws/apigateway/my-api"
```
**Response**: Log group metadata, retention settings, and statistics

## 🔄 Cross-Account Commands

### Cross-Account Log Groups
```
"List log groups in account 123456789012"
"Show log groups in account 987654321098"
"What log groups are in account 555666777888?"
```
**Response**: Log groups from the specified target account

### Cross-Account Log Search
```
"Search logs in account 123456789012 for errors"
"Find 'timeout' in logs for account 987654321098"
"Search /aws/lambda/function logs in account 555666777888"
```
**Response**: Log events from the specified account matching your criteria

## 📈 CloudWatch Metrics Commands

### List Metrics
```
"List CloudWatch metrics"
"Show metrics for AWS/Lambda"
"What metrics are available for AWS/EC2?"
"List metrics in namespace AWS/ApiGateway"
```
**Response**: Available CloudWatch metrics with namespaces and dimensions

### Metric Details
```
"Show metric details for AWS/Lambda CPUUtilization"
"Get metric info for AWS/EC2 NetworkIn"
```
**Response**: Metric metadata and available dimensions

## 🚨 CloudWatch Alarms Commands

### List Alarms
```
"List CloudWatch alarms"
"Show alarms in ALARM state"
"What alarms are in OK state?"
"List alarms with prefix 'prod-'"
```
**Response**: CloudWatch alarms with their current states and configurations

### Alarm Details
```
"Show details for alarm 'high-cpu-usage'"
"Get alarm info for 'api-error-rate'"
```
**Response**: Detailed alarm configuration and history

## 🎭 Demo Script for Presentations

### 1. Introduction & Health Check
```
Presenter: "Let me show you our Enterprise CloudWatch MCP Server integration with Kiro IDE."

Command: "Check server health"
Expected: Server status with Identity Center authentication confirmation

Presenter: "As you can see, we're authenticated through AWS Identity Center with full audit trails."
```

### 2. Current Account Access
```
Presenter: "Let's start by exploring CloudWatch resources in our current account."

Command: "List my log groups"
Expected: List of ~20-50 log groups from current account

Presenter: "We can see all our CloudWatch log groups. Now let's search for specific events."

Command: "Search logs in /aws/lambda/my-function for ERROR"
Expected: Recent error events from the specified log group
```

### 3. Cross-Account Demonstration
```
Presenter: "Now here's where it gets interesting - cross-account access with proper security."

Command: "List log groups in account 123456789012"
Expected: Log groups from target account

Presenter: "Notice how we seamlessly access another AWS account while maintaining security through role assumption and external IDs."

Command: "Search logs in account 123456789012 for timeout"
Expected: Timeout events from target account logs
```

### 4. CloudWatch Metrics & Alarms
```
Presenter: "We also have full access to CloudWatch metrics and alarms."

Command: "List CloudWatch metrics for AWS/Lambda"
Expected: Lambda-related metrics

Command: "Show alarms in ALARM state"
Expected: Current alarms that are firing

Presenter: "This gives developers complete observability access through natural language."
```

### 5. Security & Compliance
```
Presenter: "Let me show you the security features."

Command: "What's my user info?"
Expected: Identity Center user details with permissions

Presenter: "Every action is logged with full user context for compliance and audit purposes."
```

## 💡 Advanced Usage Tips

### Combining Commands
You can chain related queries:
```
1. "List log groups with prefix /aws/lambda"
2. "Search logs in /aws/lambda/my-function for ERROR"
3. "Show recent logs from /aws/lambda/my-function"
```

### Time-Based Queries
```
"Search logs from the last hour for errors"
"Show logs from /aws/apigateway/my-api in the last 30 minutes"
```

### Pattern Matching
```
"Search for 'HTTP 5' in /aws/apigateway/my-api logs"
"Find 'OutOfMemory' in /aws/lambda logs"
"Search for IP address patterns in security logs"
```

### Cross-Account Workflows
```
1. "List log groups in account 123456789012"
2. "Search logs in account 123456789012 for application errors"
3. "Show alarms in account 123456789012"
```

## 🔍 Troubleshooting Commands

### Connection Issues
```
"Check server health"
"What's my user info?"
```

### Permission Issues
```
"List my log groups" (should work for current account)
"List log groups in account [target]" (test cross-account access)
```

### Service Availability
```
"List CloudWatch metrics" (test CloudWatch API access)
"Show alarms in OK state" (test alarm API access)
```

## 📊 Expected Response Formats

### Log Groups Response
```json
{
  "log_groups": [
    {
      "logGroupName": "/aws/lambda/my-function",
      "creationTime": "2024-01-15T10:30:00Z",
      "retentionInDays": 14,
      "storedBytes": 1048576
    }
  ],
  "account_id": "123456789012",
  "total_count": 25
}
```

### Log Events Response
```json
{
  "events": [
    {
      "timestamp": "2024-01-15T10:35:22Z",
      "message": "ERROR: Connection timeout after 30 seconds",
      "logStream": "2024/01/15/[$LATEST]abc123"
    }
  ],
  "log_group": "/aws/lambda/my-function",
  "search_pattern": "ERROR"
}
```

### User Info Response
```json
{
  "user_email": "developer@company.com",
  "identity_center_instance": "arn:aws:sso:::instance/ssoins-123456789",
  "account_id": "123456789012",
  "permissions": ["CloudWatchReadOnly", "CrossAccountAccess"],
  "session_duration": 3600
}
```

## 🎉 Success Indicators

You'll know the commands are working when:
- ✅ Health check returns server status
- ✅ Log groups are listed with proper formatting
- ✅ Search commands return relevant log events
- ✅ Cross-account commands work without errors
- ✅ User info shows correct Identity Center details
- ✅ All responses include proper audit context

**Ready to demonstrate the power of natural language CloudWatch access!**