# 05 — Agent Skills

Extend harness agent with **Agent Skills** — bundles of files, code, and instructions installed on the agent's microVM filesystem. Skills give the agent domain-specific capabilities (e.g., generating spreadsheets, creating financial reports) without retraining the model.

## What's in this folder

| File | Type | What it does |
|---|---|---|
| [`05_agent_skills.ipynb`](05_agent_skills.ipynb) | Notebook | Install skills, invoke with `skills` parameter, multiple skills per session, advanced financial-report example. |

## What you'll learn

- What **Agent Skills** are and why they matter
- How to install skills on the agent's VM (via `invoke_agent_runtime_command`)
- Using the `skills` parameter in invoke calls
- Working with file-format skills (xlsx, pdf, docx)
- Installing multiple skills per session
- When to use Skills vs other approaches (MCP, custom containers, etc.)

## Notebook structure

- **Part 0-1:** Setup + create a standard Harness
- **Part 2:** Install Agent Skills on the VM + verify installation
- **Part 3:** Using skills in invocations (Travel Budget Spreadsheet example)
- **Part 4:** Installing multiple skills in one session
- **Part 5:** Advanced example — Financial Report generation
- **Part 6:** Best practices (session lifecycle, errors, custom skills)
- **Part 7:** When to use Skills vs other approaches
- **Cleanup:** Delete Harness + IAM role

## How to run

```bash
cd 05-agent-skills
jupyter notebook 05_agent_skills.ipynb
# or open in VSCode
```

Run cells top-to-bottom. Part 2 (install skills) must run before any invocation that uses them.

## Key takeaway

Skills live on the agent's filesystem and are referenced by `path` in the `skills` parameter. Install once per session, then use across multiple invocations:

```python
# Install the skill once
command_client.invoke_agent_runtime_command(
    harnessArn=harness_arn,
    runtimeSessionId=session_id,
    command="npx skills add xlsx",
)

# Then reference in invocations
response = client.invoke_harness(
    harnessArn=harness_arn,
    runtimeSessionId=session_id,
    messages=[{"role": "user", "content": [{"text": "Create a budget spreadsheet..."}]}],
    skills=[{"path": "/tmp/skills/xlsx"}],
)
```

## Skills vs other approaches

| Need | Use |
|---|---|
| External API / dynamic data | **MCP tool** |
| Custom runtime / deps at VM level | **Custom container** |
| Pre-packaged capability (file formats, templates) | **Skills** |
| Lightweight one-shot instruction | **System prompt** |
