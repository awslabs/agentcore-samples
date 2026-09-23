# LlamaIndex + AgentCore memory — short-term

Four domain-specific notebooks showing how to wire LlamaIndex's `AgentCoreMemory` context into a `FunctionAgent` for session-scoped conversation memory.

| Notebook | Domain | Focus |
|---|---|---|
| `academic-research-assistant-short-term-memory-tutorial.py` | Academic research | Paper tracking, cross-reference within a session |
| `legal-document-analyzer-short-term-memory-tutorial.py` | Legal | Contract analysis, risk assessment |
| `medical-knowledge-assistant-short-term-memory-tutorial.py` | Medical | Patient consultation, drug interactions |
| `investment-portfolio-advisor-short-term-memory-tutorial.py` | Finance | Client profiling, portfolio recommendations |

All four follow the same pattern — pick whichever domain is closest to your use case.

## Integration pattern

```python
from llama_index.memory.bedrock_agentcore import AgentCoreMemory, AgentCoreMemoryContext

context = AgentCoreMemoryContext(
    actor_id="user-id",
    memory_id=memory_id,
    session_id="session-id",
    namespace="/your-namespace/",
)
agentcore_memory = AgentCoreMemory(context=context)

# Pass memory explicitly on each run
await agent.run(message, memory=agentcore_memory)
```

The long-term counterparts live in [`../../../../02-long-term-memory/examples/single-agent/with-llamaindex-agent/03-memory-tool/`](../../../../02-long-term-memory/examples/single-agent/with-llamaindex-agent/03-memory-tool/).

## Prerequisites

- Python 3.10+
- AWS credentials with AgentCore memory permissions
- Bedrock model access (default: `us.anthropic.claude-3-7-sonnet-20250219-v1:0`)
- `pip install -r requirements.txt`

## See also

- [AgentCore memory documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)
- [Concepts primer](../../../../00-getting-started/01-memory-concepts.md)

## Running the Python Scripts

Install dependencies (if a `requirements.txt` is present):

```bash
pip install -r requirements.txt
```

Run each script directly:

```bash
python academic-research-assistant-short-term-memory-tutorial.py
python investment-portfolio-advisor-short-term-memory-tutorial.py
python legal-document-analyzer-short-term-memory-tutorial.py
python medical-knowledge-assistant-short-term-memory-tutorial.py
```
