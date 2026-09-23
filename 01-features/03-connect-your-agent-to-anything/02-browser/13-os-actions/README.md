# AgentCore Browser — OS-Level Actions (InvokeBrowser API)

| Information         | Details                                                                     |
|:--------------------|:----------------------------------------------------------------------------|
| Tutorial type       | Feature demonstration                                                       |
| Agent type          | Direct SDK (SigV4 REST) — no LLM agent                                      |
| Agentic Framework   | None (raw HTTP)                                                             |
| LLM model           | None                                                                        |
| Tutorial components | AgentCore Browser, InvokeBrowser API, SigV4, mouse/keyboard/screenshot     |
| Example complexity  | Advanced                                                                    |

## Overview

The **InvokeBrowser API** lets you send raw OS-level input events directly to the browser sandbox
VM — bypassing the CDP/Playwright automation layer entirely. This is the lowest-level interface
to the browser and enables interactions that CDP cannot reach:

| OS-level action | Example use case |
|:----------------|:-----------------|
| Mouse click/drag | Canvas apps, drag-and-drop without DOM |
| Scroll | WebGL scenes, non-scrollable containers |
| Keyboard shortcut | Ctrl+S (save), Ctrl+P (print), Alt+Tab |
| Screenshot | Full VM screen capture including OS dialogs |
| OS dialogs | File upload/download prompts, print dialogs, auth pop-ups |

## Architecture

```
┌──────────┐    SigV4-signed     ┌──────────────────────┐    OS-level     ┌──────────────────┐
│  Client   │ ──────────────────▶│  AgentCore Browser   │ ──────────────▶│  Browser Sandbox  │
│(os_actions│    REST (PUT)       │  InvokeBrowser API   │    events       │  (Headless VM)    │
│  .py)     │ ◀──────────────────│                      │ ◀──────────────│                   │
└──────────┘   JSON + screenshot └──────────────────────┘   results       └──────────────────┘
```

## API Pattern

All InvokeBrowser requests are SigV4-signed PUT calls:

```python
PUT /browsers/{browser_id}/sessions/invoke
x-amzn-browser-session-id: <session_id>
Authorization: <SigV4>

{
  "action": {
    "mouseClick": {"x": 600, "y": 370, "button": "LEFT"}
  }
}
```

## Action Reference

```python
# Mouse
{"mouseClick": {"x": 600, "y": 370, "button": "LEFT"}}         # left click
{"mouseClick": {"x": 500, "y": 300, "button": "LEFT", "clickCount": 2}}  # double click
{"mouseClick": {"x": 200, "y": 400, "button": "RIGHT"}}         # right click
{"mouseMove": {"x": 800, "y": 600}}                             # move without click
{"mouseDrag": {"startX": 1, "startY": 1, "endX": 705, "endY": 180, "button": "LEFT"}}

# Scroll
{"mouseScroll": {"x": 500, "y": 300, "deltaX": 0, "deltaY": -500}}   # scroll up
{"mouseScroll": {"x": 500, "y": 300, "deltaX": 300, "deltaY": 0}}    # scroll right

# Keyboard
{"keyType": {"text": "Hello World"}}                            # type text
{"keyPress": {"key": "enter"}}                                  # press Enter
{"keyPress": {"key": "backspace", "presses": 5}}                # backspace x5
{"keyShortcut": {"keys": ["ctrl", "s"]}}                        # Ctrl+S
{"keyShortcut": {"keys": ["ctrl", "shift", "i"]}}               # Ctrl+Shift+I

# Screenshot
{"screenshot": {"format": "PNG"}}                               # returns base64 PNG
```

## IAM Permissions

The execution role requires `bedrock-agentcore:InvokeBrowser` in addition to the standard
session permissions:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:InvokeBrowser",
    "bedrock-agentcore:StartBrowserSession",
    "bedrock-agentcore:StopBrowserSession"
  ],
  "Resource": "*"
}
```

## Running the Script

```bash
pip install -r ../requirements.txt

python os_actions.py --region us-west-2

# Keep resources to inspect browser in console
python os_actions.py --region us-west-2 --skip-cleanup
```

The script outputs one line per action with status (`OK` / `HTTP <code>`) and saves screenshots
to `screenshot.png`.

## Troubleshooting

### HTTP 403 on InvokeBrowser
**Issue**: Missing `bedrock-agentcore:InvokeBrowser` permission on the execution role.
**Solution**: The script creates a role with this permission automatically. If using an external role, add `InvokeBrowser` to its policy.

### Session ID header missing
**Issue**: The `x-amzn-browser-session-id` header was not included in the request.
**Solution**: The `invoke()` helper always adds this header from the session ID returned by `start_session()`.

### Screenshot returns empty data
**Issue**: The browser sandbox may not have rendered anything yet.
**Solution**: Add a `time.sleep(2)` before taking the screenshot to let the browser VM initialise.

## Clean Up

```bash
# Automatic (default):
python os_actions.py --region us-west-2

# Manual if --skip-cleanup was used:
aws bedrock-agentcore-control delete-browser --browser-id <id>
aws iam delete-role-policy --role-name BrowserOSActAgentCoreRole --policy-name BrowserOSActPolicy
aws iam delete-role --role-name BrowserOSActAgentCoreRole
```

## Files

| File | Description |
|:-----|:------------|
| `os_actions.py` | Main demo — exercises the full InvokeBrowser action surface |

## boto3 SDK Alternative

`os_actions.py` uses manual SigV4 signing to call the InvokeBrowser REST API directly. If you prefer to use the **boto3 SDK** (requires `boto3 >= 1.42.85`), the `invoke_browser()` method handles authentication automatically — no `requests`, no `SigV4Auth`:

```python
import boto3, time

REGION = "us-west-2"
control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
runtime_client = boto3.client("bedrock-agentcore", region_name=REGION)

# Create browser
created = control_client.create_browser(
    name="browser_with_os_actions",
    executionRoleArn=execution_role_arn,
    networkConfiguration={"networkMode": "PUBLIC"},
)
browser_id = created["browserId"]

# Start session
session = runtime_client.start_browser_session(
    browserIdentifier=browser_id,
    name="os-actions-demo",
    sessionTimeoutSeconds=3600,
    viewPort={"width": 1920, "height": 1080},
)
session_id = session["sessionId"]
time.sleep(3)

# Invoke actions — no manual SigV4 required
def invoke(action: dict) -> dict:
    resp = runtime_client.invoke_browser(
        browserIdentifier=browser_id, sessionId=session_id, action=action
    )
    return resp["result"]

invoke({"mouseClick": {"x": 600, "y": 370, "button": "LEFT"}})
invoke({"keyType": {"text": "Hello World"}})
invoke({"keyShortcut": {"keys": ["ctrl", "s"]}})

result = invoke({"screenshot": {"format": "PNG"}})
# Screenshot comes back as bytes from boto3 (blob type)
img_bytes = result["screenshot"]["data"]
```

The boto3 approach is simpler for most use cases. The manual SigV4 approach in `os_actions.py` is useful when you need direct HTTP control or are calling from a non-Python environment.

## Further Reading

- [AgentCore Browser InvokeBrowser API](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html)
- [AgentCore Browser documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html)
