# Automated Visual QA with harness

| Information         | Details                                                               |
|:--------------------|:----------------------------------------------------------------------|
| Tutorial type       | Use Case                                                              |
| Agent type          | QA / visual testing agent                                             |
| Agentic Framework   | None (direct boto3)                                                   |
| LLM model           | Anthropic Claude Haiku 4.5                                            |
| Tutorial components | harness — Node.js container, ExecuteCommand, Puppeteer, screenshots   |
| Example complexity  | Advanced                                                              |

## Overview

Use the harness microVM as a complete CI/CD test environment. The agent builds a TodoMVC
web app, serves it on `localhost:3000`, writes Puppeteer test scripts in natural language,
runs headless browser tests, and the screenshots are pulled back to your local machine.

## Use Cases

The harness microVM is a full Linux environment with its own filesystem and network stack —
making it a natural fit for automated visual QA:

- **CI/CD pipelines** — After every commit, an agent spins up the app, runs visual tests, and flags UI regressions before code review.
- **Cross-version comparison** — Build two versions of the app side by side, screenshot both, and diff them.
- **Exploratory QA** — Give the agent a URL and say *"find anything that looks broken"* — it navigates, interacts, and reports.
- **Onboarding docs** — Agent walks through the app and generates an annotated screenshot walkthrough automatically.

## Architecture

```
webapp_visual_testing.py
│
├── Part 1: Create harness with node:20-slim container
│              └─ update_harness(containerConfiguration)
│
├── Part 2: ExecuteCommand to prepare the VM
│              ├─ apt-get install chromium
│              ├─ invoke_harness → agent generates index.html (TodoMVC)
│              ├─ npx serve -l 3000 (background)
│              └─ npm install puppeteer-core
│
├── Part 3: invoke_harness → agent writes /tmp/test.mjs
│              └─ Puppeteer: launch chromium, screenshots, todos, checkboxes
│
└── Part 4: ExecuteCommand → base64 screenshots → decode → save locally
               └─ /tmp/screenshot_1.png, _2.png, _3.png
```

## Sample Prompts

**Prompt (TodoMVC generation)**: "Create a self-contained TodoMVC HTML with inline CSS/JS."
**Expected Behavior**: Agent writes a complete todo app to `/tmp/todomvc/index.html`.

**Prompt (Puppeteer test)**: "Write and run a Puppeteer test that adds 3 todos, marks one complete, and takes screenshots at each step."
**Expected Behavior**: Agent writes `/tmp/test.mjs`, runs it, produces `screenshot_1.png`, `_2.png`, `_3.png`.

**Prompt (exploratory QA)**: "Navigate the todo app and identify any UX issues. Save a report."
**Expected Behavior**: Agent browses the app, interacts with features, writes a markdown QA report.

**Prompt (regression test)**: "Compare the current app with the expected layout. Flag any differences."
**Expected Behavior**: Agent takes reference and current screenshots, describes visual differences.

## Key Concepts

**Puppeteer inside the VM**: Puppeteer runs inside the same VM as the web server, so `localhost` just works — no port forwarding or network isolation issues.

**Agent-written tests**: Instead of writing test scripts yourself, you describe the test steps in natural language. The agent generates the Puppeteer script, runs it, and handles errors.

**Screenshot transfer**: Screenshots are binary files on the agent's VM. Use `base64` + `invoke_agent_runtime_command` to transfer them to your local machine.

**Production pipeline pattern**:
```
git clone → npm install → npm start      (via ExecuteCommand)
npm install puppeteer-core               (via ExecuteCommand)
invoke_harness → "Test the app at localhost:3000"  (agent writes + runs tests)
Pull screenshots → upload to S3 or attach to PR
```

## Troubleshooting

### Issue: Chromium not found at `/usr/bin/chromium`
**Solution**: The install command uses `apt-get install chromium`. On some Node.js base images, the path may differ. Run `which chromium || which chromium-browser` via ExecuteCommand to find it, then update the Puppeteer launch path.

### Issue: Screenshots are all blank or black
**Solution**: Chromium in headless mode may need `--no-sandbox --disable-setuid-sandbox` flags. Ensure the Puppeteer launch options include these. The test prompt explicitly requests them.

### Issue: Server not responding on port 3000
**Solution**: Check `/tmp/server.log` for errors via `run_command(harness_arn, session_id, "cat /tmp/server.log")`. The `npx serve` command requires network access to download on first run.

## AgentCore CLI

Create a harness with a Node.js container for visual testing via the CLI:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
agentcore create --name myqaagent --model-provider bedrock
```

In the interactive wizard, choose a **Custom Environment** and specify `public.ecr.aws/docker/library/node:20-slim` as the container URI. After setup:

```bash
agentcore deploy
agentcore invoke --harness myqaagent \
  --session-id "$(uuidgen)" \
  "Create a self-contained TodoMVC HTML app and write Puppeteer tests for it."
```

## Clean Up

```python
control.delete_harness(harnessId=harness_id)
from utils.iam import delete_harness_role
delete_harness_role()
```

## Running the Python Scripts

```bash
pip install -r ../../requirements.txt
```

```bash
python webapp_visual_testing.py

# Keep harness running for manual inspection via ExecuteCommand
python webapp_visual_testing.py --skip-cleanup
```

Screenshots are saved to `/tmp/screenshot_1.png`, `/tmp/screenshot_2.png`, `/tmp/screenshot_3.png`.

Open with:
```bash
open /tmp/screenshot_1.png  # macOS
```
