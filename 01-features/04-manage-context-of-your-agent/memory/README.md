# Amazon Bedrock AgentCore Memory

AgentCore Memory is a fully managed service that gives your AI agents the ability to remember past interactions, enabling more intelligent, context-aware, and personalised conversations. It handles both short-term context and long-term knowledge retention without the need to build or manage infrastructure.

![AgentCore memory](images/ac-memory.png)

## Start here

New to AgentCore Memory? → [`00-getting-started/`](./00-getting-started/). You'll get the vocabulary, pick a surface (CLI / boto3 / AgentCore SDK), and walk the same end-to-end flow.

## Top-level layout

| Folder | What's inside |
|---|---|
| [`00-getting-started/`](./00-getting-started/) | Concepts, surface decision guide, three quickstarts (CLI, boto3, AgentCore SDK) |
| [`01-short-term-memory/`](./01-short-term-memory/) | Events, sessions, isolation, branching, payload types — plus framework examples under `examples/` |
| [`02-long-term-memory/`](./02-long-term-memory/) | Strategies, overrides, self-managed, namespaces, retrieval, metadata, batch CRUD, redrive, streaming — plus framework examples |
| [`03-integrations/`](./03-integrations/) | Runtime, identity, Guardrails, memory-browser |
| [`04-observability/`](./04-observability/) | CloudWatch metrics, alarms, ingestion logs |
| [`05-security/`](./05-security/) | IAM scoping, Cognito federation, KMS encryption |
| [`06-production-patterns/`](./06-production-patterns/) | Error handling & retries, cost optimization, deploy-to-prod checklist |

## How this tree is organised

Each feature folder follows the same shape:

```
<NN-feature>/
├── README.md             # what it is, when to use, best practices
├── standard-usage.py     # canonical end-to-end flow
├── <NN-sub-feature>/     # one folder per sub-feature, each with its own README + script
└── examples/             # framework-specific use cases (single-agent, multi-agent)
    ├── single-agent/
    └── multi-agent/
```

The two memory types (`01-short-term-memory/`, `02-long-term-memory/`) are the primary axis. Sub-features sit underneath. Framework integrations are use cases that exercise a feature, not a parallel hierarchy — they live under each feature's `examples/`.

## The three integration patterns

When wiring memory into an agent framework, you have three options:

| Pattern | What it is | When to use |
|---|---|---|
| **Built-in hook / callback / memory-block** | The framework's out-of-the-box AgentCore adapter | Fastest path; standard save/retrieve lifecycle |
| **Custom hook / callback / memory-block** | You implement your own | Conditional logic, custom retrieval, multi-strategy orchestration |
| **Memory-as-tool** | Memory ops exposed as tools the LLM calls | Agent decides when to recall/save |

You'll find these patterns under `examples/single-agent/` and `examples/multi-agent/` in both memory-type folders.

## AgentCore CLI

Add memory to an existing runtime agent project with the AgentCore CLI:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }

# Interactive
agentcore add memory

# Non-interactive
agentcore add memory \
  --name mymemory \
  --strategies SEMANTIC,USER_PREFERENCE \
  --expiry 30

agentcore deploy
```

Supported strategies: `SEMANTIC`, `SUMMARIZATION`, `USER_PREFERENCE`, `EPISODIC`.

For direct control over memory resources, use the AWS CLI — see [`00-getting-started/03-quickstart-cli.md`](./00-getting-started/03-quickstart-cli.md).

## Prerequisites

- Python 3.10 or higher
- AWS account with Amazon Bedrock and AgentCore access
- Per-tutorial `requirements.txt` where present

## Resources

- [AgentCore Memory documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)
- [Deep-dive video](https://www.youtube.com/live/-N4v6-kJgwA)
