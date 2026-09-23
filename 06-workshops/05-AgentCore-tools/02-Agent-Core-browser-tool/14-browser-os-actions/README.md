# AgentCore Browser Tool — OS-Level Actions (InvokeBrowser API)

This tutorial demonstrates how to use **OS-level actions** with Amazon Bedrock AgentCore Browser Tool via the `InvokeBrowser` API, using SigV4-signed REST calls.

## Overview

OS-level actions let you perform raw mouse, keyboard, screenshot, and scroll operations directly on the browser sandbox — bypassing the CDP/Playwright automation layer entirely. This is useful for interacting with:

- **OS-native dialogs** — file upload/download prompts, print dialogs, authentication pop-ups
- **Browser chrome elements** — address bar, extension popups, permission banners
- **Keyboard shortcuts** — Ctrl+S, Ctrl+A, Alt+Tab that CDP-based automation cannot send to the OS
- **Canvas / WebGL content** — where DOM selectors don't exist
- **Any element** that resists CDP-based automation

## Use Cases

- Automate file upload dialogs that Playwright cannot reach
- Send OS-level keyboard shortcuts (Ctrl+S, Ctrl+P) to the browser
- Interact with Canvas/WebGL applications using mouse coordinates
- Take screenshots of the full browser VM (including OS-level elements)
- Drag-and-drop operations at the OS level

## Architecture

```
┌──────────┐    SigV4-signed     ┌──────────────────────┐    OS-level     ┌─────────────────┐
│  Client   │ ──────────────────▶│  AgentCore Browser   │ ──────────────▶│  Browser Sandbox │
│ (Notebook │    REST calls      │  InvokeBrowser API   │    actions      │  (Headless VM)   │
│  / Script)│ ◀──────────────────│                      │ ◀──────────────│                  │
└──────────┘   JSON + screenshot └──────────────────────┘   results       └─────────────────┘
```

The `InvokeBrowser` API accepts SigV4-signed requests and translates them into OS-level input events executed inside an isolated browser sandbox VM.

## Getting Started

### Prerequisites

- Python 3.10 or later
- An AWS account with Amazon Bedrock AgentCore access enabled
- AWS credentials configured (`aws sts get-caller-identity`)
- An AWS Region where Amazon Bedrock AgentCore is available

> **Note:** The notebook creates all required resources (IAM role, custom browser) automatically. You do not need to pre-create any resources.

### Installation

```bash
pip install -r requirements.txt
```

### Run

```bash
jupyter notebook browser-os-actions.ipynb
```

Run the cells sequentially. The notebook walks through setup, OS-level actions, and cleanup.

## Notebook Walkthrough

The [browser-os-actions.ipynb](browser-os-actions.ipynb) notebook demonstrates:

### Setup

- Creates an IAM execution role with a trust policy for `bedrock-agentcore.amazonaws.com` and `InvokeBrowser`, `StartBrowserSession`, `StopBrowserSession` permissions
- Creates a custom AgentCore Browser with public network configuration
- Starts a browser session with OS-level actions enabled

### OS-Level Actions

1. **Mouse actions** — Click (left, right, middle, double-click), move, and drag operations at specific screen coordinates
2. **Scroll actions** — Vertical and horizontal scrolling with configurable deltas
3. **Keyboard actions** — Text typing, key presses (Enter, Tab, Escape, Backspace, arrows), and keyboard shortcuts (Ctrl+S, Ctrl+P, Ctrl+Shift+I)
4. **Screenshots** — Capture the full browser VM screen in PNG format and display inline

### Cleanup

Stops the browser session, deletes the custom browser, and removes the IAM role and policy.

## Sample Actions

```python
# Mouse click
invoke(endpoint, sid, {"mouseClick": {"x": 600, "y": 370, "button": "LEFT"}}, ...)

# Keyboard typing
invoke(endpoint, sid, {"keyType": {"text": "Hello World"}}, ...)

# Keyboard shortcut
invoke(endpoint, sid, {"keyShortcut": {"keys": ["ctrl", "s"]}}, ...)

# Screenshot
invoke(endpoint, sid, {"screenshot": {"format": "PNG"}}, ...)

# Mouse scroll
invoke(endpoint, sid, {"mouseScroll": {"x": 500, "y": 300, "deltaX": 0, "deltaY": -500}}, ...)
```

## Files

| File | Description |
|------|-------------|
| `browser-os-actions.ipynb` | Interactive tutorial notebook with setup, OS-level actions, and cleanup |
| `helpers/browser.py` | Helper functions for SigV4-signed requests and session management |
| `helpers/utils.py` | IAM role creation and cleanup utilities |
| `requirements.txt` | Python dependencies |
| `.env_sample` | Template for AWS credentials environment variables |
| `README.md` | This file |

## Security Considerations

- All API calls use SigV4 authentication — unauthenticated requests are rejected with HTTP 403
- Each browser session runs in an isolated sandbox VM with 1:1 session-to-VM mapping
- IAM role follows least-privilege with only `InvokeBrowser`, `StartBrowserSession`, and `StopBrowserSession` permissions
- Never commit AWS credentials — use `.env` (excluded via `.gitignore`) or `isengardcli creds`

## Additional Resources

- [Amazon Bedrock AgentCore Browser documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html)
- [Amazon Bedrock AgentCore Python SDK](https://github.com/aws/bedrock-agentcore-sdk-python)
