# Migrating from the Bedrock AgentCore Starter Toolkit to the AgentCore CLI

This guide walks you through migrating an existing [Bedrock AgentCore Starter Toolkit](https://github.com/aws/bedrock-agentcore-starter-toolkit) project to the [AgentCore CLI](https://github.com/aws/agentcore-cli). The CLI provides a richer feature set including a terminal UI, CDK/CloudFormation-managed infrastructure, evaluations, local development server, and more.

## Overview

The AgentCore CLI includes an `agentcore import` command that automates the migration of your Starter Toolkit project. It reads your existing `.bedrock_agentcore.yaml` configuration, imports your deployed AWS resources (agents, memories, credentials) into a new CLI-managed CloudFormation stack, and copies your agent source code into the CLI project structure — all without downtime or resource re-creation.

## Prerequisites

- **Node.js** 20.x or later
- **uv** for Python agents ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **AWS credentials** configured (`aws configure`) with the same account/region as your Starter Toolkit deployment
- An existing Starter Toolkit project with a `.bedrock_agentcore.yaml` file

## Step 1: Install the AgentCore CLI

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

If you still have the old Python-based Starter Toolkit CLI installed, uninstall it to avoid command conflicts (both use the `agentcore` command name):

```bash
# Uninstall using whichever tool you originally used:
pip uninstall bedrock-agentcore-starter-toolkit    # if installed via pip
pipx uninstall bedrock-agentcore-starter-toolkit   # if installed via pipx
uv tool uninstall bedrock-agentcore-starter-toolkit # if installed via uv
```

## Step 2: Create a New CLI Project

Create a new AgentCore CLI project that will receive the imported resources:

```bash
agentcore create --name my-project
cd my-project
```

You can also use the interactive wizard by running `agentcore create` without flags.

## Step 3: Run the Import Command

From within your new CLI project directory, run the import command pointing to your Starter Toolkit YAML config file:

```bash
agentcore import --source /path/to/your/starter-toolkit-project/.bedrock_agentcore.yaml
```

### Command Options

| Option | Description |
| --- | --- |
| `--source <path>` | **(Required)** Path to the `.bedrock_agentcore.yaml` configuration file |
| `--target <target>` | Deployment target name (only needed if your CLI project has multiple targets) |
| `-y, --yes` | Auto-confirm prompts |

### What the Import Does

The import command performs a 3-phase migration:

1. **Configuration merge** — Parses your `.bedrock_agentcore.yaml` and merges agents, memories, and credentials into the CLI's `agentcore.json` configuration. Duplicate resources are automatically skipped.

2. **Source code copy** — Copies your agent source code into the CLI project's `app/<agent-name>/` directory. Excludes virtual environments (`.venv`), `__pycache__`, and other build artifacts. For container-based agents, Dockerfiles are also copied.

3. **CloudFormation resource import** — For deployed agents (those with an `agent_id`), the command uses CloudFormation's import mechanism to adopt your existing AWS resources (runtimes, memories) into the CLI-managed stack. This preserves your live resources without any downtime or re-creation.

### Example Output

```
[done]  Parsed starter toolkit config (2 agents, 1 memory, 1 credential)
[done]  Merged agent: search_agent
[done]  Merged agent: chat_agent
[done]  Merged memory: shared_memory
[done]  Copied source: app/search_agent/
[done]  Copied source: app/chat_agent/
[done]  Phase 1: Updated companion resources
[done]  Phase 2: Imported existing resources into stack

Import complete!

Imported:
  Stack: AgentCore-my-project-default
  Agent: search_agent
  Agent: chat_agent
  Memory: shared_memory

To continue:

  agentcore deploy     Deploy the imported stack
  agentcore status     Verify resource status
  agentcore invoke     Test your agent
```

## Step 4: Deploy

After the import completes, run a deploy to reconcile the full stack:

```bash
agentcore deploy
```

This final deploy replaces any temporary placeholders created during the import and ensures the full CDK template is applied with proper resource dependencies.

## Step 5: Verify

Check that everything is running correctly:

```bash
# Check resource status
agentcore status

# Test your agent
agentcore invoke
```

## What Gets Migrated

| Starter Toolkit Resource | CLI Equivalent |
| --- | --- |
| Agents (`agents:` in YAML) | `agents` in `agentcore.json` + source in `app/<name>/` |
| Memory (`memory:` per agent) | `memories` in `agentcore.json` |
| Credentials (OAuth, API key providers) | `credentials` in `agentcore.json` |
| Deployment type (`direct_code_deploy`) | Build type `CodeZip` |
| Deployment type (`container`) | Build type `Container` |
| Network configuration (VPC) | `networkMode` + `networkConfig` in agent spec |
| Protocol (HTTP, MCP) | `protocol` in agent spec |
| Observability settings | `instrumentation.enableOtel` in agent spec |
| AWS account/region | Deployment target in `aws-targets.json` |

## Important Notes

- **Idempotent** — You can safely re-run `agentcore import` if something goes wrong. Already-imported resources are skipped.
- **Undeployed agents** — Agents that haven't been deployed yet (no `agent_id` in YAML) are imported as config-only — no CloudFormation operations are performed for them.
- **Memory environment variables** — If your agent code references memory IDs via environment variables, the import will display a diff-style hint showing any mismatches to address.
- **Execution role** — The existing execution role on your runtime is preserved during import. The imported agent will continue to use its original execution role, and the CLI will not manage or modify it.
- **Shared memory** — If multiple agents share the same memory, the import deduplicates it into a single memory resource.

## Project Structure Comparison

### Before (Starter Toolkit)

```
my-starter-project/
├── .bedrock_agentcore.yaml    # Configuration
├── my_agent.py                # Agent source
├── requirements.txt
└── Dockerfile                 # (if container build)
```

### After (AgentCore CLI)

```
my-project/
├── agentcore/
│   ├── agentcore.json         # Resource specifications
│   ├── aws-targets.json       # Deployment targets
│   └── cdk/                   # CDK infrastructure
├── app/
│   └── my_agent/              # Agent source (copied)
│       ├── main.py
│       └── pyproject.toml
```

## Troubleshooting

### Import fails with AWS credential errors

Ensure your AWS credentials are configured for the same account and region as your Starter Toolkit deployment:

```bash
aws sts get-caller-identity
```

### CloudFormation stack already exists

If a stack with the same name already exists, the import will update it. If you encounter conflicts, you can specify a different target:

```bash
agentcore import --source /path/to/.bedrock_agentcore.yaml --target my-new-target
```

### Agent source not found

The import expects agent source code to be at the path specified by `source_path` or `entrypoint` in your YAML config. Ensure these paths are valid relative to the YAML file location.

## Further Reading

- [AgentCore CLI Documentation](https://github.com/aws/agentcore-cli)
- [AgentCore CLI Commands Reference](https://github.com/aws/agentcore-cli/blob/main/docs/commands.md)
- [Bedrock AgentCore Starter Toolkit](https://github.com/aws/bedrock-agentcore-starter-toolkit)
- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)
