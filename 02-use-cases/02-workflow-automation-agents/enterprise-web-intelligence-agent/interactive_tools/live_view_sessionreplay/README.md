# Amazon Bedrock AgentCore SDK Tools Examples

This folder contains examples demonstrating the use of Amazon Bedrock AgentCore SDK tools:

## Browser Tools

* `browser_viewer_replay.py` - Amazon Bedrock AgentCore Browser Live Viewer with proper display sizing support.
* `browser_interactive_session.py` - Complete end-to-end browser experience with live viewing, recording, and replay capabilities.
* `session_replay_viewer.py` - Viewer for replaying recorded browser sessions.
* `view_recordings.py` - Standalone script to view recorded sessions from S3.

## Prerequisites

### Python Dependencies
```bash
pip install -r requirements.txt
```

Required packages: fastapi, uvicorn, rich, boto3, bedrock-agentcore

### AWS Credentials
Ensure AWS credentials are configured:
```bash
aws configure
```

## Running the Examples

### Complete Browser Experience with Recording & Replay
From the `02-Agent-Core-browser-tool/interactive_tools` directory:
```bash
python -m live_view_sessionreplay.browser_interactive_session
```

### View Recordings
From the `02-Agent-Core-browser-tool/interactive_tools` directory:
```bash
python -m live_view_sessionreplay.view_recordings --bucket YOUR_BUCKET --prefix YOUR_PREFIX
```

## Complete Browser Experience with Recording & Replay

Run a complete end-to-end workflow that includes live browser viewing, automatic recording to S3, and integrated session replay.

### Features
- Create browser sessions with automatic recording to S3
- Live view with interactive control (take/release)
- Adjust display resolution on the fly
- Automatic session recording to S3
- Integrated session replay viewer for watching recordings

### How It Works
1. The script creates a browser with recording enabled
2. A browser session is started and displayed in your local browser
3. You can take manual control of the browser or let automation run
4. All actions are automatically recorded to S3
5. After you end the session (Ctrl+C), a replay viewer opens showing your recording

### Environment Variables
- `AWS_REGION` - AWS region (default: us-west-2)
- `AGENTCORE_ROLE_ARN` - IAM role ARN for browser execution (default: automatically generated from account ID)
- `RECORDING_BUCKET` - S3 bucket for recordings (default: session-record-test-{ACCOUNT_ID})
- `RECORDING_PREFIX` - S3 prefix for recordings (default: replay-data)

### Required IAM Permissions
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:CreateBucket",
                "s3:PutObject",
                "s3:GetObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::session-record-test-*",
                "arn:aws:s3:::session-record-test-*/*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": "bedrock:*",
            "Resource": "*"
        }
    ]
}
```

## Standalone Session Replay Viewer

A separate tool for viewing recorded browser sessions directly from S3 without creating a new browser.

### Features
- Connect directly to S3 to view recordings
- View any past recording by specifying its session ID
- Automatically finds the latest recording if no session ID is provided

### Usage

```bash
# View the latest recording in a bucket
python -m live_view_sessionreplay.view_recordings --bucket session-record-test-123456789012 --prefix replay-data

# View a specific recording
python -m live_view_sessionreplay.view_recordings --bucket session-record-test-123456789012 --prefix replay-data --session 01JZVDG02M8MXZY2N7P3PKDQ74

# Use a specific AWS profile
python -m live_view_sessionreplay.view_recordings --bucket session-record-test-123456789012 --prefix replay-data --profile my-profile
```

### Finding Recordings

List S3 recordings:
```bash
aws s3 ls s3://session-record-test-123456789012/replay-data/ --recursive
```

## Troubleshooting

### DCV SDK Not Found
Ensure the DCV SDK files are placed in `interactive_tools/static/dcvjs/`

### Browser Session Not Visible
- Verify DCV SDK is properly installed
- Check browser console (F12) for errors
- Ensure AWS credentials have proper permissions

### Recording Not Working
- Verify S3 bucket exists and is accessible
- Check IAM permissions for S3 operations
- Ensure the execution role has appropriate permissions

### Session Replay Issues
- Verify recordings exist in S3 (use AWS CLI or console)
- Check for errors in the console logs
- Ensure S3 bucket policy allows reading objects

### S3 Access Errors
- Verify AWS credentials are configured
- Check IAM permissions for S3 operations
- Ensure bucket name is globally unique

## Architecture Notes
- Live viewer uses FastAPI to serve presigned DCV URLs
- Recording is handled directly by the browser service in the data plane
- Replay uses rrweb-player for playback of recorded events
- All components can work together or independently