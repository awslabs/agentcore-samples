# Travel Guide Agent

| Information         | Details                                                                    |
|:--------------------|:---------------------------------------------------------------------------|
| Tutorial type       | Use Case                                                                   |
| Agent type          | Travel guide / multi-tool assistant                                        |
| Agentic Framework   | None (direct boto3)                                                        |
| LLM model           | Anthropic Claude Haiku 4.5                                                 |
| Tutorial components | harness, memory, Browser tool, MCP (Exa), Code Interpreter                 |
| Example complexity  | Advanced                                                                   |

## Overview

A complete travel guide agent that showcases all core harness features working together:
HTML generation, AgentCore memory for multi-turn conversations, the browser tool for live
web data, and combining Exa search with Code Interpreter for data visualization.

## Architecture

```
travel_agent.py
│
├── Part 1: create_harness (control plane)
│
├── Part 2: invoke_harness → HTML travel guide
│              └─ file_operations + shell tools (built-in)
│
├── Part 3: fetch HTML from microVM → save to local file
│
├── Part 4: create_memory → update_harness (attach memory)
│              └─ multi-turn: agent remembers user name + preferences
│
├── Part 5: invoke_harness (browser tool)
│              └─ agentcore_browser → live Amsterdam weather
│
└── Part 6: invoke_harness (MCP search) → invoke_harness (code_interpreter)
               ├─ remote_mcp (Exa) → tourism JSON data
               └─ agentcore_code_interpreter → matplotlib chart
```

## Sample Prompts

**Prompt (Part 2)**: "Recommend three fun things to do in NYC on a rainy day. Save as self-contained HTML with swipeable cards."
**Expected Behavior**: Agent creates `/tmp/travel.html` with dark theme, three activity cards, CSS/JS inline.

**Prompt (memory Turn 1)**: "My name is John Doe and I love electronic music — deep house, nu-disco."
**Expected Behavior**: Agent acknowledges and stores the preferences in memory.

**Prompt (memory Turn 2)**: "What's my name and what music do I like? Recommend a venue in Amsterdam."
**Expected Behavior**: Agent recalls name and music preference from memory, suggests a specific venue.

**Prompt (Browser)**: "Check the weather forecast for Amsterdam this week and save as an HTML weather card."
**Expected Behavior**: Agent browses a weather site, creates `/tmp/weather.html` with day cards showing temperature and conditions.

## observability

Every harness invocation automatically generates traces in CloudWatch via AgentCore observability.
The traces show each step of the agent loop: model calls, tool invocations, and timing details.

**Setup**: Enable [Transaction Search](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Transaction-Search-getting-started.html)
in CloudWatch (one-time, per account) to view agent traces in X-Ray.

```python
import boto3
xray = boto3.client("xray", region_name="us-west-2")
rules = xray.get_indexing_rules()
sampling = rules["IndexingRules"][0]["Rule"]["Probabilistic"]["DesiredSamplingPercentage"]
print(f"Transaction Search sampling: {sampling}%")
```

Navigate to **CloudWatch → X-Ray → Traces** to see the full agent loop breakdown for each invocation.

## Key Concepts

**memory provisioning**: memory takes 3-5 minutes to become `ACTIVE`. The script polls with a 10-second interval. Use `--skip-memory` to bypass this during quick testing.

**Same session, different tools**: Parts 6 uses two consecutive `invoke_harness` calls with the same `session_id`. The VM state (files) persists between calls, so the chart generator can read the JSON file from the search step.

**HTML output handling**: Instead of rendering in a notebook iframe, the script saves HTML files to `/tmp/` on your local machine. Open with `open /tmp/travel_guide.html` (macOS) or `xdg-open` (Linux).

**Browser tool limitations**: The browser tool browses real websites. Results depend on network accessibility and site availability at invocation time.

## Troubleshooting

### Issue: memory creation fails with `ConflictException`
**Solution**: A memory named `TravelGuideMemory` already exists — usually left behind by an earlier run that did not finish cleaning up. The script only deletes memories it created itself, so it will not silently reuse and then delete one it does not own; it prints a warning and skips the memory demo. Delete the leftover memory (`aws bedrock-agentcore-control delete-memory --memory-id <id>`), or re-run with `--skip-memory`.

### Issue: Part 4 takes too long
**Solution**: memory provisioning takes 3-5 minutes. Use `--skip-memory` to skip the memory demo entirely.

### Issue: Browser tool returns no data
**Solution**: The `agentcore_browser` tool requires internet access from the microVM. If your AWS account uses restricted VPC configurations, outbound browsing may be blocked.

## AgentCore CLI

Create and deploy a travel agent harness via the CLI:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
agentcore create --name travelguideagent --model-provider bedrock
agentcore deploy
```

Add memory to your harness project:

```bash
agentcore add memory
agentcore deploy
```

Invoke with the CLI:

```bash
agentcore invoke --harness travelguideagent \
  --session-id "$(uuidgen)" \
  "Recommend three fun things to do in NYC on a rainy day."
```

Reuse the same `--session-id` to continue conversations with memory context.

## Clean Up

```python
# harness
control.delete_harness(harnessId=harness_id)

# memory (if created)
control.delete_memory(memoryId=memory_id)

# IAM role
from utils.iam import delete_harness_role
delete_harness_role()
```

## Running the Python Scripts

```bash
pip install -r ../../requirements.txt
```

```bash
# Full demo (includes 3-5 min memory wait)
python travel_agent.py

# Skip memory provisioning (faster)
python travel_agent.py --skip-memory

# Keep resources after demo
python travel_agent.py --skip-cleanup
```

To run the local chat server:
```bash
pip install fastapi uvicorn sse-starlette
export HARNESS_ARN="arn:aws:bedrock-agentcore:REGION:ACCOUNT:harness/HARNESS_ID"
python travel_chat/server.py
# Open http://localhost:8000
```
