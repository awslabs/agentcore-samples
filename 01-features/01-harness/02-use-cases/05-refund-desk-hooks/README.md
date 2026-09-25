# Refund Desk — Harness Lifecycle Hooks

![Guided tour and hook activity](images/hook_timeline.png)

## Overview

### Use case details
| Information         | Details                                                                                  |
|---------------------|------------------------------------------------------------------------------------------|
| Use case type       | Conversational                                                                           |
| Agent type          | Single agent                                                                             |
| Use case components | Harness lifecycle hooks (Lambda, SNS, EventBridge targets), inline function tools        |
| Use case vertical   | Customer support / e-commerce                                                            |
| Example complexity  | Intermediate                                                                             |
| SDK used            | boto3 (`bedrock-agentcore`, `bedrock-agentcore-control`), FastAPI, React                 |

A customer-support agent that issues refunds, built to show off **AgentCore Harness lifecycle hooks** end to end. It's the same app shape as the weather agent, but everything except hooks is stripped out.

Hooks run at four points in the agent loop. **Only Lambda targets can change behavior**, by returning `allow` or `deny`, and what a `deny` does depends on the event. The demo has one hook for each case:

| Hook | Event | Target | Demo | Effect of `deny` |
|---|---|---|---|---|
| `screen_request` | `before_invocation` | λ Lambda | Blocks prompt-injection attempts | The agent never starts (`stopReason: hook_stopped`) |
| `refund_policy` | `before_tool_call` | λ Lambda | Blocks refunds over $500, on undelivered or already-refunded orders, and emails to non-customers | **Only that tool call is skipped.** The loop continues and the model explains or escalates |
| `validate_result` | `after_tool_call` | λ Lambda | Verifies the HMAC signature on client-supplied refund receipts | Keeps the tool result, then **stops the invocation** |
| `audit_tool_calls` | `after_tool_call` | ✉ SNS | Publishes every tool result to an audit topic | Fire-and-forget, no decision |
| `token_budget` | `after_invocation` | λ Lambda | Flags turns over a 300 output-token budget | **Reported only**, since the answer has already streamed |
| `usage_meter` | `after_invocation` | ⇶ EventBridge | Sends token usage to a bus for metering | Fire-and-forget, no decision |

The tools (`lookup_order`, `issue_refund`, `send_email`) are **inline functions**. The harness hands each call back to the backend, which runs it against a fake order DB and resumes with a `toolResult` in a new `InvokeHarness` request. That's the flow the docs warn about ("don't trust client-supplied results"), and `validate_result` shows how to guard it.

The web app has three columns:
- **Start here**, a guided tour of six numbered steps, one hook behavior each. Every step prepares its own setup (resetting orders, tampering with receipts, breaking a hook, switching `failureMode`), runs in a fresh session, and then restores the setup.
- **Conversation**, the chat with the agent. You can also type your own messages.
- **What the hooks did**, which gives a plain-English summary of each turn above every `hookEvent` from the stream (event, hook, decision, reason), interleaved with tool handoffs and each `InvokeHarness` request. Before anything runs, it shows where each hook sits in the agent loop. The panel has two more tabs:
  - **Configure hooks**: switch hooks on and off, flip `failureMode` (each change calls `UpdateHarness` live), simulate a broken hook, and turn on receipt tampering for messages you type.
  - **Behind the scenes**: the fake order DB, the email outbox, and what the SNS and EventBridge hooks actually delivered (via SQS), with a running token tally.

![Configure hooks](images/hooks_panel.png)

## Prerequisites

* Python 3.10+ and Node.js 18+
* AWS CLI with credentials, in a region where AgentCore Harness is available (default `us-east-1`)
* Model access to Claude Haiku 4.5 in Amazon Bedrock
* `boto3 >= 1.43.102`, because older versions don't have the `hooks` parameter (`start.sh` installs it in `venv/`)

## Use case setup and execution

```bash
./start.sh
```

This one command installs dependencies, provisions the AWS resources (four Lambdas, SNS, EventBridge, SQS, IAM roles and the harness), then starts the backend and frontend. Open **http://localhost:5173** and follow the guided tour below.

To stop the servers, press `Ctrl+C`. The next `./start.sh` reuses the resources.

## The guided tour (sample prompts)

| Step | What happens |
|---|---|
| 1. A normal refund | Every hook allows it. The refund is issued, the receipt verified, the email sent |
| 2. A refund over the $500 limit | `refund_policy` denies `issue_refund`. The call comes back as an error result, the agent keeps going and tells the customer it's escalated |
| 3. A prompt injection | `screen_request` denies it before the agent starts, so no model call is made |
| 4. A tampered tool result | The backend alters the signed receipt, `validate_result` catches the signature mismatch, and the invocation stops |
| 5. A broken hook, `failureMode: deny` | `screen_request` is made to sleep past its 3s timeout: `Hook target timed out after 3 seconds`, then `hook_stopped` |
| 6. Same broken hook, `failureMode: allow` | The same timeout, but the request goes through |

To try more, type your own messages (for example "refund order 1003", which isn't delivered yet). Or use **Configure hooks** to turn hooks off, or set *Simulate a broken hook* to *raises* to see `Hook target invocation failed`.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite) — http://localhost:5173                     │
│  Guided tour │ Conversation │ What the hooks did / Configure / Behind │
└───────────────┬──────────────────────────────────────────────────────┘
                │ SSE /api/chat, REST /api/hooks, /api/chaos, ...
┌───────────────▼──────────────────────────────────────────────────────┐
│  Backend (FastAPI) — http://localhost:8000                           │
│  agent.py   InvokeHarness loop: stream events → UI, run inline tools,│
│             resume with toolResult on the same session               │
│  tools.py   lookup_order / issue_refund (HMAC-signed) / send_email   │
│  hooks.py   hook definitions → UpdateHarness (replaces whole list)   │
│  notifications.py  long-polls the SQS feed queue                     │
└───────────────┬──────────────────────────────────────────────────────┘
                │ InvokeHarness
┌───────────────▼──────────────────────────────────────────────────────┐
│  AgentCore Harness (Claude Haiku 4.5, inline function tools)         │
│                                                                      │
│  before_invocation ──► λ screen_request                              │
│  before_tool_call  ──► λ refund_policy                               │
│  after_tool_call   ──► λ validate_result   ✉ SNS audit topic ──┐     │
│  after_invocation  ──► λ token_budget      ⇶ EventBridge bus ──┤     │
└────────────────────────────────────────────────────────────────┼─────┘
                                                   SQS feed queue ◄┘
```

## Clean up instructions

```bash
./cleanup.sh
```

Deletes the harness, the Lambdas and their log groups, the SNS topic, the EventBridge bus and rule, the SQS queue, and both IAM roles.

## Disclaimer
The examples provided in this repository are for experimental and educational purposes only. They demonstrate concepts and techniques but are not intended for direct use in production environments. Make sure to have Amazon Bedrock Guardrails in place to protect against [prompt injection](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-injection.html).
