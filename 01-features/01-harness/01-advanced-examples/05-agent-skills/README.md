# Agent Skills

| Information         | Details                                                             |
|:--------------------|:--------------------------------------------------------------------|
| Tutorial type       | Advanced Example                                                    |
| Agent type          | Document and spreadsheet generation assistant                       |
| Agentic Framework   | None (direct boto3)                                                 |
| LLM model           | Anthropic Claude Haiku 4.5                                          |
| Tutorial components | AgentCore harness — skills parameter, Node.js container, xlsx skill |
| Example complexity  | Intermediate                                                        |

## Overview

Extend agent capabilities with pre-built skill bundles that provide specialized instructions,
code templates, and domain knowledge. Demonstrates the `xlsx` skill to create professional
Excel spreadsheets with formulas, formatting, and multiple sheets.

## What are Agent Skills?

Agent Skills are pre-built capability bundles installed on the agent's VM:

- **Specialized instructions** — step-by-step guidance for complex tasks
- **Code templates** — proven implementations for file formats (xlsx, pdf, docx)
- **Domain knowledge** — best practices and common patterns

Skills are especially valuable with smaller/cheaper models that lack built-in knowledge
of specialized file formats. The skill provides the knowledge the model needs.

```python
# Install skill on the VM
client.invoke_agent_runtime_command(
    agentRuntimeArn=harness_arn,
    runtimeSessionId=session_id,
    body={"command": "npx skills add https://github.com/anthropics/skills --skill xlsx --yes"},
)

# Use skill in an invocation
client.invoke_harness(
    harnessArn=harness_arn,
    runtimeSessionId=session_id,
    skills=[{"path": ".agents/skills/xlsx"}],
    messages=[...],
)
```

## Sample Prompts

**Prompt**: "Create a 5-day Amsterdam trip budget spreadsheet with EUR/USD columns, formulas, and formatting."
**Expected Behavior**: Agent uses xlsx skill to generate `/tmp/amsterdam_budget.xlsx` with currency formatting and SUM formulas.

**Prompt**: "Create a quarterly sales report with 3 sheets: Summary, Monthly Breakdown, Top Products."
**Expected Behavior**: Agent generates a multi-sheet report with conditional formatting, freeze panes, and formula-driven status columns.

**Prompt**: "Create a project tracking spreadsheet with 10 tasks, priorities, and % completion."
**Expected Behavior**: Agent creates a formatted task tracker with status columns and a summary row.

**Prompt**: "Make a comparison table of 5 programming languages by speed, ease, ecosystem."
**Expected Behavior**: Agent creates a formatted comparison table with color coding.

## Key Concepts

**Node.js container required**: The xlsx skill uses npm packages (`xlsx` or `exceljs`). Attach a Node.js container before installing skills.

**Session persistence**: Install the skill once in a session. Subsequent invocations in the same session can use the skill — the installation persists on the VM.

**File download**: Generated files live on the agent's remote VM. Use `invoke_agent_runtime_command` with `base64` to transfer them locally.

**Read all three outputs of a command**: `invoke_agent_runtime_command` streams `stdout`,
`stderr` *and* `contentStop.exitCode`. Check the exit code before treating the output as
good — a command that failed still streams whatever it managed to write, so stdout alone
cannot tell success from failure:

```python
for event in resp["stream"]:
    if "chunk" in event:
        chunk = event["chunk"]
        if "contentDelta" in chunk:
            stdout += chunk["contentDelta"].get("stdout", "")
            stderr += chunk["contentDelta"].get("stderr", "")
        elif "contentStop" in chunk:
            exit_code = chunk["contentStop"].get("exitCode")
```

**`node:slim` has no LibreOffice, and the skill wants one**: the xlsx skill recalculates a
finished workbook so that each formula also carries its *cached result* — the value
`openpyxl(data_only=True)` and pandas read without opening Excel. That step needs
LibreOffice (`soffice`), which the Node image does not ship. In practice the agent notices
and `apt-get install`s LibreOffice mid-run, so the downloaded files do come back fully
recalculated (`docProps/app.xml` names LibreOffice as the generating application). It works,
but it is a large package to install on every fresh session — expect the first spreadsheet
prompt to take noticeably longer. If the install is skipped or fails, the formulas are still
correct; only their cached values are missing, and `data_only=True` reads `None` until Excel
or LibreOffice opens the file. Attach an image that already includes `soffice` to avoid the
download entirely.

## Troubleshooting

### Issue: `npx skills add` command hangs
**Solution**: The command downloads from GitHub. Ensure outbound HTTPS is available from your microVM. First-run may take 2-3 minutes.

### Issue: Skill not found error during invocation
**Solution**: Verify the path exists: `ls -la .agents/skills/xlsx/`. Run the installation in the same session you're invoking from.

### Issue: `ConflictException: Cannot update agent ... while it is CREATING`
**Solution**: The harness was not READY before the next call was made. Creating a harness
takes ~150s on the public network and longer when a container image has to be pulled on top,
so poll to `READY` before calling `update_harness` or running a command. Use
`poll_harness_status` from `utils/harness.py`, which raises on timeout and on
`CREATE_FAILED`/`UPDATE_FAILED` instead of falling through silently.

### Issue: Generated xlsx file is empty or corrupted
**Solution**: Check that the agent's file path matches the base64 read command. The file must
exist on the VM before you can download it. This sample validates the transfer: it reports the
`base64` exit code and stderr when the file is missing, and verifies that what arrived is a zip
container (an `.xlsx` is a zip) before reporting a sheet count read from the file itself.

### Issue: The script reports the skill install failed
**Solution**: Read the command output above the error. The install runs
`apt-get update && apt-get install git -y && npx skills add ...`, so it needs a Debian-based
container (the `node:slim` image this sample attaches in Part 1) and outbound network access.
The script stops here deliberately — every later part depends on the skill being present.

## AgentCore CLI

Create and deploy a harness project via the CLI, then use `ExecuteCommand` to install skills in your session:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
agentcore create --name myskillsagent --model-provider bedrock
agentcore deploy
agentcore invoke --harness myskillsagent \
  --session-id "$(uuidgen)" \
  "Create a 5-day Amsterdam trip budget spreadsheet with EUR/USD columns and formulas."
```

Skills installation (`npx skills add`) happens programmatically via `invoke_agent_runtime_command` as shown in this tutorial.

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
python agent_skills.py
```

Downloaded files are saved to `/tmp/amsterdam_budget.xlsx` and `/tmp/q1_sales_report.xlsx`.
