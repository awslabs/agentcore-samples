# Refund Desk: Amazon Bedrock AgentCore Harness Lifecycle Hooks

Refund Desk is a customer-support agent that looks up orders, issues refunds and emails customers. The model decides which tools to call, but **lifecycle hooks** enforce the business rules in code: they screen each request before the agent starts, approve or block each tool call, validate tool results, and report usage after every turn. The model cannot skip or talk its way past them.

The sample runs as a local web app with a six-step guided tour. Each step triggers a different hook behavior, and the app shows what each hook decided and why, next to the conversation. It covers all four lifecycle events (`before_invocation`, `before_tool_call`, `after_tool_call`, `after_invocation`), all three target types (AWS Lambda, Amazon SNS and Amazon EventBridge), `allow` and `deny` decisions, and what happens when a hook fails under `failureMode: deny` and `failureMode: allow`.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Web app (React + Vite), http://localhost:5173                          │
│  Guided tour  |  Conversation  |  Hook activity / Configure / Behind    │
└──────────────┬──────────────────────────────────────────────────────────┘
               │ (1) chat, hook settings
┌──────────────▼──────────────────────────────────────────────────────────┐
│  Backend (FastAPI), http://localhost:8000                               │
│  Runs the inline tools: lookup_order, issue_refund, send_email          │
└──────────────┬───────────────────────────────────────────────────▲──────┘
               │ (2) InvokeHarness, UpdateHarness                  │ (7) reads
┌──────────────▼──────────────────────────────────────────┐  ┌─────┴──────┐
│  Amazon Bedrock AgentCore Harness                       │  │ Amazon SQS │
│  Model: Anthropic Claude Haiku 4.5                      │  │ queue      │
│                                                         │  │            │
│  (3) before_invocation ──► AWS Lambda: screen_request   │  │            │
│  (4) before_tool_call  ──► AWS Lambda: refund_policy    │  │            │
│  (5) after_tool_call   ──► AWS Lambda: validate_result  │  │            │
│                        ──► Amazon SNS: audit topic ─────┼─►│            │
│  (6) after_invocation  ──► AWS Lambda: token_budget     │  │            │
│                        ──► Amazon EventBridge: bus ─────┼─►│            │
└─────────────────────────────────────────────────────────┘  └────────────┘
```

How a request flows through the system:

1. You send a message from the web app. The backend calls `InvokeHarness` and streams the response back, including a `hookEvent` for every hook decision. Changes in the **Configure hooks** tab call `UpdateHarness`.
2. The harness runs the agent loop with Anthropic Claude Haiku 4.5. The tools are **inline functions**: when the model calls one, the harness hands the call back to the backend. The backend runs it against a sample order list (`lambdas/orders.json`) and resumes the session with the `toolResult`.
3. **Before the agent starts**, the `screen_request` Lambda function checks the prompt. A `deny` stops the request before the model is called.
4. **Before each tool call**, the `refund_policy` Lambda function checks the call against the refund rules. A `deny` skips only that call, and the agent continues and explains the outcome to the customer.
5. **After each tool call**, the `validate_result` Lambda function verifies the signature on the refund receipt, because the result comes from the client and could have been altered. A `deny` stops the run. An Amazon SNS topic receives every tool result for auditing.
6. **After the agent finishes**, the `token_budget` Lambda function flags long answers (report only, since the answer has already streamed), and token usage is sent to an Amazon EventBridge event bus for metering.
7. The Amazon SNS topic and the Amazon EventBridge rule deliver their messages to an Amazon SQS queue. The backend reads the queue so the web app can show what was delivered.

### Use case details
| Information         | Details                                                                                                 |
|---------------------|---------------------------------------------------------------------------------------------------------|
| Use case type       | Conversational                                                                                          |
| Agent type          | Single agent                                                                                            |
| Use case components | Amazon Bedrock AgentCore Harness lifecycle hooks (AWS Lambda, Amazon SNS and Amazon EventBridge targets), inline function tools |
| Use case vertical   | Customer support and e-commerce                                                                         |
| Example complexity  | Intermediate                                                                                            |
| SDK used            | AWS SDK for Python (Boto3), FastAPI, React                                                              |

### Lifecycle hooks in this sample

Only AWS Lambda targets can change the agent's behavior, by returning `allow` or `deny`. What a `deny` does depends on the event. Amazon SNS and Amazon EventBridge targets receive a notification and cannot block anything.

| Hook | Event | Target | What it does | Effect of `deny` |
|---|---|---|---|---|
| `screen_request` | `before_invocation` | AWS Lambda | Blocks prompt-injection attempts | The agent never starts (`stopReason: hook_stopped`) |
| `refund_policy` | `before_tool_call` | AWS Lambda | Blocks refunds over $500, on undelivered or already-refunded orders, and emails to non-customers | **Only that tool call is skipped.** The loop continues and the model explains or escalates |
| `validate_result` | `after_tool_call` | AWS Lambda | Verifies the HMAC signature on client-supplied refund receipts | Keeps the tool result, then **stops the invocation** |
| `audit_tool_calls` | `after_tool_call` | Amazon SNS | Publishes every tool result to an audit topic | Notification only, no decision |
| `token_budget` | `after_invocation` | AWS Lambda | Flags turns over a 300 output-token budget | **Reported only**, since the answer has already streamed |
| `usage_meter` | `after_invocation` | Amazon EventBridge | Sends token usage to an event bus for metering | Notification only, no decision |

The hook configuration is in `backend/hooks.py`, and the code for the Lambda functions is in `lambdas/`.

### The web app

![Guided tour and hook activity](images/hook_timeline.png)

The web app has three columns:
- **Start here**, a guided tour of six numbered steps, one hook behavior each. Every step prepares its own setup (resetting orders, tampering with receipts, breaking a hook, switching `failureMode`), runs in a fresh session, and then restores the setup.
- **Conversation**, the chat with the agent. You can also type your own messages.
- **What the hooks did**, which gives a plain-English summary of each turn above every `hookEvent` from the stream (event, hook, decision, reason), interleaved with tool handoffs and each `InvokeHarness` request. Before anything runs, it shows where each hook sits in the agent loop. The panel has two more tabs:
  - **Configure hooks**: switch hooks on and off, change `failureMode` (each change calls `UpdateHarness`), simulate a broken hook, and turn on receipt tampering for messages you type.
  - **Behind the scenes**: the sample orders, the email outbox, and the messages the Amazon SNS and Amazon EventBridge hooks delivered, with a running token count.

![Configure hooks](images/hooks_panel.png)

## Prerequisites

* Python 3.10+ and Node.js 18+
* AWS Command Line Interface (AWS CLI) with credentials, in an AWS Region where Amazon Bedrock AgentCore Harness is available (default `us-east-1`)
* Model access to Anthropic Claude Haiku 4.5 in Amazon Bedrock
* AWS SDK for Python (Boto3) 1.43.102 or later, because older versions don't have the `hooks` parameter (`start.sh` installs it in `venv/`)

## Use case setup and execution

```bash
./start.sh
```

This one command installs dependencies, creates the AWS resources (four AWS Lambda functions, an Amazon SNS topic, an Amazon EventBridge event bus and rule, an Amazon SQS queue, two AWS Identity and Access Management (IAM) roles and the harness), then starts the backend and the web app. Open **http://localhost:5173** and follow the guided tour below.

To stop the servers, press `Ctrl+C`. The next `./start.sh` reuses the resources.

## The guided tour (sample prompts)

| Step | What happens |
|---|---|
| 1. A normal refund | Every hook allows it. The refund is issued, the receipt verified, the email sent |
| 2. A refund over the $500 limit | `refund_policy` denies `issue_refund`. The call comes back as an error result, the agent keeps going and tells the customer it's escalated |
| 3. A prompt injection | `screen_request` denies it before the agent starts, so no model call is made |
| 4. A tampered tool result | The backend alters the signed receipt, `validate_result` catches the signature mismatch, and the invocation stops |
| 5. A broken hook, `failureMode: deny` | `screen_request` is made to sleep past its 3-second timeout: `Hook target timed out after 3 seconds`, then `hook_stopped` |
| 6. Same broken hook, `failureMode: allow` | The same timeout, but the request goes through |

To try more, type your own messages (for example "refund order 1003", which isn't delivered yet). Or use **Configure hooks** to turn hooks off, or set *Simulate a broken hook* to *raises* to see `Hook target invocation failed`.

## Clean up instructions

```bash
./cleanup.sh
```

Deletes the harness, the AWS Lambda functions and their Amazon CloudWatch Logs log groups, the Amazon SNS topic, the Amazon EventBridge event bus and rule, the Amazon SQS queue, and both IAM roles.

## Disclaimer
The examples provided in this repository are for experimental and educational purposes only. They demonstrate concepts and techniques but are not intended for direct use in production environments. Make sure to have Amazon Bedrock Guardrails in place to protect against [prompt injection](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-injection.html).
