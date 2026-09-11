# AgentCore Warmup Demo

## Introduction

A demonstration application showcasing how pre-emptive warmup of AgentCore runtime reduces latency in agentic chatbots.
AgentCore Runtime can deploy agent code using code deployment or container deployment.
When AgentCore Runtime receives the first invocation, it allocates a new VM. In case of container deployment there are 10 VM in a warm pool, and up to 10 new sessions are served from that pool, with sub-second cold starts. From the 11th session, new VMs must be allocated, the container downloaded and started. This will make the cold start longer and dependent on the size of the container.
For code deployment, AgentCore Runtime manages a larger pool of instances and only pushes the agent code. This ensure a more consistent startup time and higher session per second limit, but the cold start will be around 2-3 seconds.

For agentic chat applications, a low TTFT is paramount for a good user experience. This sample show a pattern to pre-emptively warmup agent core instances so that when the user actually send the message to the agent, the VM has already been initialized.

## Features

- **Normal Mode**: Agent invoked only when user sends messages (cold start latency)
- **Optimized Mode**: Agent pre-warmed with ping request on session creation (reduced latency)
- Real-time latency tracking and comparison
- Cognito authentication integration
- React + TypeScript frontend with Tailwind CSS
- Express backend serving configuration

## Setup

1. Install dependencies:

```bash
pnpm install
```

2. Run `uvx --from bedrock-agentcore-starter-toolkit agentcore identity setup-cognito`

3. Run `uxv --from bedrock-agentcore-starter-toolkit agentcore configure`

- Select the `agent/main.py` as agent code.
- Give a name to your agent
- You can choose code deploy or container deployment to test both strategies. For container deployment, see what happens up to 10 sessions, and what happens on the 11th session.
- Setup OAuth authorization
  - use the discovery url and clientId from the `.agentcore_identity_cognito_user.json` file under the `runtime` section

4. Run `uxv --from bedrock-agentcore-starter-toolkit agentcore deploy`

5. Start the development server:

```bash
pnpm dev
```

This will start:

- Express server on http://localhost:3001
- Vite dev server on http://localhost:3000

## How It Works

### Normal Mode

- Agent is invoked with `{"prompt": "user_message"}` only when user presses Enter
- Experiences cold start latency on first invocation

### Optimized Mode

- Agent is pre-warmed with `{"ping": "now"}` when session is created
- Subsequent invocations have reduced latency due to warm runtime
- Warmup happens automatically when accessing chat or creating new session

## How Pre-emptive Warmup Works

### The Problem: Cold Start Latency

When AgentCore Runtime receives the first invocation for a new session, it must:

1. **Initialize VM (cold start)** - Allocate compute resources
2. **Load dependencies** - Import libraries and initialize the runtime environment
3. **Execute agent code** - Process the actual request

This initialization process introduces significant latency, typically:

- **Code deployment**: 2-3 seconds cold start
- **Container deployment**: Sub-second for first 10 sessions (warm pool), longer for subsequent sessions

For conversational AI applications, this cold start directly impacts **Time to First Token (TTFT)**, degrading user experience.

### The Solution: Pre-emptive Warmup

Pre-emptive warmup eliminates cold start latency by initializing the agent runtime **before** the user sends their first message. The key innovation is executing warmup **in parallel** with user activity.

#### Normal Mode Flow

```
User sends message
        ↓
Initialize VM (cold start) ← 2-3 seconds latency
        ↓
Load dependencies
        ↓
Execute agent
        ↓
Response to user
```

**Result**: User experiences full cold start latency (2-3s TTFT)

#### Optimized Mode Flow (Parallel Execution)

```
Session Created
    ↓
    ├─→ [Background Process]
    │       Warmup ping request
    │           ↓
    │       Initialize VM (cold start)
    │           ↓
    │       Load dependencies
    │           ↓
    │       Agent ready ✓
    │
    └─→ [Foreground Activity]
            User types message...
            User reviews UI...
                ↓
            User sends message
                ↓
            Wait for warmup completion (if needed)
                ↓
            Execute agent (already warm!)
                ↓
            Response to user
```

**Result**: User experiences minimal latency (~100-300ms TTFT) because VM is already initialized

### Sequence Diagram Comparison

```
NORMAL MODE:
┌──────┐                    ┌──────────┐
│ User │                    │ AgentCore│
└──┬───┘                    └────┬─────┘
   │                             │
   │ "Hello" message             │
   │────────────────────────────>│
   │                             │
   │                    [Initialize VM]
   │                    [Load dependencies]
   │                             │
   │ Response (2-3s later)       │
   │<────────────────────────────│
   │                             │

OPTIMIZED MODE:
┌──────┐                    ┌──────────┐
│ User │                    │ AgentCore│
└──┬───┘                    └────┬─────┘
   │                             │
   │ [Session created]           │
   │ Warmup ping ───────────────>│
   │                             │
   │                    [Initialize VM]
   │ [User typing...]   [Load dependencies]
   │                             │
   │                    [Agent ready ✓]
   │                             │
   │ "Hello" message             │
   │────────────────────────────>│
   │                             │
   │ Response (300ms)            │
   │<────────────────────────────│
   │                             │
```

## Implementation Details

### 1. Agent Handler with Ping Support

The agent code in `agent/main.py` distinguishes between warmup pings and actual prompts:

```python
@app.entrypoint
async def main(message, context):
    # Warmup ping - lightweight response to initialize runtime
    if "ping" in message:
        yield {"status": "pong"}

    # Actual prompt - process with LLM
    if "prompt" in message:
        async for event in agent.stream_async(message["prompt"]):
            if "data" in event:
                yield event["data"]
```

**Key Points**:

- Ping requests are minimal - just return immediately
- This initializes the VM and loads all dependencies
- No expensive LLM calls during warmup
- Agent runtime is fully initialized after ping completes

### 2. Session State Management

The frontend tracks warmup state per session using Promises:

```typescript
// Track warmup state per session
const sessionWarmupState = new Map<
  string,
  { isWarmedUp: boolean; warmupPromise: Promise<void> | null }
>();

function getSessionState(sessionId: string) {
  if (!sessionWarmupState.has(sessionId)) {
    sessionWarmupState.set(sessionId, {
      isWarmedUp: false,
      warmupPromise: null,
    });
  }
  return sessionWarmupState.get(sessionId)!;
}
```

**Key Points**:

- Each session has independent warmup state
- Uses Promise-based synchronization
- Tracks both completion status and in-flight warmup

### 3. Automatic Warmup Trigger

Warmup is triggered automatically when entering optimized mode:

```typescript
// In Chat.tsx - Warmup on session creation in optimized mode
useEffect(() => {
  if (mode === "optimized" && !isWarmedUp && config.agentArn) {
    const warmupPromise = warmupAgent();
    setSessionWarmupPromise(sessionId, warmupPromise);
  }
}, [mode, sessionId]);

const warmupAgent = async () => {
  if (!config.agentArn) return;

  try {
    await invokeAgent(
      config.agentArn,
      config.region,
      sessionId,
      { ping: "now" }, // Warmup payload
      accessToken,
    );
    setIsWarmedUp(true);
  } catch (error) {
    console.error("Warmup failed:", error);
  }
};
```

**Key Points**:

- Fires immediately when session is created in optimized mode
- Runs asynchronously - doesn't block UI
- Stores warmup promise for synchronization

### 4. Wait Mechanism for User Requests

Before processing user prompts, the system ensures warmup completes:

```typescript
async function waitForWarmup(sessionId: string): Promise<void> {
  const state = getSessionState(sessionId);
  if (state.isWarmedUp) return; // Already warm, proceed immediately
  if (state.warmupPromise) {
    await state.warmupPromise; // Wait for in-flight warmup
  }
}

export async function invokeAgentStream() {
  // ... parameters
  // If this is a prompt request (not a ping), wait for warmup first
  if (payload.prompt && !payload.ping) {
    await waitForWarmup(sessionId);
  }

  // Now invoke the agent...
}
```

**Key Points**:

- Non-blocking for warmup pings
- Blocks user prompts only if warmup isn't complete yet
- In practice, warmup usually completes before user finishes typing

## Performance Impact

### Expected Improvements

| Metric                  | Normal Mode      | Optimized Mode   | Improvement          |
| ----------------------- | ---------------- | ---------------- | -------------------- |
| **First Message TTFT**  | 2000-3000ms      | 100-300ms        | **~90% reduction**   |
| **Subsequent Messages** | 100-300ms        | 100-300ms        | Same (already warm)  |
| **User Experience**     | Noticeable delay | Instant response | Significantly better |

### Container vs Code Deployment

**Container Deployment** (10 VM warm pool):

- Sessions 1-10: Sub-second cold starts from pool
- Session 11+: Full container download + initialization
- Warmup benefit: High for sessions beyond pool capacity

**Code Deployment** (larger managed pool):

- More consistent 2-3s cold starts
- Higher session/second limits
- Warmup benefit: Consistent across all sessions

### When Warmup is Most Beneficial

✅ **High Impact Scenarios**:

- First message in a conversation (biggest TTFT improvement)
- Code deployment strategy (predictable 2-3s savings)
- High traffic periods (when warm pool is exhausted)
- User onboarding flows (first impressions matter)

⚠️ **Lower Impact Scenarios**:

- Container deployment with low traffic (warm pool handles it)
- Long conversations (subsequent messages already warm)
- Batch processing (latency less critical)

## Architecture

- **Backend** (`server/index.js`): Express server that reads AgentCore configuration and exposes it via `/config.json`
- **Frontend** (`src/`): React + TypeScript application with:
  - Cognito authentication
  - Agent invocation with session management
  - Latency tracking and visualization
  - Mode comparison UI

## Configuration

The app reads configuration from:

- `.bedrock_agentcore.yaml`: Agent configuration including ARN and region
- `.agentcore_identity_cognito_user.json`: Cognito authentication credentials

## Cleanup

To clean up resources created during the setup, run the following commands:

```bash
uvx --from bedrock-agentcore-starter-toolkit agentcore destroy
uvx --from bedrock-agentcore-starter-toolkit agentcore identity cleanup
```

This will remove the deployed agent and associated Cognito resources.
