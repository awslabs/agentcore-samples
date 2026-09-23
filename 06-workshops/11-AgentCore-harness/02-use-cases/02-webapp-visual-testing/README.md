# 02 — Webapp Visual Testing Agent

An AI-powered **automated visual QA** pipeline: hand the agent a web app and it builds it, runs it, tests it, and returns screenshots of every step — all inside the isolated Harness microVM.

## What's in this folder

| File | Type | What it does |
|---|---|---|
| [`02_webapp_visual_testing_agent.ipynb`](02_webapp_visual_testing_agent.ipynb) | Notebook | End-to-end demo: agent generates a TodoMVC app, serves it on `localhost:3000`, installs Puppeteer, writes + runs a test script, takes screenshots, pulls them back into the notebook. |

## The idea

The Harness microVM is a full Linux ARM64 environment with its own filesystem and network stack. That means the agent can:

1. Install system tools (`apt-get install chromium`)
2. Clone or generate a web app
3. Start a web server on `localhost`
4. Install `puppeteer-core` and drive a headless browser
5. Capture screenshots and save them to `/tmp`
6. Return them to you for review

All in isolation. Nothing touches your local machine.

## Why this is interesting

The "clone → build → serve → test → screenshot" pattern unlocks:

- **CI/CD visual validation** — After every commit, an agent spins up the app, runs visual tests, flags regressions before code review
- **Cross-version comparison** — Build two versions side by side, screenshot both, diff them
- **Exploratory QA** — Give the agent a URL and say *"find anything that looks broken"* — it navigates, interacts, reports
- **Automated docs** — Agent walks through the app and generates an annotated screenshot tour
- **Feed back to the agent** — *"Do these screenshots look correct?"* — closing the visual-QA loop entirely

## Key insight

Puppeteer **runs inside the same VM** as the web server, so `localhost:3000` just works from the browser tool — no network-isolation issues. (This is one of the advantages of keeping the browser tool and the VM shell in the same network namespace.)

## Notebook walkthrough

| Part | What happens |
|---|---|
| **0** | Setup — IAM role, boto3 clients, load beta service models |
| **1** | Create Harness with a **Node.js 20 container** attached |
| **2** | Prepare environment — `apt-get install chromium`, generate a self-contained TodoMVC app, start `npx serve` on port 3000, `npm install puppeteer-core` |
| **3** | **Agent writes and runs the tests** — we describe the test steps in natural language, the agent writes a Puppeteer script, runs it, and saves screenshots |
| **4** | Pull screenshots back via `ExecuteCommand` (base64-encoded) and display inline |
| **5** | Cleanup |

## The test flow (Part 3)

The agent is told (in plain English) to:

1. Launch Chromium headless, open `http://localhost:3000`
2. Screenshot → `/tmp/screenshot_1.png` (empty app)
3. Add three todos
4. Screenshot → `/tmp/screenshot_2.png` (three todos)
5. Click the first todo's checkbox to mark it complete
6. Screenshot → `/tmp/screenshot_3.png` (one completed)
7. Close the browser

The agent writes the Puppeteer script itself, runs it with `node /tmp/test.mjs`, then lists the screenshots. We pull them back and render inline.

## How to run

```bash
cd 02-use-cases/02-webapp-visual-testing
jupyter notebook 02_webapp_visual_testing_agent.ipynb
# or open in VSCode
```

Run cells top-to-bottom. **Part 2** takes ~1 minute because `npm install puppeteer-core` downloads a big dependency tree.

## How to adapt this for your own app

Replace the TodoMVC generation step with your own build:

```python
# Part 2 — generic pattern
run_command("git clone https://github.com/your/repo /tmp/app")
run_command("cd /tmp/app && npm install && npm run build")
run_command("cd /tmp/app && nohup npm start > /tmp/server.log 2>&1 &")
run_command("cd /tmp && npm install puppeteer-core")
# Then Part 3 — describe your test scenarios to the agent in natural language
```

Everything else — test generation, execution, screenshot capture — stays the same.

## Known limitation

The standalone `agentcore_browser` tool (separate from this pattern) **cannot** access services on the VM's `localhost` because it runs in a different network namespace. See the feature request in the Harness launch article. This notebook works around that by running **Puppeteer directly inside the VM** via `ExecuteCommand`, so both the browser and the server share the same network stack.
