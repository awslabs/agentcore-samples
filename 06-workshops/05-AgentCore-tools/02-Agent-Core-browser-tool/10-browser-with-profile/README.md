# AgentCore Browser Tool with Browser Profiles

This example demonstrates how to use [browser profiles](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-profiles.html) with Amazon Bedrock AgentCore Browser Tool. Browser profiles enable you to persist and reuse browser session data (cookies, local storage) across multiple browser sessions.

## Overview

Browser profiles allow you to:
- **Persist session data**: Save cookies and local storage
- **Simulate user behavior**: Test workflows that require persistent browser state
- **Share context**: Use the same profile across multiple browser sessions

## Use Cases

- E-commerce testing with persistent shopping carts
- Testing authenticated workflows without re-login
- Multi-step user journeys that span multiple sessions

## Getting Started

### Pre-requisites

Before start, you should navigate through [sample-ecommerce](sample-ecommerce/README.md) and follow instructions to deploy fake e-commerce that will be used in this example.

### Installation

```bash
pip install -r requirements.txt
```

## Notebook Walkthrough

The [browser-profile.ipynb](browser-profile.ipynb) notebook demonstrates:

### 1. Setup
- Create S3 bucket for browser recordings
- Create IAM role with required permissions
- Create custom AgentCore Browser
- Create browser profile

### 2. First Session
- Start browser session
- Navigate to Cloud Front DNS (which is pointing to S3 bucket with our fake e-commerce)
- Add products to the cart
- **Save session to profile**
- Stop session

### 3. Second Session
- Start new session **with saved profile**
- Navigate to the cart
- Verify product persists from previous session

### 4. Optional: Download Recordings
- Download session recordings from S3
- Convert to rrweb format
- Replay session in notebook

### 5. Troubleshooting
- Profile not loading: Ensure the profile was saved before stopping the session
- Permission errors: Verify IAM role has SaveBrowserSessionProfile permission
- Session timeout: Browser sessions have a maximum duration; save profiles before timeout
- **Expired Cookies:** Cookies have their own expiration times set by websites. Browser profiles preserve cookies, but expired cookies are automatically removed by the browser according to their expiration dates

## Files

- **browser-profile.ipynb**: Complete tutorial notebook with step-by-step examples
- **browser_helper.py**: Helper functions for SigV4 signing and WebSocket URL generation
- **requirements.txt**: Python dependencies

## Key Concepts

### Browser Profile
A browser profile stores session information including:
- Cookies
- Local storage

### Profile Lifecycle
1. **Create profile**: `create_browser_profile()`
2. **Save session**: `save_browser_session_profile()` - captures current session state
3. **Load profile**: `start_browser_session(profileConfiguration={...})` - restores saved state
4. **Delete profile**: `delete_browser_profile()` - cleanup

## IAM Permissions

The execution role requires:
```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:StartBrowserSession",
    "bedrock-agentcore:SaveBrowserSessionProfile"
  ],
  "Resource": [
    "arn:aws:bedrock-agentcore:REGION:ACCOUNT:browser-profile/PROFILE_NAME",
    "arn:aws:bedrock-agentcore:REGION:ACCOUNT:browser-custom/BROWSER_NAME"
  ]
}
```

## Cleanup

To remove all resources:
```python
# Delete browser
browser_boto3.delete_browser(browserId=browser_id)

# Delete profile
browser_boto3.delete_browser_profile(profileId=profile_id)

# Delete IAM role (via console or CLI)
# Delete S3 bucket (via console or CLI)
```

## Security Considerations

- Browser profiles may contain sensitive session data
- Use appropriate IAM policies to restrict profile access
- Consider profile retention policies for compliance
- Recordings stored in S3 should have proper encryption and access controls

## Troubleshooting

**Profile not loading**: Ensure the profile was saved before stopping the session

**Permission errors**: Verify IAM role has `SaveBrowserSessionProfile` permission

**Session timeout**: Browser sessions have a maximum duration; save profiles before timeout

## Additional Resources

- [AgentCore Browser Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-browser.html)
- [Playwright Documentation](https://playwright.dev/docs/intro)
- [rrweb Player](https://github.com/rrweb-io/rrweb)
