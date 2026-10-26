# AgentCore Evaluations — Supported Frameworks

This folder contains code samples demonstrating AgentCore Evaluations running
against agents built with different frameworks. Each sample builds the **same
HR Assistant agent** (same tools, same mock data, same evaluation scenarios) so
results are directly comparable.

## Samples

| Framework | Instrumentation | Scope Name | Sample |
|-----------|----------------|------------|--------|
| [Google ADK](google-adk/) | `openinference-instrumentation-google-adk >= 0.1.13` | `openinference.instrumentation.google_adk` | [→](google-adk/) |
| [Claude Agent SDK](claude-agent-sdk/) | `openinference-instrumentation-claude-agent-sdk >= 0.1.3` | `openinference.instrumentation.claude_agent_sdk` | [→](claude-agent-sdk/) |

## Key takeaway

**AgentCore Evaluations is framework-agnostic.** You add the instrumentation
library to your dependencies, deploy to AgentCore Runtime (where ADOT
auto-discovers it), and the evaluation service reads the spans — regardless of
which framework produced them.

No changes to your agent code. No custom telemetry plumbing. Just:

```
requirements.txt:
    openinference-instrumentation-<your-framework>>=X.Y.Z
```

## Evaluation pipeline (shared across all samples)

```
Deploy agent → Invoke (3 turns) → Wait for spans → On-demand eval → Online eval config
```

### Evaluators used

| Evaluator | Level | What it measures |
|-----------|-------|------------------|
| `Builtin.GoalSuccessRate` | SESSION | Did the agent fulfill the user's goal? |
| `Builtin.Correctness` | TRACE | Is each response factually correct? |
| `Builtin.Helpfulness` | TRACE | Was each response helpful? |
| `HRResponseQuality` (custom) | TRACE | HR-specific accuracy + completeness + tone |

## Shared code

`shared/mock_data.py` contains the HR mock data and evaluation scenarios imported
by both framework samples. This ensures identical tool behavior across frameworks.

## Full list of supported frameworks

See [Supported agent frameworks](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/supported-frameworks.html)
for the complete list including Strands, LangGraph, OpenAI Agents, and LlamaIndex.
